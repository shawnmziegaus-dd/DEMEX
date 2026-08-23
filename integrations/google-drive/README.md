# Google Drive

**Key** `google-drive` · **Category** document_storage · **Status** `partial` · **Direction** bidirectional · **Last verified** 2026-08-11

Long-term storage of plan sets, marked-up takeoffs, proposals and executed contracts, organised by project.

## Accounts

- Admin@disassembledynamics.com

## Access

- **Auth method** — Claude Google Drive connector
- **Notes** — Historical bid sheets and pay applications were already mined from Drive - see demex_rate_reference.json provenance.
- **Protocol** — Drive API via MCP

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | bid sheets, historical proposals, AIA pay applications, plan sets |
| DEMEX writes | generated proposal PDF, marked-up takeoff PDF |

## Field mapping

| Source | DEMEX target |
|---|---|
| historical bid sheet | `demex_rate_reference.json derived_unit_rates` |
| pay application | `actual_realized_costs` |

## Wiring

- **Code** — `proposal_pdf.py`
- **Trigger** — manual

## Known limits

- Folder naming is inconsistent across the history, so automated project matching is fuzzy.

## Needs a decision from Shawn

- [ ] Agree one Drive folder convention per project so DEMEX can file documents without guessing.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

