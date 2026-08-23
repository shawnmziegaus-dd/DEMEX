# Bluebeam Revu

**Key** `bluebeam` · **Category** takeoff_markup · **Status** `live` · **Direction** bidirectional · **Last verified** 2026-08-11

Where the takeoff actually happens. Shawn marks up the plan set in Revu; DEMEX reads the measurement annotations back out of the saved PDF and turns them into priced quantities. This is the single most important integration in the stack.

## Access

- **Auth method** — file-based (no API)
- **Notes** — Revu is a desktop application. Integration is by reading the annotation objects embedded in the saved PDF.
- **Protocol** — PDF annotation objects
- **Annotation Types** — /Polygon + /IT /PolygonDimension (area, sf), /PolyLine + /IT /PolyLineDimension (polylength, ft-in), /PolyLine perimeter measurement, /Highlight and /Ink (count / point items), /Square (sheet detail boxes - ignore)

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | area measurements, length measurements, perimeter measurements, count highlights, annotation colour, measurement scale, author |
| DEMEX writes | markup legend / colour standard applied by the estimator in Revu |

## Field mapping

| Source | DEMEX target |
|---|---|
| annotation colour | `estQty[].svc (scope bucket)` |
| /Contents 'NNN.NN sf' | `estQty[].qty with unit SF` |
| /Contents "NN'-N 1/4\"" | `estQty[].qty with unit LF` |
| highlight count | `estQty[].qty with unit EA` |

## Wiring

- **Code** — `tools/takeoff_markup_extract.py`, `takeoff_scalar.py`
- **Trigger** — manual: run tools/takeoff_markup_extract.py on the saved marked-up PDF

## Known limits

- Colour is the only scope signal in the file - if the estimator deviates from the legend the extraction is wrong. See standards/takeoff/DEMEX_Takeoff_Markup_Standard.md.
- Freehand /Ink highlights carry no text, so they extract as an unlabeled count and always need review.
- Extracted quantities are gross geometry. Waste, overlap, thickness and access factors are applied by the pricing engine, not the extractor.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

