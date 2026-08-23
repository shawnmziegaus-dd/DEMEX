# Dropbox (GC-shared plan folders)

**Key** `dropbox` · **Category** file_transfer · **Status** `planned` · **Direction** inbound · **Last verified** 2026-08-11

Several GCs (Summit GC among them) distribute plan sets as public Dropbox folder links inside the ITB email.

## Access

- **Auth method** — public shared link (no auth)
- **Notes** — Links are public; no Dropbox account required to fetch.
- **Protocol** — https
- **Base** — https://www.dropbox.com

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | plan set PDFs, spec PDFs |
| DEMEX writes | — |

## Field mapping

| Source | DEMEX target |
|---|---|
| folder link in email body | `plans/<project>/` |

## Wiring

- **Code** — not built yet
- **Trigger** — event: on ITB import when body contains a dropbox.com link

## Known limits

- Folder links need '?dl=1' handling and can be large; download must be size-capped and logged.
- Links are revoked after award.

## Needs a decision from Shawn

- [ ] Not yet built. Next step: auto-detect dropbox.com links during ITB intake and mirror the folder into plans/<project>/.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

