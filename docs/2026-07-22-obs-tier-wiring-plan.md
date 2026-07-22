# deploy obs-tier wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire obs-plane into deploy as a first-class, health-gated tier between state and apps, covered by every lifecycle target.

**Architecture:** `OBS_DIR ?= obs-plane` beside the tier vars; explicit guarded lines per target (`[ -z "$(OBS_DIR)" ] || …`) so an empty value skips the tier; health gate reuses `scripts/wait-healthy.sh` probing `prometheus:9090` over `data-net` (prometheus carries its service-name alias there). Spec: `docs/2026-07-22-obs-tier-wiring-design.md`.

**Tech Stack:** GNU Make, bash. No new files beyond docs.

## Global Constraints

- Repo: the deploy checkout at the infra workspace root, branch `feature/obs-tier` (already exists, has the spec committed). All paths repo-relative.
- Nothing committed may contain real data or absolute local paths (run `grep -rnE '/(Users|home)/[A-Za-z0-9_.-]+' . --exclude-dir=.git` before each commit — must print nothing).
- Tier order is load-bearing: `inference → state → obs → apps`, `down` exact reverse. `down` never touches volumes.
- Empty `OBS_DIR` must skip the tier cleanly in EVERY target (no `//*.tar.gz` glob artifacts, no empty `-C` paths).
- Keep the Makefile's delegation split (CLAUDE.md): lifecycle targets delegate via `$(MAKE) -C`; only `ps`/`logs` use the `compose` helper.
- README.md and CLAUDE.md must stay in sync with the behavior change (CLAUDE.md's own rule) — the spec lists README; CLAUDE.md sync is added here as Task 2.
- Local gate before pushing (deploy has lint-only CI): `shellcheck scripts/*.sh`, `make help >/dev/null && make -n ps >/dev/null`, `yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/`.

---

### Task 1: Makefile wiring

**Files:**
- Modify: `Makefile`

**Interfaces:**
- Consumes: obs-plane's member contract — `make network volumes / up / up-dev / down / bundle` targets, `docker/compose.yaml`, `.env`; `prometheus` service-name alias on `data-net`.
- Produces: `OBS_DIR` make/env variable (consumed by Task 2's docs and `federation.env.example`).

- [ ] **Step 1: Header + variable**

In the header comment, change the order line:

```
# Bring-up order (load-bearing, per ../CLAUDE.md): inference (vllm-service) ->
# state (data-plane) -> obs (obs-plane) -> apps. Each tier must be healthy
# before the next starts.
```

After the `OPENWEBUI_DIR ?= open-webui-service` block, before `DATA_PROFILE`:

```make
# obs-plane is the observability plane (Prometheus + Grafana + Loki) — a
# pulled-image member with a bespoke Makefile (data-plane pattern). Its tier
# comes up after state and before the apps so app bring-up is observed
# (logs land in Loki, first scrapes catch a crash-looping app). Set empty
# to run this host without observability.
OBS_DIR      ?= obs-plane
```

- [ ] **Step 2: help**

Change the `up` line and the summary line:

```make
	@echo "  make up       bring the stack up in order (inference -> data -> obs -> apps), health-gated"
```
```make
	@echo "Apps on this host: $(APP_DIRS) $(OPENWEBUI_DIR)   obs: $(if $(OBS_DIR),$(OBS_DIR),disabled)   data-plane profile: $(DATA_PROFILE)"
```

- [ ] **Step 3: setup**

After the `$(DATA_DIR) network volumes` line:

```make
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) network volumes
```

- [ ] **Step 4: up / up-dev shared recipe**

Between the state tier's `wait-healthy` line and the `== app tier ==` echo:

```make
	@[ -z "$(OBS_DIR)" ] || echo "== obs tier ($(OBS_DIR) $(MODE_UP)) =="
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) $(MODE_UP)
	[ -z "$(OBS_DIR)" ] || ./scripts/wait-healthy.sh data-net prometheus:9090
```

- [ ] **Step 5: down**

Between the app loop and the `$(DATA_DIR) down` line (reverse order — obs watches the apps shut down):

```make
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) down
```

- [ ] **Step 6: ps**

After the `$(DATA_DIR)` line, before the app loop:

```make
	@[ -z "$(OBS_DIR)" ] || { echo "== $(OBS_DIR) =="; $(call compose,$(OBS_DIR)) ps; }
```

- [ ] **Step 7: logs**

Change the loop list (an empty `$(OBS_DIR)` simply drops out of the shell word list):

```make
	@for a in $(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(APP_DIRS) $(OPENWEBUI_DIR); do echo "== $$a =="; $(call compose,$$a) logs --tail=50; done
```

- [ ] **Step 8: pull**

Change the `addprefix` list (empty word is safely dropped by make):

```make
	for r in . $(addprefix $(INFRA_ROOT)/,$(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(APP_DIRS) $(OPENWEBUI_DIR)); do \
```

- [ ] **Step 9: bundle**

After the data-plane line:

```make
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) bundle
```

- [ ] **Step 10: load**

Fold `OBS_DIR` into the existing `foreach` (NOT a bare `$(INFRA_ROOT)/$(OBS_DIR)/*.tar.gz` — with an empty var that glob would become `$(INFRA_ROOT)//*.tar.gz` and match the workspace root; `foreach` over an empty word list produces nothing):

```make
	@found=0; for f in $(INFRA_ROOT)/$(VLLM_DIR)/*.tar.gz $(INFRA_ROOT)/$(DATA_DIR)/*.tar.gz $(foreach a,$(OBS_DIR) $(APP_DIRS) $(OPENWEBUI_DIR),$(INFRA_ROOT)/$(a)/*.tar.gz); do \
```

- [ ] **Step 11: Verify parse + both shapes dry-run**

```bash
make help
make -n up | grep -n "obs tier"          # obs block present, between state wait and app tier
make -n up OBS_DIR= | grep -c "obs"      # expect 0 — tier fully skipped
make -n down | sed -n '1,6p'             # first lines: app downs, then obs-plane down, then data-plane
make -n ps >/dev/null && make -n load >/dev/null
```
Expected: all exit 0; the greps show the obs block exactly between state and apps in `up`, absent with `OBS_DIR=`, and `down` in exact reverse order.

- [ ] **Step 12: Lint gate + commit**

```bash
shellcheck scripts/*.sh
grep -rnE '/(Users|home)/[A-Za-z0-9_.-]+' . --exclude-dir=.git || echo clean
git add Makefile && git commit -m "feat: obs tier (obs-plane) — ordered bring-up, health gate, full target fan-out"
```

---

### Task 2: federation.env.example + README + CLAUDE.md sync

**Files:**
- Modify: `federation.env.example`, `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: `OBS_DIR` variable and tier behavior from Task 1 (guarded lines, `prometheus:9090` gate on `data-net`, mode-sensitive `up-dev`).

- [ ] **Step 1: federation.env.example**

After the `DATA_DIR=data-plane` line:

```
# Observability plane (Prometheus + Grafana + Loki). Pulled-image member,
# bespoke Makefile. Set empty to run this host without observability.
OBS_DIR=obs-plane
```

- [ ] **Step 2: README.md**

Exact edits (keep surrounding text):

1. On-host layout tree — insert after the `data-plane/` line:
   ```
     obs-plane/         # observability tier (Prometheus, Grafana, Loki)
   ```
2. "Bring-up order (load-bearing)" — first line becomes:
   `` `inference (vllm-service) → state (data-plane) → obs (obs-plane) → apps`. `` and extend the `make up` sentence: after "waits for `neo4j:7687` + `qdrant:6333` on `data-net`," insert "brings up `obs-plane` and waits for `prometheus:9090` on `data-net`," before "then brings up the apps."
3. Targets table — `up` row order text becomes "Inference → state → obs → apps (incl. `open-webui-service`)…"; `down` row becomes "Apps (incl. `open-webui-service`) → obs → state → inference…"; `bundle` row appends "+ obs-plane (`OBS_DIR`)".
4. Airgap flow section — after the sentence about members producing tarballs, add: "obs-plane's `obs-plane-pulled-<version>.tar.gz` is included in the fan-out."
5. New short section after the open-webui-service one:

   ```markdown
   **obs-plane is the obs tier, via `OBS_DIR`.** The observability plane
   (Prometheus + Grafana + Loki; pulled images, bespoke Makefile) comes up
   after state and before the apps, so app bring-up is observed. The health
   gate probes `prometheus:9090` over `data-net` (prometheus carries its
   service-name alias there); Grafana and Loki live on obs-plane's internal
   network — use `make -C ../obs-plane health` for the deep check. In
   production shape it publishes no host ports; `make up-dev` publishes
   Grafana (see obs-plane's README). Set `OBS_DIR` empty in `federation.env`
   to run without observability.
   ```

- [ ] **Step 3: CLAUDE.md sync**

1. "The load-bearing invariant" section — order becomes
   `inference (vllm-service) → state (data-plane) → obs (obs-plane) → apps`, and add one sentence: "The obs tier is gated on `prometheus:9090` over `data-net`; it is optional (`OBS_DIR` empty skips it) but when present its position is fixed."
2. "The central design split" item 1 — extend the bundle/load sentence's member list with "+ `obs-plane` (via `OBS_DIR`; bespoke, data-plane pattern)".
3. "Cross-repo contract" — add `OBS_DIR` to the variable list sentence; note obs-plane honors the same `network`/`volumes`/`down`/`bundle` contract with a bespoke Makefile.
4. "Configuration" — add `OBS_DIR` to the knob list ("the observability plane; set empty to disable").
5. Commands block — `make up` comment line gains the four-tier order.

- [ ] **Step 4: Verify + commit**

```bash
make -n ps >/dev/null   # README/CLAUDE edits can't break make, but re-run the gate anyway
yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
grep -rnE '/(Users|home)/[A-Za-z0-9_.-]+' . --exclude-dir=.git || echo clean
git add federation.env.example README.md CLAUDE.md
git commit -m "docs: obs tier in runbook, host profile, and CLAUDE.md"
```

---

### Task 3: Live partial verification + PR

**Files:** none (verification; fix-forward commits only if something fails).

- [ ] **Step 1: Live gate check** (dev machine: data-plane is running; inference can't run here)

```bash
make -C ../obs-plane up
./scripts/wait-healthy.sh data-net prometheus:9090
```
Expected: "prometheus:9090 reachable." If the network probe reports "bad address": prometheus isn't attached to data-net — that would be an obs-plane bug, stop and report BLOCKED.

- [ ] **Step 2: OBS_DIR-empty live check + teardown**

```bash
make -n up OBS_DIR= >/dev/null && echo "skip-shape OK"
make -C ../obs-plane down
```

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feature/obs-tier
gh pr create --title "deploy: obs tier (obs-plane) wiring" --body "Wires obs-plane into the federation lifecycle per docs/2026-07-22-obs-tier-wiring-design.md: bring-up order inference -> data -> obs -> apps with a wait-healthy gate on prometheus:9090 over data-net; reverse-order down; setup/ps/logs/pull/bundle/load fan-out; OBS_DIR host knob (empty = disabled). README + CLAUDE.md synced. Live-verified: obs tier gate passes against a running obs-plane + data-plane; empty-OBS_DIR shape dry-run clean."
gh pr checks --watch
```
Expected: CI (shellcheck/yamllint/Makefile-parse) green. If red: fix minimally, commit, re-watch (max 2 attempts, else BLOCKED with logs).
