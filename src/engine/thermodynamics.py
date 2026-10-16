"""Heat-rejection thermodynamics, evaporation and corrugated-fill simulation.

Core balances (US customary, EAEE 4163 convention)
    Q_cooling (BTU/hr) = ṁ·cp·ΔT,   ṁ = Q(gpm)·8.34·60
    Tower Load         = Q_cooling·(1 + 1/COP)
    E (gph)            = Tower Load / (970 × 8.34) = Tower Load / 8089.8
    Recirculation (gpm)= Tower Load / (500 · Range)
    Drift (gpm)        = f_drift · Q_recirc

Corrugated-fill model (physically consistent)
    For a fixed heat load the evaporation is fixed by the energy balance, so the
    fill cannot "add" evaporation. Better fill raises the tower characteristic
    KaV/L → colder condenser water (smaller approach) → higher chiller COP →
    smaller tower load → less evaporation, blowdown and makeup. A higher-SSA
    fill also raises air-side pressure drop → more fan power. Both effects are
    reported so the net benefit is honest.

    Available:  KaV/L = c0·(SSA/SSA_ref)·(H/H_ref)·(θ/30°)^a·(L/G)^(−n)
    Required:   KaV/L = ∫ dT / (h_sat(T) − h_air)    (Chebyshev 4-point)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .constants import (
    BTU_PER_KWH,
    BTU_PER_TON,
    CP_WATER,
    EVAP_DIVISOR,
    GPM_BTU_FACTOR,
    LB_PER_GAL,
)

CHEBYSHEV_POINTS = (0.1, 0.4, 0.6, 0.9)


# --- Heat loads ------------------------------------------------------------------
def mass_flow_lb_hr(q_gpm: float) -> float:
    """ṁ = Q·ρ·60 (lb/hr)."""
    return q_gpm * LB_PER_GAL * 60.0


def cooling_load_from_flow(q_gpm: float, delta_t_f: float, cp: float = CP_WATER) -> float:
    """Q_cooling = ṁ·cp·ΔT (BTU/hr)."""
    return mass_flow_lb_hr(q_gpm) * cp * delta_t_f


def cooling_load_from_it(it_load_mw: float, other_heat_fraction: float = 0.0) -> float:
    """IT electrical load (MW) → heat to chilled water (BTU/hr)."""
    return it_load_mw * 1000.0 * BTU_PER_KWH * (1.0 + other_heat_fraction)


def chilled_water_flow_gpm(q_cooling_btuh: float, delta_t_f: float) -> float:
    return q_cooling_btuh / (GPM_BTU_FACTOR * delta_t_f)


def tower_load(q_cooling_btuh: float, cop: float) -> float:
    """Tower Load = Q_cooling·(1 + 1/COP): compressor work is rejected too."""
    return q_cooling_btuh * (1.0 + 1.0 / cop)


def tons(btuh: float) -> float:
    return btuh / BTU_PER_TON


def chiller_power_kw(q_cooling_btuh: float, cop: float) -> float:
    return q_cooling_btuh / BTU_PER_KWH / cop


def evaporation_gph(tower_load_btuh: float) -> float:
    """E = Tower Load / (970 BTU/lb × 8.34 lb/gal)."""
    return tower_load_btuh / EVAP_DIVISOR


def recirculation_gpm(tower_load_btuh: float, range_f: float) -> float:
    return tower_load_btuh / (GPM_BTU_FACTOR * range_f)


def drift_gpm(recirc_gpm: float, drift_fraction: float) -> float:
    return recirc_gpm * drift_fraction


def blowdown_simple(evap: float, coc: float) -> float:
    """Course form B = E/(CoC − 1) (drift included in B)."""
    return evap / (coc - 1.0)


def makeup_simple(evap: float, coc: float) -> float:
    """M = E·CoC/(CoC − 1)."""
    return evap * coc / (coc - 1.0)


# --- Psychrometrics ----------------------------------------------------------------
def saturation_pressure_kpa(t_f: float) -> float:
    """Magnus–Alduchov saturation vapour pressure."""
    t_c = (t_f - 32.0) / 1.8
    return 0.61094 * math.exp(17.625 * t_c / (t_c + 243.04))


def saturated_air_enthalpy(t_f: float, p_kpa: float = 101.325) -> float:
    """h_sat (BTU/lb dry air) = 0.240·T + W_s·(1061 + 0.444·T)."""
    pw = saturation_pressure_kpa(t_f)
    w = 0.62198 * pw / (p_kpa - pw)
    return 0.240 * t_f + w * (1061.0 + 0.444 * t_f)


# --- Merkel tower characteristic ------------------------------------------------------
def merkel_required(t_hot: float, t_cold: float, t_wb: float, l_over_g: float,
                    p_kpa: float = 101.325) -> float:
    """Required KaV/L (Chebyshev 4-point integration of the Merkel equation)."""
    rng = t_hot - t_cold
    h_in = saturated_air_enthalpy(t_wb, p_kpa)
    total = 0.0
    for f in CHEBYSHEV_POINTS:
        tw = t_cold + f * rng
        dh = saturated_air_enthalpy(tw, p_kpa) - (h_in + l_over_g * (tw - t_cold))
        if dh <= 0:
            return float("inf")
        total += 1.0 / dh
    return rng / 4.0 * total


@dataclass(frozen=True)
class FillDesign:
    name: str
    ssa_m2_m3: float
    flute_angle_deg: float
    sheet_thickness_mm: float
    fill_height_m: float
    drift_fraction: float
    eliminator_dp_pa: float

    @classmethod
    def from_dict(cls, d: Mapping) -> "FillDesign":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def fill_open_area(fill: FillDesign) -> float:
    """ε = 1 − SSA·t/2 (sheets have two wetted faces)."""
    return 1.0 - fill.ssa_m2_m3 * fill.sheet_thickness_mm / 1000.0 / 2.0


def fill_kavl_available(fill: FillDesign, l_over_g: float, model: Mapping[str, float]) -> float:
    return (model["c0"]
            * (fill.ssa_m2_m3 / model["ssa_ref_m2_m3"])
            * (fill.fill_height_m / model["height_ref_m"])
            * (fill.flute_angle_deg / 30.0) ** model["angle_exponent"]
            * l_over_g ** (-model["lg_exponent"]))


def solve_cold_water_temp(kavl_available: float, range_f: float, t_wb: float,
                          l_over_g: float, p_kpa: float = 101.325) -> float:
    """Bisection for the cold-water temperature where required = available KaV/L."""
    lo, hi = t_wb + 0.01, t_wb + 60.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if merkel_required(mid + range_f, mid, t_wb, l_over_g, p_kpa) > kavl_available:
            lo = mid
        else:
            hi = mid
    return hi


def chiller_cop(cop_ref: float, cwst_f: float, cwst_ref_f: float, sensitivity_per_f: float) -> float:
    """Linear COP response to condenser-water supply temperature (screening)."""
    return cop_ref * (1.0 + sensitivity_per_f * (cwst_ref_f - cwst_f))


@dataclass(frozen=True)
class FillPerformance:
    kavl_available: float
    cold_water_f: float
    hot_water_f: float
    approach_f: float
    open_area: float


def simulate_fill(fill: FillDesign, model: Mapping[str, float], range_f: float,
                  t_wb: float, l_over_g: float, p_kpa: float = 101.325) -> FillPerformance:
    kavl = fill_kavl_available(fill, l_over_g, model)
    t_cold = solve_cold_water_temp(kavl, range_f, t_wb, l_over_g, p_kpa)
    return FillPerformance(kavl, t_cold, t_cold + range_f, t_cold - t_wb, fill_open_area(fill))
