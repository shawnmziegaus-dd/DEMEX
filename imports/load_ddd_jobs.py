"""Load imports/ddd_jobs_*_projects.json rows into demex_records.db `projects` (upsert by project_id)."""
import json, sqlite3, sys, glob, os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "demex_records.db")
con = sqlite3.connect(db)
cols = [r[1] for r in con.execute("pragma table_info(projects)")]
n = 0
for f in sorted(glob.glob(os.path.join(REPO, "imports", "ddd_jobs_*_projects.json"))):
    for p in json.load(open(f, encoding="utf-8")):
        keys = [k for k in cols if k in p]
        con.execute(f"INSERT OR REPLACE INTO projects ({','.join(keys)}) VALUES ({','.join('?'*len(keys))})", [p[k] for k in keys])
        n += 1
con.commit()
print("projects upserted:", n, "| total projects:", con.execute("select count(*) from projects").fetchone()[0])
