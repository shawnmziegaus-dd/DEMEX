# DEMEX Takeoff Scalar — Blueprint CV Engine

`takeoff_scalar.py` measures PDF construction drawings — square footage, linear
footage, and symbol counts — at true scale, and writes the results into the same
`demex_records.db` the email ingestor uses. From there they import into
**DEMEX.html** and populate the estimate.

A language model can't measure scale off a drawing; this tool can, using OpenCV
plus the PDF's own vector geometry. DEMEX.html already has a *manual* takeoff
workspace, so the unique value here is **automation** — counting one symbol
across every sheet, auto-tracing room contours, and unattended batch runs.

---

## The four features

| Mode | What it does | Output |
|---|---|---|
| `calibrate` | Draw a line over a known dimension, type its real length → locks a **pixels-per-foot** ratio for that sheet, saved to a sidecar so you never redo it. | `<pdf>.scale.json` |
| `area` | Click a room's corners → polygon **square footage** (and SY = SF ÷ 9). | quantity (SF) |
| `auto-area` | Auto-detect enclosed regions — exact PDF **vector rectangles** when present, else OpenCV contour detection. | quantities (SF) |
| `linear` | Trace a wall/pipe run → precise **linear footage**. | quantity (LF) |
| `count` | Drag a box around ONE symbol → template-match it across **all pages** → verified count + a `_count_overlay.png` showing every hit. | quantity (EA) |

### Window controls (interactive modes)
- **Left-click** — place a point / corner
- **ENTER** or **SPACE** — finish (area auto-closes the polygon)
- **u** — undo last point · **c** — clear all · **ESC** — cancel
- `count` uses a drag-box selector: drag, then press **ENTER**.

---

## Two honest caveats

- **Auto-results are a first pass, not a certified count.** Template matching is
  sensitive to symbol rotation and scale; we search several scales and
  de-duplicate overlapping hits, but every auto-count and auto-contour area is
  written with `needs_review = 1`. Always eyeball the `_count_overlay.png` and
  confirm before bidding. Tune with `--match-threshold` (lower = more, looser
  hits) and `--min-sf` / `--max-sf` for `auto-area`.
- **Dollars require a service id.** A quantity only prices out in DEMEX if its
  `svc` matches the rate card. Defaults follow the app (`area→demo_floor`,
  `linear→demo_walls`, `count→doors`); override with `--svc` (e.g.
  `concrete_cut`, `plumb_fixture`, `light_fixture`, `masonry`). The full id list
  is in `SVC_CATALOG` at the top of the script.

---

## Install

```bash
pip install -r requirements.txt
```

This pulls `opencv-python`, `numpy`, `pypdfium2` (raster renderer — no
Ghostscript/poppler needed) and `pdfplumber` (exact vector geometry).

> **Not yet runnable on this machine:** Python isn't installed here, and OpenCV
> is a heavy dependency. Install Python 3.10+ and the requirements first. The
> interactive window also needs a desktop session (fine on this Windows PC).

---

## Typical workflow

```bash
# 1. Calibrate the sheet once (draw over a known 10'-0" dimension).
python takeoff_scalar.py --pdf "plan.pdf" --mode calibrate --page 1

# 2. Area takeoff of a flooring/abatement zone.
python takeoff_scalar.py --pdf "plan.pdf" --project "Sunglass Hut Naples" --mode area

# 3. Linear takeoff of sawcut concrete.
python takeoff_scalar.py --pdf "plan.pdf" --project "Sunglass Hut Naples" \
    --mode linear --svc concrete_cut

# 4. Count plumbing fixtures across every page, then export for the app.
python takeoff_scalar.py --pdf "plan.pdf" --project "Sunglass Hut Naples" \
    --mode count --svc plumb_fixture --export-json takeoff_import.json
```

Then in **DEMEX.html** → Import → choose `takeoff_import.json` with **Merge**
(projects merge by name). The quantities appear on the project; open the
Estimator to confirm rates and roll them into the bid total.

---

## How it connects to DEMEX (integration contract)

- Writes the same `demex_records.db` v4 schema as `email_ingestor.py`, reusing
  its `db_init`, `project_id_for`, `upsert_project`, and `export_demex_json`.
- **Attach by name:** `--project "Name"` finds the existing bid (e.g. one the
  email ingestor created) and **merges** the new quantities into its
  `estimated_quantities`; it only creates a new record if no name matches.
- Each quantity is stored as `{item, svc, unit, qty, location, notes,
  scope_type}` (the shape DEMEX's `mapEQ` expects) plus a matching `unit_rates`
  entry carrying the rate-card midpoint, so the estimate can compute dollars.
- The measurement math mirrors the in-app takeoff: pixels ÷ pixels-per-foot,
  `Σ hypot` for length, shoelace for area.

---

## Command-line options

| Flag | Default | Purpose |
|---|---|---|
| `--pdf PATH` | *(required)* | Blueprint PDF to measure. |
| `--mode` | *(required)* | `calibrate` · `area` · `auto-area` · `linear` · `count`. |
| `--project "Name"` | — | Project to attach to (required except for `calibrate`). |
| `--page N` | page 1 (`count`: all) | 1-based page number. |
| `--dpi N` | `200` | Render resolution; scale auto-adjusts if the sidecar used another DPI. |
| `--svc ID` | per-mode default | Rate-card service id for the quantity. |
| `--scope-type` | `demolition` | `demolition` · `containment` · `both`. |
| `--location TEXT` | — | Optional room/area label stored on the quantity. |
| `--known-feet N` | prompt | Real length of the calibration line (skips the prompt). |
| `--match-threshold N` | `0.8` | `count` confidence 0–1 (lower = more hits). |
| `--min-sf` / `--max-sf` | `20` / `100000` | `auto-area` region size filter (SF). |
| `--db PATH` | `demex_records.db` | Shared SQLite database. |
| `--export-json PATH` | — | Write a DEMEX.html-importable JSON after persisting. |
| `--no-window` | off | Headless: reuse the saved sidecar scale, no GUI (not for `calibrate`/`count`). |
| `-v`, `--verbose` | off | Debug logging. |

---

## First-run checklist

- [ ] Install Python 3.10+ and `pip install -r requirements.txt`
- [ ] `--mode calibrate` each sheet you'll measure (draw a known dimension)
- [ ] Run a measuring mode with `--project` matching the bid name in the DB
- [ ] For `count`, open `<pdf>_count_overlay.png` and confirm the boxes
- [ ] `--export-json`, then Import (Merge) into DEMEX.html
- [ ] Open the Estimator and confirm rates → bid total
