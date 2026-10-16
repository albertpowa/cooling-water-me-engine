"""Streamlit dashboard for the Data Center Cooling Water M&E engine.

Run:  streamlit run app.py      (from the repository root)
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine import compare_scenarios, load_config  # noqa: E402
from src.engine import thermodynamics as th  # noqa: E402
from src.engine.constants import (  # noqa: E402
    M3_PER_GAL,
    PATHOGEN_LABELS,
    SPECIES,
    SPECIES_LABELS,
    SPECIES_UNITS,
)
from src.engine.scenario import Comparison, ScenarioResult  # noqa: E402
from src.excel.exporter import DEFAULT_FILENAME, workbook_bytes  # noqa: E402

# Validated categorical palette (dataviz reference instance): slot 1 blue, slot 2 orange, slot 3 aqua…
C_BASE, C_OPT, C3, C4, C5 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "rgba(82,81,78,0.15)"

st.set_page_config(page_title="Cooling Water M&E Engine", page_icon="💧", layout="wide")


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------
def style_fig(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=90, b=10), font=dict(color=INK),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      title=dict(y=0.97, yanchor="top", x=0, xanchor="left"),
                      legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0), hoverlabel=dict(font_size=12))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def paired_bar(cats: List[str], base: List[float], opt: List[float], title: str, unit: str,
               labels: Tuple[str, str]) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(name=labels[0], x=cats, y=base, marker_color=C_BASE, marker_line_width=0,
                hovertemplate="%{x}<br>%{y:,.0f} " + unit + "<extra>" + labels[0] + "</extra>")
    fig.add_bar(name=labels[1], x=cats, y=opt, marker_color=C_OPT, marker_line_width=0,
                hovertemplate="%{x}<br>%{y:,.0f} " + unit + "<extra>" + labels[1] + "</extra>")
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08, title=title, yaxis_title=unit)
    return style_fig(fig)


def fmt(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}"


def check(ok: bool, text: str) -> str:
    return ("✅ " if ok else "❌ ") + text


@st.cache_data(show_spinner=False)
def _run(cfg_json: str) -> Comparison:
    return compare_scenarios(json.loads(cfg_json))


@st.cache_data(show_spinner="Building Excel workbook…")
def _excel(cfg_json: str) -> bytes:
    return workbook_bytes(json.loads(cfg_json))


# ---------------------------------------------------------------------------------------
# Sidebar — Process Network Builder
# ---------------------------------------------------------------------------------------
def sidebar(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(cfg)
    sb = st.sidebar
    sb.title("💧 Process Network Builder")
    sources = cfg["library"]["sources"]
    keys = list(sources)
    cfg["site"]["source"] = sb.selectbox("Raw water source", keys, index=keys.index(cfg["site"]["source"]),
                                         format_func=lambda k: sources[k]["label"])

    sb.subheader("Heat load")
    dc = cfg["data_center"]
    mode = sb.radio("Specify load as", ["IT load (MW)", "Cooling load (tons)"], horizontal=True)
    if mode == "IT load (MW)":
        dc["it_load_mw"] = sb.slider("IT load (MW)", 1.0, 200.0, float(dc["it_load_mw"]), 1.0)
    else:
        tons_default = th.tons(th.cooling_load_from_it(dc["it_load_mw"], dc["other_heat_fraction"]))
        tons = sb.number_input("Cooling load (tons)", 100.0, 60_000.0, float(round(tons_default, -1)), 100.0)
        dc["it_load_mw"] = tons * 12_000 / 3412 / 1000 / (1 + dc["other_heat_fraction"])
        sb.caption(f"≡ IT load {dc['it_load_mw']:.1f} MW")
    cfg["site"]["wet_bulb_f"] = sb.slider("Design wet-bulb (°F)", 55.0, 85.0, float(cfg["site"]["wet_bulb_f"]), 0.5)
    dc["cop_ref"] = sb.slider("Chiller COP at 85 °F CWST", 3.0, 8.0, float(dc["cop_ref"]), 0.1)

    sb.subheader("Treatment train")
    tr = cfg["treatment"]
    for key in ("coag_sed", "mmf", "uf", "ro", "chlorination"):
        tr[key]["enabled"] = sb.checkbox(tr[key]["label"], value=bool(tr[key]["enabled"]), key=f"on_{key}")
    if tr["ro"]["enabled"]:
        tr["ro"]["recovery"] = sb.slider("RO recovery Y", 0.40, 0.90, float(tr["ro"]["recovery"]), 0.01)

    with sb.expander("Chemical dosing"):
        alum = st.slider("Alum (mg/L)", 0.0, 60.0, float(tr["coag_sed"]["alum_dose_mg_l"]), 1.0)
        f = alum / max(float(tr["coag_sed"]["alum_dose_mg_l"]), 1e-9)
        tr["coag_sed"]["dosing"] = {k: v * f for k, v in tr["coag_sed"]["dosing"].items()}
        tr["coag_sed"]["alum_dose_mg_l"] = alum
        tr["chlorination"]["dosing"]["cl2"] = st.slider("NaOCl at contact tank (mg/L Cl₂)", 0.0, 5.0,
                                                        float(tr["chlorination"]["dosing"]["cl2"]), 0.1)
        ct = cfg["cooling_tower"]
        ct["biocide_dose_mg_l_makeup"] = st.slider("Tower biocide (mg/L of makeup)", 0.0, 6.0,
                                                   float(ct["biocide_dose_mg_l_makeup"]), 0.1)
        ct["inhibitor_mg_l_recirc"] = st.slider("Scale inhibitor in recirc (mg/L)", 0.0, 30.0,
                                                float(ct["inhibitor_mg_l_recirc"]), 0.5)
        ct["max_lsi"] = st.slider("Max LSI with inhibitor program", 0.5, 3.0, float(ct["max_lsi"]), 0.1)

    sc = cfg["scenarios"]
    sb.subheader("Scenario levers")
    c1, c2 = sb.columns(2)
    sc["baseline"]["coc_target"] = c1.slider("Baseline CoC", 2.0, 10.0, float(sc["baseline"]["coc_target"]), 0.5)
    sc["optimized"]["coc_target"] = c2.slider("Optimized CoC", 2.0, 10.0, float(sc["optimized"]["coc_target"]), 0.5)
    sc["baseline"]["blowdown_recovery"] = c1.checkbox("Base BD-RO", bool(sc["baseline"]["blowdown_recovery"]))
    sc["optimized"]["blowdown_recovery"] = c2.checkbox("Opt BD-RO", bool(sc["optimized"]["blowdown_recovery"]))

    for skey, title in (("optimized", "Corrugated plate (Upgrade)"), ("baseline", "Existing fill (Baseline)")):
        fill = sc[skey]["fill"]
        with sb.expander(title, expanded=(skey == "optimized")):
            fill["ssa_m2_m3"] = st.slider("SSA (m²/m³)", 50.0, 400.0, float(fill["ssa_m2_m3"]), 1.0, key=f"ssa{skey}")
            fill["flute_angle_deg"] = st.slider("Flute angle (°)", 15.0, 75.0, float(fill["flute_angle_deg"]), 1.0,
                                                key=f"ang{skey}")
            fill["sheet_thickness_mm"] = st.slider("Sheet thickness (mm)", 0.15, 1.0,
                                                   float(fill["sheet_thickness_mm"]), 0.01, key=f"thk{skey}")
            fill["fill_height_m"] = st.slider("Fill height (m)", 0.5, 2.5, float(fill["fill_height_m"]), 0.05,
                                              key=f"h{skey}")
            pct = st.select_slider("Drift eliminator efficiency (drift % of recirc)",
                                   [0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001],
                                   value=min([0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001],
                                             key=lambda v: abs(v - fill["drift_fraction"] * 100)), key=f"dr{skey}")
            fill["drift_fraction"] = pct / 100.0

    with sb.expander("Economics (NYC DEP FY22 defaults)"):
        eco = cfg["economics"]
        eco["water_rate_per_hcf"] = st.number_input("Water $/100 ft³", 0.0, 20.0, float(eco["water_rate_per_hcf"]), 0.05)
        eco["electricity_per_kwh"] = st.number_input("Electricity $/kWh", 0.0, 1.0, float(eco["electricity_per_kwh"]), 0.01)
        eco["interest_rate"] = st.number_input("Interest rate", 0.0, 0.3, float(eco["interest_rate"]), 0.005, format="%.3f")
        eco["inflation_rate"] = st.number_input("Inflation rate", 0.0, 0.2, float(eco["inflation_rate"]), 0.005, format="%.3f")
        eco["social_cost_carbon_per_tonne"] = st.number_input("Social cost of carbon $/t", 0.0, 500.0,
                                                              float(eco["social_cost_carbon_per_tonne"]), 1.0)
    return cfg


# ---------------------------------------------------------------------------------------
# Sankey
# ---------------------------------------------------------------------------------------
def sankey(res: ScenarioResult) -> go.Figure:
    nodes: List[str] = []
    colors: List[str] = []
    links: List[Tuple[int, int, float, str]] = []

    def node(label: str, color: str = "#8a8984") -> int:
        if label not in nodes:
            nodes.append(label)
            colors.append(color)
        return nodes.index(label)

    src = node(f"{res.source_label} intake", C_BASE)
    sewer = node("Sewer / waste", C3)
    prev = src
    short = {"intake": "Intake", "coag_sed": "Coag/Floc/Sed", "mmf": "Multi-media filter", "uf": "UF",
             "ro": "RO / IX", "chlorination": "Cl₂ contact"}
    for n in res.treatment_nodes:
        if not n.enabled or n.key == "intake":      # intake is the source node itself
            continue
        idx = node(short.get(n.key, n.label), "#52514e")
        links.append((prev, idx, n.q_in_gpd, "rgba(42,120,214,0.35)"))
        if n.q_waste_gpd > 0.5:
            links.append((idx, sewer, n.q_waste_gpd, "rgba(27,175,122,0.35)"))
        prev = idx
    t = res.tower
    tower = node("Cooling tower", C_BASE)
    links.append((prev, tower, t.fresh_makeup_gpd, "rgba(42,120,214,0.35)"))
    if t.permeate_gpd > 0:
        reuse_in = node("Reuse ↺ (permeate in)", C5)
        links.append((reuse_in, tower, t.permeate_gpd, "rgba(232,123,164,0.4)"))
    links.append((tower, node("Evaporation", C4), t.evaporation_gpd, "rgba(237,161,0,0.4)"))
    links.append((tower, node("Drift", C_OPT), max(t.drift_gpd, 1.0), "rgba(235,104,52,0.45)"))
    if t.recovery_enabled:
        bdro = node("Blowdown RO", "#52514e")
        links.append((tower, bdro, t.blowdown_gpd, "rgba(82,81,78,0.35)"))
        links.append((bdro, node("Permeate out ↺", C5), t.permeate_gpd, "rgba(232,123,164,0.4)"))
        links.append((bdro, sewer, t.brine_gpd, "rgba(27,175,122,0.35)"))
    else:
        links.append((tower, sewer, t.blowdown_gpd, "rgba(27,175,122,0.35)"))
    fig = go.Figure(go.Sankey(
        arrangement="snap",
        textfont=dict(color=INK, size=13, shadow="none"),
        node=dict(label=nodes, color=colors, pad=18, thickness=16, line=dict(width=0),
                  hovertemplate="%{label}<br>%{value:,.0f} gpd<extra></extra>"),
        link=dict(source=[s for s, *_ in links], target=[d for _, d, *_ in links], value=[v for *_, v, _ in links],
                  color=[c for *_, c in links], hovertemplate="%{source.label} → %{target.label}<br>%{value:,.0f} gpd"
                                                              "<extra></extra>"),
    ))
    fig.update_layout(title=f"Water balance — {res.label} (gpd)", height=520, margin=dict(l=10, r=10, t=50, b=10),
                      font=dict(size=12, color=INK), paper_bgcolor="rgba(0,0,0,0)")
    return fig


# ---------------------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------------------
def tab_dashboard(c: Comparison) -> None:
    b, o = c.baseline, c.optimized
    cfg = st.session_state["cfg"]
    max_lsi = cfg["cooling_tower"]["max_lsi"]
    st.caption(f"Source: **{o.source_label}** · IT load {cfg['data_center']['it_load_mw']:.1f} MW · "
               f"metrics show Optimized; deltas are vs Baseline")
    k = st.columns(4)
    k[0].metric("Raw water intake (gpd)", fmt(o.intake_gpd), fmt(o.intake_gpd - b.intake_gpd), delta_color="inverse")
    k[1].metric("WUE (L/kWh IT)", f"{o.kpis['wue_l_per_kwh']:.2f}", f"{o.kpis['wue_l_per_kwh'] - b.kpis['wue_l_per_kwh']:.2f}",
                delta_color="inverse")
    k[2].metric("Cycles of concentration", f"{o.tower.coc:.1f}", f"{o.tower.coc - b.tower.coc:+.1f}")
    k[3].metric("PUE", f"{o.kpis['pue']:.3f}", f"{o.kpis['pue'] - b.kpis['pue']:.3f}", delta_color="inverse")
    k = st.columns(4)
    k[0].metric("Cooling load", f"{o.kpis['cooling_load_mw']:.1f} MW", f"{o.kpis['cooling_load_tons']:,.0f} tons",
                delta_color="off")
    k[1].metric("Annual cost ($/yr)", fmt(o.costs["total"]), fmt(o.costs["total"] - b.costs["total"]), delta_color="inverse")
    k[2].metric("NPV of upgrade", f"${c.finance.npv:,.0f}",
                f"IRR {c.finance.irr:.0%}" if c.finance.irr is not None else "IRR n/a", delta_color="off")
    k[3].metric("Simple payback", f"{c.finance.simple_payback_years:.2f} yr" if c.finance.simple_payback_years else "n/a",
                f"CapEx ${c.capex['total']:,.0f}", delta_color="off")

    for w in dict.fromkeys(b.warnings + o.warnings):
        st.warning(w)
    left, right = st.columns([1.1, 1])
    with left:
        st.plotly_chart(paired_bar(["Evaporation", "Drift", "Blowdown/brine to sewer", "Treatment rejects"],
                                   [b.daily["evaporation_gpd"], b.daily["drift_gpd"], b.daily["brine_gpd"],
                                    b.daily["treatment_waste_gpd"]],
                                   [o.daily["evaporation_gpd"], o.daily["drift_gpd"], o.daily["brine_gpd"],
                                    o.daily["treatment_waste_gpd"]],
                                   "Water losses breakdown", "gpd", (b.label, o.label)))
    with right:
        st.markdown("**Compliance & engineering checks**")
        rows = []
        for r in (b, o):
            leg = r.risks["legionella"]
            ingest_ok = all(x.compliant for p, x in r.risks.items() if p != "legionella")
            rows.append({
                "Scenario": r.label,
                "CoC limit": f"{r.tower.coc:.2f} ({r.coc_limiting})",
                "LSI hot": check(r.scaling["recirc_hot"].lsi <= max_lsi + 1e-6,
                                 f"{r.scaling['recirc_hot'].lsi:.2f} · {r.scaling['recirc_hot'].lsi_status}"),
                "Silica": check(not r.silica_alert, f"{r.tower.c_recirc['sio2']:.0f} mg/L"),
                "LEED limits / pts": check(not any(r.recirc_exceedances.values()), f"within · {r.leed_points} pt"),
                "Legionella Py": check(leg.compliant, f"{leg.py:.1e}"),
                "Ingestion QMRA": check(ingest_ok, "Py ≤ 1e-4" if ingest_ok else "LRV < LRT"),
                "Pumps POR": check(all(p.in_por for p in r.pumps.values()), "HI 9.6.3"),
            })
        st.dataframe(pd.DataFrame(rows).set_index("Scenario").T, width="stretch")


def tab_sankey(c: Comparison) -> None:
    pick = st.radio("Scenario", [c.baseline.label, c.optimized.label], index=1, horizontal=True)
    res = c.baseline if pick == c.baseline.label else c.optimized
    st.plotly_chart(sankey(res))
    d = res.daily
    st.caption(f"Closure check: intake {fmt(d['intake_gpd'])} = evaporation {fmt(d['evaporation_gpd'])} + drift "
               f"{fmt(d['drift_gpd'])} + sewer {fmt(d['sewer_gpd'])} gpd "
               f"(residual {d['intake_gpd'] - d['evaporation_gpd'] - d['drift_gpd'] - d['sewer_gpd']:.2e}). "
               "BD-RO permeate is shown leaving and re-entering the tower (↺).")


def tab_mass_balance(c: Comparison) -> None:
    pick = st.radio("Scenario ", [c.baseline.label, c.optimized.label], index=1, horizontal=True)
    res = c.baseline if pick == c.baseline.label else c.optimized
    rows = []
    for n in res.treatment_nodes:
        row = {"Step": n.label, "On": "✅" if n.enabled else "⏸", "Q_in (gpd)": n.q_in_gpd, "Reject (gpd)": n.q_waste_gpd,
               "Q_out (gpd)": n.q_out_gpd, "Energy (kWh/d)": n.energy_kwh_d}
        row.update({f"{SPECIES_LABELS[s]}": n.c_out[s] for s in SPECIES})
        rows.append(row)
    t = res.tower
    tower_row = {"Step": "Cooling tower (recirc)", "On": "✅", "Q_in (gpd)": t.tower_makeup_gpd,
                 "Reject (gpd)": t.drift_gpd, "Q_out (gpd)": t.blowdown_gpd, "Energy (kWh/d)": 0.0}
    tower_row.update({SPECIES_LABELS[s]: t.c_recirc[s] for s in SPECIES})
    rows.append(tower_row)
    df = pd.DataFrame(rows).set_index("Step")
    st.markdown("**Node outflow quality** (mg/L unless noted; EC µS/cm). Flows obey Q_out = Q_in − Q_reject − Q_evap.")
    st.dataframe(df.style.format("{:,.2f}", subset=[col for col in df.columns if col != "On"]), width="stretch")
    st.caption(f"Tower evaporation {fmt(t.evaporation_gpd)} gpd · blowdown {fmt(t.blowdown_gpd)} gpd · "
               f"BD-RO permeate {fmt(t.permeate_gpd)} gpd · brine {fmt(t.brine_gpd)} gpd")

    st.markdown("**Cycles-of-concentration limits** — operating CoC is the most restrictive bar.")
    cands = {k: min(v, 50.0) for k, v in res.coc_candidates.items()}
    fig = go.Figure(go.Bar(x=list(cands.values()), y=list(cands.keys()), orientation="h", marker_color=C_BASE,
                           marker_line_width=0, hovertemplate="%{y}: %{x:.2f} cycles<extra></extra>"))
    fig.add_vline(x=res.tower.coc, line_dash="dash", line_color=INK,
                  annotation_text=f"operating {res.tower.coc:.2f}", annotation_position="top")
    fig.update_layout(title="Allowable cycles by constraint (display capped at 50)", xaxis_title="cycles")
    st.plotly_chart(style_fig(fig, 340))


def tab_fill(c: Comparison) -> None:
    b, o, f = c.baseline, c.optimized, c.fill_only
    st.markdown("For a fixed heat load, evaporation is set by the energy balance. A better fill **cannot add "
                "evaporation**: it lowers the approach, so the chiller COP rises and the tower rejects less heat. "
                "The higher-SSA fill also costs fan power. Both effects are shown below.")
    cols = st.columns(4)
    cols[0].metric("Approach (°F)", f"{o.fill_perf.approach_f:.2f}", f"{o.fill_perf.approach_f - b.fill_perf.approach_f:.2f}",
                   delta_color="inverse")
    cols[1].metric("Chiller COP", f"{o.cop:.2f}", f"{o.cop - b.cop:+.2f}")
    cols[2].metric("Fan static (Pa)", f"{o.fan.total_dp_pa:.0f}", f"{o.fan.total_dp_pa - b.fan.total_dp_pa:+.0f}",
                   delta_color="inverse")
    cols[3].metric("Drift (gpd)", fmt(o.daily["drift_gpd"]), fmt(o.daily["drift_gpd"] - b.daily["drift_gpd"]),
                   delta_color="inverse")

    rows = []
    for r in (b, f, o):
        rows.append({"Scenario": r.label, "SSA m²/m³": r.fill.ssa_m2_m3, "Flute °": r.fill.flute_angle_deg,
                     "KaV/L": r.fill_perf.kavl_available, "Approach °F": r.fill_perf.approach_f, "COP": r.cop,
                     "Evaporation gpd": r.daily["evaporation_gpd"], "Drift gpd": r.daily["drift_gpd"],
                     "Blowdown gpd": r.daily["blowdown_gpd"], "Intake gpd": r.intake_gpd,
                     "Fill ΔP Pa": r.fan.fill_dp_pa, "Fan kW": r.fan.kw, "Chiller kW": r.power_kw["chiller"]})
    st.dataframe(pd.DataFrame(rows).set_index("Scenario").style.format("{:,.2f}"), width="stretch")
    st.caption("Middle row isolates the fill: at the baseline CoC, the drift removed becomes extra blowdown. "
               "The water saving comes when the high-efficiency eliminator lets the tower run at a higher CoC.")

    # Merkel operating chart
    cfg = st.session_state["cfg"]
    wb, rng, lg = cfg["site"]["wet_bulb_f"], cfg["cooling_tower"]["range_f"], cfg["cooling_tower"]["l_over_g"]
    approaches = np.linspace(1.5, 20, 120)
    req = [th.merkel_required(wb + a + rng, wb + a, wb, lg) for a in approaches]
    fig = go.Figure()
    fig.add_scatter(x=approaches, y=req, mode="lines", line=dict(color=INK, width=2), name="Required KaV/L (Merkel)",
                    hovertemplate="approach %{x:.1f} °F<br>KaV/L %{y:.2f}<extra></extra>")
    for r, col in ((b, C_BASE), (o, C_OPT)):
        fig.add_hline(y=r.fill_perf.kavl_available, line_color=col, line_width=2)
        fig.add_scatter(x=[r.fill_perf.approach_f], y=[r.fill_perf.kavl_available], mode="markers+text",
                        marker=dict(size=10, color=col, line=dict(color="white", width=2)), name=r.label,
                        text=[f"{r.fill_perf.approach_f:.1f} °F"], textposition="top right",
                        hovertemplate=r.label + "<br>approach %{x:.2f} °F<br>KaV/L %{y:.2f}<extra></extra>")
    fig.update_layout(title="Tower characteristic: available fill KaV/L vs required (Merkel)",
                      xaxis_title="Approach (°F)", yaxis_title="KaV/L")
    st.plotly_chart(style_fig(fig, 400))


def tab_quality(c: Comparison) -> None:
    rows = []
    for r in (c.baseline, c.optimized):
        for kname, idx in r.scaling.items():
            rows.append({"Scenario": r.label, "Stream": kname.replace("_", " "), "T °C": idx.temperature_c,
                         "pH": idx.ph, "pHs": idx.phs, "LSI": idx.lsi, "LSI status": idx.lsi_status,
                         "RSI": idx.rsi, "RSI status": idx.rsi_status})
    df = pd.DataFrame(rows)
    st.dataframe(df.style.format({c_: "{:.2f}" for c_ in ("T °C", "pH", "pHs", "LSI", "RSI")}),
                 width="stretch", hide_index=True)
    lim = st.session_state["cfg"]["library"]["leed_condenser_limits"]
    rows = []
    for r in (c.baseline, c.optimized):
        rc = r.tower.c_recirc
        vals = {"Ca as CaCO₃": rc["ca"] * 2.497, "Alkalinity": rc["alkalinity"], "SiO₂": rc["sio2"], "Cl⁻": rc["cl"],
                "EC": rc["ec"]}
        rows.append({"Scenario": r.label, **{k: f"{v:,.0f}" for k, v in vals.items()},
                     "LEED max cycles": f"{r.leed_max_cycles:.1f}", "LEED pts": str(r.leed_points),
                     "Silica": check(not r.silica_alert, "≤150 mg/L")})
    rows.append({"Scenario": "LEED v4 limit", "Ca as CaCO₃": f"{lim['ca_as_caco3']:,}", "Alkalinity": f"{lim['alkalinity']:,}",
                 "SiO₂": f"{lim['sio2']:,}", "Cl⁻": f"{lim['cl']:,}", "EC": f"{lim['ec']:,}", "LEED max cycles": "",
                 "LEED pts": "", "Silica": ""})
    st.markdown("**Recirculating water vs LEED v4 WE Cooling Tower Water Use (Table 1)**")
    st.dataframe(pd.DataFrame(rows).set_index("Scenario"), width="stretch")
    st.markdown("**Raw source vs treated makeup**")
    o = c.optimized
    q = pd.DataFrame({"Unit": [SPECIES_UNITS[s] for s in SPECIES],
                      "Raw source": [o.raw_quality[s] for s in SPECIES],
                      "Treated makeup": [o.tower.c_fresh[s] for s in SPECIES],
                      "Recirc (Base)": [c.baseline.tower.c_recirc[s] for s in SPECIES],
                      "Recirc (Opt)": [o.tower.c_recirc[s] for s in SPECIES]},
                     index=[SPECIES_LABELS[s] for s in SPECIES])
    st.dataframe(q.style.format("{:,.2f}", subset=q.columns[1:]), width="stretch")


def tab_micro(c: Comparison) -> None:
    rows = []
    for r in (c.baseline, c.optimized):
        rows.append({"Scenario": r.label, "Residual mg/L": r.ct["residual_mg_l"], "Q_peak gpm": r.ct["q_peak_gpm"],
                     "TDT min": r.ct["tdt_min"], "T10 min": r.ct["t10_min"], "CT mg·min/L": r.ct["ct"],
                     "CT req 4-log virus": r.ct.get("ct_req_virus_4log", float("nan")),
                     "CT req 3-log Giardia": r.ct.get("ct_req_giardia_3log", float("nan"))})
    st.markdown("**Chlorine CT verification** — CT = C × T10, T10 = TDT × BF")
    st.dataframe(pd.DataFrame(rows).set_index("Scenario").style.format("{:,.2f}"), width="stretch")
    rows = []
    for r in (c.baseline, c.optimized):
        for p, x in r.risks.items():
            rows.append({"Scenario": r.label, "Pathogen": PATHOGEN_LABELS[p], "Route": x.route, "Model": x.model,
                         "C source": f"{x.c_source:.3g}", "LRV credited": f"{x.lrv_credit:.1f}" if p != "legionella" else "—",
                         "C exposure": f"{x.c_exposure:.3g}", "Dose/event": f"{x.dose:.2e}", "Py": f"{x.py:.2e}",
                         "LRT required": f"{x.lrt_required:.2f}", "Status": check(x.compliant, "Py ≤ 1e-4" if x.compliant else "Py > 1e-4")})
    st.markdown("**QMRA** — ingestion pathogens use Beta-Poisson / exponential models with treatment-train credits. "
                "Legionella is an inhalation risk from drift aerosol at the recirculating control level. Its LRT is "
                "the additional log reduction needed in the recirculating water.")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    lb, lo = c.baseline.risks["legionella"], c.optimized.risks["legionella"]
    fig = go.Figure(go.Bar(x=[c.baseline.label, c.optimized.label], y=[lb.py, lo.py], marker_color=[C_BASE, C_OPT],
                           marker_line_width=0, text=[f"{lb.py:.1e}", f"{lo.py:.1e}"], textposition="outside",
                           hovertemplate="%{x}<br>Py %{y:.2e}<extra></extra>"))
    fig.add_hline(y=1e-4, line_dash="dash", line_color=INK, annotation_text="Target 1e-4", annotation_position="top left")
    fig.update_yaxes(type="log", title="Annual infection risk (log scale)", dtick=1, tickformat=".0e")
    fig.update_layout(title="Legionella inhalation risk — drift eliminator effect", showlegend=False)
    st.plotly_chart(style_fig(fig, 360))


def tab_hydraulics(c: Comparison) -> None:
    rows = []
    for r in (c.baseline, c.optimized):
        for p in r.pumps.values():
            rows.append({"Scenario": r.label, "Station": p.label, "Q total gpm": p.q_total_gpm, "TDH ft": p.tdh_ft,
                         "N pumps": p.n_pumps, "Q/pump gpm": p.q_per_pump_gpm, "POR min": p.por_min_gpm,
                         "POR max": p.por_max_gpm, "In POR": check(p.in_por, ""), "WHP": p.whp, "BHP": p.bhp, "kW": p.kw})
    df = pd.DataFrame(rows)
    st.dataframe(df.style.format({k: "{:,.1f}" for k in df.columns if df[k].dtype != object}),
                 width="stretch", hide_index=True)
    comps = ["chiller", "tower_fans", "chw_pumps", "cw_pumps", "makeup_pumps", "water_treatment"]
    names = ["Chiller", "Tower fans", "CHW pumps", "CW pumps", "Makeup pumps", "Water treatment"]
    st.plotly_chart(paired_bar(names, [c.baseline.power_kw[k] for k in comps], [c.optimized.power_kw[k] for k in comps],
                               "Plant power by component", "kW", (c.baseline.label, c.optimized.label)))
    fb, fo = c.baseline.fan, c.optimized.fan
    st.caption(f"IDF: airflow {fo.airflow_m3_s:,.0f} m³/s, face velocity {fo.face_velocity_m_s:.2f} m/s, "
               f"static {fb.total_dp_pa:.0f} → {fo.total_dp_pa:.0f} Pa, fan power {fb.kw:,.0f} → {fo.kw:,.0f} kW")


def tab_economics(c: Comparison) -> None:
    fin = c.finance
    k = st.columns(5)
    k[0].metric("CapEx", f"${c.capex['total']:,.0f}")
    k[1].metric("Year-1 savings", f"${fin.annual_savings_year1:,.0f}")
    k[2].metric("NPV", f"${fin.npv:,.0f}")
    k[3].metric("IRR", f"{fin.irr:.1%}" if fin.irr is not None else "n/a")
    k[4].metric("BEP / SPBT", f"{fin.break_even_year:.2f} / {fin.simple_payback_years:.2f} yr"
                if fin.break_even_year and fin.simple_payback_years else "n/a")
    years = [0] + [r.year for r in fin.rows]
    cum = [-c.capex["total"]] + [r.cumulative_pv for r in fin.rows]
    fig = go.Figure()
    fig.add_scatter(x=years, y=cum, mode="lines+markers", line=dict(color=C_BASE, width=2),
                    marker=dict(size=8, line=dict(color="white", width=2)), name="Cumulative PV",
                    hovertemplate="Year %{x}<br>$%{y:,.0f}<extra></extra>")
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    fig.update_layout(title=f"Cumulative discounted cash flow (i = {fin.rate:.1%})", xaxis_title="Year",
                      yaxis_title="$", showlegend=False)
    left, right = st.columns(2)
    left.plotly_chart(style_fig(fig))
    items = ["water", "sewer", "electricity", "chemicals", "sludge", "carbon"]
    right.plotly_chart(paired_bar([i.title() for i in items], [c.baseline.costs[i] for i in items],
                                  [c.optimized.costs[i] for i in items], "Annual cost by item", "$/yr",
                                  (c.baseline.label, c.optimized.label)))
    st.dataframe(pd.DataFrame([{"Year": r.year, "Cash flow $": r.cash_flow, "Discount factor": r.discount_factor,
                                "PV $": r.present_value, "Cumulative PV $": r.cumulative_pv} for r in fin.rows])
                 .set_index("Year").style.format("{:,.2f}"), width="stretch")
    st.caption("CapEx: " + ", ".join(f"{k} ${v:,.0f}" for k, v in c.capex.items()))


def tab_compare(c: Comparison) -> None:
    b, o = c.baseline, c.optimized
    spec = [
        ("Approach", "°F", lambda r: r.fill_perf.approach_f, 2), ("Chiller COP", "-", lambda r: r.cop, 2),
        ("Tower load", "tons", lambda r: r.kpis["tower_load_tons"], 0), ("CoC", "cycles", lambda r: r.tower.coc, 2),
        ("Limiting constraint", "", lambda r: r.coc_limiting, None),
        ("Raw water intake", "gpd", lambda r: r.intake_gpd, 0),
        ("Raw water intake", "m³/d", lambda r: r.intake_gpd * M3_PER_GAL, 0),
        ("Evaporation", "gpd", lambda r: r.daily["evaporation_gpd"], 0), ("Drift", "gpd", lambda r: r.daily["drift_gpd"], 0),
        ("Blowdown", "gpd", lambda r: r.daily["blowdown_gpd"], 0), ("Recycled permeate", "gpd", lambda r: r.daily["permeate_gpd"], 0),
        ("Sewer discharge", "gpd", lambda r: r.daily["sewer_gpd"], 0),
        ("WUE", "L/kWh", lambda r: r.kpis["wue_l_per_kwh"], 3), ("PUE", "-", lambda r: r.kpis["pue"], 3),
        ("Cooling + water plant", "kW", lambda r: r.power_kw["total"], 0),
        ("Water energy intensity", "kWh/m³", lambda r: r.kpis["water_energy_kwh_m3"], 3),
        ("Annual cost", "$/yr", lambda r: r.costs["total"], 0), ("CO₂e", "t/yr", lambda r: r.annual["co2_tonne"], 0),
        ("LEED WE cooling tower", "pts", lambda r: r.leed_points, 0),
        ("Legionella Py", "/yr", lambda r: r.risks["legionella"].py, None),
    ]
    rows = []
    for label, unit, fn, d in spec:
        vb, vo = fn(b), fn(o)
        if d is None:
            rows.append({"Metric": label, "Unit": unit, b.label: f"{vb:.2e}" if isinstance(vb, float) else vb,
                         o.label: f"{vo:.2e}" if isinstance(vo, float) else vo, "Δ": ""})
        else:
            rows.append({"Metric": label, "Unit": unit, b.label: fmt(vb, d), o.label: fmt(vo, d), "Δ": fmt(vo - vb, d)})
    st.dataframe(pd.DataFrame(rows).set_index("Metric"), width="stretch", height=760)


# ---------------------------------------------------------------------------------------
def main() -> None:
    base_cfg = load_config()
    cfg = sidebar(base_cfg)
    st.session_state["cfg"] = cfg
    cfg_json = json.dumps(cfg, sort_keys=True)
    c = _run(cfg_json)

    head, dl = st.columns([4, 1])
    head.title("Data Center Cooling Water M&E")
    head.caption("Mass balance · heat rejection · corrugated fill · LSI/RSI · CT & QMRA · pumps & fans · lifecycle cost")
    dl.write("")
    dl.download_button("⬇️ Export dynamic Excel", data=_excel(cfg_json), file_name=DEFAULT_FILENAME,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch", type="primary")

    tabs = st.tabs(["📊 Dashboard", "🌊 Sankey", "🧪 Mass balance", "🧱 Corrugated fill", "💎 Water quality",
                    "🦠 Microbial risk", "⚙️ Hydraulics & energy", "💲 Economics", "⚖️ Scenario comparison"])
    with tabs[0]:
        tab_dashboard(c)
    with tabs[1]:
        tab_sankey(c)
    with tabs[2]:
        tab_mass_balance(c)
    with tabs[3]:
        tab_fill(c)
    with tabs[4]:
        tab_quality(c)
    with tabs[5]:
        tab_micro(c)
    with tabs[6]:
        tab_hydraulics(c)
    with tabs[7]:
        tab_economics(c)
    with tabs[8]:
        tab_compare(c)


if __name__ == "__main__":
    main()
