"""Node-by-node flow and species mass balances.

Every process node enforces

    Q_out   = Q_in − Q_waste − Q_evap
    C_out,j = [Q_in·C_in,j·(1 − R_j) + Dosing_j] / Q_out
    C_waste,j = Q_in·C_in,j·R_j / Q_waste

where R_j is the *mass* removal fraction of species j to the waste stream and
Dosing_j is a mass rate (here: dose in mg/L referenced to Q_in, × Q_in).
pH is not a conserved quantity: it passes through unless a node sets it.

The cooling tower with optional blowdown-recovery RO is solved in closed form
(see :func:`cooling_tower_balance`), so the whole network is solved without
iteration — the same equations are written as live formulas in Excel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from .constants import SPECIES

Quality = Dict[str, float]


@dataclass
class ProcessStep:
    """A toggleable treatment node."""

    key: str
    label: str
    enabled: bool = True
    recovery: float = 1.0                      # Q_out / Q_in
    removal: Dict[str, float] = field(default_factory=dict)   # mass-removal fraction R_j
    dosing: Dict[str, float] = field(default_factory=dict)    # mg/L referenced to Q_in
    ph_out: Optional[float] = None
    lrv: Dict[str, float] = field(default_factory=dict)       # virus/protozoa/bacteria credits
    energy_kwh_per_kgal: Optional[float] = None

    def effective(self, name: str, attr: str) -> float:
        """Removal or dose for a species, zeroed when the node is bypassed."""
        if not self.enabled:
            return 0.0
        return float(getattr(self, attr).get(name, 0.0) or 0.0)

    @property
    def effective_recovery(self) -> float:
        return self.recovery if self.enabled else 1.0


@dataclass
class NodeResult:
    key: str
    label: str
    enabled: bool
    q_in_gpd: float
    q_waste_gpd: float
    q_evap_gpd: float
    q_out_gpd: float
    c_in: Quality
    c_out: Quality
    c_waste: Quality
    energy_kwh_d: float = 0.0

    @property
    def flow_residual(self) -> float:
        """Q_in − Q_out − Q_waste − Q_evap (should be ~0)."""
        return self.q_in_gpd - self.q_out_gpd - self.q_waste_gpd - self.q_evap_gpd


def outlet_concentration(q_in: float, c_in: float, removal: float, dosing_mass: float, q_out: float) -> float:
    """C_out = [Q_in·C_in·(1 − R) + Dosing] / Q_out (any consistent units)."""
    if q_out <= 0:
        return 0.0
    return (q_in * c_in * (1.0 - removal) + dosing_mass) / q_out


def waste_concentration(q_in: float, c_in: float, removal: float, q_waste: float) -> float:
    """C_waste = Q_in·C_in·R / Q_waste."""
    if q_waste <= 0:
        return 0.0
    return q_in * c_in * removal / q_waste


def ro_mass_removal(rejection: float, recovery: float) -> float:
    """Convert membrane rejection (1 − Cp/Cf) to mass-removal fraction R = 1 − Y·(1 − rej)."""
    return 1.0 - recovery * (1.0 - rejection)


def run_step(step: ProcessStep, q_in: float, c_in: Mapping[str, float]) -> NodeResult:
    """Apply one node's flow and species balance."""
    y = step.effective_recovery
    q_out = q_in * y
    q_waste = q_in - q_out
    c_out: Quality = {}
    c_waste: Quality = {}
    for s in SPECIES:
        if s == "ph":
            c_out[s] = step.ph_out if (step.enabled and step.ph_out is not None) else c_in[s]
            c_waste[s] = c_in[s]
            continue
        r = step.effective(s, "removal")
        dose_mass = step.effective(s, "dosing") * q_in
        c_out[s] = max(outlet_concentration(q_in, c_in[s], r, dose_mass, q_out), 0.0)
        c_waste[s] = waste_concentration(q_in, c_in[s], r, q_waste)
    return NodeResult(step.key, step.label, step.enabled, q_in, q_waste, 0.0, q_out,
                      dict(c_in), c_out, c_waste)


@dataclass
class ProcessTrain:
    """Ordered chain of toggleable nodes (Intake → … → Chlorine contact)."""

    steps: List[ProcessStep]

    @property
    def overall_recovery(self) -> float:
        prod = 1.0
        for st in self.steps:
            prod *= st.effective_recovery
        return prod

    def required_intake(self, q_product_gpd: float) -> float:
        """Intake flow needed to deliver ``q_product_gpd`` at the train outlet."""
        return q_product_gpd / self.overall_recovery

    def run(self, q_in_gpd: float, c_in: Mapping[str, float]) -> List[NodeResult]:
        results: List[NodeResult] = []
        q, c = q_in_gpd, dict(c_in)
        for st in self.steps:
            node = run_step(st, q, c)
            results.append(node)
            q, c = node.q_out_gpd, node.c_out
        return results

    def product_quality(self, c_in: Mapping[str, float]) -> Quality:
        """Outlet quality is flow-independent (doses are mg/L of node inflow)."""
        return self.run(1.0, c_in)[-1].c_out


@dataclass
class TowerBalance:
    """Steady-state cooling tower (+ optional blowdown-recovery RO) balance."""

    coc: float                    # system cycles = C_recirc,TDS / C_fresh,TDS
    evaporation_gpd: float
    drift_gpd: float
    blowdown_gpd: float           # bleed-off leaving the basin (to sewer or BD-RO)
    permeate_gpd: float           # BD-RO permeate recycled to makeup
    brine_gpd: float              # BD-RO concentrate (or = blowdown without recovery)
    fresh_makeup_gpd: float       # from the treatment train
    tower_makeup_gpd: float       # fresh + recycled permeate
    c_fresh: Quality
    c_blend: Quality
    c_recirc: Quality
    c_permeate: Quality
    c_brine: Quality
    recovery_enabled: bool

    @property
    def sewer_gpd(self) -> float:
        return self.brine_gpd

    @property
    def tower_coc(self) -> float:
        """Cycles relative to the blended (fresh + permeate) tower makeup."""
        return self.c_recirc["tds"] / max(self.c_blend["tds"], 1e-9)


def blowdown_flow(evap: float, drift: float, coc: float, recovery: float = 0.0, k_tds: float = 1.0) -> float:
    """Bleed-off required for a target system CoC.

    Water:  M_f = E + D + B·(1 − Y)
    Solute: M_f·C_f = C_r·(D + B·k),  k = mass fraction of B's solute rejected to brine
    With N = C_r/C_f:  B = (E − (N − 1)·D) / (N·k − 1 + Y)
    Without recovery (Y = 0, k = 1) and D → 0 this reduces to the course form B = E/(CoC − 1).
    """
    denom = coc * k_tds - 1.0 + recovery
    if denom <= 0:
        raise ValueError("CoC too low for the blowdown-recovery configuration")
    return max((evap - (coc - 1.0) * drift) / denom, 0.0)


def max_coc_for_drift(evap: float, drift: float) -> float:
    """Drift is an uncontrolled bleed: the bleed cannot go negative → N ≤ 1 + E/D."""
    return float("inf") if drift <= 0 else 1.0 + evap / drift


def cooling_tower_balance(
    evap_gpd: float,
    drift_gpd: float,
    coc: float,
    c_fresh: Mapping[str, float],
    recirc_ph: float,
    recirc_cl2: float,
    recovery: float = 0.0,
    rejection: Optional[Mapping[str, float]] = None,
) -> TowerBalance:
    """Solve the tower + optional blowdown-recovery loop in closed form."""
    enabled = recovery > 0 and rejection is not None
    y = recovery if enabled else 0.0
    k: Dict[str, float] = {
        s: (ro_mass_removal(float(rejection.get(s, 0.0)), y) if enabled else 1.0)  # type: ignore[union-attr]
        for s in SPECIES
    }
    b = blowdown_flow(evap_gpd, drift_gpd, coc, y, k["tds"])
    perm = y * b
    brine = b - perm
    m_fresh = evap_gpd + drift_gpd + brine
    m_tower = m_fresh + perm

    c_r: Quality = {}
    c_p: Quality = {}
    c_br: Quality = {}
    c_bl: Quality = {}
    for s in SPECIES:
        if s == "ph":
            c_r[s], c_p[s], c_br[s] = recirc_ph, recirc_ph, recirc_ph
            c_bl[s] = c_fresh[s]
            continue
        if s == "cl2":
            c_r[s] = recirc_cl2
        else:
            c_r[s] = m_fresh * c_fresh[s] / max(drift_gpd + b * k[s], 1e-12)
        c_p[s] = c_r[s] * (1.0 - k[s]) / y if (enabled and y > 0) else 0.0
        c_br[s] = c_r[s] * k[s] / (1.0 - y) if enabled else c_r[s]
        c_bl[s] = (m_fresh * c_fresh[s] + perm * c_p[s]) / m_tower
    return TowerBalance(coc, evap_gpd, drift_gpd, b, perm, brine, m_fresh, m_tower,
                        dict(c_fresh), c_bl, c_r, c_p, c_br, enabled)


def species_mass_residual(node: NodeResult, species: str, dose_mg_l: float = 0.0) -> float:
    """Q_in·C_in + Dose − Q_out·C_out − Q_waste·C_waste (≈ 0 when balanced)."""
    return (node.q_in_gpd * node.c_in[species] + dose_mg_l * node.q_in_gpd
            - node.q_out_gpd * node.c_out[species] - node.q_waste_gpd * node.c_waste[species])
