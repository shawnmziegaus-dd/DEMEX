# Takeoff Accuracy Benchmark — auto-area vs. the estimator's markups

**Date:** 2026-08-25
**Candidate under test:** `takeoff_scalar.py --mode auto-area` (the "Run Visual Takeoff" path)
**Ground truth:** Shawn's Bluebeam markups on the two reference sheets under
`standards/takeoff/reference/` (the `*_MarkedUp.pdf` files and their
`*_takeoff.json` extracts)
**How to re-run:** `python scripts/benchmark_takeoff.py` (add `--out-json results.json`
for machine-readable output; `--rederive-scale` to rebuild the scale sidecars)

---

## 1. What this benchmark does

The estimator already measured these two sheets by hand in Bluebeam. Every
markup carries a real quantity (855.41 sf, 89'-9 3/4", etc.). That is the
answer key. The benchmark runs the app's automatic visual takeoff on the same
sheets, at the same scale, and scores it against the answer key:

- **Region count** — how many demo areas it found vs. how many the estimator marked
- **Total SF** — detected square footage vs. marked square footage (signed error)
- **Region matching** — each detection is paired with the closest unmatched
  truth area if it lands within ±25%; anything left over on either side is a
  miss or a false positive
- **Coverage** — how much of the marked scope auto mode can even *see*
  (it only produces areas; linear-feet and each-count markups are invisible to it)

The real `demex_records.db` is never touched — each run writes to a throwaway
copy in a temp directory.

## 2. Scale derivation (validated)

Auto mode needs a pixels-per-foot scale to run headless. The marked-up PDFs
contain the fair answer: every Bluebeam measurement stores both its geometry
(in PDF points) and its stated real-world value. Each one independently pins
down points-per-foot:

- length markup: `pts/ft = drawn path length ÷ stated feet`
- area markup: `pts/ft = √(drawn polygon area ÷ stated SF)`

Every annotation was cross-checked against the takeoff JSON (matched by page +
value) before being used. The median becomes the sheet scale:

| Sheet | Samples | Length median | Area median | Combined median | Min–max spread |
|---|---|---|---|---|---|
| SCPS Lake Howell C100 | 17 length + 17 area | 7.2009 pts/ft | 7.2000 pts/ft | **7.2001 pts/ft** | 7.1933–7.2048 (**0.16%**) |
| Harbor Freight D1.0 | 4 length + 14 area | 6.7498 pts/ft | 6.7500 pts/ft | **6.7500 pts/ft** | 6.7496–6.7501 (**0.01%**) |

Two independent geometric methods (path length and polygon area) agree to
about a tenth of a percent across 52 annotations — the scale is solid. As a
sanity check, both values are real drawing scales: 7.20 pts/ft is exactly
**1" = 10'-0"** (a standard civil scale) and 6.75 pts/ft is exactly
**3/32" = 1'-0"** (a standard architectural scale).

Written to the app's sidecar convention (`<pdf>.scale.json`, px/ft at 200 dpi):
SCPS **20.0003 px/ft**, Harbor Freight **18.7500 px/ft**. The sidecars sit next
to the PDFs, so the app itself now has calibrated scales for both reference sheets.

## 3. Results

### SCPS Lake Howell — C100 Site Demolition (civil sheet)

| Metric | Ground truth | Auto-area (before tuning) | Auto-area (after tuning) |
|---|---|---|---|
| Area regions | 17 | 1 | 1 |
| Total area SF | 2,531.12 | 72.0 (**−97.2%**) | 72.0 (**−97.2%**) |
| Matched regions (±25%) | — | 1 (72 → 60.75, 18.5% off) | 1 (same) |
| Missed truth regions | — | 16 | 16 |
| False positives | — | 0 | 0 |
| Matched SF as % of truth SF | — | 2.4% | 2.4% |
| Out of reach (not areas) | 17 lines / 611.0 LF + 11 counts / 11 EA | invisible to this mode | invisible to this mode |

The demolition areas on a civil sheet are hatched, irregular regions bounded by
many short line segments — they are not drawn as rectangles in the PDF, so the
vector-rectangle detector fundamentally cannot see them. Only one honest
rectangle ≥20 SF exists in the entire drawing (a 72 SF pad). No filter tuning
changes this; it is a ceiling of the approach, not a bug.

### Harbor Freight Jax Beach — D1.0 Demolition (interior sheet)

| Metric | Ground truth | Auto-area (before tuning) | Auto-area (after tuning) |
|---|---|---|---|
| Area regions | 14 | 4 | 1 |
| Total area SF | 16,856.17 | 5,071.66 (**−69.9%**) | 1,351.82 (**−92.0%**) |
| Matched regions (±25%) | — | 3 | 1 (1,351.82 → 1,357.10, **0.4% off**) |
| Missed truth regions | — | 11 | 13 |
| False positives | — | 1 (3,338 SF) | 0 |
| Matched SF as % of truth SF | — | 10.5% | 8.1% |
| Out of reach (not areas) | 4 lines / 144.4 LF + 15 counts / 15 EA | invisible to this mode | invisible to this mode |

**Read the before/after honestly.** The "before" numbers look better than they
are. Inspection showed that 3 of the 4 baseline detections were **text boxes,
not demolition areas**: the 3,338 SF false positive was the DEMOLITION GENERAL
NOTES frame (59% of its area covered in text), and the two smaller "matches"
(238 → 258 SF and 144 → 161 SF) were a telco-demarc note box (49% text) and a
fire-riser note box (38% text) that happened to be about the same size as real
scope areas. Those were coincidences that would have put note boxes on an
estimate as priced square footage. Only the 1,352 SF slab rectangle (2.8% text)
was a genuine demo region — and it is accurate to 0.4%.

## 4. What was changed (tuning)

One surgical, principled filter was added to `takeoff_scalar.py`
(`filter_auto_rects` + `vector_geometry`):

- **Text-coverage rejection** (`MAX_RECT_TEXT_COVERAGE = 0.20`):
  `vector_geometry` now also extracts text word bounding boxes, and
  `filter_auto_rects` drops any candidate rectangle whose interior is more
  than 20% covered by text glyphs. Rationale: general-notes, legend, and
  schedule frames are text-dominated on *every* construction drawing (the ones
  on these sheets ran 38–59%), while real rooms and slabs carry only a name tag
  and a few dimensions (single-digit coverage). This is a property of drawings
  in general, not of these two sheets, so it is not overfitting — but it was
  only verified on these two sheets, so watch it as more references are added.

Effect: Harbor Freight false positives 1 → 0, and the two coincidental
note-box "matches" are gone; every surviving detection is a genuine demolition
region. Total detected SF drops (−69.9% → −92.0% signed error) because the
removed detections were wrong to begin with — precision went up, recall was
already near zero. SCPS is unchanged (its one detection has no text in it).

No other tuning was justified. Loosening the page-margin band or the min/max SF
bounds was tested and adds only noise: none of the rectangles those filters
drop correspond to any truth area on either sheet.

Also worth recording: the OpenCV **raster contour fallback cannot be fairly
benchmarked on these PDFs** — rendering a marked-up sheet paints the
estimator's own colored polygons onto the page, and the contour detector then
happily traces the answer key (it "found" 3,256.2 SF where the markups total
3,256.17 SF). On clean sheets it over-detects wildly (140+ regions, 135K–232K
SF per sheet, every viewport, frame, and hatch cell). It was left out of the
scored run; benchmarking it would need the clean (unmarked) sheets.

## 5. Coverage — what auto mode can even see

Counting the estimator's marked scope lines:

| Sheet | Marked lines | Area lines (reachable in principle) | LF/EA lines (structurally invisible) | Truth SF actually matched |
|---|---|---|---|---|
| SCPS C100 | 45 | 17 (38%) | 28 (62%) — 611 LF + 11 EA | **2.4%** |
| Harbor Freight D1.0 | 33 | 14 (42%) | 19 (58%) — 144 LF + 15 EA | **8.1%** |

So even in the best case, auto-area addresses well under half of the marked
scope *by line count* — utility runs, fence removal, sawcutting, and every
counted fixture (poles, inlets, hydrants, doors) are simply not the kind of
thing an area detector produces. Of the scope it can address, it currently
recovers 2–8% of the square footage.

## 6. Honest conclusions

1. **Auto-area is not a takeoff tool today; it is a rectangle finder.** When a
   demo region happens to be a clean vector rectangle (the Harbor Freight
   vestibule slab), it is *excellent* — 0.4% off. Everything else — hatched
   civil areas, the 11,885 SF strip-out footprint bounded by wall lines, tile
   areas outlined with polylines — is invisible to it.
2. **The scale machinery is not the problem.** Scale derivation from markups is
   accurate to ~0.1%, and where a region is detected, the math on it is nearly
   exact. The gap is *detection*, not measurement.
3. **The `needs_review=1` flag on every auto result is doing real work.** Before
   tuning, this benchmark would have handed an estimator three note boxes
   priced as demo square footage. Keep every auto quantity behind review.
4. **What would actually move accuracy**, in rough order of value:
   - **Vision-based AI takeoff**: a multimodal model reading the rendered sheet
     plus the demo legend/keynotes can identify hatched demolition regions,
     dashed "remove" linework, and scope notes the way an estimator does. This
     is the only path to the 92–97% of area SF currently missed, and to reading
     "EXISTING SLAB TO BE REMOVED" instead of guessing from geometry.
   - **Symbol recognition for counts**: 26 of the 78 marked lines across these
     sheets are EA counts (poles, inlets, doors, fixtures). The existing
     template-match `--mode count` needs a human to pick each template; a
     symbol detector trained on legend symbols would automate the pick.
   - **Closed-region tracing from wall/boundary lines**: the strip-out
     footprint is fully bounded by vector lines already in the PDF (10,775 line
     segments on that sheet). Polygonizing the line network into closed rooms —
     rather than only trusting literal rectangles — is the biggest classical-CV
     win available and would have reached the 11,885 SF footprint.
   - **More reference sheets**: two sheets prove the harness; ten sheets across
     civil/interior/structural would make the filters tunable without overfit.
   Auto-count and the LF modes should be benchmarked next with the same
   harness pattern (the marked-up PDFs already contain the count and length
   truth).

The benchmark is repeatable: every number above regenerates from
`python scripts/benchmark_takeoff.py`.
