# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`deploy` is the **federation lifecycle layer** for the nos-tromo stack: a thin Make/shell
orchestration layer that brings the whole federation up and down on a **single host**, in
dependency order, health-gated. It owns **no services and no data** — every target sequences
*other* repos' `make`/compose lifecycles.

It is its own git repo but operates on **sibling repos** under `INFRA_ROOT` (default `..`, the
`infra/` workspace layout). The functional surface is 3 files — `Makefile` (the spine),
`scripts/wait-healthy.sh` (the health gate), `.github/workflows/ci.yml` (lint-only CI) — but the
blast radius is cross-repo: editing anything here changes how the entire federation boots. The
`README.md` is the human runbook; keep it and this file in sync when behavior changes.

> **Scaffold status.** The structure and ordering are real, but the health-probe targets in
> `wait-healthy.sh` and the host profile are **unvalidated against a real deployment**. Don't treat
> `make up` as trustworthy-unattended until those are verified on the actual host.

## The load-bearing invariant: bring-up order

`inference (vllm-service) → state (data-plane) → apps`, each tier **healthy before the next
starts**. `down` is the exact reverse. This is not a preference — the apps assume the router
(`inference-net`) and the databases (`data-net`) are already reachable when they start. `make up`
enforces it by health-gating each tier with `wait-healthy.sh` before starting the next. **Never
reorder these tiers.**

`down` **never** passes `-v` / removes data volumes. Only `data-plane`'s own `make nuke` may
destroy state. Preserve this in any teardown edit.

## The central design split (read before editing the Makefile)

The Makefile does **two different things** to member repos, and conflating them will break bring-up:

1. **Lifecycle (`up`): bypass each repo's `make up`.** Several members' `make up` are dev-foreground
   (`compose up` without `-d`: docint, Nextext, translator, vllm-service). A sequencer can't chain a
   foreground `up`, so this layer invokes compose **directly** via the helper:
   `compose = docker compose --env-file $(INFRA_ROOT)/$(1)/.env -f $(INFRA_ROOT)/$(1)/docker/compose.yaml`
   then `up -d --no-build`.
2. **Uniform targets (`setup`/`down`/`bundle`): delegate via `$(MAKE) -C`.** `network`, `volumes`,
   `down`, `bundle` ARE uniform across members (they share `common.mk`), so those are delegated to
   each repo's own Makefile, never reimplemented here.

Rule of thumb: anything that must be detached / ordered / health-gated → drive compose directly;
anything uniform and order-independent → delegate to the member's Make target. If the members later
grow a detached production `up`, the `up` target can switch back to delegating.

## Cross-repo contract (not visible from this repo alone)

The Makefile assumes every member listed in `VLLM_DIR` / `DATA_DIR` / `APP_DIRS`:

- lives at `$(INFRA_ROOT)/<dir>/`,
- has `.env` and `docker/compose.yaml` (used by the `compose` helper above),
- exposes the `common.mk` targets `network`, `volumes`, `down`, `bundle`.

`open-webui-service` is **deliberately excluded** from `APP_DIRS`: it kept a bespoke Makefile
(`volume` singular, pulls an image instead of building, self-creates its network/volume on `up -d`).
It self-manages — run `make -C ../open-webui-service up` separately. **Do not add it to `APP_DIRS`**
without also special-casing its `volume`/`pull` interface here.

## Configuration

All host-specific knobs live in `federation.env` (gitignored; copy from `federation.env.example`).
The Makefile `-include`s it. To change which apps run, where member repos live, or the data-plane
profile, **edit `federation.env`, not the Makefile**: `INFRA_ROOT`, `VLLM_DIR`, `DATA_DIR`,
`APP_DIRS`, `DATA_PROFILE` (`cpu`|`cuda`), and optional `WAIT_TIMEOUT` / `WAIT_PROBE_IMAGE`.

## Commands

```bash
# Operate the federation (needs the member repos present under INFRA_ROOT):
make setup     # one-time: external networks + volumes for every tier (idempotent)
make up        # ordered, health-gated bring-up, detached
make ps        # status across all tiers       make logs  # tail across all tiers
make down      # reverse-order stop (never removes data volumes)
make bundle    # build every tier's airgap image tarball(s)        (online build host)
make load      # docker load every *.tar.gz under the member repos (offline host)
```

### Develop / validate this repo in isolation

There is **no test suite** — CI is lint-only, and that IS the gate. Run the same three checks
locally before pushing:

```bash
shellcheck scripts/*.sh                                    # shell lint
make help >/dev/null && make -n ps >/dev/null              # Makefile parses + compose helper expands
yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
```

`make -n ps` is the key trick: it dry-runs the `$(call compose,…)` helper and prints the exact
`docker compose` lines **without needing the member repos on disk** (`ps` has no sub-make
prerequisites). Use `make -n <target>` to inspect what any change will actually run.

## Airgap flow

`make bundle` (online build host) → copy the `*.tar.gz` → `make load` (offline host) → `make setup`
→ `make up`. `wait-healthy.sh` spins up a throwaway `busybox` probe container, so that image must be
loaded on the airgap host (or override `WAIT_PROBE_IMAGE`).

## Scope boundary

Single-host by design. Multi-host orchestration is explicitly **out of scope** — that's the trigger
to reach for Ansible (per the workspace design doc), not to expand this Makefile. No federation
`compose.yaml` with `include:` either; the Make sequencing is the spine.
