# UF Health Jonesville - Addendum A Architectural

Ingested 2026-09-29 from `UFHealthJonesville-AddendumA-Architectural_-_Marked_Up_-_Scorpio.pdf`.

The extractor ran cleanly on both legend profiles but found ZERO Bluebeam measurement annotations in this PDF (19 page(s)). The markups are either flattened into the page content or were never saved as Bluebeam measurement objects (/Polygon, /PolyLine, /Line with a dimension intent, or count subtypes). Nothing was fabricated; no *_takeoff.json was written.

Skipped (non-measurement) annotations by subtype: {"/Square": 15}

Verified 2026-09-29: the 15 skipped /Square annotations were inspected directly - they are Addendum-A revision boxes and UL design-note callouts ('A', 'Design No. U419...'), with no measurement intent and no colour. Any takeoff markups are flattened into page content. Re-mark in Bluebeam (unflattened) to ingest this set.
