# <Product name> — connector template

Copy this folder, rename it to the connector key, fill in `connector.json`, then run:

    python integrations/_scaffold.py

The scaffold is idempotent — it creates anything missing and never overwrites a
`connector.json` you have edited.

## Rules for a DEMEX connector

1. **`status` must be honest.** `live` means it runs today. `planned` means the
   folder exists and nothing else does. `unverified` means we do not yet know
   enough about the product to describe it. Never write `live` aspirationally.
2. **Every claim is checkable.** Sender addresses, base URLs and env var names
   go in the spec so a parser can be pointed at them without a hunt.
3. **`known_limits` is mandatory.** An integration that quietly drops a field
   produces a wrong bid, and a wrong bid costs more than a missing feature.
4. **Secrets never land in this tree.** Env var *names* go in `auth.secrets`;
   values live in `.env`, which is git-ignored.
5. **`samples/` holds one real payload**, scrubbed of anything private. That is
   the fixture the parser is tested against.
