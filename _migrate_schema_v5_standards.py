#!/usr/bin/env python3
"""
_migrate_schema_v5_standards.py
===============================

Adds the standards + integrations layer to demex_records.db, then loads it:

  takeoff_legend         the markup colour standard, one row per colour x measurement kind
  reference_takeoffs     exemplar marked-up sheets - what "done right" looks like
  takeoff_measurements   every individual measurement extracted from a marked-up sheet
  integrations           mirror of integrations/registry.json so the app can list connectors

Idempotent. Re-running re-loads the current legend, reference and registry
without duplicating rows. Existing tables (projects, emails, line_items,
attachments) are not touched.

    python _migrate_schema_v5_standards.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(REPO, "demex_records.db")
LEGEND = os.path.join(REPO, "standards", "takeoff", "markup_legend.json")
REGISTRY = os.path.join(REPO, "integrations", "registry.json")
REF_DIR = os.path.join(REPO, "standards", "takeoff", "reference")

REFERENCES = [
    {
        "ref_id": "ref_takeoff_scps_lake_howell_c100",
        "title": "SCPS Lake Howell HS CEP Replacement - C100 Demolition Plan",
        "discipline": "Site / Civil demolition",
        "sheet": "C100 Site Plan Rev.0 - Demolition Plan",
        "owner": "Seminole County Public Schools",
        "engineer": "Matern Professional Engineering, Inc. / Klima Weeks Civil Engineering (Jay A. Klima, P.E.)",
        "issue_date": "2026-02-18",
        "engineer_project_no": "2025-073",
        "pdf": "standards/takeoff/reference/scps_lake_howell_c100/SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp.pdf",
        "takeoff_json": "standards/takeoff/reference/scps_lake_howell_c100/SCPS_Lake_Howell_HS_CEP_C100_Demolition_MarkedUp_takeoff.json",
        "marked_up_in": "Bluebeam Revu",
        "is_gold_standard": 1,
        "notes": (
            "Adopted 2026-08-11 as the reference for how a site/civil demolition takeoff should be marked up. "
            "Colour carries scope, measurement type carries unit. Documented exclusion on this sheet: the "
            "+/-843 SF one-storey metal building is removed by SCPS prior to start of construction - only the "
            "concrete pad is Disassemble Dynamics scope. Under standard v1.0 that hatch should carry a gray "
            "by-others polygon; it was not marked on this file, which is the one gap in an otherwise clean set."
        ),
    },
    {
        "ref_id": "ref_takeoff_harbor_freight_d1_0",
        "title": "Harbor Freight Tools, Jacksonville FL - D1.0 Demolition Plan",
        "discipline": "Interior / selective demolition",
        "sheet": "D1.0 Demolition Plan",
        "owner": "Harbor Freight Tools",
        "engineer": None,
        "issue_date": "2026-08-11",
        "engineer_project_no": None,
        "pdf": "standards/takeoff/reference/harbor_freight_jax_beach/HarborFreight_JaxBeach_D1.0_Demolition_MarkedUp.pdf",
        "takeoff_json": "standards/takeoff/reference/harbor_freight_jax_beach/HarborFreight_JaxBeach_D1.0_Demolition_MarkedUp_takeoff.json",
        "marked_up_in": "Bluebeam Revu",
        "is_gold_standard": 0,
        "notes": (
            "Interior/selective demolition exemplar for the interior legend profile (v1.1) - "
            "14333 Beach Blvd, Jacksonville FL. Priced companion bid sheet at "
            "standards/takeoff/reference/harbor_freight_jax_beach/bid_sheet.json (DDD-2026/0811R1). "
            "21 of 33 measurements carry needs_review=1 as unconfirmed_legend - the interior profile "
            "colours are not yet confirmed against a second marked-up sheet."
        ),
    },
]

DDL = """
CREATE TABLE IF NOT EXISTS takeoff_legend (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    legend_version TEXT NOT NULL,
    profile       TEXT NOT NULL DEFAULT 'site_civil',  -- legend profile (site_civil | interior | ...)
    color_name    TEXT NOT NULL,
    hex           TEXT,
    rgb           TEXT,              -- JSON [r,g,b] 0-1
    kind          TEXT NOT NULL,     -- area | length | count
    unit          TEXT,              -- SF | LF | EA
    svc           TEXT,              -- DEMEX rate-card service id, NULL if not priced
    rate_card     TEXT,              -- existing | proposed | n/a
    description   TEXT,
    confidence    TEXT,              -- confirmed | unconfirmed (legend v1.1 per-colour confidence)
    in_scope      INTEGER DEFAULT 1,
    UNIQUE(legend_version, profile, color_name, kind)
);

CREATE TABLE IF NOT EXISTS reference_takeoffs (
    ref_id              TEXT PRIMARY KEY,
    title               TEXT,
    discipline          TEXT,
    sheet               TEXT,
    owner               TEXT,
    engineer            TEXT,
    issue_date          TEXT,
    engineer_project_no TEXT,
    pdf                 TEXT,
    takeoff_json        TEXT,
    marked_up_in        TEXT,
    legend_version      TEXT,
    is_gold_standard    INTEGER DEFAULT 0,
    measurement_count   INTEGER,
    rollup              TEXT,        -- JSON
    est_qty             TEXT,        -- JSON, projects[].estQty shape
    notes               TEXT,
    loaded_at           TEXT
);

CREATE TABLE IF NOT EXISTS takeoff_measurements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id       TEXT,               -- reference_takeoffs.ref_id
    project_id   TEXT,               -- projects.project_id, when run against live work
    page         INTEGER,
    kind         TEXT,               -- area | length | count
    qty          REAL,
    unit         TEXT,
    label        TEXT,               -- the text Bluebeam wrote, e.g. "855.41 sf"
    color_hex    TEXT,
    color_name   TEXT,
    svc          TEXT,
    scope        TEXT,
    in_scope     INTEGER DEFAULT 1,
    author       TEXT,
    source_pdf   TEXT,
    needs_review INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS integrations (
    key           TEXT PRIMARY KEY,
    name          TEXT,
    vendor        TEXT,
    category      TEXT,
    status        TEXT,              -- live | partial | planned | unverified | blocked
    direction     TEXT,
    path          TEXT,
    last_verified TEXT,
    synced_at     TEXT
);

CREATE INDEX IF NOT EXISTS ix_takeoff_meas_ref     ON takeoff_measurements(ref_id);
CREATE INDEX IF NOT EXISTS ix_takeoff_meas_project ON takeoff_measurements(project_id);
CREATE INDEX IF NOT EXISTS ix_takeoff_meas_svc     ON takeoff_measurements(svc);
"""


def ensure_legend_schema(cur) -> None:
    """Rebuild takeoff_legend if it predates the v1.1 profile-aware schema.
    Safe: the table is fully re-derived from markup_legend.json on every run."""
    cols = [r[1] for r in cur.execute("PRAGMA table_info(takeoff_legend)")]
    if cols and "profile" not in cols:
        cur.execute("DROP TABLE takeoff_legend")


def load_legend(cur, now: str) -> int:
    legend = json.load(open(LEGEND, encoding="utf-8"))
    ver = str(legend["version"])
    units = {"area": "SF", "length": "LF", "count": "EA"}
    cur.execute("DELETE FROM takeoff_legend WHERE legend_version = ?", (ver,))
    # legend v1.0 was a flat {"colors":[...]}; v1.1+ is {"profiles":{name:{colors:[...]}}}
    if "profiles" in legend:
        profiles = {name: p.get("colors", []) for name, p in legend["profiles"].items()}
    else:
        profiles = {"site_civil": legend.get("colors", [])}
    n = 0
    for profile, colors in profiles.items():
        for c in colors:
            for kind, scope in (c.get("scope") or {}).items():
                if scope is None:
                    continue
                cur.execute(
                    "INSERT INTO takeoff_legend (legend_version,profile,color_name,hex,rgb,kind,unit,svc,"
                    "rate_card,description,confidence,in_scope) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ver, profile, c["name"], c.get("hex"), json.dumps(c.get("rgb")), kind, units[kind],
                     scope.get("svc"), scope.get("rate_card"), scope.get("description"),
                     c.get("confidence"), int(c.get("in_scope", True))),
                )
                n += 1
    return n


def load_references(cur, now: str) -> tuple[int, int]:
    legend_version = json.load(open(LEGEND, encoding="utf-8"))["version"]
    refs = meas = 0
    for r in REFERENCES:
        tj = os.path.join(REPO, r["takeoff_json"])
        if not os.path.exists(tj):
            print(f"  ! takeoff json missing, skipping {r['ref_id']}: {tj}")
            continue
        t = json.load(open(tj, encoding="utf-8"))
        cur.execute(
            "INSERT OR REPLACE INTO reference_takeoffs (ref_id,title,discipline,sheet,owner,engineer,"
            "issue_date,engineer_project_no,pdf,takeoff_json,marked_up_in,legend_version,is_gold_standard,"
            "measurement_count,rollup,est_qty,notes,loaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["ref_id"], r["title"], r["discipline"], r["sheet"], r["owner"], r["engineer"],
             r["issue_date"], r["engineer_project_no"], r["pdf"], r["takeoff_json"], r["marked_up_in"],
             legend_version, r["is_gold_standard"], t["counts"]["measurements"],
             json.dumps(t["rollup"]), json.dumps(t.get("est_qty", [])), r["notes"], now),
        )
        refs += 1
        cur.execute("DELETE FROM takeoff_measurements WHERE ref_id = ?", (r["ref_id"],))
        for m in t["measurements"]:
            cur.execute(
                "INSERT INTO takeoff_measurements (ref_id,project_id,page,kind,qty,unit,label,color_hex,"
                "color_name,svc,scope,in_scope,author,source_pdf,needs_review) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["ref_id"], None, m.get("page"), m.get("kind"), m.get("qty"), m.get("unit"),
                 m.get("label"), m.get("color_hex"), m.get("color_name"), m.get("svc"),
                 m.get("scope"), int(m.get("in_scope", True)),
                 m.get("author"), t.get("source_pdf"), int(m.get("needs_review", 0))),
            )
            meas += 1
    return refs, meas


def load_integrations(cur, now: str) -> int:
    if not os.path.exists(REGISTRY):
        print("  ! integrations/registry.json not found - run integrations/_scaffold.py first")
        return 0
    reg = json.load(open(REGISTRY, encoding="utf-8"))
    n = 0
    for c in reg["connectors"]:
        cur.execute(
            "INSERT OR REPLACE INTO integrations (key,name,vendor,category,status,direction,path,"
            "last_verified,synced_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (c["key"], c["name"], c["vendor"], c["category"], c["status"], c["direction"],
             c["path"], c["last_verified"], now),
        )
        n += 1
    return n


def main() -> int:
    if not os.path.exists(DB):
        sys.exit(f"database not found: {DB}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    cur = con.cursor()
    ensure_legend_schema(cur)
    cur.executescript(DDL)

    n_legend = load_legend(cur, now)
    n_refs, n_meas = load_references(cur, now)
    n_int = load_integrations(cur, now)
    con.commit()

    print(f"takeoff_legend        {n_legend} rows")
    print(f"reference_takeoffs    {n_refs} rows")
    print(f"takeoff_measurements  {n_meas} rows")
    print(f"integrations          {n_int} rows")

    print("\ngold-standard takeoff on file:")
    for row in cur.execute(
        "SELECT ref_id, title, measurement_count FROM reference_takeoffs WHERE is_gold_standard = 1"
    ):
        print(f"  {row[0]}  {row[1]}  ({row[2]} measurements)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
