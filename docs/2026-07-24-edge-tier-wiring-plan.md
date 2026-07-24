# Edge Tier Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add edge-plane as the final, health-gated tier of deploy's federation bring-up.

**Architecture:** Per the approved design (`docs/2026-07-24-edge-tier-wiring-design.md`): `EDGE_DIR ?= edge-plane` in the obs-plane empty-to-disable pattern; edge runs last in `up`/`up-dev` pinned to production `up` (like inference, inverse reason); gated by `wait-healthy.sh edge-net caddy:443`; stopped first in `down`; joins the `ps`/`logs`/`pull`/`bundle`/`load` fan-outs. Fresh-host `edge-net` existence is guaranteed by `setup` (every member's `make network` creates it) and documented, not engineered.

**Tech Stack:** GNU Make, bash, the repo's existing `scripts/wait-healthy.sh`.

## Global Constraints

- Data confidentiality (hard rule): no real data / absolute local paths in anything committed.
- Variable name exactly `EDGE_DIR`, default `edge-plane`, empty-to-disable with `[ -z "$(EDGE_DIR)" ] ||` guards (obs pattern).
- Edge tier: LAST in `up`/`up-dev`, pinned to production `up` (never `$(MODE_UP)`); FIRST in `down`.
- Health probe exactly: `./scripts/wait-healthy.sh edge-net caddy:443`.
- Branch `feature/edge-tier` (already exists, carries the committed spec). PR at the end, CI green, do NOT merge.
- The repo is a scaffold-honest Makefile: preserve its comment voice and tab indentation.

---

### Task 1: Makefile + federation.env.example

**Files:**
- Modify: `Makefile` (variables block, `setup`, `up`/`up-dev`, `down`, `ps`, `logs`, `pull`, `bundle`, `load`, help)
- Modify: `federation.env.example`

**Interfaces:**
- Produces: `EDGE_DIR` consumed by Task 2's README text and Task 3's verification.

- [ ] **Step 1: Variable.** In `Makefile`, directly after the `OBS_DIR ?= obs-plane` block (before `DATA_PROFILE`), add:

```makefile
# edge-plane is the federation's entry point (Caddy + Authelia) — a
# pulled-image member with a bespoke Makefile. Its tier comes up LAST
# (inference -> state -> obs -> apps -> edge): Caddy answers 502 for a
# still-warming upstream, so the position is operator ergonomics, not a
# correctness gate. Set empty to run this host without the gateway
# (LAN access then requires each member's own dev override).
EDGE_DIR     ?= edge-plane
```

- [ ] **Step 2: setup.** Add to the `setup` recipe, after the obs line and before the app loop:

```makefile
	[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) network volumes
```

- [ ] **Step 3: up/up-dev.** At the END of the shared `up up-dev` recipe (after the app-tier loop, replacing the final `@echo "federation up."` line so it stays last):

```makefile
	@[ -z "$(EDGE_DIR)" ] || echo "== edge tier ($(EDGE_DIR), production up) =="
	[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) up
	[ -z "$(EDGE_DIR)" ] || ./scripts/wait-healthy.sh edge-net caddy:443
	@echo "federation up."
```

Also extend the recipe's leading comment ("`up` and `up-dev` share one recipe…") with one sentence: the edge tier is pinned to production `up` like inference — its production shape already publishes the entry ports, and its `up-dev` overlay only adds a repo-local test container.

- [ ] **Step 4: down.** Add as the FIRST line of the `down` recipe:

```makefile
	[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) down
```

- [ ] **Step 5: fan-outs.** 
- `ps`: after the obs line add `@[ -z "$(EDGE_DIR)" ] || { echo "== $(EDGE_DIR) =="; $(call compose,$(EDGE_DIR)) ps; }`
- `logs`: add `$(EDGE_DIR)` to the loop list (`... $(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) ...`)
- `pull`: add `$(EDGE_DIR)` to the `$(addprefix ...)` list after `$(OBS_DIR)`
- `bundle`: after the obs line add `[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) bundle`
- `load`: add `$(EDGE_DIR)` to the `$(foreach ...)` list after `$(OBS_DIR)`
- `help`: extend the trailing status line to include `edge: $(if $(EDGE_DIR),$(EDGE_DIR),disabled)` alongside the obs entry.

- [ ] **Step 6: federation.env.example.** After the `OBS_DIR=obs-plane` block add:

```bash
# Edge gateway (Caddy + Authelia) — the single production entry point.
# Pulled-image member, bespoke Makefile. Comes up last, health-gated.
# Set empty to run this host without the gateway.
EDGE_DIR=edge-plane
```

- [ ] **Step 7: parse + lint checks.**

Run: `make -n up EDGE_DIR=edge-plane | tail -8` → shows the edge tier lines after the app loop, `wait-healthy.sh edge-net caddy:443`, then `federation up.`.
Run: `make -n up EDGE_DIR= | grep -c edge-plane` → `0` (disable path clean). `make -n down | head -3` → edge down first. `make help` renders.

- [ ] **Step 8: Commit.**

```bash
git add Makefile federation.env.example
git commit -m "feat: edge tier (edge-plane) — last in bring-up, health-gated on caddy:443, first in down"
```

---

### Task 2: README

**Files:**
- Modify: `README.md` (layout block ~line 22-25, bring-up order §29-38, quickstart ~45-49, target table ~56-62, airgap/bundle §105-106, up-dev note ~115-117)

**Interfaces:**
- Consumes: Task 1's `EDGE_DIR` semantics.

- [ ] **Step 1: Edits, keeping the file's voice:**
- Layout block: add `edge-plane/        # edge tier (Caddy gateway + Authelia — the entry point)` after the obs line.
- § Bring-up order: order becomes `inference (vllm-service) → state (data-plane) → obs (obs-plane) → apps → edge (edge-plane)`; extend the prose walk-through: after the app tier, deploy brings up `edge-plane` (production `up` in both modes) and waits for `caddy:443` on `edge-net`.
- Add a short **fresh-host seam note** under the bring-up section:

```markdown
All three external network seams (`inference-net`, `data-net`, `edge-net`)
are created by `make setup` before any tier starts — every member's own
`make network` creates the seams it joins, so an app tier can never fail
on a missing `edge-net` even though the edge tier itself comes up last.
```

- Target table: `up` row order text gains `→ edge`; `down` row gains leading `edge → `; `bundle` row mentions edge-plane (`EDGE_DIR`); `up-dev` row notes the edge tier stays on production `up` like inference.
- Airgap section: mention `edge-plane-pulled-<version>.tar.gz` joins the fan-out.
- Add one client-prerequisites sentence: browsers reach the federation at `https://<EDGE_HOST>/` — hosts-entry/DNS and CA trust are documented in edge-plane's README (link `../edge-plane/README.md`).

- [ ] **Step 2: Consistency check.** `grep -n edge README.md` — every mention consistent with Task 1's mechanics (last up, first down, production-pinned, empty-to-disable).

- [ ] **Step 3: Commit.**

```bash
git add README.md
git commit -m "docs: edge tier in bring-up order, fresh-host seam note, client-prereq pointer"
```

---

### Task 3: Live verification + PR

**Files:** none (verification + remote ops).

- [ ] **Step 1: Idempotent pass on the running host.** The full federation incl. edge-plane is currently up. Run `make setup` → idempotent no-ops. Run `make up` → every tier's `up` no-ops against running containers, ending with the edge tier lines and `federation up.`; the `wait-healthy` probe for `caddy:443` passes in seconds.
- [ ] **Step 2: Full cycle.** `make down` (edge stops first — verify from output order) then `make up`; afterwards `make ps` lists the edge tier, and an unauthenticated `curl -sk -o /dev/null -w '%{http_code}' -H 'Accept: text/html' https://federation.test/` returns 302 (portal redirect — gateway healthy end-to-end). Note: `down` de-registers nothing stateful; Authelia sessions are in-memory, so users simply re-login.
- [ ] **Step 3: PR.**

```bash
git push -u origin feature/edge-tier
gh pr create --title "feat: edge tier (edge-plane) in the federation bring-up" \
  --body "Step 5 of the edge rollout, per docs/2026-07-24-edge-tier-wiring-design.md: EDGE_DIR member (obs pattern, empty-to-disable), last in up/up-dev pinned to production up, health-gated on caddy:443 via edge-net, first in down, wired into ps/logs/pull/bundle/load. Fresh-host edge-net creation guaranteed by setup (documented). Verified live: idempotent up on a running federation + full down/up cycle ending healthy."
gh pr checks --watch
```

Expected: shellcheck/yamllint/Makefile-parse validation green. Do not merge.
