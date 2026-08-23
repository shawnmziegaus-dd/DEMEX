# Qualora

**Key** `qualora` · **Category** unverified · **Status** `unverified` · **Direction** unknown · **Last verified** 2026-08-11

PLACEHOLDER - Shawn named Qualora as an application DEMEX must tie into. No product could be identified from the codebase, the mail history or a web search, so nothing about it has been assumed. Fill in the fields below and this connector becomes real.

## Access

- **Auth method** — TBD
- **Notes** — TBD
- **Protocol** — TBD

## Wiring

- **Code** — not built yet
- **Trigger** — TBD: TBD

## Known limits

- Nothing has been implemented or inferred. Every field here is deliberately blank rather than guessed.

## Needs a decision from Shawn

- [ ] Confirm the vendor and product URL (spelling may differ).
- [ ] State what DEMEX should read from it and what, if anything, DEMEX should write back.
- [ ] Say whether access is API, portal export, or email.
- [ ] Then this folder gets a real connector.json and a parser under tools/.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

