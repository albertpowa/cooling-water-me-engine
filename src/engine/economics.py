"""Lifecycle economics: discounting, NPV, IRR, break-even and simple payback.

    i    = r_interest + r_inflation            (course convention)
    PV   = FV / (1 + i)^n
    NPV  = Σ_{t=1..N} CF_t / (1 + i)^t − CapEx
    BEP  = year t where Σ discounted CF = CapEx (linearly interpolated)
    SPBT = CapEx / annual net operational savings (year 1)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .constants import GAL_PER_HCF


def discount_rate(interest: float, inflation: float) -> float:
    return interest + inflation


def present_value(fv: float, rate: float, n: float) -> float:
    return fv / (1.0 + rate) ** n


def per_kgal_from_hcf(rate_per_hcf: float) -> float:
    """$/100 ft³ → $/1,000 gal."""
    return rate_per_hcf / GAL_PER_HCF * 1000.0


def escalated_cash_flows(year1: float, escalation: float, years: int) -> List[float]:
    return [year1 * (1.0 + escalation) ** (t - 1) for t in range(1, years + 1)]


def npv(rate: float, cash_flows: Sequence[float], capex: float) -> float:
    """NPV with CF_1..CF_N at end of years 1..N and CapEx at t = 0."""
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cash_flows, start=1)) - capex


def irr(capex: float, cash_flows: Sequence[float], lo: float = -0.99, hi: float = 10.0) -> Optional[float]:
    """Internal rate of return by bisection; None if NPV does not change sign."""
    def f(r: float) -> float:
        return npv(r, cash_flows, capex)

    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)


def cumulative_discounted(rate: float, cash_flows: Sequence[float], capex: float) -> List[float]:
    """Cumulative position after each year, starting from −CapEx."""
    out, acc = [], -capex
    for t, cf in enumerate(cash_flows, start=1):
        acc += present_value(cf, rate, t)
        out.append(acc)
    return out


def break_even_year(rate: float, cash_flows: Sequence[float], capex: float) -> Optional[float]:
    """Fractional year when the cumulative discounted position crosses zero."""
    prev = -capex
    for t, cum in enumerate(cumulative_discounted(rate, cash_flows, capex), start=1):
        if cum >= 0:
            return (t - 1) + (-prev) / (cum - prev) if cum != prev else float(t)
        prev = cum
    return None


def simple_payback(capex: float, annual_savings: float) -> Optional[float]:
    return capex / annual_savings if annual_savings > 0 else None


@dataclass(frozen=True)
class CashFlowRow:
    year: int
    cash_flow: float
    discount_factor: float
    present_value: float
    cumulative_pv: float


@dataclass(frozen=True)
class FinancialSummary:
    capex: float
    annual_savings_year1: float
    rate: float
    years: int
    rows: List[CashFlowRow]
    npv: float
    irr: Optional[float]
    break_even_year: Optional[float]
    simple_payback_years: Optional[float]


def evaluate_investment(capex: float, savings_year1: float, interest: float, inflation: float,
                        escalation: float, years: int) -> FinancialSummary:
    rate = discount_rate(interest, inflation)
    cfs = escalated_cash_flows(savings_year1, escalation, years)
    cum = cumulative_discounted(rate, cfs, capex)
    rows = [CashFlowRow(t, cf, 1.0 / (1.0 + rate) ** t, present_value(cf, rate, t), cum[t - 1])
            for t, cf in enumerate(cfs, start=1)]
    return FinancialSummary(capex, savings_year1, rate, years, rows, npv(rate, cfs, capex),
                            irr(capex, cfs), break_even_year(rate, cfs, capex),
                            simple_payback(capex, savings_year1))
