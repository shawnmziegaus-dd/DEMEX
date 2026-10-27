# Deploying the DEMEX server (Fly.io)

One-time setup, about 10 minutes. Everything runs from the DEMEX folder in PowerShell.

1. Create a free Fly.io account at https://fly.io/app/sign-up and install the CLI:
   `iwr https://fly.io/install.ps1 -useb | iex`   (then close and reopen PowerShell)
2. `fly auth login`
3. `fly launch --no-deploy --copy-config --name demex-ddd`   (say **No** to Postgres/Redis; keep region `atl`)
4. `fly volumes create demex_data --size 3 --region atl`
5. Set the secrets (pick a strong access key — this is the sign-in for the site):
   `fly secrets set DEMEX_ACCESS_KEY="choose-a-long-key" ANTHROPIC_API_KEY="sk-ant-..." DEMEX_ALLOWED_HOSTS="demex-ddd.fly.dev"`
   Optional, for Ingest Inbox: `fly secrets set DEMEX_GMAIL_USER="..." DEMEX_GMAIL_APP_PASSWORD="..."`
6. `fly deploy`

Then open https://demex-ddd.fly.dev — sign in with the access key. Data lives on the volume
(`/data`): SQLite db, plan PDFs, rate card, ITB drop files. First boot seeds the pipeline from
`server/seed_state.json`.

Updates later: just `fly deploy` from the folder (or let Claude push to GitHub and deploy).

Cost: shared-cpu-1x / 1 GB with auto-stop ≈ $3–6 / month; the machine sleeps when idle and wakes on the first request (a few seconds).
