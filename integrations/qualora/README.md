# Qualora

**Key** `qualora` · **Category** prequalification · **Status** `partial` · **Direction** bidirectional · **Last verified** 2026-10-27

Qualora is Disassemble Dynamics' own subcontractor-prequalification product and part of the DEMEX ecosystem.
It takes the company's information and applies to general contractors by email or through their
prequalification portals. It does **not** track bids — DEMEX does.

## How it sits in DEMEX

- **App of the family** — served at `/qualora/` by the DEMEX server with its own icon and install (see `INSTALL_APPS.md`). Until the Qualora build is placed at `apps/qualora/app.html` (or `QUALORA_URL` is set) the route shows an honest "not deployed yet" page.
- **Sub-agent of the DEMEX Agent** — commands like *prequal with Scorpio*, *who are we not prequalified with*, *open Qualora* route to the Qualora sub-agent (`AGENT_SUBAGENTS.qualora` in `DEMEX.html`). It reports which GCs in the DEMEX client list have no prequalification on record and opens Qualora.
- **Data contract (planned)** — Qualora writes `clients[].prequal` (`none | applied | approved | expired`) back onto the DEMEX client record; DEMEX reads GC contacts as prequal targets.

## Known limits

- The Qualora application build is not in this repository yet; the sub-agent cannot submit applications until it is.
- `clients[].prequal` is defined but unpopulated.

## Needs a decision from Shawn

- [ ] Where the Qualora code lives (folder or URL) so it can be deployed with the family.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | prequal confirmations / portal exports received |
| `outbound/` | applications staged for approval before sending |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, used as a parser fixture |
