"""Scenario orchestrator: source → treatment train → cooling tower → risk → cost.

``run_scenario`` evaluates one operating scenario end-to-end; ``compare_scenarios``
runs Baseline vs Optimized and the lifecycle-cost analysis of the upgrade.
All inputs come from the config dict (see ``config/design_defaults.yaml``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from . import hydraulics as hyd
from . import microbiology_qmra as qm
from . import thermodynamics as th
from . import water_quality as wq
from .config import deep_merge
from .constants import (
    BTU_PER_KWH,
    CACO3_PER_CA,
    G_ACCEL,
    L_PER_GAL,
    LB_PER_TONNE,
    LRV_CLASSES,
    M3_PER_GAL,
    MIN_PER_DAY,
    SPECIES,
)
from .economics import FinancialSummary, evaluate_investment, per_kgal_from_hcf
from .mass_balance import (
    NodeResult,
    ProcessStep,
    ProcessTrain,
    TowerBalance,
    cooling_tower_balance,
    max_coc_for_drift,
    ro_mass_removal,
)

TREATMENT_ORDER = ("intake", "coag_sed", "mmf", "uf", "ro", "chlorination")
CHLORINE_CREDIT_KEY = {"rotavirus": "virus", "giardia": "giardia", "cryptosporidium": "cryptosporidium",
                       "vibrio_cholerae": "bacteria", "legionella": "bacteria"}
SHORT_TON_KG = 907.185


def stokes_velocity_m_s(d_um: float, rho_p: float, rho_w: float, mu: float) -> float:
    """v_s = g·(ρp − ρw)·d² / (18·μ)."""
    d = d_um * 1.0e-6
    return G_ACCEL * (rho_p - rho_w) * d ** 2 / (18.0 * mu)


def stokes_m_h(s: Mapping[str, float]) -> float:
    return stokes_velocity_m_s(s["particle_diameter_um"], s["particle_density_kg_m3"],
                               s["water_density_kg_m3"], s["viscosity_pa_s"]) * 3600.0


def ro_energy_kwh_per_kgal(feed_tds_mg_l: float) -> float:
    """Screening RO specific energy: 0.3 + 0.1·TDS(g/L) kWh/m³ (≈3.8 kWh/m³ for seawater)."""
    return (0.3 + 0.1 * feed_tds_mg_l / 1000.0) * 1000.0 * M3_PER_GAL


def coag_tss_removal(cfg_coag: Mapping[str, Any]) -> float:
    """Hazen ideal-settling: R_TSS = R_design × min(1, v_s / overflow rate)."""
    s = cfg_coag["stokes"]
    return cfg_coag["removal"].get("tss", 0.0) * min(1.0, stokes_m_h(s) / s["overflow_rate_m_h"])


def build_train(cfg: Mapping[str, Any]) -> ProcessTrain:
    """Create the ordered, toggleable treatment train from config."""
    steps: List[ProcessStep] = []
    for key in TREATMENT_ORDER:
        n = cfg["treatment"][key]
        removal = dict(n.get("removal") or {})
        if key == "coag_sed":
            removal["tss"] = coag_tss_removal(n)
        if key == "ro":
            removal = {s: ro_mass_removal(r, n["recovery"]) for s, r in n["rejection"].items()}
        steps.append(ProcessStep(
            key=key, label=n["label"], enabled=bool(n["enabled"]), recovery=float(n["recovery"]),
            removal=removal, dosing=dict(n.get("dosing") or {}), ph_out=n.get("ph_out"),
            lrv=dict(n.get("lrv") or {}), energy_kwh_per_kgal=n.get("energy_kwh_per_kgal"),
        ))
    return ProcessTrain(steps)


@dataclass
class ScenarioResult:
    key: str
    label: str
    source_key: str
    source_label: str
    raw_quality: Dict[str, float]
    raw_pathogens: Dict[str, float]
    treatment_nodes: List[NodeResult]
    tower: TowerBalance
    fill: th.FillDesign
    fill_perf: th.FillPerformance
    fan: hyd.FanResult
    q_cooling_btuh: float
    cop: float
    tower_load_btuh: float
    recirc_gpm: float
    coc_target: float
    coc_candidates: Dict[str, float]
    coc_limiting: str
    coc_feasible: bool
    leed_max_cycles: float
    leed_points: int
    nonpotable_fraction: float
    scaling: Dict[str, wq.ScalingIndices]
    silica_alert: bool
    recirc_exceedances: Dict[str, bool]
    stokes_velocity_m_h: float
    ct: Dict[str, float]
    lrv_total: Dict[str, float]
    risks: Dict[str, qm.PathogenRisk]
    pumps: Dict[str, hyd.PumpResult]
    power_kw: Dict[str, float]
    daily: Dict[str, float]
    annual: Dict[str, float]
    costs: Dict[str, float]
    kpis: Dict[str, float]
    warnings: List[str] = field(default_factory=list)

    @property
    def intake_gpd(self) -> float:
        return self.treatment_nodes[0].q_in_gpd


def _scenario_cfg(cfg: Mapping[str, Any], key: str, overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    sc = dict(cfg["scenarios"][key])
    return deep_merge(sc, dict(overrides)) if overrides else sc


def run_scenario(cfg: Mapping[str, Any], key: str = "baseline",
                 overrides: Optional[Mapping[str, Any]] = None) -> ScenarioResult:
    """Evaluate one scenario. ``overrides`` deep-merge into ``cfg['scenarios'][key]``."""
    sc = _scenario_cfg(cfg, key, overrides)
    lib = cfg["library"]
    site, dc, ct_cfg = cfg["site"], cfg["data_center"], cfg["cooling_tower"]
    src = lib["sources"][site["source"]]
    raw = {s: float(src["chemistry"][s]) for s in SPECIES}
    raw_p = {p: float(v) for p, v in src["pathogens"].items()}
    warnings: List[str] = []

    # 1) Treatment-train product quality (flow independent) ----------------------------
    train = build_train(cfg)
    c_fresh = train.product_quality(raw)

    # 2) Heat rejection & fill ----------------------------------------------------------
    fill = th.FillDesign.from_dict(sc["fill"])
    perf = th.simulate_fill(fill, cfg["fill_model"], ct_cfg["range_f"], site["wet_bulb_f"],
                            ct_cfg["l_over_g"], site["barometric_kpa"])
    q_cool = th.cooling_load_from_it(dc["it_load_mw"], dc["other_heat_fraction"])
    cop = th.chiller_cop(dc["cop_ref"], perf.cold_water_f, dc["cwst_ref_f"], dc["cop_sensitivity_per_f"])
    tl = th.tower_load(q_cool, cop)
    recirc = th.recirculation_gpm(tl, ct_cfg["range_f"])
    evap_gpd = th.evaporation_gph(tl) * 24.0
    drift_gpd = th.drift_gpm(recirc, fill.drift_fraction) * MIN_PER_DAY
    fan = hyd.induced_draft_fan(recirc, fill, ct_cfg, cfg["fill_model"])

    # 3) Cycles of concentration --------------------------------------------------------
    hot_c = (perf.hot_water_f - 32.0) / 1.8
    leed = wq.species_coc_limits(c_fresh, lib["leed_condenser_limits"])
    candidates: Dict[str, float] = {"target": float(sc["coc_target"])}
    candidates.update({f"LEED {k}": v for k, v in leed.items()})
    candidates["LSI"] = wq.lsi_coc_limit(c_fresh, sc["recirc_ph"], hot_c, ct_cfg["max_lsi"])
    candidates["drift"] = max_coc_for_drift(evap_gpd, drift_gpd)
    limiting, coc = wq.limiting_coc(candidates)
    feasible = coc >= ct_cfg["coc_min"]
    if not feasible:
        warnings.append(f"Makeup water limits CoC to {coc:.2f} (< {ct_cfg['coc_min']}); "
                        "enable RO/IX (seawater-grade membranes for seawater, ~99.8 % rejection) or change source.")
        coc = ct_cfg["coc_min"]

    # 4) Tower + blowdown recovery mass balance ---------------------------------------------
    bdr = cfg["blowdown_recovery"]
    rec = bdr["recovery"] if sc["blowdown_recovery"] else 0.0
    tower = cooling_tower_balance(evap_gpd, drift_gpd, coc, c_fresh, sc["recirc_ph"],
                                  ct_cfg["recirc_cl2_mg_l"], rec, bdr["rejection"] if rec else None)

    # 5) Treatment flows sized from fresh makeup ------------------------------------------------
    intake = train.required_intake(tower.fresh_makeup_gpd)
    nodes = train.run(intake, raw)
    for st, nd in zip(train.steps, nodes):
        if not nd.enabled:
            continue
        e = st.energy_kwh_per_kgal
        if st.key == "ro" and e is None:
            e = ro_energy_kwh_per_kgal(nd.c_in["tds"])
        nd.energy_kwh_d = (e or 0.0) * nd.q_in_gpd / 1000.0
    bd_ro_kwh_d = ro_energy_kwh_per_kgal(tower.c_recirc["tds"]) * tower.blowdown_gpd / 1000.0 if rec else 0.0

    # 6) Water quality checks ----------------------------------------------------------------
    cold_c = (perf.cold_water_f - 32.0) / 1.8
    scaling = {
        "makeup": wq.scaling_indices(c_fresh, cold_c),
        "recirc_cold": wq.scaling_indices(tower.c_recirc, cold_c),
        "recirc_hot": wq.scaling_indices(tower.c_recirc, hot_c),
    }
    sio2_alert = wq.silica_alert(tower.c_recirc["sio2"], lib["silica_alert_mg_l"])
    lim = lib["leed_condenser_limits"]
    rc = tower.c_recirc
    exceed = {
        "ca_as_caco3": rc["ca"] * CACO3_PER_CA > lim["ca_as_caco3"] * 1.0001,
        "alkalinity": rc["alkalinity"] > lim["alkalinity"] * 1.0001,
        "sio2": rc["sio2"] > lim["sio2"] * 1.0001,
        "cl": rc["cl"] > lim["cl"] * 1.0001,
        "ec": rc["ec"] > lim["ec"] * 1.0001,
    }
    if sio2_alert:
        warnings.append(f"Recirculating silica {rc['sio2']:.0f} mg/L > {lib['silica_alert_mg_l']} mg/L: blowdown alert.")
    if scaling["recirc_hot"].lsi > ct_cfg["max_lsi"] + 1e-6:
        warnings.append("Recirculating LSI exceeds the inhibitor program ceiling.")

    # LEED v4 WE Cooling Tower Water Use
    leed_max = min(min(leed.values()), 10.0)
    nonpot = tower.permeate_gpd + (tower.fresh_makeup_gpd if src.get("recycled_nonpotable") else 0.0)
    nonpot_frac = nonpot / tower.tower_makeup_gpd
    extra_treatment = cfg["treatment"]["ro"]["enabled"] or rec > 0
    pt1 = coc >= leed_max - 1e-6
    leed_points = 2 if ((coc >= 10.0 - 1e-6 and extra_treatment) or (pt1 and nonpot_frac >= 0.20)) else (1 if pt1 else 0)

    # 7) Disinfection CT & QMRA -------------------------------------------------------------
    cl_cfg = cfg["treatment"]["chlorination"]
    cl_node = nodes[TREATMENT_ORDER.index("chlorination")]
    ct_info: Dict[str, float] = {"residual_mg_l": 0.0, "q_peak_gpm": 0.0, "tdt_min": 0.0, "t10_min": 0.0, "ct": 0.0}
    cl_credit = {k: 0.0 for k in ("virus", "giardia", "cryptosporidium", "bacteria")}
    if cl_node.enabled:
        q_peak = cl_node.q_in_gpd / MIN_PER_DAY * cl_cfg["peak_factor"]
        tdt = qm.total_detention_time_min(cl_cfg["contact_volume_gal"], q_peak)
        t10 = qm.t10_min(tdt, cl_cfg["baffling_factor"])
        resid = cl_node.c_out["cl2"]
        ct_val = qm.ct_value(resid, t10)
        cr = qm.chlorine_lrv(ct_val, cl_cfg["temperature_c"])
        cl_credit = {k: cr[k] for k in cl_credit}
        ct_info = {"residual_mg_l": resid, "q_peak_gpm": q_peak, "tdt_min": tdt, "t10_min": t10, "ct": ct_val,
                   "ct_req_virus_4log": cr["ct_req_virus_4log"], "ct_req_giardia_3log": cr["ct_req_giardia_3log"]}

    physical = {c: 0.0 for c in LRV_CLASSES}
    for st in train.steps:
        if st.enabled:
            for c in LRV_CLASSES:
                physical[c] += float(st.lrv.get(c, 0.0))
    dr = lib["dose_response"]
    ex = cfg["microbial_exposure"]
    lrv_total: Dict[str, float] = {}
    risks: Dict[str, qm.PathogenRisk] = {}
    aerosol = qm.aerosol_water_l_per_m3(tower.drift_gpd / MIN_PER_DAY, fan.airflow_m3_s, ex["receptor_dilution"])
    inh_vol = qm.inhalation_volume_l(aerosol, ex["respirable_fraction"], ex["breathing_rate_m3_h"],
                                     ex["exposure_hours_per_event"])
    for p, params in dr.items():
        lrv_total[p] = physical[params["class"]] + cl_credit[CHLORINE_CREDIT_KEY[p]]
        if params["route"] == "inhalation":
            risks[p] = qm.assess_pathogen(p, params, raw_p.get(p, 0.0), lrv_total[p], inh_vol,
                                          ex["inhalation_events_per_year"], ex["target_annual_risk"],
                                          c_exposure_override=ex["legionella_recirc_cfu_l"])
        else:
            risks[p] = qm.assess_pathogen(p, params, raw_p.get(p, 0.0), lrv_total[p], ex["ingestion_volume_l"],
                                          ex["ingestion_events_per_year"], ex["target_annual_risk"])

    # 8) Hydraulics & power -------------------------------------------------------------------
    pumps = {
        "chw": hyd.size_pump_station(cfg["pumps"]["chw"]["label"],
                                     th.chilled_water_flow_gpm(q_cool, dc["chw_delta_t_f"]), cfg["pumps"]["chw"]),
        "cw": hyd.size_pump_station(cfg["pumps"]["cw"]["label"], recirc, cfg["pumps"]["cw"]),
        "makeup": hyd.size_pump_station(cfg["pumps"]["makeup"]["label"], intake / MIN_PER_DAY, cfg["pumps"]["makeup"]),
    }
    treat_kwh_d = sum(n.energy_kwh_d for n in nodes) + bd_ro_kwh_d
    power = {
        "chiller": th.chiller_power_kw(q_cool, cop),
        "tower_fans": fan.kw,
        "chw_pumps": pumps["chw"].kw,
        "cw_pumps": pumps["cw"].kw,
        "makeup_pumps": pumps["makeup"].kw,
        "water_treatment": treat_kwh_d / 24.0,
    }
    power["total"] = sum(power.values())
    it_kw = dc["it_load_mw"] * 1000.0

    # 9) Daily & annual quantities ----------------------------------------------------------------
    treat_waste = sum(n.q_waste_gpd for n in nodes)
    sewer_gpd = treat_waste + tower.brine_gpd
    eco = cfg["economics"]
    coag_n = nodes[TREATMENT_ORDER.index("coag_sed")]
    alum_kg_d = cfg["treatment"]["coag_sed"]["alum_dose_mg_l"] * coag_n.q_in_gpd * L_PER_GAL / 1e6 if coag_n.enabled else 0.0
    naocl_kg_d = (cl_cfg["dosing"].get("cl2", 0.0) * cl_node.q_in_gpd * L_PER_GAL / 1e6 if cl_node.enabled else 0.0) \
        + ct_cfg["biocide_dose_mg_l_makeup"] * tower.tower_makeup_gpd * L_PER_GAL / 1e6
    inhib_kg_d = ct_cfg["inhibitor_mg_l_recirc"] * (tower.blowdown_gpd + tower.drift_gpd) * L_PER_GAL / 1e6
    tss_kg_d = sum(n.q_waste_gpd * n.c_waste["tss"] for n in nodes if n.enabled) * L_PER_GAL / 1e6
    sludge_ton_d = (tss_kg_d + alum_kg_d * eco["alum_sludge_yield"]) / SHORT_TON_KG
    daily = {
        "intake_gpd": intake, "fresh_makeup_gpd": tower.fresh_makeup_gpd, "tower_makeup_gpd": tower.tower_makeup_gpd,
        "evaporation_gpd": evap_gpd, "drift_gpd": drift_gpd, "blowdown_gpd": tower.blowdown_gpd,
        "permeate_gpd": tower.permeate_gpd, "brine_gpd": tower.brine_gpd, "treatment_waste_gpd": treat_waste,
        "sewer_gpd": sewer_gpd, "alum_kg": alum_kg_d, "naocl_kg": naocl_kg_d, "inhibitor_kg": inhib_kg_d,
        "sludge_dry_ton": sludge_ton_d, "treatment_kwh": treat_kwh_d,
    }
    days = site["hours_per_year"] / 24.0 * site["utilization"]
    annual = {k.replace("_gpd", "_gal"): v * days for k, v in daily.items()}
    annual["electricity_kwh"] = power["total"] * site["hours_per_year"] * site["utilization"]
    annual["it_kwh"] = it_kw * site["hours_per_year"] * site["utilization"]
    annual["co2_tonne"] = annual["electricity_kwh"] * eco["grid_lb_co2_per_kwh"] / LB_PER_TONNE

    # 10) Costs (year 1) -------------------------------------------------------------------------
    water_rate = per_kgal_from_hcf(eco["water_rate_per_hcf"])
    sewer_rate = water_rate * eco["sewer_multiplier"]
    sewer_billed = annual["sewer_gal"] if eco["cooling_tower_sewer_allowance"] else annual["intake_gal"]
    costs = {
        "water": annual["intake_gal"] / 1000.0 * water_rate if src.get("purchased") else 0.0,
        "sewer": sewer_billed / 1000.0 * sewer_rate,
        "electricity": annual["electricity_kwh"] * eco["electricity_per_kwh"],
        "chemicals": annual["alum_kg"] * eco["alum_per_kg"] + annual["naocl_kg"] * eco["naocl_per_kg"]
        + annual["inhibitor_kg"] * eco["inhibitor_per_kg"],
        "sludge": annual["sludge_dry_ton"] * eco["sludge_per_dry_ton"],
        "carbon": annual["co2_tonne"] * eco["social_cost_carbon_per_tonne"],
    }
    costs["total"] = sum(costs.values())

    # 11) KPIs ----------------------------------------------------------------------------------
    water_kw = power["makeup_pumps"] + power["water_treatment"]
    kpis = {
        "cooling_load_mw": q_cool / BTU_PER_KWH / 1000.0,
        "cooling_load_tons": th.tons(q_cool),
        "tower_load_tons": th.tons(tl),
        "cop": cop,
        "approach_f": perf.approach_f,
        "coc": coc,
        "intake_gpd": intake,
        "intake_m3d": intake * M3_PER_GAL,
        "wue_l_per_kwh": intake * L_PER_GAL / (it_kw * 24.0),
        "cooling_pue_share": (power["total"]) / it_kw,
        "pue": 1.0 + power["total"] / it_kw + dc["other_overhead_pue"],
        "water_energy_kwh_m3": water_kw * 24.0 / (intake * M3_PER_GAL),
        "annual_cost": costs["total"],
        "co2_tonne": annual["co2_tonne"],
        "leed_points": float(leed_points),
    }
    return ScenarioResult(
        key=key, label=sc["label"], source_key=site["source"], source_label=src["label"],
        raw_quality=raw, raw_pathogens=raw_p, treatment_nodes=nodes, tower=tower, fill=fill, fill_perf=perf,
        fan=fan, q_cooling_btuh=q_cool, cop=cop, tower_load_btuh=tl, recirc_gpm=recirc,
        coc_target=float(sc["coc_target"]), coc_candidates=candidates, coc_limiting=limiting, coc_feasible=feasible,
        leed_max_cycles=leed_max, leed_points=leed_points, nonpotable_fraction=nonpot_frac, scaling=scaling,
        silica_alert=sio2_alert, recirc_exceedances=exceed,
        stokes_velocity_m_h=stokes_m_h(cfg["treatment"]["coag_sed"]["stokes"]),
        ct=ct_info, lrv_total=lrv_total, risks=risks, pumps=pumps, power_kw=power, daily=daily, annual=annual,
        costs=costs, kpis=kpis, warnings=warnings,
    )


@dataclass
class Comparison:
    baseline: ScenarioResult
    optimized: ScenarioResult
    fill_only: ScenarioResult
    capex: Dict[str, float]
    finance: FinancialSummary
    deltas: Dict[str, float]


def upgrade_capex(cfg: Mapping[str, Any], base: ScenarioResult, opt: ScenarioResult) -> Dict[str, float]:
    cx = cfg["economics"]["capex"]
    area = opt.fan.plan_area_m2
    items = {
        "fill": area * opt.fill.fill_height_m * cx["fill_per_m3"],
        "drift_eliminators": area * cx["eliminator_per_m2"],
        "blowdown_ro": (opt.tower.blowdown_gpd * cx["blowdown_ro_per_gpd"]
                        if opt.tower.recovery_enabled and not base.tower.recovery_enabled else 0.0),
    }
    subtotal = sum(items.values())
    items["installation"] = subtotal * (cx["installation_factor"] - 1.0)
    items["total"] = subtotal + items["installation"]
    return items


def compare_scenarios(cfg: Mapping[str, Any]) -> Comparison:
    """Baseline vs Optimized, a fill-only attribution case, and the upgrade's LCC."""
    base = run_scenario(cfg, "baseline")
    opt = run_scenario(cfg, "optimized")
    fill_only = run_scenario(cfg, "baseline", {"fill": cfg["scenarios"]["optimized"]["fill"],
                                               "label": "Baseline + corrugated fill only"})
    capex = upgrade_capex(cfg, base, opt)
    eco = cfg["economics"]
    savings = base.costs["total"] - opt.costs["total"]
    fin = evaluate_investment(capex["total"], savings, eco["interest_rate"], eco["inflation_rate"],
                              eco["escalation_rate"], int(eco["horizon_years"]))
    deltas = {
        "intake_gpd": base.intake_gpd - opt.intake_gpd,
        "evaporation_gpd": base.daily["evaporation_gpd"] - opt.daily["evaporation_gpd"],
        "drift_gpd": base.daily["drift_gpd"] - opt.daily["drift_gpd"],
        "blowdown_to_sewer_gpd": base.daily["brine_gpd"] - opt.daily["brine_gpd"],
        "fill_only_intake_gpd": base.intake_gpd - fill_only.intake_gpd,
        "fill_only_evaporation_gpd": base.daily["evaporation_gpd"] - fill_only.daily["evaporation_gpd"],
        "approach_f": base.fill_perf.approach_f - opt.fill_perf.approach_f,
        "chiller_kw": base.power_kw["chiller"] - opt.power_kw["chiller"],
        "fan_kw": base.power_kw["tower_fans"] - opt.power_kw["tower_fans"],
        "fan_dp_pa": opt.fan.total_dp_pa - base.fan.total_dp_pa,
        "total_kw": base.power_kw["total"] - opt.power_kw["total"],
        "annual_cost": savings,
    }
    return Comparison(base, opt, fill_only, capex, fin, deltas)
