"""Physical constants, unit conversions and tracked-parameter registries.

US customary units are primary (matching the EAEE 4163 course convention);
SI conversions are provided for reporting.
"""

from __future__ import annotations

from typing import Dict, Tuple

# --- Water & heat --------------------------------------------------------------
LB_PER_GAL: float = 8.34            # weight of water, lb/gal
CP_WATER: float = 1.0               # specific heat, BTU/(lb·°F)
H_FG: float = 970.0                 # latent heat of vaporization, BTU/lb
EVAP_DIVISOR: float = H_FG * LB_PER_GAL   # 8089.8 BTU/gal
BTU_PER_KWH: float = 3412.0
BTU_PER_TON: float = 12_000.0       # 1 refrigeration ton = 12,000 BTU/hr
GPM_BTU_FACTOR: float = LB_PER_GAL * 60.0 * CP_WATER   # ≈ 500 (lb/hr per gpm·°F)

# --- Pumping -------------------------------------------------------------------
WHP_CONSTANT: float = 3960.0        # WHP = Q(gpm)·TDH(ft)/3960
KW_PER_HP: float = 0.7457
POR_LOW: float = 0.70               # HI 9.6.3-2017 preferred operating range
POR_HIGH: float = 1.20

# --- Volume / mass / length ----------------------------------------------------
L_PER_GAL: float = 3.78541
M3_PER_GAL: float = L_PER_GAL / 1000.0
GAL_PER_HCF: float = 748.052        # 100 ft³ (NYC DEP billing unit)
MIN_PER_DAY: float = 1440.0
KG_PER_LB: float = 0.453592
LB_PER_TONNE: float = 2204.6
LB_PER_SHORT_TON: float = 2000.0
M3_PER_FT3: float = 0.0283168
FT2_PER_M2: float = 10.7639
G_ACCEL: float = 9.81               # m/s²

# --- Chemistry -----------------------------------------------------------------
CACO3_PER_CA: float = 2.497         # mg/L Ca → mg/L as CaCO3

# Tracked physical-chemical parameters (15) - order is used in tables/Excel.
SPECIES: Tuple[str, ...] = (
    "ph", "ec", "tds", "sio2", "na", "ca", "mg", "cl",
    "po4", "so4", "alkalinity", "cl2", "tss", "bod5", "cod",
)
SPECIES_LABELS: Dict[str, str] = {
    "ph": "pH", "ec": "EC", "tds": "TDS", "sio2": "SiO₂", "na": "Na⁺",
    "ca": "Ca²⁺", "mg": "Mg²⁺", "cl": "Cl⁻", "po4": "PO₄³⁻", "so4": "SO₄²⁻",
    "alkalinity": "Alk (CaCO₃)", "cl2": "Free Cl₂", "tss": "TSS",
    "bod5": "BOD₅", "cod": "COD",
}
SPECIES_UNITS: Dict[str, str] = {s: "mg/L" for s in SPECIES}
SPECIES_UNITS.update({"ph": "-", "ec": "µS/cm"})

PATHOGENS: Tuple[str, ...] = (
    "rotavirus", "giardia", "cryptosporidium", "vibrio_cholerae",
    "e_coli", "hpc", "legionella",
)
PATHOGEN_LABELS: Dict[str, str] = {
    "rotavirus": "Rotavirus", "giardia": "Giardia", "cryptosporidium": "Cryptosporidium",
    "vibrio_cholerae": "Vibrio cholerae", "e_coli": "E. coli", "hpc": "HPC",
    "legionella": "Legionella pneumophila",
}
PATHOGEN_UNITS: Dict[str, str] = {
    "rotavirus": "org/L", "giardia": "cysts/L", "cryptosporidium": "oocysts/L",
    "vibrio_cholerae": "CFU/L", "e_coli": "CFU/100 mL", "hpc": "CFU/mL",
    "legionella": "CFU/L",
}
LRV_CLASSES: Tuple[str, ...] = ("virus", "protozoa", "bacteria")


def gpd_to_gpm(gpd: float) -> float:
    return gpd / MIN_PER_DAY


def gpm_to_gpd(gpm: float) -> float:
    return gpm * MIN_PER_DAY


def gpd_to_m3d(gpd: float) -> float:
    return gpd * M3_PER_GAL


def gpd_to_mgd(gpd: float) -> float:
    return gpd / 1.0e6


def btuh_to_mw(btuh: float) -> float:
    return btuh / BTU_PER_KWH / 1000.0


def lb_per_day(q_gpd: float, c_mg_l: float) -> float:
    """Classic 'pounds formula': lb/d = Q(MGD) × C(mg/L) × 8.34."""
    return gpd_to_mgd(q_gpd) * c_mg_l * LB_PER_GAL


def kg_per_day(q_gpd: float, c_mg_l: float) -> float:
    return q_gpd * L_PER_GAL * c_mg_l / 1.0e6
