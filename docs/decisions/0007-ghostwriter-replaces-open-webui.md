# 0007 — Ghostwriter replaces Open WebUI

Status: accepted (2026-11-16)
Date: 2026-11-16

## Context

Of Open WebUI only the chat is still used: documents are docint's, tools and
code are workbench's (the
[design](../superpowers/specs/2026-11-16-ghostwriter-design.md), § 1). What
remains costs a lot:

- workbench is awkward in it: a pick per chat, results through a file menu.
- Sign-in is a special path: a trusted header, the `edge-guard` sidecar and a
  port of its own (`:8443`).
- The upstream image is heavy: 5.8 GB, 20 `edge-guard` rules, and a retention
  sidecar pinned to one migration revision, which every image bump stops until
  it is re-verified.

## Decision

**A new first-party app, `ghostwriter`, takes over the chat for the same
signed-in users.**

- **The app.** FastAPI and a React SPA, translator as the template, in the app
  tier.
- **workbench's third caller.** Tools over MCP, as in
  [0006](0006-docint-calls-workbench-over-mcp.md); files over REST, for this
  caller only, each file confined to its chat.
- **Networks.** The backend joins `inference-net` and `workbench-net` (alias
  `ghostwriter-backend`), the frontend `edge-net` (alias
  `ghostwriter-frontend`). Never `data-net`.
- **Entry.** The route `/ghostwriter/` on `:443`, with an edge secret of its
  own.
- **State.** Its own SQLite on the external `ghostwriter-data` volume. Chats are
  kept 90 days, with a 30-day notice before a new or shorter period takes
  effect; tool contents go with the files, at 7 days.
- **Audit.** The emitter `ghostwriter` / `backend` (ADR 0003).
- **Open WebUI runs alongside** until the owner accepts Ghostwriter. Retiring it
  is phase 2, with its own spec.
- **Unchanged:** the tier order, the four seams, vllm-service.

## Alternatives considered

- **A third caller on workbench's REST terminal door:** a special path kept for
  one caller after Open WebUI is gone.
- **A mode in docint:** its sessions are bound to collections.
- **Another open-source chat UI:** it swaps one heavy upstream for another.
- **Keeping Open WebUI with open-webui-service#67:** the costs stay.

## Consequences

- A new trust edge on `workbench-net`: the key is a bearer secret, and workbench
  confines it to one chat (workbench v0.3.0).
- The delivery order is the design's § 7. deploy's wiring (`APP_DIRS`) lands in
  a later PR, once the repo has a release.
- Residual risks (design § 8): prompt injection into a network-less sandbox;
  two periods, so older chats show "expired"; the router does not see the user;
  one chat model.
- This record states no legal conclusions, as in 0005 and 0006.
