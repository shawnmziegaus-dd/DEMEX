"""Container entrypoint: point the bridge's runtime files at the persistent
volume, then run it. Everything else is demex_bridge.main()."""
import os, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DEMEX_DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "projects").mkdir(exist_ok=True)

# Files the bridge reads/writes next to itself -> symlink them into the volume.
# The root filesystem is reset to the image on every machine start, so this runs each boot: the volume
# copy always wins, and an image copy is only used to seed a volume that doesn't have the file yet.
for name in ("demex_records.db", "demex_records.db-wal", "demex_records.db-journal",
             "demex_rate_card.json", "projects"):
    src, dst = ROOT / name, DATA / name
    if src.is_symlink():
        src.unlink()
    elif src.exists():
        if dst.exists() or dst.is_symlink():      # volume already has it: drop the image copy
            shutil.rmtree(src) if src.is_dir() else src.unlink()
        else:                                     # first boot: seed the volume
            shutil.move(str(src), str(dst))
    src.symlink_to(dst)

os.environ.setdefault("DEMEX_IMPORTS_DIR", str(DATA / "imports"))
(DATA / "imports").mkdir(exist_ok=True)
if not os.environ.get("DEMEX_ACCESS_KEY") and not os.environ.get("DEMEX_ACCESS_KEYS"):
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
# Inbox sweep (DEMEX_MAIL_ACCOUNTS secret): bid emails -> DEMEX, Wayne's Yahoo -> PAVORA. Off when unset.
import mail_sweep                       # server/ is sys.path[0] when run as server/entry.py
mail_sweep.start(demex_bridge.DB, Path(os.environ["DEMEX_IMPORTS_DIR"]))
sys.exit(demex_bridge.main())
