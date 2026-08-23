# DocuSign

**Key** `docusign` · **Category** contracts · **Status** `planned` · **Direction** bidirectional · **Last verified** 2026-08-11

Subcontract agreements, change orders and lien waivers on awarded work.

## Access

- **Auth method** — oauth2 / Claude DocuSign connector
- **Notes** — Connector currently reports as requiring authorisation.
- **Protocol** — REST
- **Base** — https://demo.docusign.net

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | envelope status, executed contract amount |
| DEMEX writes | envelope from proposal |

## Field mapping

| Source | DEMEX target |
|---|---|
| executed contract amount | `projects[].bidAmount (final) -> learning loop` |
| envelope completed | `projects[].awardedAt` |

## Wiring

- **Code** — not built yet
- **Trigger** — event: on award

## Known limits

- Not yet authorised.

## Needs a decision from Shawn

- [ ] Authorise the DocuSign connector if contract execution should live inside DEMEX.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

