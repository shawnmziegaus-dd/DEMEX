# Takeoff standards

How a Disassemble Dynamics bid takeoff is marked up, and how DEMEX reads it back.

| File | What it is |
|---|---|
| `DEMEX_Takeoff_Markup_Standard.md` | The standard. Read this first. |
| `markup_legend.json` | The same legend, machine-readable. Consumed by `tools/takeoff_markup_extract.py`. |
| `reference/` | The gold-standard marked-up sheet and its extracted takeoff. |

## The reference sheet

`reference/SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp.pdf` — SCPS Lake
Howell HS CEP Replacement, C100 Demolition Plan, marked up in Bluebeam Revu.
This is what a correct site/civil takeoff looks like: 45 measurements, colour
carrying scope, measurement type carrying unit, 1,675 SF of concrete flatwork,
456 LF of utility removal, 155 LF of fence and 10 counted equipment items.

`reference/..._takeoff.json` is what the extractor pulled out of it — every
individual measurement, a rollup by scope, and an `est_qty[]` block ready to
drop into `projects[].estQty`.

## In the database

`_migrate_schema_v5_standards.py` loads all of it into `demex_records.db`:

```
takeoff_legend         21 rows   the colour standard, one row per colour x measurement kind
reference_takeoffs      1 row    the exemplar sheet, with rollup and est_qty
takeoff_measurements   45 rows   every annotation on the reference sheet
integrations           13 rows   mirror of integrations/registry.json
```

Re-run it after changing the legend or adding a reference. It is idempotent.

## Adding another reference

Not every discipline is covered yet — this one is site/civil. Interior
demolition, selective structural and abatement containment each deserve their
own exemplar so the legend is proven end to end.

1. Mark the sheet up in Revu against the legend.
2. `python tools/takeoff_markup_extract.py --pdf "<sheet> - Marked Up.pdf"`
3. Drop the PDF and the `_takeoff.json` into `reference/`.
4. Add an entry to `REFERENCES` in `_migrate_schema_v5_standards.py` and re-run it.
