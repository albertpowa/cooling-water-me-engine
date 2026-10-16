"""CT disinfection and QMRA tests, anchored to EAEE 4163 worked examples."""

import math

import pytest

from src.engine import compare_scenarios, load_config
from src.engine import microbiology_qmra as qm

ROTAVIRUS = {"model": "beta_poisson", "alpha": 0.253, "beta": 0.422, "route": "ingestion"}
CHOLERA = {"model": "beta_poisson", "alpha": 0.097, "beta": 13020.0, "route": "ingestion"}


def test_course_rotavirus_lrt_8_5():
    # Midterm 2023: Py = 1e-4, 1,100 uses/yr, 1e-5 L per use, 5e6 org/L → LRT ≈ 8.5
    pd = qm.allowable_event_risk(1e-4, 1100)
    assert pd == pytest.approx(9.1e-8, rel=0.01)
    d = qm.dose_for_probability(pd, ROTAVIRUS)
    assert d == pytest.approx(1.52e-7, rel=0.01)
    c_target = d / 1e-5
    assert qm.log_removal_target(5e6, c_target) == pytest.approx(8.52, abs=0.01)


def test_course_vibrio_cholerae():
    # Weekly Q (week 2): N50 ≈ 16.5e6; 4,400 units/day → Pd ≈ 2.8 %
    assert qm.median_infectious_dose(0.097, 13020) == pytest.approx(16.5e6, rel=0.01)
    assert qm.beta_poisson_pd(4400, 0.097, 13020) == pytest.approx(0.028, abs=0.001)


def test_dose_response_inverses_round_trip():
    for params in (ROTAVIRUS, CHOLERA, {"model": "exponential", "r": 0.0199}):
        d = qm.dose_for_probability(1e-6, params)
        assert qm.infection_probability(d, params) == pytest.approx(1e-6, rel=1e-9)
    assert qm.annual_risk(qm.allowable_event_risk(1e-4, 250), 250) == pytest.approx(1e-4, rel=1e-9)


def test_course_chlorine_ct_example():
    # 200,000 gal lowest volume, 1,200 gpm peak, unbaffled BF 0.1, 0.1 mg/L, 25 °C
    tdt = qm.total_detention_time_min(200_000, 1200)
    t10 = qm.t10_min(tdt, 0.1)
    ct = qm.ct_value(0.1, t10)
    assert ct == pytest.approx(1.667, rel=1e-3)
    credit = qm.chlorine_lrv(ct, 25.0)
    assert credit["ct_req_virus_4log"] == 2.0
    assert credit["virus"] == pytest.approx(3.33, abs=0.01)   # < 4 → "Not Achieved"
    assert credit["cryptosporidium"] == 0.0


def test_ct_table_interpolation():
    assert qm.interpolate(qm.CT_VIRUS_4LOG, 17.5) == pytest.approx(3.5)
    assert qm.interpolate(qm.CT_VIRUS_4LOG, 40.0) == 2.0


def test_legionella_risk_falls_with_drift_eliminator():
    c = compare_scenarios(load_config())
    rb, ro = c.baseline.risks["legionella"], c.optimized.risks["legionella"]
    assert ro.py < rb.py
    # dose scales with drift: 0.2 % → 0.001 % is a 200× reduction at equal airflow & Legionella level
    ratio = rb.dose / ro.dose
    expected = (c.baseline.daily["drift_gpd"] / c.baseline.fan.airflow_m3_s) / \
               (c.optimized.daily["drift_gpd"] / c.optimized.fan.airflow_m3_s)
    assert ratio == pytest.approx(expected, rel=1e-9)


def test_ingestion_risk_meets_target_for_reuse_source():
    cfg = load_config({"site": {"source": "mbr_permeate"}})
    res = compare_scenarios(cfg).optimized
    rota = res.risks["rotavirus"]
    assert rota.lrv_credit >= rota.lrt_required
    assert rota.py <= 1e-4
    assert math.isclose(rota.c_exposure, rota.c_source / 10 ** rota.lrv_credit)
