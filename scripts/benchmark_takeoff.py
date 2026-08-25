#!/usr/bin/env python3
"""
benchmark_takeoff.py -- repeatable takeoff-accuracy benchmark for takeoff_scalar.py
===================================================================================

Ground truth = the estimator's Bluebeam markups on the reference sheets under
standards/takeoff/reference/ (the *_MarkedUp.pdf files and their *_takeoff.json
extracts produced by tools/takeoff_markup_extract.py).

Candidate under test = takeoff_scalar.py --mode auto-area (the "Run Visual
Takeoff" path in the app): PDF vector rectangles filtered by
filter_auto_rects(), with an OpenCV contour fallback.

WHAT THIS SCRIPT DOES
---------------------
1. SCALE DERIVATION (the only fair way to run auto mode headless).
   The marked-up PDFs contain Bluebeam measurement annotations that carry BOTH
   the geometry (/Vertices in PDF points) and the estimator's stated real-world
   value (/Contents, e.g. 89'-9 3/4" or 855.41 sf). Each annotation therefore
   pins down pdf-points-per-foot on its own:

       length:  pts_per_ft = polyline_path_length_pts / stated_feet
       area:    pts_per_ft = sqrt(shoelace_area_pts^2 / stated_sf)

   Every annotation is cross-checked against the takeoff JSON (matched by page
   + value) so a mis-parsed label can never poison the scale. The median across
   all annotations becomes the sheet scale; the min/max spread is reported --
   a tight spread validates the whole method. The result is written to the
   sidecar `<pdf>.scale.json` in takeoff_scalar's convention:
   px_per_ft = pts_per_ft * dpi / 72 at the stored dpi.

2. GROUND TRUTH LOADING. In-scope measurements from the takeoff JSON, grouped
   by kind (area SF / length LF / count EA). Out-of-scope lines (abatement /
   by-others) are totalled separately and never counted against the candidate.

3. CANDIDATE RUN. takeoff_scalar.py auto-area is run headless via subprocess
   against a TEMP COPY of the records DB in a temp dir (the real
   demex_records.db is never opened, let alone written). Detected regions are
   read back from the temp DB's line_items rows.

4. COMPARISON.
   - detected region count vs truth area count
   - total detected SF vs truth total SF (signed error %)
   - greedy value-match: each detection (largest first) is matched to the
     closest unmatched truth area when within +/-tolerance (default 25%);
     reports matched / missed truth areas / unmatched detections (false
     positives).
   - LF and EA truth lines are counted as OUT-OF-REACH scope: auto-area only
     produces areas, so linear and count markups are structurally invisible
     to it. They are part of the honest coverage story.

USAGE
    python scripts/benchmark_takeoff.py                    # both reference sheets
    python scripts/benchmark_takeoff.py --sheet scps_lake_howell_c100
    python scripts/benchmark_takeoff.py --out-json results.json --rederive-scale

Exit code 0 = benchmark ran (accuracy is reported, not asserted).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The reference sheets that define this benchmark. `page` is 1-based (the
# sheet inside the PDF that carries the markups).
SHEETS = [
    {
        "id": "scps_lake_howell_c100",
        "label": "SCPS Lake Howell HS CEP -- C100 Site Demolition (civil)",
        "pdf": "standards/takeoff/reference/scps_lake_howell_c100/"
               "SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp.pdf",
        "truth": "standards/takeoff/reference/scps_lake_howell_c100/"
                 "SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp_takeoff.json",
        "page": 1,
    },
    {
        "id": "harbor_freight_jax_beach",
        "label": "Harbor Freight Jax Beach -- D1.0 Demolition (interior)",
        "pdf": "standards/takeoff/reference/harbor_freight_jax_beach/"
               "HarborFreight_JaxBeach_D1.0_Demolition_MarkedUp.pdf",
        "truth": "standards/takeoff/reference/harbor_freight_jax_beach/"
                 "HarborFreight_JaxBeach_D1.0_Demolition_MarkedUp_takeoff.json",
        "page": 4,
    },
]


# --------------------------------------------------------------------------- #
# Scale derivation from Bluebeam measurement annotations                       #
# --------------------------------------------------------------------------- #
_FEET_RE = re.compile(r"^(\d+(?:\.\d+)?)'(?:-((?:\d+)?(?:\s*\d+/\d+)?)\"?)?$")
_SF_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s*sf$", re.I)


def parse_feet(text: str) -> float | None:
    """Parse a Bluebeam feet-inches label like 89'-9 3/4\" into decimal feet."""
    m = _FEET_RE.match((text or "").strip())
    if not m:
        return None
    feet = float(m.group(1))
    inches = 0.0
    for part in (m.group(2) or "").strip().split():
        if "/" in part:
            num, den = part.split("/")
            inches += float(num) / float(den)
        else:
            inches += float(part)
    return feet + inches / 12.0


def parse_sf(text: str) -> float | None:
    m = _SF_RE.match((text or "").strip())
    return float(m.group(1).replace(",", "")) if m else None


def _shoelace(verts: list[tuple[float, float]]) -> float:
    a = 0.0
    n = len(verts)
    for i in range(n):
        x0, y0 = verts[i]
        x1, y1 = verts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _path_length(verts: list[tuple[float, float]], closed: bool) -> float:
    total = sum(math.hypot(verts[i + 1][0] - verts[i][0],
                           verts[i + 1][1] - verts[i][1])
                for i in range(len(verts) - 1))
    if closed and len(verts) > 2:
        total += math.hypot(verts[0][0] - verts[-1][0],
                            verts[0][1] - verts[-1][1])
    return total


def _json_value_exists(truth_measurements: list[dict], page: int, kind: str,
                       qty: float, rel_tol: float = 0.005) -> bool:
    """Cross-check an annotation's parsed value against the takeoff JSON
    (matched by page + kind + value). Guards against label mis-parses."""
    for m in truth_measurements:
        if m.get("page") != page or m.get("kind") != kind:
            continue
        truth_qty = float(m.get("qty") or 0)
        if truth_qty and abs(truth_qty - qty) <= max(0.02, truth_qty * rel_tol):
            return True
    return False


def derive_scale(pdf_path: Path, page_index: int,
                 truth_measurements: list[dict]) -> dict:
    """Derive pdf-points-per-foot from the sheet's own measurement annotations.

    Returns {"pts_per_ft", "n_length", "n_area", "median_length",
    "median_area", "min", "max", "spread_pct", "samples"}.
    """
    from pypdf import PdfReader  # local import: only needed for derivation

    reader = PdfReader(str(pdf_path))
    page = reader.pages[page_index]
    page_no = page_index + 1
    length_ppf: list[float] = []
    area_ppf: list[float] = []
    samples: list[dict] = []

    for ref in (page.get("/Annots") or []):
        obj = ref.get_object()
        subtype = str(obj.get("/Subtype"))
        contents = str(obj.get("/Contents") or "")

        if subtype in ("/PolyLine", "/Line"):
            feet = parse_feet(contents)
            if not feet or feet <= 0:
                continue
            if subtype == "/Line":
                coords = obj.get("/L")
                if not coords:
                    continue
                x0, y0, x1, y1 = (float(v) for v in coords)
                span_pts = math.hypot(x1 - x0, y1 - y0)
            else:
                raw = obj.get("/Vertices")
                if not raw:
                    continue
                verts = [(float(raw[i]), float(raw[i + 1]))
                         for i in range(0, len(raw), 2)]
                closed = "Perimeter" in str(obj.get("/Subj") or "")
                span_pts = _path_length(verts, closed)
            if span_pts <= 0:
                continue
            if not _json_value_exists(truth_measurements, page_no, "length", feet):
                continue  # not confirmed by the takeoff JSON -> skip
            ppf = span_pts / feet
            length_ppf.append(ppf)
            samples.append({"type": "length", "label": contents,
                            "value_ft": round(feet, 2), "pts_per_ft": round(ppf, 4)})

        elif subtype == "/Polygon":
            sf = parse_sf(contents)
            raw = obj.get("/Vertices")
            if not sf or sf <= 0 or not raw:
                continue
            verts = [(float(raw[i]), float(raw[i + 1]))
                     for i in range(0, len(raw), 2)]
            area_pts = _shoelace(verts)
            if area_pts <= 0:
                continue
            if not _json_value_exists(truth_measurements, page_no, "area", sf):
                continue
            ppf = math.sqrt(area_pts / sf)
            area_ppf.append(ppf)
            samples.append({"type": "area", "label": contents,
                            "value_sf": round(sf, 2), "pts_per_ft": round(ppf, 4)})

    all_ppf = length_ppf + area_ppf
    if not all_ppf:
        raise SystemExit(f"No usable measurement annotations on page {page_no} "
                         f"of {pdf_path.name}; cannot derive scale.")
    lo, hi = min(all_ppf), max(all_ppf)
    return {
        "pts_per_ft": statistics.median(all_ppf),
        "n_length": len(length_ppf),
        "n_area": len(area_ppf),
        "median_length": statistics.median(length_ppf) if length_ppf else None,
        "median_area": statistics.median(area_ppf) if area_ppf else None,
        "min": lo,
        "max": hi,
        "spread_pct": (hi - lo) / statistics.median(all_ppf) * 100.0,
        "samples": samples,
    }


def write_sidecar(pdf_path: Path, page_index: int, pts_per_ft: float,
                  dpi: int) -> float:
    """Write takeoff_scalar's <pdf>.scale.json sidecar (merging other pages).

    Sidecar convention (see takeoff_scalar.save_scale / get_scale):
    px_per_ft at a stored render DPI; px_per_ft = pts_per_ft * dpi / 72.
    """
    px_per_ft = pts_per_ft * dpi / 72.0
    sidecar = pdf_path.with_name(pdf_path.name + ".scale.json")
    data = {}
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[str(page_index)] = {
        "px_per_ft": round(px_per_ft, 4),
        "dpi": dpi,
        "saved_at": "benchmark_takeoff.py (derived from Bluebeam markups)",
    }
    sidecar.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return px_per_ft


# --------------------------------------------------------------------------- #
# Ground truth                                                                 #
# --------------------------------------------------------------------------- #
def load_truth(truth_path: Path, page: int) -> dict:
    """In-scope truth per kind for one sheet page; out-of-scope noted apart."""
    doc = json.loads(truth_path.read_text(encoding="utf-8"))
    measurements = [m for m in doc.get("measurements", [])
                    if m.get("page") == page]
    in_scope = [m for m in measurements if m.get("in_scope")]
    out_scope = [m for m in measurements if not m.get("in_scope")]

    def bucket(items: list[dict], kind: str) -> list[dict]:
        return [m for m in items if m.get("kind") == kind]

    areas = bucket(in_scope, "area")
    lengths = bucket(in_scope, "length")
    counts = bucket(in_scope, "count")
    return {
        "measurements": measurements,
        "areas": sorted((float(m["qty"]) for m in areas), reverse=True),
        "area_detail": [{"qty": float(m["qty"]), "scope": m.get("scope"),
                         "svc": m.get("svc")} for m in areas],
        "area_total_sf": sum(float(m["qty"]) for m in areas),
        "length_total_lf": sum(float(m["qty"]) for m in lengths),
        "length_count": len(lengths),
        "count_total_ea": sum(float(m["qty"]) for m in counts),
        "count_count": len(counts),
        "out_of_scope": [{"kind": m.get("kind"), "qty": m.get("qty"),
                          "unit": m.get("unit"), "scope": m.get("scope")}
                         for m in out_scope],
    }


# --------------------------------------------------------------------------- #
# Candidate run (headless auto-area against a temp DB copy)                    #
# --------------------------------------------------------------------------- #
def run_auto_area(pdf_path: Path, page: int, tmp_dir: Path,
                  sheet_id: str, dpi: int) -> dict:
    """Run takeoff_scalar auto-area via subprocess; return detections.

    The run is pointed at a temp COPY of demex_records.db inside tmp_dir
    (--db), so the real records DB is never touched. Detections are read back
    from the temp DB's line_items rows, which persist() tags with the source
    key '<pdf-name>|<method>|p<page>'.
    """
    temp_db = tmp_dir / f"bench_{sheet_id}.db"
    real_db = REPO_ROOT / "demex_records.db"
    if real_db.exists():
        shutil.copy2(real_db, temp_db)  # realistic schema/state, isolated copy

    project = f"BENCHMARK {sheet_id}"
    cmd = [sys.executable, str(REPO_ROOT / "takeoff_scalar.py"),
           "--pdf", str(pdf_path), "--mode", "auto-area",
           "--project", project, "--page", str(page),
           "--dpi", str(dpi), "--no-window", "--db", str(temp_db)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                          text=True, timeout=600)
    if proc.returncode != 0:
        raise SystemExit(f"auto-area failed for {pdf_path.name}:\n"
                         f"{proc.stdout}\n{proc.stderr}")

    detections: list[dict] = []
    if temp_db.exists():
        conn = sqlite3.connect(str(temp_db))
        try:
            rows = conn.execute(
                """SELECT li.qty, li.source_pdf FROM line_items li
                   JOIN projects p ON p.project_id = li.project_id
                   WHERE p.name = ? AND li.kind = 'quantity'
                     AND li.unit = 'SF' AND li.source_pdf LIKE ?""",
                (project, f"{pdf_path.name}|%|p{page}")).fetchall()
        finally:
            conn.close()
        for qty, source in rows:
            method = source.split("|")[1] if "|" in source else "?"
            detections.append({"sf": float(qty), "method": method})
    detections.sort(key=lambda d: d["sf"], reverse=True)
    return {"detections": detections, "log": proc.stderr.strip()}


# --------------------------------------------------------------------------- #
# Comparison                                                                   #
# --------------------------------------------------------------------------- #
def greedy_match(detected: list[float], truth: list[float],
                 tolerance: float) -> dict:
    """Match each detection (largest first) to the closest unmatched truth
    area whose relative difference is within +/-tolerance."""
    unmatched_truth = list(truth)
    matches: list[dict] = []
    false_positives: list[float] = []
    for det in sorted(detected, reverse=True):
        best_i, best_rel = None, None
        for i, t in enumerate(unmatched_truth):
            if t <= 0:
                continue
            rel = abs(det - t) / t
            if rel <= tolerance and (best_rel is None or rel < best_rel):
                best_i, best_rel = i, rel
        if best_i is None:
            false_positives.append(det)
        else:
            matches.append({"detected_sf": det,
                            "truth_sf": unmatched_truth.pop(best_i),
                            "rel_err_pct": round(best_rel * 100.0, 1)})
    return {"matches": matches, "missed_truth": unmatched_truth,
            "false_positives": false_positives}


def compare(truth: dict, detections: list[dict], tolerance: float) -> dict:
    det_sfs = [d["sf"] for d in detections]
    truth_total = truth["area_total_sf"]
    det_total = sum(det_sfs)
    signed_err = ((det_total - truth_total) / truth_total * 100.0
                  if truth_total else None)
    match = greedy_match(det_sfs, truth["areas"], tolerance)
    reachable = truth_total
    out_of_reach = {
        "length_lf": truth["length_total_lf"],
        "length_lines": truth["length_count"],
        "count_ea": truth["count_total_ea"],
        "count_lines": truth["count_count"],
    }
    n_lines_total = (len(truth["areas"]) + truth["length_count"]
                     + truth["count_count"])
    return {
        "truth_area_count": len(truth["areas"]),
        "detected_count": len(det_sfs),
        "truth_total_sf": round(truth_total, 2),
        "detected_total_sf": round(det_total, 2),
        "signed_error_pct": round(signed_err, 1) if signed_err is not None else None,
        "matched": match["matches"],
        "missed_truth_sf": [round(v, 2) for v in match["missed_truth"]],
        "false_positive_sf": [round(v, 2) for v in match["false_positives"]],
        "matched_truth_sf_total": round(sum(m["truth_sf"] for m in match["matches"]), 2),
        "reachable_scope_sf": round(reachable, 2),
        "out_of_reach": out_of_reach,
        "coverage": {
            "area_lines_pct_of_all_lines": round(
                len(truth["areas"]) / n_lines_total * 100.0, 1) if n_lines_total else None,
            "matched_sf_pct_of_truth_sf": round(
                sum(m["truth_sf"] for m in match["matches"]) / truth_total * 100.0, 1)
                if truth_total else None,
        },
    }


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #
def fmt_num(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def print_markdown(results: list[dict]) -> None:
    for r in results:
        s = r["scale"]
        c = r["comparison"]
        print(f"\n## {r['label']}")
        print(f"\nScale: {s['pts_per_ft']:.4f} pts/ft "
              f"(median of {s['n_length']} length + {s['n_area']} area "
              f"annotations; spread {s['spread_pct']:.2f}%) "
              f"-> sidecar {r['px_per_ft']:.4f} px/ft @ {r['dpi']} dpi")
        print(f"Detection method used: {r['method'] or 'none'}")
        print("\n| Metric | Ground truth | Auto-area detected |")
        print("|---|---|---|")
        print(f"| Area regions | {c['truth_area_count']} | {c['detected_count']} |")
        print(f"| Total area SF | {fmt_num(c['truth_total_sf'])} | "
              f"{fmt_num(c['detected_total_sf'])} "
              f"({c['signed_error_pct']:+.1f}% signed error) |")
        print(f"| Matched regions (+/-25%) | -- | {len(c['matched'])} |")
        print(f"| Missed truth regions | -- | {len(c['missed_truth_sf'])} |")
        print(f"| False positives | -- | {len(c['false_positive_sf'])} |")
        print(f"| Matched SF as % of truth SF | -- | "
              f"{c['coverage']['matched_sf_pct_of_truth_sf']}% |")
        if c["matched"]:
            print("\nMatched pairs (detected -> truth, SF):")
            for m in c["matched"]:
                print(f"  - {fmt_num(m['detected_sf'])} -> {fmt_num(m['truth_sf'])} "
                      f"({m['rel_err_pct']}% off)")
        if c["false_positive_sf"]:
            print(f"\nFalse-positive detections (SF): "
                  f"{', '.join(fmt_num(v) for v in c['false_positive_sf'])}")
        oor = c["out_of_reach"]
        print(f"\nOut-of-reach scope (auto-area is areas-only): "
              f"{oor['length_lines']} linear lines totalling "
              f"{fmt_num(oor['length_lf'])} LF and {oor['count_lines']} count "
              f"lines totalling {fmt_num(oor['count_ea'])} EA are structurally "
              f"invisible to this mode.")
        if r["truth"]["out_of_scope"]:
            print(f"Noted separately (abatement / by-others, excluded from truth): "
                  f"{len(r['truth']['out_of_scope'])} line(s).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Benchmark takeoff_scalar auto-area against Bluebeam "
                    "ground-truth markups.")
    ap.add_argument("--sheet", action="append", choices=[s["id"] for s in SHEETS],
                    help="Limit to one reference sheet (repeatable). "
                         "Default: all.")
    ap.add_argument("--dpi", type=int, default=200,
                    help="Render DPI for the candidate run (default 200, the "
                         "app default).")
    ap.add_argument("--tolerance", type=float, default=0.25,
                    help="Greedy-match relative tolerance (default 0.25).")
    ap.add_argument("--rederive-scale", action="store_true",
                    help="Recompute and rewrite the .scale.json sidecars even "
                         "if they exist.")
    ap.add_argument("--out-json", metavar="PATH",
                    help="Also write the full results as JSON to this path.")
    args = ap.parse_args(argv)

    wanted = set(args.sheet or [s["id"] for s in SHEETS])
    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="bench_takeoff_") as td:
        tmp_dir = Path(td)
        for sheet in SHEETS:
            if sheet["id"] not in wanted:
                continue
            pdf_path = REPO_ROOT / sheet["pdf"]
            truth_path = REPO_ROOT / sheet["truth"]
            if not pdf_path.exists() or not truth_path.exists():
                print(f"SKIP {sheet['id']}: reference files missing.",
                      file=sys.stderr)
                continue
            page_index = sheet["page"] - 1

            truth = load_truth(truth_path, sheet["page"])
            scale = derive_scale(pdf_path, page_index, truth["measurements"])

            sidecar = pdf_path.with_name(pdf_path.name + ".scale.json")
            have = None
            if sidecar.exists() and not args.rederive_scale:
                try:
                    have = json.loads(sidecar.read_text())[str(page_index)]
                except Exception:
                    have = None
            if have and int(have.get("dpi", 0)) == args.dpi:
                px_per_ft = float(have["px_per_ft"])
            else:
                px_per_ft = write_sidecar(pdf_path, page_index,
                                          scale["pts_per_ft"], args.dpi)

            run = run_auto_area(pdf_path, sheet["page"], tmp_dir,
                                sheet["id"], args.dpi)
            comparison = compare(truth, run["detections"], args.tolerance)
            methods = {d["method"] for d in run["detections"]}
            results.append({
                "id": sheet["id"],
                "label": sheet["label"],
                "page": sheet["page"],
                "dpi": args.dpi,
                "px_per_ft": px_per_ft,
                "method": "/".join(sorted(methods)),
                "scale": {k: v for k, v in scale.items() if k != "samples"},
                "scale_samples": scale["samples"],
                "truth": {k: v for k, v in truth.items() if k != "measurements"},
                "detections": run["detections"],
                "comparison": comparison,
            })

    print_markdown(results)
    if args.out_json:
        payload = [{k: v for k, v in r.items()} for r in results]
        Path(args.out_json).write_text(json.dumps(payload, indent=2),
                                       encoding="utf-8")
        print(f"\nJSON written -> {args.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
