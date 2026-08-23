# DEMEX Takeoff Markup Standard — v1.0

**Adopted 2026-08-11 · Disassemble Dynamics, Inc.**
**Reference sheet:** SCPS Lake Howell HS CEP Replacement, C100 Site Plan Rev.0 — Demolition, marked up in Bluebeam Revu.

---

## Why this exists

A marked-up plan has always done two jobs at once: it tells the crew what to
tear out, and it tells the estimator what to price. It now does a third — it is
the **data source**. `tools/takeoff_markup_extract.py` reads the measurement
annotations straight out of the saved PDF and turns them into priced quantities
with no re-keying.

That only works if the markup is legible to a machine, and a machine can read
exactly two things: **what colour the annotation is**, and **what kind of
measurement it is**. Everything else on the sheet — the leader text, the cloud,
the arrow pointing at the pad — is invisible to it.

So the rule is short:

> **Colour decides the scope. Measurement type decides the unit.
> If it isn't carried by colour and measurement type, the machine never sees it.**

A markup that drifts off the legend doesn't fail loudly. It quietly lands 1,675
SF of concrete in the wrong bucket, and the first anyone knows is the job that
lost money. That is the whole reason this is a written standard and not a habit.

---

## The legend

Machine-readable copy: `standards/takeoff/markup_legend.json`

| Colour | Hex | Area → SF | Length → LF | Count → EA |
|---|---|---|---|---|
| **Red** | `#FF0000` | Structure demo / limits of work | Fence, gate, guardrail, handrail | — |
| **Blue** | `#0000FF` | Concrete flatwork — pads, slabs on grade, sidewalk, apron | Concrete sawcutting | — |
| **Dark Green** | `#004000` | — | Utility line removal — elec, water, storm, sanitary, gas | — |
| **Yellow** | `#FFFF00` | — | — | Equipment / fixture removal — panels, transformers, generators, poles, inlets |
| **Orange** | `#FF8000` | Floor finish removal to slab | Wall base | — |
| **Magenta** | `#FF00FF` | Masonry / CMU wall | Partition wall demolition | — |
| **Cyan** | `#00FFFF` | Ceiling — ACT + grid, gypsum, soffit | — | — |
| **Purple** | `#800080` | **Abatement containment** — ACM, PCB, lead | Linear abatement — pipe insulation, mastic | — |
| **Dark Red** | `#800000` | Structural slab / foundation | Structural sawcut | — |
| **Gray** | `#808080` | **BY OTHERS** — measured, never priced | BY OTHERS | BY OTHERS |
| **Black** | `#000000` | Annotation only — never a quantity | " | " |

Three of those earn their own colour for a reason:

- **Purple is regulated material.** Abatement carries a different licence, a
  different crew and a different liability than demolition. If it shares a colour
  with demo it eventually gets priced at a demo rate, and that is the single
  most expensive mistake available on a bid sheet.
- **Dark red is structural concrete**, separated from blue flatwork because the
  production rate is not remotely comparable.
- **Gray is work that is not ours.** It gets measured anyway, so the exclusion is
  provable when the GC asks why it isn't in the number — and it is flagged
  `in_scope: false` so it can never reach the priced total.

---

## Measurement type decides the unit

Use the Bluebeam tool that produces the right annotation object. The tool
matters more than what the mark looks like.

| Use this Revu tool | Writes | Extracts as |
|---|---|---|
| **Measure → Area** | `/Polygon` with `/IT /PolygonDimension` | SF |
| **Measure → Polylength** | `/PolyLine` with `/IT /PolyLineDimension` | LF |
| **Measure → Perimeter** | `/PolyLine`, perimeter measurement | LF |
| **Highlight** (or Count stamp) | `/Highlight`, `/Ink`, `/Circle` | EA |

A shape drawn with the plain Rectangle or Polygon tool instead of the Measure
tool carries **no measurement** and is skipped entirely. On the reference sheet
299 `/Square` annotations were skipped for exactly this reason — they are the
engineer's detail boxes, not takeoff.

---

## Rules

1. **Calibrate the scale before the first mark.** Every measurement on the
   reference sheet carries the scale it was taken at. A recalibration partway
   through silently rescales everything after it.
2. **One colour, one scope, one sheet.** Do not reuse a legend colour for a
   different meaning because the sheet is crowded.
3. **Label every count.** A yellow highlight over the letters `EP` extracts with
   the text `EP`. A freehand yellow scribble extracts as an unlabeled count of 1
   and is flagged for review forever. Highlight the *tag*, don't circle the area.
4. **Mark by-others work gray. Do not skip it.** An unmarked exclusion is an
   argument later; a gray measured exclusion is an exhibit.
5. **Measure gross geometry only.** No waste factor, no overlap allowance, no
   thickness, no haul factor, no access difficulty. Those are pricing decisions
   and they belong to `pricing_engine.py`. Doubling them up in the markup is how
   a bid goes 30% high without anyone being able to say where.
6. **Save the marked file as `<sheet> - Marked Up.pdf`** next to the plan set, and
   run the extractor against that file — not a flattened print. Flattening
   destroys every annotation object and with it the entire takeoff.
7. **Anything off-legend surfaces as `UNMAPPED`.** The extractor never guesses a
   bucket. On the reference sheet the engineer's red digital signature seal
   extracted as one unmapped count — correct behaviour, and it gets discarded on
   review rather than silently counted as an equipment removal.

---

## The reference takeoff

**Sheet:** C100 Site Plan Rev.0 — Demolition Plan
**Project:** SCPS Lake Howell HS CEP Replacement, Seminole County Public Schools
**Engineer:** Matern Professional Engineering / Klima Weeks Civil Engineering (Jay A. Klima, P.E.)
**Issue date:** 02/18/2026 · **MPE Proj #** 2025-073
**Files:** `reference/SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp.pdf`
and `reference/SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp_takeoff.json`

45 measurements extracted from the sheet — 17 area, 17 length, 11 count:

| Scope | Qty | Unit | Annotations | svc |
|---|---:|---|---:|---|
| Concrete flatwork removal — pads, slabs, sidewalk | 1,675.71 | SF | 16 | `concrete_flat` |
| Structure demo / limits of work | 855.41 | SF | 1 | `structure_demo` |
| Utility line removal | 456.11 | LF | 13 | `utility_demo` |
| Fence, gate and enclosure removal | 154.90 | LF | 4 | `fence_demo` |
| Equipment / fixture removal | 10 | EA | 10 | `count` |
| *Off-legend (engineer's seal)* | *1* | *EA* | *1* | *discard* |

**What the sheet says that the markup does not.** The ±843 SF one-storey metal
building is hatched "TO BE REMOVED", but the plan note reads *"REMOVE CONC. PAD
(REMOVAL OF METAL BLDG. BY SCPS PRIOR TO START OF CONST.)"*. The building is
**by others** — only its pad is ours. Under this standard that hatch gets a gray
by-others polygon so the exclusion carries into the proposal automatically. It
was not marked on the reference file, which is precisely the gap rule 4 closes.

Of the ten yellow counts, six are labelled (3× EP electrical panel, 1× TF
transformer, 2× light pole) and four are freehand ink with no text — see rule 3.

---

## Running it

```bash
python tools/takeoff_markup_extract.py --pdf "C100 - Demolition - Marked Up.pdf"

# and to push straight into the database against a project
python tools/takeoff_markup_extract.py \
    --pdf "C100 - Demolition - Marked Up.pdf" \
    --project pj_itb_20260811_07 --db
```

Output is a `*_takeoff.json` beside the PDF containing every individual
measurement, a rollup by scope, and an `est_qty[]` block in the exact shape
`projects[].estQty` expects in a DEMEX import file.

---

## Rate card gaps this standard exposes

Four of the scope buckets have no rate on the DEMEX rate card yet, so they
extract as quantities that cannot price themselves:

| svc | Unit | Note |
|---|---|---|
| `structure_demo` | SF | Closest existing is `exterior_demo`, which prices wall and cladding — a different operation. |
| `fence_demo` | LF | Currently absorbed into lump site items. |
| `utility_demo` | LF | Production swings hard with depth and surface above; likely needs depth bands rather than one rate. |
| `abate_containment` / `abate_linear` | SF / LF | Must never inherit a demolition rate. |

Site and civil demolition is a real and growing share of the pipeline — the
reference sheet alone carries 456 LF of utility removal and 155 LF of fence with
nowhere to land. Adding these five rates is the highest-value next change to the
rate card.
