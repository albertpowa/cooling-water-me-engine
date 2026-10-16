"""Run the M&E engine programmatically.

    python examples/run_sample_simulation.py

Shows: loading config, overriding inputs, comparing scenarios, reading the
mass balance node by node, QMRA, and exporting the dynamic Excel workbook.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine import compare_scenarios, load_config, run_scenario  # noqa: E402
from src.excel.exporter import export_workbook  # noqa: E402


def main() -> None:
    # 1) Default 50 MW NYC case, Baseline vs "Corrugated Fill + High CoC"
    cfg = load_config()
    c = compare_scenarios(cfg)
    print("=== Baseline vs Optimized (50 MW, municipal potable) ===")
    for r in (c.baseline, c.optimized):
        print(f"{r.label:30s} intake {r.intake_gpd:>10,.0f} gpd | CoC {r.tower.coc:4.1f} ({r.coc_limiting}) | "
              f"approach {r.fill_perf.approach_f:4.1f} °F | COP {r.cop:4.2f} | WUE {r.kpis['wue_l_per_kwh']:.2f} L/kWh | "
              f"PUE {r.kpis['pue']:.3f} | ${r.costs['total']:,.0f}/yr")
    f = c.finance
    print(f"Upgrade CapEx ${c.capex['total']:,.0f} → NPV ${f.npv:,.0f}, IRR {f.irr:.1%}, "
          f"BEP {f.break_even_year:.2f} yr, SPBT {f.simple_payback_years:.2f} yr\n")

    # 2) Node-by-node mass balance (Q_out = Q_in − Q_reject − Q_evap)
    print("=== Mass balance — optimized scenario ===")
    for n in c.optimized.treatment_nodes:
        flag = "on " if n.enabled else "off"
        print(f"[{flag}] {n.label:45s} Q_in {n.q_in_gpd:>10,.0f}  reject {n.q_waste_gpd:>8,.0f}  "
              f"Q_out {n.q_out_gpd:>10,.0f}  TDS {n.c_out['tds']:7.1f}  TSS {n.c_out['tss']:6.2f}")
    t = c.optimized.tower
    print(f"Tower: E {t.evaporation_gpd:,.0f} | D {t.drift_gpd:,.0f} | B {t.blowdown_gpd:,.0f} | "
          f"permeate {t.permeate_gpd:,.0f} | brine {t.brine_gpd:,.0f} gpd | recirc TDS {t.c_recirc['tds']:.0f} mg/L\n")

    # 3) Switch to MBR permeate reuse with RO on, and check QMRA
    reuse = load_config({"site": {"source": "mbr_permeate"}, "treatment": {"ro": {"enabled": True}}})
    r = run_scenario(reuse, "optimized")
    print("=== MBR permeate + RO: QMRA ===")
    for p, x in r.risks.items():
        lrv = "  rec" if p == "legionella" else f"{x.lrv_credit:5.1f}"   # Legionella: recirc control level
        print(f"{p:16s} {x.route:10s} LRV {lrv}  LRT {x.lrt_required:5.2f}  Py {x.py:.2e}  "
              f"{'OK' if x.compliant else 'EXCEEDS 1e-4'}")

    # 4) Export the live-formula workbook for this case
    out = export_workbook(reuse, Path(__file__).with_name("mbr_reuse_case.xlsx"))
    print(f"\nWorkbook: {out}")


if __name__ == "__main__":
    main()
