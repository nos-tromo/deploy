# Federation lifecycle — brings the nos-tromo stack up/down on a single host in
# dependency order, health-gated. SCAFFOLD: validate the health probes and the
# host profile against your real deployment before relying on it.
#
# Bring-up order (load-bearing, per ../CLAUDE.md): inference (vllm-service) ->
# state (data-plane) -> apps. Each tier must be healthy before the next starts.
#
# Assumes the member repos sit as siblings of this one (the infra/ workspace
# layout). Override INFRA_ROOT / the dir + app lists in federation.env.
#
# NOTE: every member's `make up` is now detached + `--no-build` (the apps via
# common.mk v3.2; data-plane/open-webui via their bespoke Makefiles), so this
# layer delegates `make up` per tier — like it already does for
# network/volumes/down/bundle — instead of driving compose directly. Only
# `ps`/`logs` still use the compose helper below (there is no uniform `ps`
# target, and `make logs` follows with -f, which a sequencer can't chain).

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
# federation entirely. (deploy itself has no images.)
OPENWEBUI_DIR ?= open-webui-service
DATA_PROFILE ?= cpu

# Production-shape compose invocation for a member repo. $(1) = repo dir.
compose = docker compose --env-file $(INFRA_ROOT)/$(1)/.env -f $(INFRA_ROOT)/$(1)/docker/compose.yaml

.PHONY: help setup up down ps logs bundle load

help:
	@echo "Federation lifecycle (single host). Member repos under INFRA_ROOT=$(INFRA_ROOT)."
	@echo
	@echo "  make setup    create external networks + volumes for every tier (idempotent)"
	@echo "  make up       bring the stack up in order (inference -> data -> apps), health-gated"
	@echo "  make down     stop the stack in reverse order (never removes data volumes)"
	@echo "  make ps       service status across all tiers"
	@echo "  make logs     tail logs across all tiers"
	@echo "  make bundle   run 'make bundle' in every image-bearing member repo"
	@echo "  make load     docker load every *.tar.gz found under the member repos"
	@echo
	@echo "Apps on this host: $(APP_DIRS) $(OPENWEBUI_DIR)   data-plane profile: $(DATA_PROFILE)"

# One-time host setup. Each repo knows its own networks/volumes (common.mk).
setup:
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) network volumes
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) network volumes
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do $(MAKE) -C $(INFRA_ROOT)/$$a network volumes; done

up: setup
	@echo "== inference tier (vllm-service) =="
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) up
	./scripts/wait-healthy.sh inference-net vllm-router:4000
	@echo "== state tier (data-plane, profile=$(DATA_PROFILE)) =="
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) up PROFILE=$(DATA_PROFILE)
	./scripts/wait-healthy.sh data-net neo4j:7687 qdrant:6333
	@echo "== app tier =="
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo ">> $$a"; $(MAKE) -C $(INFRA_ROOT)/$$a up; done
	@echo "federation up."

# Reverse order; delegates to each repo's `down` (never touches data volumes —
# only data-plane's `make nuke` can, per the workspace invariant).
down:
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do $(MAKE) -C $(INFRA_ROOT)/$$a down; done
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) down
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) down

ps:
	@echo "== $(VLLM_DIR) =="; $(call compose,$(VLLM_DIR)) ps
	@echo "== $(DATA_DIR) =="; $(call compose,$(DATA_DIR)) ps
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo "== $$a =="; $(call compose,$$a) ps; done

logs:
	@for a in $(VLLM_DIR) $(DATA_DIR) $(APP_DIRS) $(OPENWEBUI_DIR); do echo "== $$a =="; $(call compose,$$a) logs --tail=50; done

bundle:
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) bundle
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) bundle PROFILE=$(DATA_PROFILE)
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo ">> $$a"; $(MAKE) -C $(INFRA_ROOT)/$$a bundle; done

# Airgapped host: load every image tarball produced by `make bundle`.
load:
	@found=0; for f in $(INFRA_ROOT)/$(VLLM_DIR)/*.tar.gz $(INFRA_ROOT)/$(DATA_DIR)/*.tar.gz $(foreach a,$(APP_DIRS) $(OPENWEBUI_DIR),$(INFRA_ROOT)/$(a)/*.tar.gz); do \
	  [ -e "$$f" ] || continue; found=1; echo ">> docker load -i $$f"; docker load -i "$$f"; \
	done; [ $$found -eq 1 ] || echo "no *.tar.gz found under the member repos — run 'make bundle' on the build host first."
