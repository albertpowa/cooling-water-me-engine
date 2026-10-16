"""Pumping and induced-draft-fan energy.

    WHP          = Q(gpm)·TDH(ft) / 3960
    BHP          = WHP / η_hydraulic
    P_elec (kW)  = BHP·0.7457 / (η_mechanical·η_electrical)
    N_pumps      = ⌈Q_total / Q_BEP⌉
    HI 9.6.3-2017 POR: 0.70·Q_BEP ≤ Q_per_pump ≤ 1.20·Q_BEP
    Fan (kW)     = V_air(m³/s)·ΔP_static(Pa) / (η_fan·η_motor) / 1000
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .constants import FT2_PER_M2, GPM_BTU_FACTOR, KW_PER_HP, M3_PER_FT3, POR_HIGH, POR_LOW, WHP_CONSTANT
from .thermodynamics import FillDesign, fill_open_area


def water_horsepower(q_gpm: float, tdh_ft: float) -> float:
    return q_gpm * tdh_ft / WHP_CONSTANT


def brake_horsepower(whp: float, eff_hydraulic: float) -> float:
    return whp / eff_hydraulic


def motor_power_kw(bhp: float, eff_mechanical: float, eff_electrical: float) -> float:
    return bhp * KW_PER_HP / (eff_mechanical * eff_electrical)


def pump_count(q_total_gpm: float, q_bep_gpm: float) -> int:
    return max(1, math.ceil(q_total_gpm / q_bep_gpm))


def por_limits(q_bep_gpm: float) -> tuple:
    """(Q_min, Q_max) of the HI 9.6.3-2017 preferred operating range."""
    return POR_LOW * q_bep_gpm, POR_HIGH * q_bep_gpm


@dataclass(frozen=True)
class PumpResult:
    label: str
    q_total_gpm: float
    tdh_ft: float
    n_pumps: int
    q_per_pump_gpm: float
    por_min_gpm: float
    por_max_gpm: float
    in_por: bool
    whp: float
    bhp: float
    kw: float


def size_pump_station(label: str, q_total_gpm: float, spec: Mapping[str, float]) -> PumpResult:
    """Size identical parallel pumps at BEP and compute total electrical demand."""
    n = pump_count(q_total_gpm, spec["q_bep_gpm"])
    q_each = q_total_gpm / n
    lo, hi = por_limits(spec["q_bep_gpm"])
    whp = water_horsepower(q_total_gpm, spec["tdh_ft"])
    bhp = brake_horsepower(whp, spec["eff_hydraulic"])
    kw = motor_power_kw(bhp, spec["eff_mechanical"], spec["eff_electrical"])
    return PumpResult(label, q_total_gpm, spec["tdh_ft"], n, q_each, lo, hi, lo <= q_each <= hi, whp, bhp, kw)


# --- Induced-draft fan ----------------------------------------------------------------
def air_mass_flow_lb_hr(recirc_gpm: float, l_over_g: float) -> float:
    """G = L / (L/G), L = Q·500 lb/hr."""
    return recirc_gpm * GPM_BTU_FACTOR / l_over_g


def airflow_m3_s(recirc_gpm: float, l_over_g: float, specific_volume_ft3_lb: float) -> float:
    cfm = air_mass_flow_lb_hr(recirc_gpm, l_over_g) * specific_volume_ft3_lb / 60.0
    return cfm * M3_PER_FT3 / 60.0


def tower_plan_area_m2(recirc_gpm: float, water_loading_gpm_ft2: float) -> float:
    return recirc_gpm / water_loading_gpm_ft2 / FT2_PER_M2


def fill_pressure_drop_pa(fill: FillDesign, face_velocity_m_s: float, air_density: float,
                          model: Mapping[str, float]) -> float:
    """ΔP = c·SSA·H·½ρv²·(θ/30°)^b / ε²  (screening correlation)."""
    eps = fill_open_area(fill)
    return (model["dp_coefficient"] * fill.ssa_m2_m3 * fill.fill_height_m
            * 0.5 * air_density * face_velocity_m_s ** 2
            * (fill.flute_angle_deg / 30.0) ** model["dp_angle_exponent"] / eps ** 2)


def fan_power_kw(airflow: float, static_pa: float, eff_fan: float, eff_motor: float) -> float:
    return airflow * static_pa / (eff_fan * eff_motor) / 1000.0


@dataclass(frozen=True)
class FanResult:
    plan_area_m2: float
    airflow_m3_s: float
    face_velocity_m_s: float
    fill_dp_pa: float
    eliminator_dp_pa: float
    other_dp_pa: float
    total_dp_pa: float
    kw: float


def induced_draft_fan(recirc_gpm: float, fill: FillDesign, tower: Mapping[str, float],
                      model: Mapping[str, float]) -> FanResult:
    area = tower_plan_area_m2(recirc_gpm, tower["water_loading_gpm_ft2"])
    v_air = airflow_m3_s(recirc_gpm, tower["l_over_g"], tower["air_specific_volume_ft3_lb"])
    face_v = v_air / area
    dp_fill = fill_pressure_drop_pa(fill, face_v, tower["air_density_kg_m3"], model)
    dp_total = dp_fill + fill.eliminator_dp_pa + tower["other_static_pa"]
    kw = fan_power_kw(v_air, dp_total, tower["fan_efficiency"], tower["fan_motor_efficiency"])
    return FanResult(area, v_air, face_v, dp_fill, fill.eliminator_dp_pa, tower["other_static_pa"], dp_total, kw)
