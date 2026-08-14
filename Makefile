# Federation lifecycle — brings the nos-tromo stack up/down on a single host in
# dependency order, health-gated. SCAFFOLD: validate the health probes and the
# host profile against your real deployment before relying on it.
#
# Bring-up order (load-bearing, per ../CLAUDE.md): inference (vllm-service) ->
# state (data-plane) -> obs (obs-plane) -> apps. Each tier must be healthy
# before the next starts.
#
# Assumes the member repos sit as siblings of this one (the infra/ workspace
# layout). Override INFRA_ROOT / the dir + app lists in federation.env.
#
# NOTE: every member's `make up` is now detached + `--no-build` (the apps via
# common.mk v3.2; data-plane/open-webui via their bespoke Makefiles), so this
# layer delegates `make up` per tier — like it already does for
# network/volumes/down/bundle — instead of driving compose directly. Each
# member's `make up-dev` is detached too, so `make up-dev` sequences a dev-shape
# bring-up the same way (state + obs + app tiers via `up-dev` to publish host ports;
# inference stays on production `up`). Only `ps`/`logs` still use the compose
# helper below (there is no uniform `ps` target, and `make logs` follows with
# -f, which a sequencer can't chain).

.DEFAULT_GOAL := help

# Host profile: copy federation.env.example -> federation.env and edit.
-include federation.env

INFRA_ROOT   ?= ..
VLLM_DIR     ?= vllm-service
DATA_DIR     ?= data-plane
# The first-party (common.mk) apps — built locally, brought up in the app tier.
APP_DIRS     ?= chorus docint Nextext translator
# open-webui-service is the upstream chat UI (pulled image, bespoke Makefile),
# kept in its own variable rather than folded into APP_DIRS because it is a
# distinct pulled-image member. It still participates in every app-tier loop
# below (setup/up/down/ps/logs) and the bundle/load fan-out — it honors the same
# network/volumes/down/bundle contract as the apps (its volume target was renamed
# from the singular `volume` to `volumes` to match). Set empty to drop it from the
# federation entirely. (deploy itself carries no service images — its `bundle`
# contributes only the wait-healthy probe image tarball, saved in this repo.)
OPENWEBUI_DIR ?= open-webui-service
# obs-plane is the observability plane (Prometheus + Grafana + Loki) — a
# pulled-image member with a bespoke Makefile (data-plane pattern). Its tier
# comes up after state and before the apps so app bring-up is observed
# (logs land in Loki, first scrapes catch a crash-looping app). Set empty
# to run this host without observability.
OBS_DIR      ?= obs-plane
# edge-plane is the federation's entry point (Caddy + Authelia) — a
# pulled-image member with a bespoke Makefile. Its tier comes up LAST
# (inference -> state -> obs -> apps -> edge): Caddy answers 502 for a
# still-warming upstream, so the position is operator ergonomics, not a
# correctness gate. Set empty to run this host without the gateway
# (LAN access then requires each member's own dev override).
EDGE_DIR     ?= edge-plane
DATA_PROFILE ?= cpu

# Account/namespace prefix the federation members are cloned from (see `clone`).
# Deliberately a PREFIX, not a full URL, so one edit switches transport for the
# whole federation: https://github.com/nos-tromo, git@github.com:nos-tromo, or an
# internal mirror. Each member's URL is $(GIT_REMOTE)/<dir>.git — repo name and
# directory name are identical for every member.
GIT_REMOTE   ?= https://github.com/nos-tromo

# Health-probe knobs consumed by wait-healthy.sh, which runs as a child process
# (not a sub-make). Export them so values set in federation.env actually reach
# the script; without this they stay make-only variables and the script falls
# back to its built-ins (180s timeout).
# WAIT_PROBE_IMAGE is the tag the probe runs under; WAIT_PROBE_PIN is the
# digest-pinned reference `bundle` pulls, re-tags as WAIT_PROBE_IMAGE, and
# saves into this repo so `load` restores the exact same image on the airgap
# host — without it every health gate tries to pull from Docker Hub and the
# bring-up times out. Override both together in federation.env if you swap
# the probe image.
WAIT_PROBE_IMAGE ?= busybox:1.37
WAIT_PROBE_PIN   ?= busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0
export WAIT_TIMEOUT WAIT_PROBE_IMAGE

# BUNDLE_FORCE is consumed by scripts/bundle-exists.sh (a child process, not a
# sub-make) — export so `make bundle BUNDLE_FORCE=1` reaches it. Command-line
# make variables are NOT exported to recipe child processes without this.
export BUNDLE_FORCE

# Production-shape compose invocation for a member repo. $(1) = repo dir.
compose = docker compose --env-file $(INFRA_ROOT)/$(1)/.env -f $(INFRA_ROOT)/$(1)/docker/compose.yaml

.PHONY: help setup up up-dev down ps logs clone pull bundle load

help:
	@echo "Federation lifecycle (single host). Member repos under INFRA_ROOT=$(INFRA_ROOT)."
	@echo
	@echo "  make clone    clone every missing federation member repo under INFRA_ROOT"
	@echo "  make setup    create external networks + volumes for every tier (idempotent)"
	@echo "  make up       bring the stack up in order (inference -> data -> obs -> apps -> edge), health-gated"
	@echo "  make up-dev   like 'up', but state + obs + app tiers publish host ports (inference & edge stay production)"
	@echo "  make down     stop the stack in reverse order (never removes data volumes)"
	@echo "  make ps       service status across all tiers"
	@echo "  make logs     tail logs across all tiers"
	@echo "  make pull     switch every federation repo (deploy + members) to main and pull from GitHub"
	@echo "  make bundle   run 'make bundle' in every image-bearing member repo (skips members already bundled at their current tag; BUNDLE_FORCE=1 rebuilds) + save the health-probe image"
	@echo "  make load     docker load every *.tar.gz found under deploy + the member repos"
	@echo
	@echo "Apps on this host: $(APP_DIRS) $(OPENWEBUI_DIR)   obs: $(if $(OBS_DIR),$(OBS_DIR),disabled)   edge: $(if $(EDGE_DIR),$(EDGE_DIR),disabled)   data-plane profile: $(DATA_PROFILE)"

# One-time host setup. Each repo knows its own networks/volumes (common.mk).
setup:
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) network volumes
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) network volumes
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) network volumes
	[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) network volumes
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do $(MAKE) -C $(INFRA_ROOT)/$$a network volumes; done

# `up` and `up-dev` share one recipe so the bring-up order + health gates can't
# drift between them. $(MODE_UP) selects `up` vs `up-dev` for the mode-sensitive
# tiers (state + obs + apps); inference (vllm-service) is pinned to production `up`
# regardless — the apps reach the router over inference-net, so its host port is
# never published, even in dev. The edge tier is pinned to production `up` like inference
# — its production shape already publishes the entry ports, and its `up-dev` overlay only
# adds a repo-local test container.
up:     MODE_UP := up
up-dev: MODE_UP := up-dev
up up-dev: setup
	@echo "== inference tier (vllm-service) =="
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) up
	./scripts/wait-healthy.sh inference-net vllm-router:4000
	@echo "== state tier (data-plane $(MODE_UP), profile=$(DATA_PROFILE)) =="
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) $(MODE_UP) PROFILE=$(DATA_PROFILE)
	./scripts/wait-healthy.sh data-net neo4j:7687 qdrant:6333
	@[ -z "$(OBS_DIR)" ] || echo "== obs tier ($(OBS_DIR) $(MODE_UP)) =="
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) $(MODE_UP)
	[ -z "$(OBS_DIR)" ] || ./scripts/wait-healthy.sh data-net prometheus:9090
	@echo "== app tier ($(MODE_UP)) =="
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo ">> $$a"; $(MAKE) -C $(INFRA_ROOT)/$$a $(MODE_UP); done
	@[ -z "$(EDGE_DIR)" ] || echo "== edge tier ($(EDGE_DIR), production up) =="
	[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) up
	[ -z "$(EDGE_DIR)" ] || ./scripts/wait-healthy.sh edge-net caddy:443
	@echo "federation up."

# Reverse order; delegates to each repo's `down` (never touches data volumes —
# only data-plane's `make nuke` can, per the workspace invariant).
down:
	[ -z "$(EDGE_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) down
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do $(MAKE) -C $(INFRA_ROOT)/$$a down; done
	[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) down
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) down
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) down

ps:
	@echo "== $(VLLM_DIR) =="; $(call compose,$(VLLM_DIR)) ps
	@echo "== $(DATA_DIR) =="; $(call compose,$(DATA_DIR)) ps
	@[ -z "$(OBS_DIR)" ] || { echo "== $(OBS_DIR) =="; $(call compose,$(OBS_DIR)) ps; }
	@[ -z "$(EDGE_DIR)" ] || { echo "== $(EDGE_DIR) =="; $(call compose,$(EDGE_DIR)) ps; }
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo "== $$a =="; $(call compose,$$a) ps; done

logs:
	@for a in $(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) $(OPENWEBUI_DIR); do echo "== $$a =="; $(call compose,$$a) logs --tail=50; done

# Populate a bare host: clone every federation member missing under INFRA_ROOT.
# Fills in gaps only — an existing directory is left untouched (refreshing is
# `pull`'s job), so the target is idempotent and safe to re-run after a partial
# network failure. A failed clone warns and the loop continues, exiting non-zero
# at the end. Never shallow: every member's `make bundle` builds the latest
# annotated tag reachable from HEAD, and a --depth clone carries no tags.
# (deploy itself is already on disk. infra-ui is a build-time pnpm git dependency
# and pr-notify is CI tooling — neither is a federation member, so neither is
# cloned.)
clone:
	@failed=""; cloned=0; skipped=0; \
	mkdir -p "$(INFRA_ROOT)"; \
	for d in $(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) $(OPENWEBUI_DIR); do \
	  if [ -d "$(INFRA_ROOT)/$$d" ]; then \
	    echo ">> $$d already present — skipping"; skipped=$$((skipped+1)); continue; \
	  fi; \
	  echo ">> $$d cloning"; \
	  git clone "$(GIT_REMOTE:%/=%)/$$d.git" "$(INFRA_ROOT)/$$d" \
	    && cloned=$$((cloned+1)) \
	    || { echo "WARNING: $$d not cloned."; failed="$$failed $$d"; }; \
	done; \
	echo "cloned $$cloned, skipped $$skipped. run 'make pull' to refresh existing repos."; \
	[ -z "$$failed" ] || { echo "WARNING: not cloned:$$failed"; exit 1; }

# Refresh every federation repo from GitHub: deploy itself (.) plus all members.
# `switch main` + `--ff-only` fail loudly on a conflicting dirty tree or
# diverged history instead of merging; a refused repo gets a WARNING and the
# loop continues, exiting non-zero at the end if any repo was skipped.
# (infra-ui is a build-time library, not a federation member, so it is not
# pulled here.)
pull:
	@failed=""; \
	for r in . $(addprefix $(INFRA_ROOT)/,$(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) $(OPENWEBUI_DIR)); do \
	  echo ">> $$r"; \
	  git -C "$$r" switch main && git -C "$$r" pull --ff-only \
	    || { echo "WARNING: $$r not updated (dirty tree or diverged history?) — skipping."; failed="$$failed $$r"; }; \
	done; \
	[ -z "$$failed" ] || { echo "WARNING: not updated:$$failed"; exit 1; }

# Each delegation is gated by scripts/bundle-exists.sh: when the member's
# .<slug>-version file matches its latest reachable tag on a clean tree and a
# tarball is present, the member is skipped with a log line (exit 0 short-
# circuits the ||); any doubt (no version file, dev bundle, moved tag, a
# *VERSION_OVERRIDE set) exits non-zero silently and the member's own `make
# bundle` runs as before. BUNDLE_FORCE=1 rebuilds everything. Caveat: the
# version record carries no profile, so after switching DATA_PROFILE force a
# data-plane rebuild with BUNDLE_FORCE=1. The probe-image step is unversioned
# and cheap — it always runs.
bundle:
	./scripts/bundle-exists.sh $(INFRA_ROOT)/$(VLLM_DIR) || $(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) bundle
	./scripts/bundle-exists.sh $(INFRA_ROOT)/$(DATA_DIR) || $(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) bundle PROFILE=$(DATA_PROFILE)
	[ -z "$(OBS_DIR)" ] || ./scripts/bundle-exists.sh $(INFRA_ROOT)/$(OBS_DIR) || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) bundle
	[ -z "$(EDGE_DIR)" ] || ./scripts/bundle-exists.sh $(INFRA_ROOT)/$(EDGE_DIR) || $(MAKE) -C $(INFRA_ROOT)/$(EDGE_DIR) bundle
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo ">> $$a"; ./scripts/bundle-exists.sh $(INFRA_ROOT)/$$a || $(MAKE) -C $(INFRA_ROOT)/$$a bundle; done
	@echo ">> wait-healthy probe image ($(WAIT_PROBE_PIN) -> $(WAIT_PROBE_IMAGE))"
	docker pull "$(WAIT_PROBE_PIN)"
	docker tag "$(WAIT_PROBE_PIN)" "$(WAIT_PROBE_IMAGE)"
	docker save "$(WAIT_PROBE_IMAGE)" | gzip > wait-probe-image.tar.gz

# Airgapped host: load every image tarball produced by `make bundle` — the
# members' plus deploy's own probe-image tarball (the bare *.tar.gz glob).
load:
	@found=0; for f in *.tar.gz $(INFRA_ROOT)/$(VLLM_DIR)/*.tar.gz $(INFRA_ROOT)/$(DATA_DIR)/*.tar.gz $(foreach a,$(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) $(OPENWEBUI_DIR),$(INFRA_ROOT)/$(a)/*.tar.gz); do \
	  [ -e "$$f" ] || continue; found=1; echo ">> docker load -i $$f"; docker load -i "$$f"; \
	done; [ $$found -eq 1 ] || echo "no *.tar.gz found under deploy or the member repos — run 'make bundle' on the build host first."
