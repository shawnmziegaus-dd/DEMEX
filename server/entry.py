"""Container entrypoint: point the bridge's runtime files at the persistent
volume, then run it. Everything else is demex_bridge.main()."""
import os, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DEMEX_DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "projects").mkdir(exist_ok=True)

# Files the bridge reads/writes next to itself -> symlink them into the volume.
for name in ("demex_records.db", "demex_records.db-wal", "demex_records.db-journal",
             "demex_rate_card.json", "projects"):
    src, dst = ROOT / name, DATA / name
    if src.is_symlink() or not src.exists():
        if src.is_symlink():
            src.unlink()
        src.symlink_to(dst)
    elif src.is_file() and not dst.exists():      # first boot: seed the volume
        shutil.move(str(src), str(dst)); src.symlink_to(dst)
    elif src.is_dir() and not dst.exists():
        shutil.move(str(src), str(dst)); src.symlink_to(dst)

os.environ.setdefault("DEMEX_IMPORTS_DIR", str(DATA / "imports"))
(DATA / "imports").mkdir(exist_ok=True)
if not os.environ.get("DEMEX_ACCESS_KEY"):
    print("WARNING: DEMEX_ACCESS_KEY is not set -- the server will refuse every sign-in. "
          "Set it with: fly secrets set DEMEX_ACCESS_KEY=...", file=sys.stderr)
    os.environ["DEMEX_ACCESS_KEY"] = os.urandom(32).hex()   # unguessable: locked until a key is set
sys.path.insert(0, str(ROOT))
import demex_bridge

# First boot: seed the shared app state (pipeline, clients, rates) from server/seed_state.json
# so the server opens with the same data as the Claude-hosted page. Never overwrites existing state.
seed = ROOT / "server" / "seed_state.json"
if seed.is_file():
    import json, sqlite3
    from datetime import datetime, timezone
    conn = sqlite3.connect(demex_bridge.DB)
    try:
        conn.execute(demex_bridge.STATE_TABLE_SQL)
        if not conn.execute("SELECT 1 FROM app_state WHERE app='demex'").fetchone():
            conn.execute("INSERT INTO app_state(app,data,version,updated_at) VALUES('demex',?,1,?)",
                         (seed.read_text(encoding="utf-8"), datetime.now(timezone.utc).isoformat()))
            conn.commit(); print("Seeded demex app state from server/seed_state.json")
    finally:
        conn.close()
sys.exit(demex_bridge.main())
