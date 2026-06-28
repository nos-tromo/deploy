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

Almost every target **delegates to each member's own Makefile** via `$(MAKE) -C`; only `ps`/`logs`
drive compose directly. Keep this split:

1. **Lifecycle + uniform targets: delegate via `$(MAKE) -C`.** `setup` (`network` + `volumes`), `up`,
   `down`, `bundle` are delegated to each member, never reimplemented here. `up` can be delegated
   because every member's `make up` is now detached + `--no-build` (apps via `common.mk` v3.2;
   `data-plane` / `open-webui-service` bespoke), so a sequencer can chain them; `data-plane` gets
   `PROFILE=$(DATA_PROFILE)`. `bundle`/`load` cover every image-bearing member — the `APP_DIRS` apps +
   `vllm-service` + `data-plane` (which `bundle` runs at `PROFILE=$(DATA_PROFILE)`) +
   `open-webui-service` (via `OPENWEBUI_DIR`; its bundle is bespoke but yields the same kind of
   tarball). Every app-tier loop (`setup`/`up`/`down`/`ps`/`logs`) iterates
   `$(APP_DIRS) $(OPENWEBUI_DIR)`, so `open-webui-service` is a full lifecycle member, not
   bundle/load-only.
2. **`ps`/`logs`: drive compose directly** via the helper
   `compose = docker compose --env-file $(INFRA_ROOT)/$(1)/.env -f $(INFRA_ROOT)/$(1)/docker/compose.yaml`.
   There is no uniform `ps` target, and `make logs` follows with `-f` (can't be chained by a
   sequencer), so these aggregate read-only views are assembled here rather than delegated.

Rule of thumb: ordered/health-gated bring-up and every uniform target → delegate to the member's Make
target; only the aggregate read-only views (`ps`/`logs`) are driven directly.

## Cross-repo contract (not visible from this repo alone)

The Makefile assumes every member listed in `VLLM_DIR` / `DATA_DIR` / `APP_DIRS` / `OPENWEBUI_DIR`:

- lives at `$(INFRA_ROOT)/<dir>/`,
- has `.env` and `docker/compose.yaml` (used by the `compose` helper above),
- exposes the `common.mk` targets `network`, `volumes`, `down`, `bundle`.

`open-webui-service` is kept in its **own variable** (`OPENWEBUI_DIR`) rather than `APP_DIRS`
because it is a distinct member — the upstream chat UI, a pulled image with a bespoke Makefile.
But it *is* a full lifecycle member: every app-tier loop (`setup`/`up`/`down`/`ps`/`logs`) plus
`bundle`/`load` iterates `$(APP_DIRS) $(OPENWEBUI_DIR)`. This became possible once its volume target
was renamed from the singular `volume` to the plural `volumes`, so it now satisfies the contract
above; keep that name aligned on the open-webui side or `setup` (`make network volumes`) breaks. It
comes up in the app tier (it attaches only to `inference-net`). Set `OPENWEBUI_DIR` empty to drop it
from the federation entirely.

## Configuration

All host-specific knobs live in `federation.env` (gitignored; copy from `federation.env.example`).
The Makefile `-include`s it. To change which apps run, where member repos live, or the data-plane
profile, **edit `federation.env`, not the Makefile**: `INFRA_ROOT`, `VLLM_DIR`, `DATA_DIR`,
`APP_DIRS`, `OPENWEBUI_DIR` (the upstream UI — a full lifecycle member, appended to every app-tier
loop + `bundle`/`load`), `DATA_PROFILE` (`cpu`|`cuda`),
and optional `WAIT_TIMEOUT` / `WAIT_PROBE_IMAGE`.

## Commands

```bash
# Operate the federation (needs the member repos present under INFRA_ROOT):
make setup     # one-time: external networks + volumes for every tier (idempotent)
make up        # ordered, health-gated bring-up, detached
make ps        # status across all tiers       make logs  # tail across all tiers
make down      # reverse-order stop (never removes data volumes)
make bundle    # build every image-bearing member's airgap tarball(s) (online build host)
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
