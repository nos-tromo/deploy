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
# NOTE: several member `make up` targets are DEV-FOREGROUND (docint, Nextext,
# translator, vllm-service run `compose up` without -d). Production bring-up must
# be detached, so this layer runs `compose up -d --no-build` directly per tier
# rather than calling each repo's `make up`. It still delegates the uniform
# targets (network/volumes/down/bundle) to each repo's Makefile.

.DEFAULT_GOAL := help

# Host profile: copy federation.env.example -> federation.env and edit.
-include federation.env

INFRA_ROOT   ?= ..
VLLM_DIR     ?= vllm-service
DATA_DIR     ?= data-plane
# The uniform (common.mk) apps. open-webui-service is intentionally NOT here for
# setup/up: it kept a bespoke Makefile (`volume` singular, pulled image,
# self-creating `up -d`), so it self-manages — run
# `make -C $(INFRA_ROOT)/open-webui-service up` separately. See README.
APP_DIRS     ?= chorus docint Nextext translator
# open-webui IS bundle-able (its `make bundle` works like the rest), so it joins
# the bundle/load fan-out below. Set empty to exclude it. (deploy has no images.)
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
	@echo "Apps on this host: $(APP_DIRS)   data-plane profile: $(DATA_PROFILE)"

# One-time host setup. Each repo knows its own networks/volumes (common.mk).
setup:
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) network volumes
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) network volumes
	@for a in $(APP_DIRS); do $(MAKE) -C $(INFRA_ROOT)/$$a network volumes; done

up: setup
	@echo "== inference tier (vllm-service) =="
	$(call compose,$(VLLM_DIR)) up -d --no-build
	./scripts/wait-healthy.sh inference-net vllm-router:4000
	@echo "== state tier (data-plane, profile=$(DATA_PROFILE)) =="
	$(call compose,$(DATA_DIR)) --profile $(DATA_PROFILE) up -d --no-build
	./scripts/wait-healthy.sh data-net neo4j:7687 qdrant:6333
	@echo "== app tier =="
	@for a in $(APP_DIRS); do echo ">> $$a"; $(call compose,$$a) up -d --no-build; done
	@echo "federation up."

# Reverse order; delegates to each repo's `down` (never touches data volumes —
# only data-plane's `make nuke` can, per the workspace invariant).
down:
	@for a in $(APP_DIRS); do $(MAKE) -C $(INFRA_ROOT)/$$a down; done
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) down
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) down

ps:
	@echo "== $(VLLM_DIR) =="; $(call compose,$(VLLM_DIR)) ps
	@echo "== $(DATA_DIR) =="; $(call compose,$(DATA_DIR)) ps
	@for a in $(APP_DIRS); do echo "== $$a =="; $(call compose,$$a) ps; done

logs:
	@for a in $(VLLM_DIR) $(DATA_DIR) $(APP_DIRS); do echo "== $$a =="; $(call compose,$$a) logs --tail=50; done

bundle:
	$(MAKE) -C $(INFRA_ROOT)/$(VLLM_DIR) bundle
	$(MAKE) -C $(INFRA_ROOT)/$(DATA_DIR) bundle PROFILE=$(DATA_PROFILE)
	@for a in $(APP_DIRS) $(OPENWEBUI_DIR); do echo ">> $$a"; $(MAKE) -C $(INFRA_ROOT)/$$a bundle; done

# Airgapped host: load every image tarball produced by `make bundle`.
load:
	@found=0; for f in $(INFRA_ROOT)/$(VLLM_DIR)/*.tar.gz $(INFRA_ROOT)/$(DATA_DIR)/*.tar.gz $(foreach a,$(APP_DIRS) $(OPENWEBUI_DIR),$(INFRA_ROOT)/$(a)/*.tar.gz); do \
	  [ -e "$$f" ] || continue; found=1; echo ">> docker load -i $$f"; docker load -i "$$f"; \
	done; [ $$found -eq 1 ] || echo "no *.tar.gz found under the member repos — run 'make bundle' on the build host first."
