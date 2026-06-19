#!/usr/bin/env python3
"""DEMEX pricing engine -- bulk discount curves + estimate roll-up.

Takes ONE project (a DEMEX project object, as exported by the app / ingestor /
takeoff) and produces a priced estimate that DEMEX.html can load straight back
in via a `saveEstimate` commit.

It does two things:

1. Bulk discount curve (v1 policy):
     For containment / abatement scope, any line whose quantity exceeds the
     bulk threshold (default 10,000 SF) gets its unit price dropped by the bulk
     discount (default 10%). Each discounted line is recorded in
     `bulk_pricing_thresholds` for transparency.

2. Markup adjustment:
     An optional `--markup-delta` (supplied by learning_loop.py) is added to the
     target profit/markup percentage before the estimate is rolled up.

Output shape (written to --export-json) mirrors what EstimatorWorkspace.save()
builds in DEMEX.html:

    {
      "estimate": {lines, direct, oh, profit, sell, savedAt, source},
      "estQty":   [{svc, label, qty, unit, rate}],
      "unit_rates": [...], "bulk_pricing_thresholds": [...],
      "summary": "human readable line for the toast"
    }

Standard library only.

    python pricing_engine.py --input project.json --export-json out.json \
        --overhead 12 --profit 15 --markup-delta -3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("demex.pricing")

# --- policy constants (centralised for easy tuning) ------------------------- #
BULK_THRESHOLD_SF = 10000.0   # quantity over this (SF) earns the bulk discount
BULK_DISCOUNT = 0.10          # 10% off the unit price
BULK_SCOPES = ("containment", "both")   # scope_types the bulk rule applies to

# Fallback rate card -- midpoints mirror takeoff_scalar.SVC_CATALOG /
# DEMEX.html RATE_DEFAULTS. Only used when a line carries no explicit rate.
RATE_CARD = {
    "demo_walls": 6.5, "demo_floor": 1.05, "demo_base": 0.875,
    "demo_ceiling": 1.275, "demo_gyp_ceiling": 2.125, "millwork": 14.0,
    "doors": 97.5, "storefront": 12.0, "mep": 1.75, "light_fixture": 30.0,
    "diffuser": 40.0, "plumb_fixture": 162.5, "concrete_flat": 3.5,
    "concrete_cut": 10.0, "masonry": 7.0, "exterior_demo": 3.5,
    "asphalt": 1.375, "dumpster": 650.0, "count": 167.5,
    "mezzanine_demo": 5.5, "casework_demo": 15.0, "slab_cut_patch": 8.0,
    "door_window_infill": 375.0, "water_heater_demo": 300.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rate_lookup(unit_rates: list[dict]) -> dict:
    """Map svc -> rate from the project's unit_rates (takeoff/manual override)."""
    table = {}
    for r in unit_rates or []:
        svc = r.get("svc") or r.get("item") or r.get("label")
        if svc and r.get("rate") is not None:
            table[svc] = float(r.get("rate") or 0)
    return table


def price_project(project: dict,
                  overhead_pct: float = 0.0,
                  profit_pct: float = 0.0,
                  markup_delta_pct: float = 0.0,
                  bulk_threshold: float = BULK_THRESHOLD_SF,
                  bulk_discount: float = BULK_DISCOUNT) -> dict:
    """Build a priced estimate from a project's quantities + rates."""
    eq = project.get("estimated_quantities") or project.get("estQty") or []
    overrides = _rate_lookup(project.get("unit_rates"))
    proj_scope = (project.get("scope_type") or "demolition").lower()

    lines, est_qty, new_unit_rates, bulk_rows = [], [], [], []
    bulk_hits = 0

    for q in eq:
        svc = q.get("svc") or ""
        qty = float(q.get("qty") or 0)
        unit = (q.get("unit") or "").upper()
        label = q.get("item") or q.get("label") or svc
        # base rate: explicit line rate -> project override -> rate card
        base_rate = q.get("rate")
        if base_rate is None:
            base_rate = overrides.get(svc)
        if base_rate is None:
            base_rate = RATE_CARD.get(svc, 0.0)
        base_rate = float(base_rate or 0)

        # line scope can override the project scope (mixed-scope jobs)
        line_scope = (q.get("scope_type") or proj_scope).lower()

        eff_rate = base_rate
        if (line_scope in BULK_SCOPES and unit == "SF"
                and qty > bulk_threshold and base_rate > 0):
            eff_rate = round(base_rate * (1.0 - bulk_discount), 4)
            bulk_hits += 1
            bulk_rows.append({
                "svc": svc, "label": label,
                "min_qty": bulk_threshold, "unit": unit,
                "rate": eff_rate, "list_rate": base_rate,
                "discount_pct": round(bulk_discount * 100, 2),
                "scope_type": line_scope,
                "note": (f"Bulk: {qty:g} {unit} > {bulk_threshold:g} -> "
                         f"-{bulk_discount * 100:g}% unit price"),
            })

        lines.append({"svc": svc, "qty": qty, "rate": eff_rate})
        est_qty.append({"svc": svc, "label": label, "qty": qty,
                        "unit": unit, "rate": eff_rate})
        new_unit_rates.append({"svc": svc, "label": label, "qty": qty,
                               "unit": unit, "rate": eff_rate,
                               "scope_type": line_scope})

    direct = round(sum(l["qty"] * l["rate"] for l in lines), 2)
    eff_profit_pct = max(0.0, float(profit_pct) + float(markup_delta_pct))
    oh = round(direct * (float(overhead_pct) / 100.0), 2)
    profit = round((direct + oh) * (eff_profit_pct / 100.0), 2)
    sell = round(direct + oh + profit, 2)

    estimate = {
        "lines": lines, "direct": direct, "oh": oh,
        "profit": profit, "sell": sell,
        "savedAt": _now(), "source": "pricing_engine",
    }

    bits = [f"{len(lines)} line(s) priced", f"sell {sell:,.0f}"]
    if bulk_hits:
        bits.append(f"{bulk_hits} bulk discount(s) @ -{bulk_discount * 100:g}%")
    if markup_delta_pct:
        bits.append(f"markup {markup_delta_pct:+g} pts "
                    f"(profit {profit_pct:g}->{eff_profit_pct:g}%)")
    summary = "; ".join(bits)

    return {
        "estimate": estimate,
        "estQty": est_qty,
        "unit_rates": new_unit_rates,
        "bulk_pricing_thresholds": bulk_rows,
        "applied": {
            "overhead_pct": float(overhead_pct),
            "profit_pct": float(profit_pct),
            "markup_delta_pct": float(markup_delta_pct),
            "effective_profit_pct": eff_profit_pct,
            "bulk_threshold": bulk_threshold,
            "bulk_discount_pct": round(bulk_discount * 100, 2),
            "bulk_hits": bulk_hits,
        },
        "summary": summary,
    }


def _project_from(data) -> dict:
    """Accept a bare project, a {project:{...}} wrapper, or a DEMEX file."""
    if isinstance(data, dict):
        if "project" in data and isinstance(data["project"], dict):
            return data["project"]
        if isinstance(data.get("projects"), list) and data["projects"]:
            return data["projects"][0]
        return data
    raise SystemExit("--input must contain a project object.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Price a DEMEX project: bulk discount curve + estimate.")
    p.add_argument("--input", required=True,
                   help="JSON with a project object (or {project:{...}}).")
    p.add_argument("--export-json", metavar="PATH",
                   help="Write the priced estimate JSON here (also stdout).")
    p.add_argument("--overhead", type=float, default=0.0,
                   help="Overhead %% (default 0; pass company.overheadPct).")
    p.add_argument("--profit", type=float, default=0.0,
                   help="Target profit/markup %% (default 0; pass "
                        "company.profitPct).")
    p.add_argument("--markup-delta", type=float, default=0.0,
                   help="Markup adjustment in points from learning_loop.py "
                        "(e.g. -3).")
    p.add_argument("--bulk-threshold", type=float, default=BULK_THRESHOLD_SF,
                   help=f"SF over which the bulk discount applies (default "
                        f"{BULK_THRESHOLD_SF:g}).")
    p.add_argument("--bulk-discount", type=float, default=BULK_DISCOUNT,
                   help=f"Fractional unit-price discount (default "
                        f"{BULK_DISCOUNT}).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def run(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    project = _project_from(data)
    result = price_project(
        project, overhead_pct=args.overhead, profit_pct=args.profit,
        markup_delta_pct=args.markup_delta,
        bulk_threshold=args.bulk_threshold, bulk_discount=args.bulk_discount,
    )
    log.info("Priced %s: %s", project.get("name", "(project)"),
             result["summary"])
    text = json.dumps(result, indent=2)
    if args.export_json:
        Path(args.export_json).write_text(text, encoding="utf-8")
        log.info("Wrote estimate -> %s", args.export_json)
    print(text)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
