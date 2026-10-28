# DEMEX — Disassemble Dynamics operating platform

DEMEX is the operations platform for **Disassemble Dynamics, Inc.** (DDD), a commercial
interior selective-demolition contractor based in Lake City, Florida, working across
Florida, Texas and Georgia. This repository holds the whole app family, the Python
backend, the reference takeoff library and the deployment setup.

| App | What it is | Where it runs |
|---|---|---|
| **DEMEX** | Operations cockpit: bid pipeline, ITB intake, takeoffs, estimates, proposals, clients, job costs, performance | `/demex/` on the server · Claude-hosted page · offline Windows build |
| **Estimatio** | The estimating engine. Runs inside DEMEX for demolition, and stands alone for flooring + demolition users (own data, DEMEX hand-off for demo bids). Dashboard, scope → takeoff lines → rate card → estimate → proposal, AI line suggestions and an AI correction chat | `/estimatio/` on the server · Claude-hosted page |
| **Qualora** | Subcontractor prequalification & applications (in-house DDD app, partial) | `/qualora/` placeholder until its build is placed at `apps/qualora/app.html` |
| **Disassemble Dynamics hub** | Launcher for the family; opens DEMEX by default; backup button | `/hub/` |

Every app installs as a real icon on Windows, Mac and iPhone (PWA) from one server and
all devices share the same saved data. See `INSTALL_APPS.md`.

## How the pieces fit

```
Qualora (find GC + project)  →  Estimatio (analyze, take off, price, propose)  →  DEMEX pipeline
          ↑                                                                            ↓
   shared intelligence  ←  actuals / corrections / awarded results  ←  project & field data
```

Estimatio is meant to be the intelligence engine of the ecosystem: every estimate,
estimator correction, awarded job and actual quantity/cost feeds a learning loop that
makes the next estimate better (`learning_loop.py`, `standards/takeoff/reference/`).

## Repository map

| Path | Purpose |
|---|---|
| `DEMEX.html` | The DEMEX app (single file, React inline). Source of truth for the UI |
| `demex_bridge.py` | Local/server backend: static serving, `/api/*` (ingest, takeoff, pricing, proposal PDF, AI, rate card, ITB imports, shared state), sign-in in server mode |
| `demex_apps.py` | App registry, PWA manifests/service workers, clean `/demex/` `/estimatio/` `/qualora/` `/hub/` routes |
| `hosted/Estimatio.html` | Estimatio standalone app (brand package applied) |
| `hosted/DEMEX_hosted.html` | DEMEX adapted for the Claude-hosted page (shared artifact db, Ask-Claude AI) |
| `apps/` | Hub launcher, Qualora placeholder, PWA icons |
| `email_ingestor.py` | Gmail ITB intake → SQLite + daily `demex_itb_import_*.json` drops |
| `takeoff_scalar.py`, `tools/takeoff_markup_extract.py` | Plan-set takeoff engine (vector geometry + vision), markup extraction |
| `pricing_engine.py`, `learning_loop.py`, `proposal_pdf.py` | Pricing from the live rate card, estimate-vs-actual learning, proposal PDFs |
| `standards/takeoff/reference/` | Reference library of real marked-up DDD jobs (takeoffs, bid sheets, meta) used for benchmarking and learning |
| `integrations/` | Connector records (BuildingConnected, PlanHub, Bluebeam, QuickBooks, Qualora, …) |
| `server/`, `Dockerfile`, `fly.toml`, `.github/workflows/` | Server deployment (Fly.io), first-boot seed, auto-deploy on push |
| `tools/github_backup.py` | Nightly / on-demand backup of app data to a private GitHub repo |
| `build_desktop.bat`, `demex_desktop.py`, `desktop/` | Offline Windows build (`DEMEX.exe`) |
| `run_demex.bat` | Local dev launcher (bridge on `localhost:8765`) |

## Running it

**Locally (Windows):** double-click `run_demex.bat` → `http://localhost:8765/DEMEX.html`.
Needs Python 3.12 and `pip install -r requirements.txt`. Secrets go in `.env` (see `.env.example`).

**Server (Fly.io):** follow `server/DEPLOY.md` — account, `fly launch`, a volume for `/data`,
secrets (`DEMEX_ACCESS_KEY`, `ANTHROPIC_API_KEY`, `DEMEX_ALLOWED_HOSTS`, optional Gmail),
`fly deploy`. After the one-time setup, every push to `main` redeploys automatically.
The site sits behind an access key; data lives on the volume and is backed up to GitHub.

**Claude-hosted pages:** lightweight versions of DEMEX and Estimatio published as Claude
artifacts (shared database, AI through Claude). Backend-only features need the server.

## Conventions

- Demolition rate ids/units (`demo_walls`, `demo_floor`, … in `RATE_DEFAULTS`) are shared by DEMEX and Estimatio so estimates move between them without translation.
- Estimate shape everywhere: `estQty[{svc, qty, unit, rate, mat}]` and `estimate {direct, oh, profit, sell}`.
- Never fake results: AI takeoff quantities must be traceable to a drawing/measurement; corrections are training data only after validation.
- Bid data, credentials, downloaded plan sets and the live rate card are gitignored (`.gitignore`); the reference library keeps extracted JSON, not the plan PDFs.
