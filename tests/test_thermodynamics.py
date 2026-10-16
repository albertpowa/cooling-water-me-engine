"""Thermodynamics tests, anchored to EAEE 4163 worked examples."""

import pytest

from src.engine import compare_scenarios, load_config
from src.engine import thermodynamics as th


def test_cooling_load_from_flow_course_final_q1():
    # 25,000 gpd reuse water cooled 10 °F ≈ 87,000 BTU/hr
    gpm = 25_000 / 24 / 60
    assert th.cooling_load_from_flow(gpm, 10.0) == pytest.approx(86_875, rel=1e-4)


def test_evaporation_divisor():
    assert th.evaporation_gph(8089.8) == pytest.approx(1.0)


def test_course_midterm_cooling_tower():
    # 537,500 ft² × 25 BTU/hr·ft², COP 3.5 → evaporation ≈ 52,000 gpd
    q = 537_500 * 25
    tl = th.tower_load(q, 3.5)
    assert tl == pytest.approx(q * (1 + 1 / 3.5))
    evap_gpd = th.evaporation_gph(tl) * 24
    assert evap_gpd == pytest.approx(51_255, rel=1e-3)
    assert th.blowdown_simple(evap_gpd, 5) == pytest.approx(evap_gpd / 4)
    assert th.makeup_simple(evap_gpd, 5) == pytest.approx(evap_gpd * 1.25)


def test_tons_and_it_load():
    assert th.tons(12_000) == 1.0
    assert th.cooling_load_from_it(1.0) == pytest.approx(3_412_000)


def test_saturated_air_enthalpy_matches_psychrometric_chart():
    # ASHRAE: ~38.6 BTU/lb at 75 °F, ~43.7 BTU/lb at 80 °F
    assert th.saturated_air_enthalpy(75.0) == pytest.approx(38.6, abs=0.4)
    assert th.saturated_air_enthalpy(80.0) == pytest.approx(43.7, abs=0.4)


def test_merkel_solution_is_self_consistent():
    kavl = 1.3
    tc = th.solve_cold_water_temp(kavl, 10.0, 76.0, 1.2)
    assert th.merkel_required(tc + 10.0, tc, 76.0, 1.2) == pytest.approx(kavl, rel=1e-4)
    # required KaV/L falls as the approach widens
    assert th.merkel_required(91, 81, 76, 1.2) > th.merkel_required(96, 86, 76, 1.2)


def test_higher_ssa_fill_gives_smaller_approach():
    cfg = load_config()
    model = cfg["fill_model"]
    base = th.FillDesign.from_dict(cfg["scenarios"]["baseline"]["fill"])
    upg = th.FillDesign.from_dict(cfg["scenarios"]["optimized"]["fill"])
    pb = th.simulate_fill(base, model, 10.0, 76.0, 1.2)
    pu = th.simulate_fill(upg, model, 10.0, 76.0, 1.2)
    assert pu.kavl_available > pb.kavl_available
    assert pu.approach_f < pb.approach_f
    assert 3.0 < pu.approach_f < pb.approach_f < 15.0


def test_colder_condenser_water_raises_cop():
    assert th.chiller_cop(5.5, 80.0, 85.0, 0.02) == pytest.approx(5.5 * 1.10)


def test_fill_upgrade_tradeoffs_reported_honestly():
    c = compare_scenarios(load_config())
    # Fill alone at the same CoC: evaporation falls (COP ↑) but drift becomes bleed
    assert c.deltas["fill_only_evaporation_gpd"] > 0
    assert c.fill_only.daily["blowdown_gpd"] > c.baseline.daily["blowdown_gpd"]
    # Higher-SSA fill costs fan energy, recovered by the chiller
    assert c.deltas["fan_kw"] < 0 < c.deltas["chiller_kw"]
    assert c.deltas["drift_gpd"] > 0
    assert c.optimized.daily["drift_gpd"] == pytest.approx(c.optimized.recirc_gpm * 1e-5 * 1440)
