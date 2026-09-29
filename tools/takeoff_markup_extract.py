#!/usr/bin/env python3
"""
takeoff_markup_extract.py -- turn a Bluebeam-marked-up plan into DEMEX quantities
================================================================================

Shawn marks the plan up in Bluebeam Revu. Revu stores every measurement as a real
annotation object inside the saved PDF -- an area polygon carries "855.41 sf" in
its /Contents, a polylength carries "89'-9 3/4\"", and the annotation's colour is
what says *which scope* it belongs to.

This reads those objects back out and writes DEMEX quantities. No pixels, no
computer vision, no scale calibration -- the numbers were already measured at
true scale by the estimator, so they come across exact.

    Bluebeam markup  ->  this script  ->  estQty[]  ->  pricing_engine.py

HOW SCOPE IS DECIDED
--------------------
Colour, and only colour. The mapping lives in
standards/takeoff/markup_legend.json and is documented for humans in
standards/takeoff/DEMEX_Takeoff_Markup_Standard.md. Mark up off-legend and this
tool will mis-bucket the quantity, which is the whole reason the legend is a
written standard rather than a habit.

A colour is matched to the nearest legend entry within a tolerance. Anything
outside tolerance is emitted as `UNMAPPED` with needs_review=1 rather than being
guessed into a bucket.

WHAT IT DOES NOT DO
-------------------
It reports gross measured geometry. It does not add waste, overlap, thickness,
haul factor or access difficulty -- those are pricing decisions and they belong
to the pricing engine, not the extractor. Freehand /Ink highlights carry no text
at all, so they come out as an unlabeled count of 1 and are always flagged for
review.

USAGE
    python tools/takeoff_markup_extract.py --pdf "plan - Marked Up.pdf"
    python tools/takeoff_markup_extract.py --pdf plan.pdf --project pj_xxx --db
    python tools/takeoff_markup_extract.py --pdf plan.pdf --out takeoff.json --quiet

DEPENDENCIES
    pip install pypdf
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

try:
    import pypdf
except ImportError:
    sys.exit("pypdf is required:  pip install pypdf")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_LEGEND = os.path.join(REPO, "standards", "takeoff", "markup_legend.json")
DEFAULT_DB = os.path.join(REPO, "demex_records.db")

AREA_SUBTYPES = {"/Polygon"}
LENGTH_SUBTYPES = {"/PolyLine", "/Line"}
COUNT_SUBTYPES = {"/Highlight", "/Ink", "/Stamp", "/Circle"}

# Bluebeam writes the measurement kind here; sheet detail boxes have no /IT.
MEASURE_INTENTS = {"/PolygonDimension", "/PolyLineDimension", "/LineDimension"}


def resolve_profile(legend: dict, profile: str | None) -> tuple[dict, str]:
    """Legend v2 is scoped by discipline. v1 (flat 'colors') still loads."""
    if "profiles" not in legend:
        return legend, "default"
    name = profile or legend.get("default_profile")
    if name not in legend["profiles"]:
        sys.exit(f"unknown profile {name!r}. Available: {', '.join(legend['profiles'])}")
    p = dict(legend["profiles"][name])
    p["version"] = legend.get("version")
    return p, name


# --------------------------------------------------------------------------- #
# value parsing
# --------------------------------------------------------------------------- #

_SF = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*(sf|sq\.?\s*ft\.?|ft2)\s*$", re.I)
_LF_DECIMAL = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*(lf|ft|feet|')\s*$", re.I)
_FT_IN = re.compile(r"^\s*(\d+)\s*'\s*-?\s*(.*?)\"?\s*$")
_EA = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*(ea|each|pcs?)\s*$", re.I)


def parse_area_sf(text: str) -> float | None:
    m = _SF.match(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def parse_length_ft(text: str) -> float | None:
    """Handles 89'-9 3/4\"  |  53'-3/4\"  |  15'-0\"  |  11'-5\"  |  42.5 ft."""
    text = (text or "").strip()
    m = _LF_DECIMAL.match(text)
    if m:
        return float(m.group(1).replace(",", ""))
    m = _FT_IN.match(text)
    if not m:
        return None
    feet = float(m.group(1))
    inches = 0.0
    for tok in (m.group(2) or "").replace("-", " ").split():
        if "/" in tok:
            try:
                num, den = tok.split("/")
                inches += float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                return None
        else:
            try:
                inches += float(tok)
            except ValueError:
                return None
    return round(feet + inches / 12.0, 4)


def parse_count(text: str) -> float | None:
    m = _EA.match(text or "")
    return float(m.group(1).replace(",", "")) if m else None


# --------------------------------------------------------------------------- #
# colour
# --------------------------------------------------------------------------- #

def to_rgb(c) -> tuple[float, float, float] | None:
    """PDF /C is DeviceGray (1), DeviceRGB (3) or DeviceCMYK (4)."""
    if not c:
        return None
    try:
        v = [float(x) for x in c]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        return (v[0], v[0], v[0])
    if len(v) == 3:
        return tuple(v)  # type: ignore[return-value]
    if len(v) == 4:
        cy, ma, ye, k = v
        return (round((1 - cy) * (1 - k), 4), round((1 - ma) * (1 - k), 4), round((1 - ye) * (1 - k), 4))
    return None


def hexof(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*[max(0, min(255, round(x * 255))) for x in rgb])


def match_colour(rgb, legend: dict, tol: float) -> dict | None:
    """Nearest legend colour inside tolerance, else None (never a guess)."""
    if rgb is None:
        return None
    best, best_d = None, 1e9
    for entry in legend["colors"]:
        er, eg, eb = entry["rgb"]
        d = ((rgb[0] - er) ** 2 + (rgb[1] - eg) ** 2 + (rgb[2] - eb) ** 2) ** 0.5
        if d < best_d:
            best, best_d = entry, d
    return best if best_d <= tol else None


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

def extract(pdf_path: str, legend: dict, tol: float = 0.30, profile_name: str = "default") -> dict:
    reader = pypdf.PdfReader(pdf_path)
    rows: list[dict] = []
    skipped = defaultdict(int)

    for page_no, page in enumerate(reader.pages, start=1):
        annots = page.get("/Annots") or []
        # Some writers store /Annots behind an indirect reference; resolve it
        # before iterating or pypdf hands back a non-iterable IndirectObject.
        if hasattr(annots, "get_object"):
            annots = annots.get_object() or []
        for ref in annots:
            try:
                a = ref.get_object()
            except Exception:
                continue
            sub = str(a.get("/Subtype"))
            intent = str(a.get("/IT"))
            contents = str(a.get("/Contents") or "").strip()
            subj = str(a.get("/Subj") or "").strip()
            rgb = to_rgb(a.get("/C"))

            kind = qty = unit = None
            if sub in AREA_SUBTYPES and intent in MEASURE_INTENTS:
                qty, kind, unit = parse_area_sf(contents), "area", "SF"
            elif sub in LENGTH_SUBTYPES and intent in MEASURE_INTENTS:
                qty, kind, unit = parse_length_ft(contents), "length", "LF"
            elif sub in COUNT_SUBTYPES:
                qty, kind, unit = (parse_count(contents) or 1.0), "count", "EA"
            else:
                skipped[sub] += 1
                continue

            if qty is None:
                skipped[f"{sub} (unparsed value: {contents[:28]!r})"] += 1
                continue

            entry = match_colour(rgb, legend, tol)
            scope = (entry or {}).get("scope", {}).get(kind) if entry else None

            rows.append({
                "page": page_no,
                "kind": kind,
                "qty": round(float(qty), 2),
                "unit": unit,
                "label": contents or None,
                "measurement": subj or None,
                "color_rgb": [round(x, 4) for x in rgb] if rgb else None,
                "color_hex": hexof(rgb) if rgb else None,
                "color_name": (entry or {}).get("name", "UNMAPPED"),
                "scope": (scope or {}).get("description") if scope else "UNMAPPED - colour not on legend",
                "svc": (scope or {}).get("svc") if scope else None,
                "in_scope": bool(entry.get("in_scope", True)) if entry else True,
                "confidence": (entry or {}).get("confidence", "unconfirmed") if entry else "unmapped",
                "author": str(a.get("/T") or "") or None,
                "needs_review": int(
                    entry is None
                    or scope is None
                    or (entry or {}).get("confidence") == "unconfirmed"
                    or (kind == "count" and not contents)
                ),
            })

    # rollup by svc
    roll = defaultdict(lambda: {"qty": 0.0, "n": 0})
    for r in rows:
        key = (r["svc"], r["unit"], r["scope"], r["in_scope"], r["confidence"])
        roll[key]["qty"] += r["qty"]
        roll[key]["n"] += 1

    rollup = [
        {"svc": k[0], "unit": k[1], "scope": k[2], "in_scope": k[3], "confidence": k[4],
         "qty": round(v["qty"], 2), "annotations": v["n"]}
        for k, v in sorted(roll.items(), key=lambda kv: (kv[0][0] is None, -kv[1]["qty"]))
    ]

    return {
        "schema": "demex_takeoff_markup/v1",
        "source_pdf": os.path.basename(pdf_path),
        "pages": len(reader.pages),
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "legend_version": legend.get("version"),
        "legend_profile": profile_name,
        "counts": {
            "measurements": len(rows),
            "area": sum(1 for r in rows if r["kind"] == "area"),
            "length": sum(1 for r in rows if r["kind"] == "length"),
            "count": sum(1 for r in rows if r["kind"] == "count"),
            "needs_review": sum(r["needs_review"] for r in rows),
            "unmapped_colour": sum(1 for r in rows if r["color_name"] == "UNMAPPED"),
            "unconfirmed_legend": sum(1 for r in rows if r["confidence"] == "unconfirmed"),
        },
        "skipped_annotations": dict(skipped),
        "rollup": rollup,
        "measurements": rows,
    }


# The line_items.scope_type vocabulary elsewhere is demolition|containment|both.
# Map a legend svc id onto it: abatement/containment services are 'containment',
# everything else the legend measures is demolition scope.
_CONTAINMENT_SVC_HINTS = ("abate", "containment", "hazmat", "remediation")


def svc_scope_type(svc: str | None) -> str:
    s = (svc or "").lower()
    if any(h in s for h in _CONTAINMENT_SVC_HINTS):
        return "containment"
    return "demolition"


def to_est_qty(result: dict) -> list[dict]:
    """The DEMEX projects[].estQty shape, in-scope items only."""
    flagged = {(m["svc"], m["unit"]) for m in result["measurements"] if m["needs_review"]}
    return [
        # "item" is what downstream consumers (pricing_engine / mapEQ) read;
        # "desc" is kept for backward compatibility with older importers.
        {"item": r["scope"], "desc": r["scope"], "svc": r["svc"],
         "scope_type": svc_scope_type(r["svc"]),
         "qty": r["qty"], "unit": r["unit"],
         "source": result["source_pdf"],
         "needs_review": int((r["svc"], r["unit"]) in flagged)}
        for r in result["rollup"] if r["in_scope"] and r["svc"]
    ]


def write_db(result: dict, project_id: str, db_path: str) -> int:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    n = 0
    for r in result["measurements"]:
        if not r["in_scope"] or not r["svc"]:
            continue
        cur.execute(
            "INSERT INTO line_items (project_id, kind, description, scope_type, qty, unit, "
            "unit_price, min_qty, source_pdf, needs_review) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, "quantity", f"{r['scope']} [{r['svc']}]",
             svc_scope_type(r["svc"]), r["qty"], r["unit"],
             None, None, f"{result['source_pdf']} p{r['page']}", r["needs_review"]),
        )
        n += 1
    con.commit()
    con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract DEMEX quantities from a Bluebeam-marked-up plan.")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--legend", default=DEFAULT_LEGEND)
    ap.add_argument("--profile", help="legend profile: site_civil | interior (default from legend)")
    ap.add_argument("--out", help="JSON output path (default: alongside the PDF)")
    ap.add_argument("--project", help="DEMEX project_id, required with --db")
    ap.add_argument("--db", nargs="?", const=DEFAULT_DB, help="write line_items into demex_records.db")
    ap.add_argument("--tolerance", type=float, default=0.30, help="colour match distance, 0-1.73")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    legend_file = json.load(open(args.legend, encoding="utf-8"))
    legend, profile_name = resolve_profile(legend_file, args.profile)
    result = extract(args.pdf, legend, args.tolerance, profile_name)
    result["est_qty"] = to_est_qty(result)

    out = args.out or os.path.splitext(args.pdf)[0] + "_takeoff.json"
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2)

    if args.db:
        if not args.project:
            sys.exit("--db requires --project <project_id>")
        n = write_db(result, args.project, args.db)
        if not args.quiet:
            print(f"wrote {n} line_items into {args.db} for project {args.project}")

    if not args.quiet:
        c = result["counts"]
        print(f"\n{result['source_pdf']}  ({result['pages']} page(s), legend {result['legend_version']} / profile {result['legend_profile']})")
        print(f"  {c['measurements']} measurements: {c['area']} area, {c['length']} length, {c['count']} count")
        print(f"  {c['needs_review']} need review, {c['unmapped_colour']} off-legend colour, "
              f"{c['unconfirmed_legend']} on unconfirmed legend entries\n")
        print(f"  {'SCOPE':<52}{'QTY':>12}  UNIT   n")
        print("  " + "-" * 78)
        for r in result["rollup"]:
            flag = ""
            if not r["in_scope"]:
                flag = "   [BY OTHERS - recorded, not priced]"
            elif not r["svc"]:
                flag = "   [REVIEW - scope not established]"
            elif r["confidence"] != "confirmed":
                flag = f"   [{r['confidence']}]"
            print(f"  {r['scope'][:50]:<52}{r['qty']:>12,.2f}  {r['unit']:<5}{r['annotations']:>3}{flag}")
        print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
