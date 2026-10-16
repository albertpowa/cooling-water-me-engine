"""Disinfection CT and Quantitative Microbial Risk Assessment (QMRA).

    TDT = V_operating / Q_peak,  T10 = TDT × BF,  CT = C_residual × T10
    LRT = log10(C_influent / C_target)
    Beta-Poisson:  P_d = 1 − (1 + d/β)^(−α)
    Exponential:   P_d = 1 − exp(−r·d)
    Annual:        P_y = 1 − (1 − P_d)^n          (target P_y ≤ 1e-4)
    Allowable daily risk:  P_d = 1 − (1 − P_y)^(1/n)

Small-probability arithmetic uses log1p/expm1 to stay accurate at 1e-8.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

# Free chlorine CT (mg·min/L), pH 6–9, 4-log virus inactivation (SWTR Guidance Manual).
CT_VIRUS_4LOG: Tuple[Tuple[float, float], ...] = ((0.5, 12.0), (5.0, 8.0), (10.0, 6.0), (15.0, 4.0), (20.0, 3.0), (25.0, 2.0))
# Free chlorine CT, 3-log Giardia, C ≈ 1.0 mg/L, pH 7.0 (SWTR Guidance Manual).
CT_GIARDIA_3LOG: Tuple[Tuple[float, float], ...] = ((5.0, 149.0), (10.0, 112.0), (15.0, 75.0), (20.0, 56.0), (25.0, 37.0))


# --- CT ------------------------------------------------------------------------------
def total_detention_time_min(volume_gal: float, q_peak_gpm: float) -> float:
    return volume_gal / q_peak_gpm


def t10_min(tdt_min: float, baffling_factor: float) -> float:
    return tdt_min * baffling_factor


def ct_value(c_residual_mg_l: float, t10: float) -> float:
    return c_residual_mg_l * t10


def interpolate(table: Sequence[Tuple[float, float]], x: float) -> float:
    """Linear interpolation, clamped to the table ends."""
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def chlorine_lrv(ct: float, temp_c: float) -> Dict[str, float]:
    """Inactivation credit = target log × CT_actual / CT_required (capped).

    Same approach as the course "Chlorine CT" sheet (inactivation ratio × 4-log).
    Free chlorine earns no Cryptosporidium credit; enteric bacteria are at least
    as sensitive as viruses, so they take the virus credit.
    """
    ct_v = interpolate(CT_VIRUS_4LOG, temp_c)
    ct_g = interpolate(CT_GIARDIA_3LOG, temp_c)
    virus = min(4.0, 4.0 * ct / ct_v)
    return {"virus": virus, "giardia": min(3.0, 3.0 * ct / ct_g), "cryptosporidium": 0.0,
            "bacteria": virus, "ct_req_virus_4log": ct_v, "ct_req_giardia_3log": ct_g}


# --- Log removal -----------------------------------------------------------------------
def log_removal_target(c_influent: float, c_target: float) -> float:
    if c_influent <= 0 or c_influent <= c_target:
        return 0.0
    return math.log10(c_influent / c_target)


# --- Dose-response ---------------------------------------------------------------------
def beta_poisson_pd(dose: float, alpha: float, beta: float) -> float:
    return -math.expm1(-alpha * math.log1p(dose / beta))


def beta_poisson_dose(pd: float, alpha: float, beta: float) -> float:
    """Inverse: d = β·[(1 − P_d)^(−1/α) − 1]."""
    return beta * math.expm1(-math.log1p(-pd) / alpha)


def exponential_pd(dose: float, r: float) -> float:
    return -math.expm1(-r * dose)


def exponential_dose(pd: float, r: float) -> float:
    return -math.log1p(-pd) / r


def infection_probability(dose: float, params: Mapping[str, float]) -> float:
    if params["model"] == "beta_poisson":
        return beta_poisson_pd(dose, params["alpha"], params["beta"])
    return exponential_pd(dose, params["r"])


def dose_for_probability(pd: float, params: Mapping[str, float]) -> float:
    if params["model"] == "beta_poisson":
        return beta_poisson_dose(pd, params["alpha"], params["beta"])
    return exponential_dose(pd, params["r"])


def annual_risk(pd: float, n: float) -> float:
    """P_y = 1 − (1 − P_d)^n."""
    return -math.expm1(n * math.log1p(-pd)) if pd < 1 else 1.0


def allowable_event_risk(py: float, n: float) -> float:
    """P_d = 1 − (1 − P_y)^(1/n)."""
    return -math.expm1(math.log1p(-py) / n)


def median_infectious_dose(alpha: float, beta: float) -> float:
    """N50 = β·(2^(1/α) − 1)."""
    return beta * (2.0 ** (1.0 / alpha) - 1.0)


# --- Exposure ----------------------------------------------------------------------------
def aerosol_water_l_per_m3(drift_gpm: float, airflow_m3_s: float, dilution: float) -> float:
    """Drift water carried per m³ of air at the receptor."""
    drift_l_s = drift_gpm * 3.78541 / 60.0
    return drift_l_s / airflow_m3_s * dilution


def inhalation_volume_l(aerosol_l_m3: float, respirable_fraction: float,
                        breathing_m3_h: float, hours: float) -> float:
    """Equivalent water volume inhaled per event (L)."""
    return aerosol_l_m3 * respirable_fraction * breathing_m3_h * hours


@dataclass(frozen=True)
class PathogenRisk:
    pathogen: str
    model: str
    route: str
    c_source: float
    lrv_credit: float
    c_exposure: float
    volume_per_event_l: float
    events_per_year: float
    dose: float
    pd: float
    py: float
    target_py: float
    c_target: float
    lrt_required: float
    compliant: bool


def assess_pathogen(pathogen: str, params: Mapping, c_source: float, lrv_credit: float,
                    volume_l: float, events: float, target_py: float,
                    c_exposure_override: Optional[float] = None) -> PathogenRisk:
    """Risk at exposure and the LRT needed to meet ``target_py``.

    For ingestion pathogens the exposure concentration is the source reduced by
    the treatment-train LRV credits. Legionella regrows inside the tower, so its
    exposure concentration is the recirculating level (``c_exposure_override``)
    and the LRT is measured against that level.
    """
    c_exp = c_exposure_override if c_exposure_override is not None else c_source / 10.0 ** lrv_credit
    dose = c_exp * volume_l
    pd = infection_probability(dose, params)
    py = annual_risk(pd, events)
    pd_allow = allowable_event_risk(target_py, events)
    c_target = dose_for_probability(pd_allow, params) / volume_l if volume_l > 0 else float("inf")
    basis = c_exp if c_exposure_override is not None else c_source
    lrt = log_removal_target(basis, c_target)
    return PathogenRisk(pathogen, params["model"], params["route"], c_source, lrv_credit, c_exp,
                        volume_l, events, dose, pd, py, target_py, c_target, lrt, py <= target_py)
