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
      "unpriced_lines":  [{item, svc, qty, unit, reason}],   # NO rate resolved
      "unpriced_count":  <int>,
      "by_others_lines": [{item, svc, qty, unit, reason}],   # measured, priced by a sub
      "by_others_count": <int>,
      "warning": null | "...",   # set whenever unpriced_count > 0
      "summary": "human readable line for the toast"
    }

Rate-resolution policy (v3): a line prices only when a rate actually resolves
(explicit line rate -> project unit_rates override -> synced app rate card
(demex_rate_card.json, pushed by DEMEX.html via the bridge) -> embedded
RATE_CARD fallback). A line whose
svc is in PRICED_BY_OTHERS (and carries no explicit/override rate) is measured
and listed under `by_others_lines` but never priced by us. Any other line with
no resolvable rate is EXCLUDED from the totals and reported in
`unpriced_lines` -- it is never silently priced at 0.

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

ROOT = Path(__file__).resolve().parent

# Live rate card synced from DEMEX.html via the bridge (POST /api/ratecard).
# When present, it is the single source of truth for catalog rates; the
# embedded RATE_CARD below remains only as a fallback for svcs the synced
# file does not carry.
RATE_CARD_FILE = ROOT / "demex_rate_card.json"

# Mirrors DEMEX.html TIER = {Lean:0.85, Market:1.0, Premium:1.2}.
TIER_MULT = {"Lean": 0.85, "Market": 1.0, "Premium": 1.2}

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
    # --- rate-card gap closures (markup_legend.json rate_card_gaps) -------- #
    # interior_strip_out: Harbor Freight Jax Beach bid DDD-2026/0811R1,
    #   standards/takeoff/reference/harbor_freight_jax_beach/bid_sheet.json
    #   derived_rates: $44,950 base bid / 11,885.24 SF measured lease space
    #   = $3.782/SF, confidence "high" (n=1 job -- all-in retail strip-out).
    "interior_strip_out": 3.78,
    # slab_removal: no clean unit rate exists (Harbor Freight Alt 4 bundles
    #   slab + trench at $14.85/SF, flagged "do not carry forward"), so this
    #   mirrors the owner's existing equivalent: concrete_flat "Concrete slab
    #   removal" (DEMEX.html RATE_DEFAULTS low 2 / high 5 -> midpoint 3.5).
    "slab_removal": 3.5,
    # NOT added (no defensible source in the owner's data -- these svcs will
    # surface in `unpriced_lines` instead of pricing at a made-up number):
    #   structure_demo -- markup standard: closest existing is exterior_demo,
    #     "which prices a different operation"; no priced data point exists.
    #   fence_demo     -- Master Reference Rate Sheet J: "blended only",
    #     absorbed into lump site items; no unit rate recoverable.
    #   utility_demo   -- markup standard: production swings with depth and
    #     surface above, likely needs depth bands; no unit rate recoverable.
}

# Svcs that are measured and listed but never priced by this engine: the
# markup standard (DEMEX_Takeoff_Markup_Standard.md) keeps regulated material
# on its own colour precisely because abatement carries a different licence,
# crew and liability -- it is priced by an abatement subcontractor, never at
# a demo rate. An explicit line rate or project unit_rates override (e.g. a
# sub quote entered by the estimator) still prices the line normally.
PRICED_BY_OTHERS = {
    "abate_containment": ("priced by abatement subcontractor -- regulated "
                          "material, never priced at a demo rate"),
    "abate_linear": ("priced by abatement subcontractor -- regulated "
                     "material, never priced at a demo rate"),
}


def load_synced_rates(path: Path = RATE_CARD_FILE):
    """Load the app-synced rate card into a {svc: effective_rate} table.

    Effective unit rate = midpoint (low+high)/2
                          x tier multiplier (Lean 0.85 / Market 1.0 /
                            Premium 1.2; default 1.0)
                          x regionMult (default 1.0)
                          x (1 + override_pct/100) when a rateOverrides
                            entry exists for the svc.

    Returns (table, source_label) or (None, None) when the file is absent
    or unusable -- callers then fall back to the embedded RATE_CARD.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except Exception as e:
        log.warning("Ignoring unreadable rate card %s: %s", path, e)
        return None, None
    rates = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rates, list) or not rates:
        return None, None
    settings = data.get("rateSettings") or {}
    tier_mult = TIER_MULT.get(settings.get("tier"), 1.0)
    try:
        region_mult = float(settings.get("regionMult") or 1.0)
    except (TypeError, ValueError):
        region_mult = 1.0
    overrides = data.get("rateOverrides") or {}
    table = {}
    for r in rates:
        if not isinstance(r, dict):
            continue
        svc = r.get("id")
        if not svc:
            continue
        try:
            low, high = float(r.get("low")), float(r.get("high"))
        except (TypeError, ValueError):
            continue
        rate = ((low + high) / 2.0) * tier_mult * region_mult
        try:
            pct = float(overrides.get(svc) or 0.0)
        except (TypeError, ValueError):
            pct = 0.0
        if pct:
            rate *= (1.0 + pct / 100.0)
        table[str(svc)] = round(rate, 4)
    if not table:
        return None, None
    label = (f"synced:{Path(path).name} "
             f"(savedAt {data.get('savedAt') or 'unknown'})")
    return table, label


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
    synced_rates, synced_label = load_synced_rates()
    rate_source = synced_label or "embedded-defaults"

    lines, est_qty, new_unit_rates, bulk_rows = [], [], [], []
    unpriced_lines, by_others_lines = [], []
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
            if svc in PRICED_BY_OTHERS:
                # Measured and listed so the scope is provable, but the price
                # belongs to a specialist sub -- never rolled into our totals.
                by_others_lines.append({
                    "item": label, "svc": svc, "qty": qty, "unit": unit,
                    "reason": PRICED_BY_OTHERS[svc],
                })
                continue
            # Synced app rate card (when present) beats the embedded card;
            # embedded RATE_CARD stays as fallback for svcs it doesn't carry.
            if synced_rates is not None:
                base_rate = synced_rates.get(svc)
            if base_rate is None:
                base_rate = RATE_CARD.get(svc)
        if base_rate is None:
            # No rate resolved anywhere: flag, don't silently price at 0.
            if not svc:
                reason = ("line carries no svc code (e.g. raw ingest scope "
                          "item) -- no rate can resolve")
            else:
                reason = (f"no rate for svc '{svc}' (no line rate, no "
                          f"project unit_rates entry, no synced/embedded "
                          f"rate-card entry)")
            unpriced_lines.append({
                "item": label, "svc": svc, "qty": qty, "unit": unit,
                "reason": reason,
            })
            continue
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
    if by_others_lines:
        bits.append(f"{len(by_others_lines)} line(s) by others "
                    f"(abatement sub) -- listed, not in totals")
    warning = None
    if unpriced_lines:
        warning = (f"{len(unpriced_lines)} line(s) have no resolvable rate "
                   f"and are EXCLUDED from the priced totals -- see "
                   f"unpriced_lines")
        bits.append(f"WARNING: {warning}")
    summary = "; ".join(bits)

    return {
        "estimate": estimate,
        "estQty": est_qty,
        "unit_rates": new_unit_rates,
        "bulk_pricing_thresholds": bulk_rows,
        "unpriced_lines": unpriced_lines,
        "unpriced_count": len(unpriced_lines),
        "by_others_lines": by_others_lines,
        "by_others_count": len(by_others_lines),
        "warning": warning,
        "rate_source": rate_source,
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
