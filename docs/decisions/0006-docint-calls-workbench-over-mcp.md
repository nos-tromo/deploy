# 0006 — docint calls workbench over an MCP door

Status: accepted (2026-11-15)
Date: 2026-11-15

## Context

[ADR 0005](0005-workbench-shared-tool-tier.md) left version 2 to a decision of
its own. docint's chat model needs Python on what a turn retrieved (passages,
keyword counts, topic listings), and docint may not run a sandbox of its own,
by 0005's invariant. Later AI services should find workbench's tools in one
place.

## Decision

**docint becomes workbench's second caller, over an MCP door.**

- **The door.** `POST /mcp` on the api: hand-written, stateless, JSON-only,
  built from the tool registry. It speaks the revisions `2026-07-28`,
  `2025-11-25` and `2025-06-18`.
- **A key, a tool list and limits per caller.** docint's key is
  `DOCINT_WORKBENCH_KEY` (docint's `WORKBENCH_API_KEY`, env-sync group
  `workbench-docint-key`), its tool `run_python` only, its wall time 60 s, its
  running jobs 2: fewer than the executor's slots, so one stays free. Open
  WebUI keeps the terminal door, its nine tools and its key. A key reaches only
  its own door; the other door answers `404`.
- **Inline data.** At most 512 KiB per call, kept only for the job and deleted
  with it; docint has no workspace. docint records the hand-over as a
  `disclose` with a purpose (ADR 0003); workbench records `combine` /
  `tool.run_python.inline` with count, job, purpose and request id, never
  content or a digest. docint's user id at workbench is a keyed pseudonym of
  the owner name.
- **A new trust edge.** docint's backend joins `workbench-net` (alias
  `docint-backend`); its frontend does not. The key is a bearer secret: its
  holder can run code as docint under any user id, and reaches no workspace.
- **The `analysis` artifact.** The table, charts, code and provenance of a
  `run_python` in a chat turn, stored with the turn, addable to a Report
  Builder report, exportable as md, html, pdf, json and zip.
- **Unchanged:** the tier order, obs-plane (the same emitter) and
  open-webui-service.

## Alternatives considered

- **The official SDK** (`mcp` 2.x, MIT): about 15 more packages in the api
  image, three of them native. It stays in the dev group for a conformance test.
- **Uploads into a workspace:** docint's data would enter a 7-day workspace
  lifecycle, where inline data leaves nothing.
- **A sandbox in docint:** against 0005's invariant, and next to its
  collections on `data-net`.
- **A network of its own for MCP callers:** a fifth seam, where a key and a
  door per caller already separate them.
- **Open WebUI over MCP:** it passes no files over MCP, as 0005 says.

## Consequences

- workbench v0.2.0 ships before docint's minor. docint works without workbench:
  with no key it has no tool; with workbench unreachable the call answers
  `unavailable` and later turns offer none.
- One more shared secret group; `make env ENV_GENERATE=1` writes it. The two
  callers' keys must differ.
- Residual risks (workbench's v2 design, § 6): the hand-over is recorded after
  the data has left, so a docint killed between send and settle loses its
  `disclose` events (workbench's own event still holds job, purpose and request
  id); an abandoned call runs to its wall time; docint's backend and Open WebUI
  can reach each other on `workbench-net`; workbench holds the key the
  pseudonyms are made with.
- This record states no legal conclusions, as in 0005.
