# Hosted builds

- `DEMEX_hosted.html` — DEMEX.html plus a cloud adapter (shared db, Ask-Claude AI, downloads). Published as the live DEMEX page.
- `Estimatio.html` — standalone Estimatio (flooring + demolition estimating). Published as the live Estimatio page.

Both are generated from this folder; the local `DEMEX.html` + `demex_bridge.py` remain the source of truth for the backend.
Server deployment plan: see the Estimatio project doc `claude/hosting-and-deployment.md`.
