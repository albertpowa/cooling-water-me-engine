"""Generate the formula-driven Excel workbook ``Data_Center_Cooling_ME_Engine.xlsx``.

Usage:
    python build_cooling_me_engine.py                     # default 50 MW NYC case
    python build_cooling_me_engine.py -o out.xlsx --source mbr_permeate --it-mw 20 --ro
"""

from __future__ import annotations

import argparse
from typing import Any, Dict

from src.engine import compare_scenarios, load_config
from src.engine.config import source_keys
from src.excel.exporter import DEFAULT_FILENAME, export_workbook


def main() -> None:
    cfg0 = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=DEFAULT_FILENAME)
    ap.add_argument("--source", choices=list(source_keys(cfg0)), help="raw water source key")
    ap.add_argument("--it-mw", type=float, help="IT load (MW)")
    ap.add_argument("--ro", action="store_true", help="enable RO/IX demineralization")
    args = ap.parse_args()

    overrides: Dict[str, Any] = {}
    if args.source:
        overrides.setdefault("site", {})["source"] = args.source
    if args.it_mw:
        overrides.setdefault("data_center", {})["it_load_mw"] = args.it_mw
    if args.ro:
        overrides.setdefault("treatment", {})["ro"] = {"enabled": True}
    cfg = load_config(overrides)

    path = export_workbook(cfg, args.output)
    c = compare_scenarios(cfg)
    print(f"Workbook written: {path.resolve()}")
    print(f"  Intake  {c.baseline.intake_gpd:,.0f} → {c.optimized.intake_gpd:,.0f} gpd   "
          f"CoC {c.baseline.tower.coc:.1f} → {c.optimized.tower.coc:.1f}")
    print(f"  NPV ${c.finance.npv:,.0f}   SPBT {c.finance.simple_payback_years or float('nan'):.2f} yr")
    print("Open in Excel — all results are live formulas (yellow = inputs).")


if __name__ == "__main__":
    main()
