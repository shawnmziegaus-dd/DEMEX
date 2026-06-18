#!/usr/bin/env python3
"""
takeoff_scalar.py  --  DEMEX blueprint Computer-Vision takeoff engine
=====================================================================

Turns PDF construction drawings into *scaled, priced quantities* and writes them
into the same SQLite database (`demex_records.db`) the email ingestor uses, so
the numbers flow into the DEMEX.html dashboard with zero re-entry.

WHY THIS EXISTS
---------------
A language model cannot measure scale off a drawing. This tool can: you lock a
pixel-to-foot ratio from a known dimension, then it computes square footage,
linear footage, and symbol counts with real geometry (OpenCV + the PDF's own
vector data). DEMEX.html already has a *manual* takeoff workspace; the unique
value here is AUTOMATION -- counting a symbol across every sheet, and
auto-tracing room contours -- plus unattended batch runs.

THE FOUR OPERATIONAL FEATURES
-----------------------------
1. Scale Calibration Engine  (--mode calibrate)
   Draw a line over a known dimension (e.g. a 10'-0" gridline), type the real
   length, and the pixel span locks a permanent Pixel-to-Foot ratio for that
   sheet. Saved to a sidecar `<pdf>.scale.json` so you never recalibrate.

2. Area Takeoffs / Square Footage  (--mode area | auto-area)
   Click a room's corners to trace a polygon (shoelake area), OR let OpenCV
   auto-detect closed boundary contours and report each enclosed area. Square
   feet (and yardage = SF / 9) for flooring containment / abatement zones.

3. Linear Takeoffs / Linear Footage  (--mode linear)
   Trace a wall or pipe run; the pixel path length x the scale ratio gives
   mathematically precise demolition lengths in LF.

4. Auto-Count Quantities  (--mode count)
   Drag a box around ONE engineering symbol (a plumbing junction, a hazmat
   callout, a light fixture). The tool template-matches it across the whole
   page set and outputs a verified count, with a `_count_overlay.png` showing
   every hit for visual confirmation.

WHAT IS / ISN'T AUTOMATIC -- READ THIS
--------------------------------------
  * Template matching is sensitive to rotation/scale variance. We search several
    scales and de-duplicate overlapping hits, but every auto-count and every
    auto-contour area is written with needs_review=1. Treat them as a fast first
    pass for an estimator, not a certified count.
  * Dollars: a quantity only prices out in DEMEX if its `svc` matches the rate
    card. Defaults follow the app: area->demo_floor (SF), length->demo_walls
    (LF), count->doors (EA); override with --svc.
  * Rendering needs a raster backend; we use pypdfium2 (pure wheel, no system
    install). Vector geometry comes from pdfplumber.
  * The interactive window needs a desktop session (fine on Windows). Headless
    runs reuse the saved sidecar scale (--no-window).

DEPENDENCIES (see requirements.txt) -- do NOT run until installed:
    pip install -r requirements.txt        # opencv-python, numpy, pypdfium2, pdfplumber

USAGE
    python takeoff_scalar.py --pdf plan.pdf --mode calibrate --page 1
    python takeoff_scalar.py --pdf plan.pdf --project "Sunglass Hut" --mode area
    python takeoff_scalar.py --pdf plan.pdf --project "Sunglass Hut" --mode linear --svc concrete_cut
    python takeoff_scalar.py --pdf plan.pdf --project "Sunglass Hut" --mode count --svc plumb_fixture
    python takeoff_scalar.py --pdf plan.pdf --project "Sunglass Hut" --mode auto-area \
        --export-json takeoff_import.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Reuse the email ingestor's DB / export plumbing so both tools write the SAME #
# demex_records.db and emit the SAME DEMEX.html import format. These helpers   #
# json.dumps the quantity lists verbatim, so the `svc` keys we add survive     #
# untouched all the way into the dashboard.                                    #
# --------------------------------------------------------------------------- #
from email_ingestor import (  # noqa: E402
    db_init,
    project_id_for,
    upsert_project,
    export_demex_json,
    slugify,
)

# --------------------------------------------------------------------------- #
# Optional heavy dependencies (graceful import -- the module still loads so it #
# can be inspected/linted before the packages are installed).                  #
# --------------------------------------------------------------------------- #
try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import pypdfium2 as pdfium  # type: ignore
except Exception:  # pragma: no cover
    pdfium = None  # type: ignore

try:
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore


log = logging.getLogger("demex.takeoff")


# --------------------------------------------------------------------------- #
# Service / rate catalog -- mirrors DEMEX.html RATE_DEFAULTS (line 353+).      #
# A quantity must carry one of these `svc` ids to price out via the rate card. #
# --------------------------------------------------------------------------- #
SVC_CATALOG: dict[str, dict] = {
    "demo_walls":        {"label": "Demo partition walls",                 "unit": "LF", "low": 4,   "high": 9},
    "demo_floor":        {"label": "Demo flooring to slab",                "unit": "SF", "low": 0.6, "high": 1.5},
    "demo_base":         {"label": "Demo wall base",                       "unit": "LF", "low": 0.5, "high": 1.25},
    "demo_ceiling":      {"label": "Demo ACT ceiling + grid",             "unit": "SF", "low": 0.8, "high": 1.75},
    "demo_gyp_ceiling":  {"label": "Demo gypsum ceiling/soffit",          "unit": "SF", "low": 1.25, "high": 3},
    "millwork":          {"label": "Casework / millwork",                  "unit": "LF", "low": 8,   "high": 20},
    "doors":             {"label": "Doors / frames / hardware",            "unit": "EA", "low": 45,  "high": 150},
    "storefront":        {"label": "Storefront / glazing",                 "unit": "SF", "low": 6,   "high": 18},
    "mep":               {"label": "MEP rough-in removal",                 "unit": "SF", "low": 1,   "high": 2.5},
    "light_fixture":     {"label": "Light fixtures",                       "unit": "EA", "low": 15,  "high": 45},
    "diffuser":          {"label": "HVAC diffusers / grilles",            "unit": "EA", "low": 20,  "high": 60},
    "plumb_fixture":     {"label": "Plumbing fixtures",                    "unit": "EA", "low": 75,  "high": 250},
    "concrete_flat":     {"label": "Concrete slab removal",                "unit": "SF", "low": 2,   "high": 5},
    "concrete_cut":      {"label": "Concrete sawcutting",                  "unit": "LF", "low": 5,   "high": 15},
    "masonry":           {"label": "Masonry / CMU removal",               "unit": "SF", "low": 4,   "high": 10},
    "exterior_demo":     {"label": "Exterior wall / cladding",            "unit": "SF", "low": 2,   "high": 5},
    "asphalt":           {"label": "Asphalt removal / milling",          "unit": "SF", "low": 0.75, "high": 2},
    "dumpster":          {"label": "Disposal (per 30yd dumpster)",        "unit": "EA", "low": 450, "high": 850},
    "count":             {"label": "Misc count item",                     "unit": "EA", "low": 85,  "high": 250},
    "mezzanine_demo":    {"label": "Mezzanine / platform + stair removal", "unit": "SF", "low": 3,  "high": 8},
    "casework_demo":     {"label": "Casework / counters / cabinets",      "unit": "LF", "low": 8,   "high": 22},
    "slab_cut_patch":    {"label": "Slab saw-cut, remove & patch prep",   "unit": "SF", "low": 4,   "high": 12},
    "door_window_infill":{"label": "Door / window remove & infill",       "unit": "EA", "low": 150, "high": 600},
    "water_heater_demo": {"label": "Water heater remove + cut/cap",       "unit": "EA", "low": 150, "high": 450},
}

# Defaults mirror DEMEX.html TakeoffWorkspace.defaultSvc() (line 4861).
DEFAULT_SVC = {"area": "demo_floor", "length": "demo_walls", "count": "doors"}
UNIT_FOR_TYPE = {"area": "SF", "length": "LF", "count": "EA"}

# Multi-scale steps for template matching (covers minor zoom/print differences).
MATCH_SCALES = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15)
# Longest display edge for the interactive window (drawings are huge).
DISPLAY_MAX_EDGE = 1400


# --------------------------------------------------------------------------- #
# Dependency guards                                                            #
# --------------------------------------------------------------------------- #
def _require(*mods: tuple[str, object]) -> None:
    """Raise a friendly error if a needed optional dependency is missing."""
    missing = [name for name, mod in mods if mod is None]
    if missing:
        raise SystemExit(
            f"Missing required package(s): {', '.join(missing)}.\n"
            f"Install everything with:  pip install -r requirements.txt"
        )


# --------------------------------------------------------------------------- #
# PDF rendering & vector geometry                                              #
# --------------------------------------------------------------------------- #
def page_count(pdf_path: Path) -> int:
    _require(("pypdfium2", pdfium))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()


def render_page(pdf_path: Path, page_index: int, dpi: int):
    """Render one PDF page (0-based) to an OpenCV BGR image at the given DPI."""
    _require(("pypdfium2", pdfium), ("opencv-python", cv2), ("numpy", np))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        bitmap = page.render(scale=dpi / 72.0)
        pil = bitmap.to_pil().convert("RGB")
        page.close()
        rgb = np.asarray(pil)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    finally:
        doc.close()


def vector_geometry(pdf_path: Path, page_index: int) -> dict:
    """Extract the PDF's own vector primitives for a page (in PDF points).

    Returns {"lines": [(x0,y0,x1,y1), ...], "rects": [(x0,y0,x1,y1), ...]}.
    These are EXACT (no pixel approximation). pdfplumber's coordinate origin is
    the top-left of the page; 1 point = 1/72 inch. Empty if the PDF is a scanned
    raster with no vectors, in which case we fall back to OpenCV.
    """
    if pdfplumber is None:
        return {"lines": [], "rects": []}
    lines, rects = [], []
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[page_index]
        for ln in page.lines:
            lines.append((ln["x0"], ln["top"], ln["x1"], ln["bottom"]))
        for rc in page.rects:
            rects.append((rc["x0"], rc["top"], rc["x1"], rc["bottom"]))
    return {"lines": lines, "rects": rects}


# --------------------------------------------------------------------------- #
# Geometry: pixels -> feet (mirrors DEMEX.html lines 4800-4823)               #
# --------------------------------------------------------------------------- #
def length_feet(pts_px: list[tuple[float, float]], px_per_ft: float) -> float:
    if px_per_ft <= 0 or len(pts_px) < 2:
        return 0.0
    total = 0.0
    for k in range(1, len(pts_px)):
        ax, ay = pts_px[k - 1]
        bx, by = pts_px[k]
        total += math.hypot((bx - ax) / px_per_ft, (by - ay) / px_per_ft)
    return total


def area_feet(pts_px: list[tuple[float, float]], px_per_ft: float) -> float:
    """Shoelace area in square feet (polygon auto-closed)."""
    if px_per_ft <= 0 or len(pts_px) < 3:
        return 0.0
    f = [(x / px_per_ft, y / px_per_ft) for x, y in pts_px]
    a = 0.0
    n = len(f)
    for k in range(n):
        j = (k + 1) % n
        a += f[k][0] * f[j][1] - f[j][0] * f[k][1]
    return abs(a) / 2.0


def points_per_foot(px_per_ft: float, dpi: int) -> float:
    """Convert a raster pixels-per-foot ratio into PDF points-per-foot, so the
    vector geometry (in points) can be measured with the same calibration."""
    return px_per_ft * 72.0 / float(dpi)


# --------------------------------------------------------------------------- #
# Scale calibration + per-sheet sidecar persistence                           #
# --------------------------------------------------------------------------- #
def sidecar_path(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.name + ".scale.json")


def load_scales(pdf_path: Path) -> dict:
    p = sidecar_path(pdf_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not read scale sidecar %s; ignoring.", p)
        return {}


def save_scale(pdf_path: Path, page_index: int, px_per_ft: float, dpi: int) -> None:
    p = sidecar_path(pdf_path)
    data = load_scales(pdf_path)
    data[str(page_index)] = {
        "px_per_ft": px_per_ft,
        "dpi": dpi,
        "saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("Saved scale for page %d -> %.4f px/ft (dpi %d) in %s",
             page_index + 1, px_per_ft, dpi, p.name)


def get_scale(pdf_path: Path, page_index: int, dpi: int) -> Optional[float]:
    """Return px_per_ft for a page, rescaled if the saved DPI differs."""
    entry = load_scales(pdf_path).get(str(page_index))
    if not entry:
        return None
    saved_dpi = entry.get("dpi", dpi)
    px = float(entry["px_per_ft"])
    if saved_dpi != dpi:  # ratio scales linearly with render DPI
        px = px * dpi / float(saved_dpi)
    return px


# --------------------------------------------------------------------------- #
# Interactive canvas (OpenCV HighGUI). Coordinates are tracked in ORIGINAL    #
# image pixels even though the canvas is shrunk to fit the screen.            #
# --------------------------------------------------------------------------- #
def _fit_factor(img) -> float:
    h, w = img.shape[:2]
    longest = max(h, w)
    return min(1.0, DISPLAY_MAX_EDGE / float(longest))


def collect_points(img, mode: str, title: str) -> list[tuple[float, float]]:
    """Let the user click points on the drawing.

    mode "line"   -> exactly 2 points (calibration / single segment).
    mode "length" -> a polyline of >=2 points.
    mode "area"   -> a polygon of >=3 points (auto-closed).

    Keys:  ENTER/SPACE = finish,  u = undo last,  c = clear,  ESC = cancel.
    Returns points in ORIGINAL pixel coordinates (empty list if cancelled).
    """
    _require(("opencv-python", cv2), ("numpy", np))
    factor = _fit_factor(img)
    disp0 = cv2.resize(img, None, fx=factor, fy=factor,
                       interpolation=cv2.INTER_AREA) if factor < 1.0 else img.copy()
    disp_pts: list[tuple[int, int]] = []  # in display space
    state = {"hover": None}

    def redraw():
        canvas = disp0.copy()
        for i, (x, y) in enumerate(disp_pts):
            cv2.circle(canvas, (x, y), 4, (0, 0, 255), -1)
            if i > 0:
                cv2.line(canvas, disp_pts[i - 1], (x, y), (0, 200, 255), 2)
        if mode == "area" and len(disp_pts) >= 3:
            cv2.line(canvas, disp_pts[-1], disp_pts[0], (0, 200, 255), 1)
        if state["hover"] and disp_pts:
            cv2.line(canvas, disp_pts[-1], state["hover"], (120, 120, 120), 1)
        hint = {
            "line": "Click 2 points on a known dimension. ENTER=done u=undo ESC=cancel",
            "length": "Click along the run. ENTER=done u=undo c=clear ESC=cancel",
            "area": "Click room corners. ENTER=close+done u=undo c=clear ESC=cancel",
        }[mode]
        cv2.putText(canvas, hint, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (40, 40, 40), 3, cv2.LINE_AA)
        cv2.putText(canvas, hint, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(title, canvas)

    def on_mouse(event, x, y, flags, _param):
        if event == cv2.EVENT_MOUSEMOVE:
            state["hover"] = (x, y)
            redraw()
        elif event == cv2.EVENT_LBUTTONDOWN:
            if mode == "line" and len(disp_pts) >= 2:
                return
            disp_pts.append((x, y))
            redraw()

    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(title, on_mouse)
    redraw()
    cancelled = False
    while True:
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 32):           # ENTER / SPACE
            break
        if key == ord("u") and disp_pts:
            disp_pts.pop(); redraw()
        elif key == ord("c"):
            disp_pts.clear(); redraw()
        elif key == 27:               # ESC
            cancelled = True; break
        if mode == "line" and len(disp_pts) == 2 and key in (13, 32):
            break
    cv2.destroyWindow(title)
    if cancelled:
        return []
    # back to original-image coordinates
    return [(x / factor, y / factor) for (x, y) in disp_pts]


def calibrate_interactive(img, known_feet: Optional[float]) -> Optional[float]:
    """Draw a line over a known dimension; return px_per_ft (or None)."""
    pts = collect_points(img, "line", "DEMEX calibrate -- draw a known dimension")
    if len(pts) < 2:
        log.warning("Calibration cancelled (need 2 points).")
        return None
    span_px = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
    if known_feet is None:
        try:
            known_feet = float(input("Real length of that line, in feet (e.g. 10): ").strip())
        except (ValueError, EOFError):
            log.warning("Invalid feet value; calibration aborted.")
            return None
    if known_feet <= 0 or span_px <= 0:
        log.warning("Non-positive length; calibration aborted.")
        return None
    px_per_ft = span_px / known_feet
    log.info("Calibrated: %.1f px over %.2f ft = %.4f px/ft", span_px, known_feet, px_per_ft)
    return px_per_ft


# --------------------------------------------------------------------------- #
# Auto room-contour detection (raster fallback; vectors preferred)            #
# --------------------------------------------------------------------------- #
def auto_contours(img, px_per_ft: float, min_sf: float, max_sf: float) -> list[dict]:
    """Detect closed boundary contours and return their areas in SF.

    Heuristic: binarize, close small gaps, find external+inner contours, keep
    those whose real area falls in [min_sf, max_sf]. Approximate -> needs_review.
    """
    _require(("opencv-python", cv2), ("numpy", np))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Lines are dark on light paper -> invert so walls become foreground.
    _th, binary = cv2.threshold(gray, 0, 255,
                                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = np.ones((5, 5), np.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _hier = cv2.findContours(closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    px2_per_ft2 = px_per_ft * px_per_ft
    out: list[dict] = []
    for c in contours:
        area_px = cv2.contourArea(c)
        if area_px <= 0:
            continue
        sf = area_px / px2_per_ft2
        if sf < min_sf or sf > max_sf:
            continue
        x, y, w, h = cv2.boundingRect(c)
        out.append({"sf": sf, "bbox": (int(x), int(y), int(w), int(h))})
    out.sort(key=lambda d: d["sf"], reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Auto-count: template matching across pages with non-max suppression         #
# --------------------------------------------------------------------------- #
def select_template(img):
    """Drag a box around one symbol; returns the cropped BGR template (or None)."""
    _require(("opencv-python", cv2), ("numpy", np))
    factor = _fit_factor(img)
    disp = cv2.resize(img, None, fx=factor, fy=factor,
                      interpolation=cv2.INTER_AREA) if factor < 1.0 else img.copy()
    title = "DEMEX auto-count -- drag a box around ONE symbol, then ENTER"
    roi = cv2.selectROI(title, disp, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    x, y, w, h = roi
    if w == 0 or h == 0:
        return None
    x0 = int(x / factor); y0 = int(y / factor)
    x1 = int((x + w) / factor); y1 = int((y + h) / factor)
    return img[y0:y1, x0:x1].copy()


def _nms(boxes: list[tuple[int, int, int, int]], scores: list[float],
         iou_thresh: float = 0.3) -> list[int]:
    """Greedy non-max suppression. boxes are (x, y, w, h). Returns kept indices."""
    if not boxes:
        return []
    b = np.array(boxes, dtype=float)
    x1 = b[:, 0]; y1 = b[:, 1]
    x2 = b[:, 0] + b[:, 2]; y2 = b[:, 1] + b[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = np.argsort(scores)[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thresh]
    return keep


def count_in_image(img, template, threshold: float) -> list[tuple[int, int, int, int]]:
    """Return de-duplicated match boxes (x, y, w, h) for one page."""
    _require(("opencv-python", cv2), ("numpy", np))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    boxes: list[tuple[int, int, int, int]] = []
    scores: list[float] = []
    H, W = gray.shape[:2]
    for s in MATCH_SCALES:
        th = max(1, int(tmpl_gray.shape[0] * s))
        tw = max(1, int(tmpl_gray.shape[1] * s))
        if th >= H or tw >= W:
            continue
        resized = cv2.resize(tmpl_gray, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        for (x, y) in zip(xs.tolist(), ys.tolist()):
            boxes.append((int(x), int(y), tw, th))
            scores.append(float(res[y, x]))
    keep = _nms(boxes, scores, iou_thresh=0.3)
    return [boxes[i] for i in keep]


def count_across_pages(pdf_path: Path, template, dpi: int, threshold: float,
                       overlay_path: Optional[Path]) -> int:
    """Match the template on every page; optionally save an overlay of page 1."""
    total = 0
    n = page_count(pdf_path)
    first_overlay = None
    for pi in range(n):
        img = render_page(pdf_path, pi, dpi)
        hits = count_in_image(img, template, threshold)
        total += len(hits)
        log.info("  page %d/%d: %d hit(s)", pi + 1, n, len(hits))
        if first_overlay is None and overlay_path is not None:
            for (x, y, w, h) in hits:
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 3)
            first_overlay = img
    if first_overlay is not None and overlay_path is not None:
        cv2.imwrite(str(overlay_path), first_overlay)
        log.info("Saved verification overlay -> %s", overlay_path.name)
    return total


# --------------------------------------------------------------------------- #
# Measurement -> v4 line-data conversion                                       #
# --------------------------------------------------------------------------- #
def make_measurement(mtype: str, svc: str, qty: float, page_index: int,
                     scope_type: str, method: str, location: str = "") -> dict:
    svc = svc if svc in SVC_CATALOG else DEFAULT_SVC[mtype]
    cat = SVC_CATALOG[svc]
    return {
        "type": mtype,
        "svc": svc,
        "label": cat["label"],
        "unit": UNIT_FOR_TYPE[mtype],
        "qty": round(float(qty), 2),
        "rate": round((cat["low"] + cat["high"]) / 2.0, 4),
        "page": page_index + 1,
        "scope_type": scope_type,
        "method": method,
        "location": location,
    }


def measurements_to_line_data(ms: list[dict]) -> dict:
    """Build the {estimated_quantities, unit_rates, bulk_pricing_thresholds}
    payload, shaped exactly as DEMEX.html expects (mapEQ / unit_rates)."""
    est_qty, rates = [], []
    for m in ms:
        note = f"takeoff {m['method']} p{m['page']} (review)"
        est_qty.append({
            "item": m["label"],
            "svc": m["svc"],          # required for DEMEX to price the line
            "unit": m["unit"],
            "qty": m["qty"],
            "location": m["location"],
            "notes": note,
            "scope_type": m["scope_type"],
        })
        rates.append({
            "svc": m["svc"],
            "label": m["label"],
            "qty": m["qty"],
            "unit": m["unit"],
            "rate": m["rate"],        # rate-card midpoint; estimator can override
            "scope_type": m["scope_type"],
        })
    return {
        "estimated_quantities": est_qty,
        "unit_rates": rates,
        "bulk_pricing_thresholds": [],
    }


# --------------------------------------------------------------------------- #
# DB: attach to an existing project (merge) or create a new one               #
# --------------------------------------------------------------------------- #
def resolve_project(conn: sqlite3.Connection, name: str, scope_type: str) -> dict:
    """Find the project row by name; if absent, synthesize a new takeoff record.

    Returns the `row` dict upsert_project() needs PLUS the project's CURRENT
    estimated_quantities / unit_rates so the caller can MERGE (upsert overwrites
    those JSON columns, so we must combine existing + new ourselves)."""
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if existing:
        log.info("Attaching to existing project '%s' (%s).",
                 existing["name"], existing["project_id"])
        return {
            "row": {
                "project_id": existing["project_id"],
                "name": existing["name"],
                "client_name": existing["client_name"],
                "address": existing["address"],
                "scope_type": existing["scope_type"] or scope_type,
                "due_date": existing["due_date"],
                "scope_summary": existing["scope_summary"] or "Takeoff",
                "status": existing["status"] or "estimating",
                "win_loss_status": existing["win_loss_status"] or "pending",
                "source_message_id": existing["source_message_id"],
            },
            "estimated_quantities": json.loads(existing["estimated_quantities"] or "[]"),
            "unit_rates": json.loads(existing["unit_rates"] or "[]"),
        }
    pid = project_id_for("", name)
    log.info("No existing project named '%s'; creating %s.", name, pid)
    return {
        "row": {
            "project_id": pid,
            "name": name,
            "client_name": "Unknown Client",
            "address": "",
            "scope_type": scope_type,
            "due_date": None,
            "scope_summary": "Takeoff",
            "status": "estimating",
            "win_loss_status": "pending",
            "source_message_id": "takeoff:" + slugify(name),
        },
        "estimated_quantities": [],
        "unit_rates": [],
    }


def persist(conn: sqlite3.Connection, name: str, measurements: list[dict],
            scope_type: str, source_pdf: str) -> str:
    """Merge measurements into the project and write to demex_records.db."""
    if not measurements:
        log.warning("No measurements to persist.")
        return ""
    proj = resolve_project(conn, name, scope_type)
    new = measurements_to_line_data(measurements)
    line_data = {
        "estimated_quantities": proj["estimated_quantities"] + new["estimated_quantities"],
        "unit_rates": proj["unit_rates"] + new["unit_rates"],
        "bulk_pricing_thresholds": [],
    }
    upsert_project(conn, proj["row"], line_data)
    # Also denormalize into line_items for SQL querying (our own minimal insert
    # so we don't depend on the email parser's exact dict-key contract).
    pid = proj["row"]["project_id"]
    for m in measurements:
        conn.execute(
            """INSERT INTO line_items
               (project_id, kind, description, scope_type, qty, unit,
                unit_price, source_pdf, needs_review)
               VALUES (?,?,?,?,?,?,?,?,1)""",
            (pid, "quantity", m["label"], m["scope_type"], m["qty"], m["unit"],
             m["rate"], source_pdf),
        )
    conn.commit()
    log.info("Persisted %d measurement(s) into project %s.", len(measurements), pid)
    return pid


# --------------------------------------------------------------------------- #
# Mode helpers                                                                 #
# --------------------------------------------------------------------------- #
def ensure_scale(pdf_path: Path, page_index: int, dpi: int,
                 args: argparse.Namespace, img=None) -> float:
    """Return px_per_ft for a page: from sidecar, else calibrate (if window)."""
    px = get_scale(pdf_path, page_index, dpi)
    if px:
        log.info("Using saved scale for page %d: %.4f px/ft.", page_index + 1, px)
        return px
    if args.no_window:
        raise SystemExit(
            f"No saved scale for page {page_index + 1} and --no-window set. "
            f"Run '--mode calibrate --page {page_index + 1}' first."
        )
    log.info("No saved scale for page %d; calibrate now.", page_index + 1)
    if img is None:
        img = render_page(pdf_path, page_index, dpi)
    px = calibrate_interactive(img, args.known_feet)
    if not px:
        raise SystemExit("Calibration required to measure; aborting.")
    save_scale(pdf_path, page_index, px, dpi)
    return px


def resolve_pages(pdf_path: Path, page_arg: Optional[int], all_default: bool) -> list[int]:
    """Translate the 1-based --page arg into 0-based indices."""
    n = page_count(pdf_path)
    if page_arg:
        if page_arg < 1 or page_arg > n:
            raise SystemExit(f"--page {page_arg} out of range (PDF has {n} page(s)).")
        return [page_arg - 1]
    return list(range(n)) if all_default else [0]


# --------------------------------------------------------------------------- #
# Core dispatch                                                                #
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    svc = args.svc
    if svc and svc not in SVC_CATALOG:
        raise SystemExit(
            f"Unknown --svc '{svc}'. Valid ids: {', '.join(sorted(SVC_CATALOG))}"
        )

    # --- calibrate: no DB writes ---
    if args.mode == "calibrate":
        pages = resolve_pages(pdf_path, args.page, all_default=False)
        for pi in pages:
            img = render_page(pdf_path, pi, args.dpi)
            px = calibrate_interactive(img, args.known_feet)
            if px:
                save_scale(pdf_path, pi, px, args.dpi)
        return 0

    # All measuring modes write to the DB and need a project.
    if not args.project:
        raise SystemExit("--project \"Name\" is required for measuring modes.")
    conn = db_init(args.db)
    measurements: list[dict] = []

    if args.mode == "area":
        pi = resolve_pages(pdf_path, args.page, all_default=False)[0]
        img = render_page(pdf_path, pi, args.dpi)
        px = ensure_scale(pdf_path, pi, args.dpi, args, img)
        pts = collect_points(img, "area", "DEMEX area takeoff")
        sf = area_feet(pts, px)
        if sf > 0:
            log.info("Area = %.1f SF (%.2f SY)", sf, sf / 9.0)
            measurements.append(make_measurement(
                "area", svc or DEFAULT_SVC["area"], sf, pi, args.scope_type,
                "interactive", args.location))

    elif args.mode == "linear":
        pi = resolve_pages(pdf_path, args.page, all_default=False)[0]
        img = render_page(pdf_path, pi, args.dpi)
        px = ensure_scale(pdf_path, pi, args.dpi, args, img)
        pts = collect_points(img, "length", "DEMEX linear takeoff")
        lf = length_feet(pts, px)
        if lf > 0:
            log.info("Length = %.1f LF", lf)
            measurements.append(make_measurement(
                "length", svc or DEFAULT_SVC["length"], lf, pi, args.scope_type,
                "interactive", args.location))

    elif args.mode == "auto-area":
        pi = resolve_pages(pdf_path, args.page, all_default=False)[0]
        img = render_page(pdf_path, pi, args.dpi)
        px = ensure_scale(pdf_path, pi, args.dpi, args, img)
        # Prefer exact PDF vector rectangles when present.
        used_vectors = False
        geom = vector_geometry(pdf_path, pi)
        ppf = points_per_foot(px, args.dpi)
        if geom["rects"] and ppf > 0:
            for (x0, y0, x1, y1) in geom["rects"]:
                sf = (abs(x1 - x0) / ppf) * (abs(y1 - y0) / ppf)
                if args.min_sf <= sf <= args.max_sf:
                    used_vectors = True
                    measurements.append(make_measurement(
                        "area", svc or DEFAULT_SVC["area"], sf, pi,
                        args.scope_type, "vector", args.location))
        if not used_vectors:
            log.info("No usable vector rects; using OpenCV contour detection.")
            for c in auto_contours(img, px, args.min_sf, args.max_sf):
                measurements.append(make_measurement(
                    "area", svc or DEFAULT_SVC["area"], c["sf"], pi,
                    args.scope_type, "auto-contour", args.location))
        log.info("auto-area found %d region(s).", len(measurements))

    elif args.mode == "count":
        sample_pi = resolve_pages(pdf_path, args.page, all_default=False)[0]
        if args.no_window:
            raise SystemExit("--mode count needs the window to pick a symbol "
                             "(remove --no-window).")
        sample_img = render_page(pdf_path, sample_pi, args.dpi)
        template = select_template(sample_img)
        if template is None:
            raise SystemExit("No symbol selected; nothing to count.")
        overlay = pdf_path.with_name(pdf_path.stem + "_count_overlay.png")
        total = count_across_pages(pdf_path, template, args.dpi,
                                   args.match_threshold, overlay)
        log.info("TOTAL COUNT = %d (threshold %.2f)", total, args.match_threshold)
        if total > 0:
            measurements.append(make_measurement(
                "count", svc or DEFAULT_SVC["count"], total, sample_pi,
                args.scope_type, "template-match", args.location))
    else:
        raise SystemExit(f"Unknown mode: {args.mode}")

    persist(conn, args.project, measurements, args.scope_type, pdf_path.name)
    conn.close()

    if args.export_json:
        export_demex_json(args.db, args.export_json)
        log.info("Exported DEMEX import file -> %s", args.export_json)
        log.info("In DEMEX.html: Import this file with MERGE to load the takeoff.")
    return 0


# --------------------------------------------------------------------------- #
# CLI (mirrors email_ingestor conventions)                                     #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="DEMEX blueprint CV takeoff engine -> demex_records.db.")
    p.add_argument("--pdf", required=True, help="Path to the blueprint PDF.")
    p.add_argument("--mode", required=True,
                   choices=["calibrate", "area", "linear", "count", "auto-area"],
                   help="Operation to perform.")
    p.add_argument("--project", help="Project name to attach results to "
                                     "(matched in demex_records.db; created if absent). "
                                     "Required for all modes except calibrate.")
    p.add_argument("--page", type=int, default=None,
                   help="1-based page number. Omit: measuring modes use page 1; "
                        "count scans ALL pages.")
    p.add_argument("--dpi", type=int, default=200,
                   help="Render resolution (default 200). Scale auto-adjusts if "
                        "the saved sidecar used a different DPI.")
    p.add_argument("--svc", default=None,
                   help="Rate-card service id to assign (e.g. demo_floor, "
                        "concrete_cut, plumb_fixture). Defaults: area->demo_floor, "
                        "linear->demo_walls, count->doors.")
    p.add_argument("--scope-type", dest="scope_type", default="demolition",
                   choices=["demolition", "containment", "both"],
                   help="Scope classification for the quantities (default demolition).")
    p.add_argument("--location", default="",
                   help="Optional location/room label stored on the quantity.")
    p.add_argument("--known-feet", dest="known_feet", type=float, default=None,
                   help="Known length (ft) of the calibration line; if omitted "
                        "you'll be prompted after drawing it.")
    p.add_argument("--match-threshold", dest="match_threshold", type=float,
                   default=0.8,
                   help="Template-match confidence 0-1 (default 0.8). Lower = "
                        "more (looser) hits.")
    p.add_argument("--min-sf", dest="min_sf", type=float, default=20.0,
                   help="auto-area: ignore regions smaller than this many SF.")
    p.add_argument("--max-sf", dest="max_sf", type=float, default=100000.0,
                   help="auto-area: ignore regions larger than this many SF.")
    p.add_argument("--db", default="demex_records.db",
                   help="SQLite database path (shared with email_ingestor).")
    p.add_argument("--export-json", metavar="PATH",
                   help="After persisting, write a DEMEX.html-importable JSON file.")
    p.add_argument("--no-window", action="store_true",
                   help="Headless: reuse the saved sidecar scale, no GUI. "
                        "(Not valid for calibrate/count, which need the window.)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
