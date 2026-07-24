# Edge tier (edge-plane) wiring — design

Status: approved design, pre-implementation
Date: 2026-07-24
Scope: deploy repo only. Adds the edge gateway (edge-plane) as the final
tier of the ordered, health-gated federation bring-up. Companion docs
updates outside this repo (workspace CLAUDE.md bring-up order, trusted-zone
seam docs) belong to the separate step-6 work.

## Context

edge-plane (Caddy + Authelia) is deployed as the federation's single
production entry point: TLS on :443 (+ :8443 for Open WebUI), path routing
over the external `edge-net` network, Authelia forward-auth injecting
`X-Auth-User`/`X-Auth-Email`. Every app frontend now joins `edge-net`, and
every app's `compose.yaml` hard-requires that external network at `up`.
deploy currently sequences four tiers (inference → state → obs → apps) and
knows nothing about edge-plane.

## Decisions

1. **New member variable `EDGE_DIR ?= edge-plane`** — obs-plane pattern:
   empty-to-disable, participating in `setup`, `up`/`up-dev`, `down`,
   `ps`, `logs`, `pull`, `bundle`, and `load`. edge-plane honors the same
   member contract (idempotent `network volumes`, detached `--no-build`
   `up`, `down`, `bundle`, compose file at `docker/compose.yaml` with
   repo-local `.env`).

2. **Fresh-host `edge-net` guarantee = `setup`, documented not
   engineered.** `up` depends on `setup`, which runs every member's
   `make network volumes` before any tier starts; since the sub-path
   integration, every app's `make network` (and edge-plane's own) creates
   `edge-net` if missing. An app tier therefore can never fail on a
   missing `edge-net`, regardless of tier order. The README states this
   explicitly; no new mechanism.

3. **Edge tier runs last** (inference → state → obs → apps → edge),
   matching the edge-plane design's bring-up order. Caddy tolerates a
   still-warming upstream with a 502 and recovers without restart, so the
   position is operator ergonomics (nothing user-visible comes up before
   its apps), not a correctness gate.

4. **Health gate:** `./scripts/wait-healthy.sh edge-net caddy:443` after
   the tier starts. Compose attaches the service-name alias on every
   network a service joins, so `caddy` resolves on `edge-net`; Caddy's
   own `depends_on: authelia: condition: service_healthy` makes the TCP
   probe transitively cover Authelia.

5. **Pinned to production `up` in both modes** — same treatment as
   inference, for the inverse reason: edge-plane's production shape
   already publishes its host ports (it is the entry point), and its
   `up-dev` overlay only adds the repo-local whoami echo container, which
   has no place in a federation bring-up. `MODE_UP` does not apply.

6. **`down` stops edge first** (reverse order: edge → apps → obs → state
   → inference), so the entry point disappears before the services behind
   it.

## Out of scope

- Any change to edge-plane or the app repos.
- Workspace-level docs (infra CLAUDE.md bring-up order + third seam +
  trusted-zone documentation) — step 6.
- Client-side prerequisites (EDGE_HOST hosts-entry/DNS, CA trust): the
  README links edge-plane's README instead of duplicating its runbook.

## Files touched

- `Makefile` — `EDGE_DIR` variable; `setup`, `up`/`up-dev` (tier + probe),
  `down` (first), `ps`, `logs`, `pull`, `bundle`, `load` loops; help text.
- `federation.env.example` — `EDGE_DIR` override comment (obs pattern).
- `README.md` — tier table/order incl. edge; fresh-host seam note
  (decision 2); pointer to edge-plane's client runbook; on-host layout
  mention of the edge repo.

## Verification

On this host (federation already up — the sequence must be idempotent):
`make setup` (no-ops, creates nothing new), `make up` (each tier's `up`
no-ops against running containers; edge probe passes), `make ps` (edge
tier listed), then a full `make down && make up` cycle ending healthy,
with the edge probe observed gating after the app tier. CI: the repo's
existing shellcheck/yamllint/Makefile-parse validation must stay green.

[Status: the live verification on the dev host substituted a partial cycle
(state/obs/edge) for the full `make down && make up`, after discovering the
app tier's date-based dev image tags strand containers across day
boundaries with `--no-build`; full-cycle sequencing was instead verified via
`make -n` dry-run ordering. The tag fragility is tracked upstream.]
