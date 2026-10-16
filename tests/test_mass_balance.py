"""Mass-balance tests: node equations, train sizing and the cooling-tower loop."""

import pytest

from src.engine import compare_scenarios, load_config, run_scenario
from src.engine.constants import SPECIES
from src.engine.mass_balance import (
    ProcessStep,
    ProcessTrain,
    blowdown_flow,
    cooling_tower_balance,
    max_coc_for_drift,
    outlet_concentration,
    ro_mass_removal,
    run_step,
    species_mass_residual,
)

RAW = {s: 10.0 for s in SPECIES}
RAW["ph"] = 7.5


def test_outlet_concentration_formula():
    # C_out = [Q_in·C_in·(1 − R) + Dosing] / Q_out
    assert outlet_concentration(100.0, 50.0, 0.2, 300.0, 80.0) == pytest.approx((100 * 50 * 0.8 + 300) / 80)


def test_node_conserves_flow_and_mass_with_dosing():
    step = ProcessStep("x", "X", recovery=0.9, removal={"tss": 0.8, "tds": 0.1}, dosing={"so4": 5.0})
    node = run_step(step, 1_000_000.0, RAW)
    assert node.flow_residual == pytest.approx(0.0)
    for s in ("tss", "tds", "cl"):
        assert species_mass_residual(node, s) == pytest.approx(0.0, abs=1e-6)
    assert species_mass_residual(node, "so4", dose_mg_l=5.0) == pytest.approx(0.0, abs=1e-6)


def test_disabled_step_is_transparent():
    step = ProcessStep("ro", "RO", enabled=False, recovery=0.75, removal={"tds": 0.99}, ph_out=6.5)
    node = run_step(step, 500.0, RAW)
    assert node.q_out_gpd == 500.0 and node.q_waste_gpd == 0.0
    assert node.c_out == RAW


def test_ph_is_set_not_mass_balanced():
    node = run_step(ProcessStep("ro", "RO", recovery=0.8, ph_out=6.5), 100.0, RAW)
    assert node.c_out["ph"] == 6.5


def test_train_sizing_delivers_target_product():
    train = ProcessTrain([ProcessStep("a", "A", recovery=0.98), ProcessStep("b", "B", recovery=0.95),
                          ProcessStep("c", "C", enabled=False, recovery=0.5)])
    intake = train.required_intake(1000.0)
    assert intake == pytest.approx(1000.0 / (0.98 * 0.95))
    assert train.run(intake, RAW)[-1].q_out_gpd == pytest.approx(1000.0)


def test_ro_mass_removal_conversion():
    # 99 % rejection at 80 % recovery → 99.2 % of the mass leaves in the concentrate
    assert ro_mass_removal(0.99, 0.80) == pytest.approx(0.992)


def test_course_blowdown_without_drift():
    # EAEE 4163 midterm: evaporation ≈ 52,000 gpd at 5 CoC → B = E/(CoC − 1)
    assert blowdown_flow(52_000.0, 0.0, 5.0) == pytest.approx(13_000.0)


def test_tower_balance_no_recovery():
    t = cooling_tower_balance(600_000.0, 6_000.0, 5.0, RAW, 8.3, 0.5)
    assert t.fresh_makeup_gpd == pytest.approx(t.evaporation_gpd + t.drift_gpd + t.blowdown_gpd)
    # solute out with blowdown + drift equals solute in
    assert t.fresh_makeup_gpd * RAW["cl"] == pytest.approx((t.blowdown_gpd + t.drift_gpd) * t.c_recirc["cl"])
    assert t.c_recirc["tds"] / RAW["tds"] == pytest.approx(5.0)
    # with drift explicit, M = E·CoC/(CoC − 1)
    assert t.fresh_makeup_gpd == pytest.approx(600_000.0 * 5 / 4)


def test_tower_balance_with_blowdown_recovery():
    rej = {s: 0.98 for s in SPECIES}
    t = cooling_tower_balance(600_000.0, 100.0, 10.0, RAW, 8.4, 0.5, recovery=0.7, rejection=rej)
    assert t.permeate_gpd == pytest.approx(0.7 * t.blowdown_gpd)
    assert t.fresh_makeup_gpd == pytest.approx(t.evaporation_gpd + t.drift_gpd + t.brine_gpd)
    assert t.tower_makeup_gpd == pytest.approx(t.fresh_makeup_gpd + t.permeate_gpd)
    # system solute balance: fresh in = drift + brine out
    out = t.drift_gpd * t.c_recirc["tds"] + t.brine_gpd * t.c_brine["tds"]
    assert t.fresh_makeup_gpd * RAW["tds"] == pytest.approx(out)
    assert t.c_recirc["tds"] / RAW["tds"] == pytest.approx(10.0)
    # recycling permeate cuts sewer discharge vs. no recovery at the same CoC
    base = cooling_tower_balance(600_000.0, 100.0, 10.0, RAW, 8.4, 0.5)
    assert t.brine_gpd < base.blowdown_gpd


def test_drift_caps_cycles():
    assert max_coc_for_drift(600.0, 100.0) == pytest.approx(7.0)
    assert blowdown_flow(600.0, 100.0, 7.0) == pytest.approx(0.0)


@pytest.fixture(scope="module")
def comparison():
    return compare_scenarios(load_config())


def test_every_node_and_whole_system_balance(comparison):
    for res in (comparison.baseline, comparison.optimized):
        for node in res.treatment_nodes:
            assert node.flow_residual == pytest.approx(0.0, abs=1e-6)
        d = res.daily
        # Intake = Evaporation + Drift + all discharges to sewer
        assert d["intake_gpd"] == pytest.approx(d["evaporation_gpd"] + d["drift_gpd"] + d["sewer_gpd"], rel=1e-9)


def test_cycles_respect_limits(comparison):
    for res in (comparison.baseline, comparison.optimized):
        assert res.tower.coc <= min(res.coc_candidates.values()) + 1e-9
        assert not any(res.recirc_exceedances.values())


def test_seawater_without_ro_is_flagged():
    cfg = load_config({"site": {"source": "sea_water"}})
    res = run_scenario(cfg, "baseline")
    assert not res.coc_feasible and res.warnings
    # Brackish-grade single-pass RO still leaves ~290 mg/L Cl⁻ (> LEED 250 ppm) → still infeasible
    cfg_bwro = load_config({"site": {"source": "sea_water"}, "treatment": {"ro": {"enabled": True}}})
    assert not run_scenario(cfg_bwro, "baseline").coc_feasible
    # Seawater-grade membranes (45 % recovery, 99.8 % ion rejection) make the source usable
    swro = {s: 0.998 for s in ("ec", "tds", "na", "ca", "mg", "cl", "so4", "po4", "alkalinity", "sio2")}
    cfg_swro = load_config({"site": {"source": "sea_water"},
                            "treatment": {"ro": {"enabled": True, "recovery": 0.45, "rejection": swro}}})
    assert run_scenario(cfg_swro, "baseline").coc_feasible
