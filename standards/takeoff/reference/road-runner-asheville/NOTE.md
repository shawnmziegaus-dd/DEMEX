# Road Runner Markets, Asheville NC

Ingested 2026-09-29 from `Road_Runner_Markets__Asheville_North_Carolina_-_Has_Construction_-_Plan_Hub_-_Marked_Up.pdf`.

The extractor ran cleanly on both legend profiles but found ZERO Bluebeam measurement annotations in this PDF (42 page(s)). The markups are either flattened into the page content or were never saved as Bluebeam measurement objects (/Polygon, /PolyLine, /Line with a dimension intent, or count subtypes). Nothing was fabricated; no *_takeoff.json was written.

Skipped (non-measurement) annotations by subtype: {"/Link": 90, "/Square": 745}

Verified 2026-09-29: the 745 skipped /Square annotations were inspected directly - they carry no measurement intent (/IT), no colour, and their contents are title-block/seal text ('SEAL', licence numbers), not Bluebeam measurements. The 90 /Link annotations are sheet hyperlinks. The markups visible on the sheets are flattened into page content and cannot be extracted as objects. Re-mark in Bluebeam (unflattened) to ingest this set.
