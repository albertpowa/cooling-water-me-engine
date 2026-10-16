"""Pumping, water chemistry and lifecycle-cost tests (EAEE 4163 examples)."""

import pytest

from src.engine import economics as ec
from src.engine import hydraulics as hy
from src.engine import water_quality as wq


def test_course_pump_power_week10():
    whp = hy.water_horsepower(200, 150)
    assert whp == pytest.approx(7.576, rel=1e-3)
    bhp = hy.brake_horsepower(whp, 0.65)
    motor_hp = bhp / (0.98 * 0.92)
    assert motor_hp == pytest.approx(12.93, rel=1e-3)
    assert hy.motor_power_kw(bhp, 0.98, 0.92) == pytest.approx(motor_hp * 0.7457)


def test_course_por_and_pump_count():
    assert hy.por_limits(200)[1] == pytest.approx(240)
    # 8 MGD with 1,700 gpm pumps → 4 pumps (ROUNDUP)
    assert hy.pump_count(8e6 / 1440, 1700) == 4
    res = hy.size_pump_station("x", 2700, {"tdh_ft": 50, "q_bep_gpm": 1000, "eff_hydraulic": 0.8,
                                           "eff_mechanical": 0.98, "eff_electrical": 0.95})
    assert res.n_pumps == 3 and res.in_por


def test_course_present_value():
    # $300,000 in 5 years at 7 % → $213,895
    assert ec.present_value(300_000, 0.07, 5) == pytest.approx(213_895.6, abs=1)
    assert ec.discount_rate(0.04, 0.03) == pytest.approx(0.07)


def test_npv_matches_annuity_and_irr():
    flows = [100.0] * 5
    annuity = (1 - 1.07 ** -5) / 0.07
    assert ec.npv(0.07, flows, 300.0) == pytest.approx(100 * annuity - 300)
    r = ec.irr(300.0, flows)
    assert ec.npv(r, flows, 300.0) == pytest.approx(0.0, abs=1e-6)


def test_break_even_and_payback():
    fin = ec.evaluate_investment(1000.0, 400.0, 0.04, 0.03, 0.0, 10)
    assert fin.simple_payback_years == pytest.approx(2.5)
    bep = fin.break_even_year
    assert 2.0 < bep < 3.0
    assert fin.rows[-1].cumulative_pv == pytest.approx(fin.npv)
    assert ec.per_kgal_from_hcf(4.10) == pytest.approx(5.481, abs=1e-3)


def test_leed_reference_guide_example():
    # LEED v4 WE "Cooling Tower Water Use" example → 10, 5, 5, 5, 6.7 cycles; max = 5
    makeup = {"ca": 100 / 2.497, "alkalinity": 200, "sio2": 20, "cl": 50, "ec": 300}
    limits = {"ca_as_caco3": 1000, "alkalinity": 1000, "sio2": 100, "cl": 250, "ec": 2000}
    cycles = wq.species_coc_limits(makeup, limits)
    assert cycles["ca_as_caco3"] == pytest.approx(10, rel=1e-3)
    assert cycles["ec"] == pytest.approx(6.67, abs=0.01)
    assert wq.limiting_coc(cycles)[1] == pytest.approx(5.0)


def test_lsi_rsi_relationship_and_lsi_limit():
    q = {"ph": 8.2, "tds": 600, "ca": 80, "alkalinity": 150}
    idx = wq.scaling_indices(q, 35.0)
    assert idx.rsi == pytest.approx(idx.ph - 2 * idx.lsi)
    n = wq.lsi_coc_limit(q, 8.4, 35.0, 2.0)
    conc = {k: v * n for k, v in q.items() if k != "ph"}
    assert wq.scaling_indices(conc, 35.0, ph=8.4).lsi == pytest.approx(2.0, abs=1e-9)
    assert wq.silica_alert(151) and not wq.silica_alert(149)
