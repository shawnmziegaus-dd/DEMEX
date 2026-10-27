# Disassemble Dynamics apps — install on Windows, Mac and iPhone

One server (the DEMEX server on Fly.io) hosts the whole family. Every app installs
from the browser as a real icon, opens in its own window with no address bar, and
all devices share the same saved data. Nothing goes through an app store.

| App | Address on the server | What it is |
|---|---|---|
| **Disassemble Dynamics** | `/hub/` | Launcher — opens DEMEX automatically (switch off in its footer) |
| **DEMEX** | `/demex/` | Operations cockpit: bids, takeoffs, estimates, proposals, job costs |
| **Estimatio** | `/estimatio/` | Flooring + demolition estimating |
| **Qualora** | `/qualora/` | Prequalification & applications — tile shows *Not deployed yet* until its build is placed at `apps/qualora/app.html` or `QUALORA_URL` is set |

Replace `https://demex-ddd.fly.dev` below with your server address if different.

## Windows (Edge or Chrome)
1. Open `https://demex-ddd.fly.dev/hub/?stay` and sign in with the access key.
2. Click the **Install on this device** button in the footer (or the install icon at the right end of the address bar) → **Install**.
3. Repeat on `/demex/`, `/estimatio/`, `/qualora/` for separate icons (each one pins to the taskbar / Start menu on its own).

The existing offline Windows build (`build_desktop.bat` → `DEMEX.exe`) still works and now also serves `/hub/` locally.

## Mac
- **Chrome / Edge:** same as Windows — address-bar install icon → **Install**. The app lands in Launchpad and the Dock.
- **Safari:** open the address → **File → Add to Dock**.

## iPhone / iPad (Safari)
1. Open `https://demex-ddd.fly.dev/demex/` in **Safari** (must be Safari, not Chrome) and sign in.
2. Tap **Share** (square with arrow) → **Add to Home Screen** → **Add**.
3. Repeat for `/hub/`, `/estimatio/`, `/qualora/` if you want each icon.

The icon opens full-screen with the DEMEX colours; sign-in lasts 30 days per device.

## GitHub as the backup server
Data (pipeline, clients, rates, corrections, ITB drops, project files) is pushed to a
**private** GitHub repo every night and whenever you press **Back up now** on the hub page.
One-time setup (about 5 minutes):

1. On GitHub create a private, empty repo, e.g. `shawnmziegaus-dd/DEMEX-data`.
2. GitHub → Settings → Developer settings → Fine-grained personal access tokens → Generate:
   repository access = only that repo; permission **Contents: Read and write**.
3. Give the server the two values:
   `fly secrets set GITHUB_TOKEN="github_pat_..." GITHUB_BACKUP_REPO="shawnmziegaus-dd/DEMEX-data"`
   (for the desktop build, put the same two lines in `.env`).
4. Open the hub — the footer shows the last backup time and a **Back up now** button.

Every run is one commit, so the repo's history is the full backup history. The tool refuses to
push to a public repo. Restore = download any commit's `state/demex.json` and import it.

## Code deploys from GitHub too
Pushing to `main` on `shawnmziegaus-dd/DEMEX` redeploys the server automatically
(`.github/workflows/deploy.yml`). One-time: run `fly tokens create deploy -x 999999h` in the
DEMEX folder and add the output as the `FLY_API_TOKEN` secret under the repo's
Settings → Secrets and variables → Actions. Every push also runs a route check (`check.yml`).

## How it works (for later changes)
- `demex_apps.py` — the app registry, manifests, service workers and head-tag injection. The app HTML files on disk are untouched; tags are added when a page is served.
- `apps/hub/index.html` — the launcher. `apps/qualora/index.html` — the honest "not deployed" page.
- `apps/pwa/make_icons.py` → `apps/pwa/icons/` — regenerate icons after a brand change.
- `tools/github_backup.py` — the backup tool (stdlib only). `python tools/github_backup.py --dry-run` lists what would go up.
- Service workers are network-first: the live page always wins when the server is reachable; the last good copy opens when offline. API calls are never cached.
