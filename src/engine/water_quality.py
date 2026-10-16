"""Water chemistry: scaling indices (LSI / RSI), silica alert, CoC limits.

Langelier Saturation Index
    LSI = pH − pHs,  pHs = (9.3 + A + B) − (C + D)
    A = (log10 TDS − 1) / 10
    B = −13.12 · log10(T°C + 273) + 34.55
    C = log10(Ca as CaCO3) − 0.4
    D = log10(Alkalinity as CaCO3)
Ryznar Stability Index
    RSI = 2·pHs − pH
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from .constants import CACO3_PER_CA

_FLOOR = 1.0e-3   # avoid log10(0) for ultra-pure (RO permeate) water


@dataclass(frozen=True)
class ScalingIndices:
    temperature_c: float
    ph: float
    phs: float
    lsi: float
    rsi: float
    lsi_status: str
    rsi_status: str


def _log(x: float) -> float:
    return math.log10(max(x, _FLOOR))


def saturation_ph(tds_mg_l: float, temp_c: float, ca_mg_l: float, alk_mg_l_caco3: float) -> float:
    """pHs of CaCO3 saturation (Ca entered as mg/L Ca²⁺, converted to CaCO3)."""
    a = (_log(tds_mg_l) - 1.0) / 10.0
    b = -13.12 * math.log10(temp_c + 273.0) + 34.55
    c = _log(ca_mg_l * CACO3_PER_CA) - 0.4
    d = _log(alk_mg_l_caco3)
    return (9.3 + a + b) - (c + d)


def classify_lsi(lsi: float) -> str:
    if lsi > 2.0:
        return "Severe scaling"
    if lsi > 0.5:
        return "Scale-forming"
    if lsi >= -0.5:
        return "Balanced"
    if lsi >= -2.0:
        return "Corrosive"
    return "Severely corrosive"


def classify_rsi(rsi: float) -> str:
    if rsi < 5.5:
        return "Heavy scale"
    if rsi < 6.2:
        return "Scale-forming"
    if rsi <= 6.8:
        return "Stable"
    if rsi <= 8.5:
        return "Corrosive"
    return "Severely corrosive"


def scaling_indices(quality: Mapping[str, float], temp_c: float, ph: Optional[float] = None) -> ScalingIndices:
    """Compute LSI and RSI for a water-quality dict at ``temp_c``."""
    ph_val = quality["ph"] if ph is None else ph
    phs = saturation_ph(quality["tds"], temp_c, quality["ca"], quality["alkalinity"])
    lsi = ph_val - phs
    rsi = 2.0 * phs - ph_val
    return ScalingIndices(temp_c, ph_val, phs, lsi, rsi, classify_lsi(lsi), classify_rsi(rsi))


def silica_alert(sio2_mg_l: float, threshold_mg_l: float = 150.0) -> bool:
    """True when amorphous-silica solubility threshold is exceeded."""
    return sio2_mg_l > threshold_mg_l


def species_coc_limits(makeup: Mapping[str, float], limits: Mapping[str, float]) -> Dict[str, float]:
    """Max cycles per LEED v4 parameter = max allowed / makeup concentration."""
    values = {
        "ca_as_caco3": makeup["ca"] * CACO3_PER_CA,
        "alkalinity": makeup["alkalinity"],
        "sio2": makeup["sio2"],
        "cl": makeup["cl"],
        "ec": makeup["ec"],
    }
    return {k: limits[k] / max(values[k], _FLOOR) for k in limits}


def lsi_coc_limit(makeup: Mapping[str, float], recirc_ph: float, temp_c: float, max_lsi: float) -> float:
    """Cycles at which recirculating LSI reaches ``max_lsi``.

    TDS, Ca and alkalinity all scale with N, so pHs(N) = pHs(1) − 1.9·log10(N)
    and LSI(N) = pH_r − pHs(1) + 1.9·log10(N)  →  closed-form solution.
    """
    phs1 = saturation_ph(makeup["tds"], temp_c, makeup["ca"], makeup["alkalinity"])
    return 10.0 ** ((max_lsi - recirc_ph + phs1) / 1.9)


def limiting_coc(candidates: Mapping[str, float]) -> Tuple[str, float]:
    """Return (name, value) of the smallest cycle limit."""
    name = min(candidates, key=lambda k: candidates[k])
    return name, candidates[name]
