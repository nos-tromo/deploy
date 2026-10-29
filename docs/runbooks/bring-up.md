# Runbook: bring-up and the make targets

What `make up` actually does tier by tier, and the full reference for every
target. The [README](../../README.md#bring-up-order-load-bearing) states the
order and why it is load-bearing; this is the detail behind it.

## What `make up` enforces

`make up` enforces this: it brings up `vllm-service`, waits for `vllm-router:4000`
on `inference-net`, brings up `data-plane` via its `make up-admin` (production
shape plus 7474/7687/6333 bound to `127.0.0.1` only, so an admin can reach the
Neo4j Browser and the Qdrant dashboard over an SSH tunnel — see data-plane's
README § Admin access), waits for `neo4j:7687` + `qdrant:6333`
on `data-net`, brings up `obs-plane` and waits for `prometheus:9090` on `data-net`,
then brings up the apps, and finally brings up `edge-plane` (production `up`, in
both `up` and `up-dev`) and waits for `caddy:443` on `edge-net` — it is the last
tier up because it is the federation's public entry point, fronting everything
behind it.

All three external network seams (`inference-net`, `data-net`, `edge-net`)
are created by `make setup` before any tier starts — every member's own
`make network` creates the seams it joins, so an app tier can never fail
on a missing `edge-net` even though the edge tier itself comes up last.

`down` is the exact reverse, and never passes `-v`. Only `data-plane`'s own
`make nuke` may destroy state.

## Targets

`make help` prints the same list, plus the apps configured on this host.

| Target | What it does |
|---|---|
| `setup` | Delegates `make network volumes` to every tier (idempotent). |
| `up` | Inference → state → obs → apps (incl. `open-webui-service`) → edge, each via the member's own `make up` (detached, `--no-build`), health-gated. The state tier is the exception: it comes up via data-plane's `make up-admin`, which adds the loopback-only dashboard ports for SSH-tunnelled admin access (nothing reachable from the network). |
| `up-dev` | Same order + health gates as `up`, but the state + obs + app tiers come up via their own `make up-dev` (publishing host ports for local dev); inference and edge stay on production `up`. |
| `down` | Edge → apps (incl. `open-webui-service`) → obs → state → inference, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `clone` | Clones every federation member missing under `INFRA_ROOT`, from `$(GIT_REMOTE)/<dir>.git` (`GIT_REMOTE` defaults to the nos-tromo GitHub account; set it to an SSH prefix or an internal mirror in `federation.env`). Existing directories are skipped untouched — refreshing is `pull`'s job — so the target is idempotent. A failed clone warns and the loop continues, exiting non-zero at the end. Clones are never shallow, because members' `make bundle` needs reachable tags. `deploy` itself, `infra-ui`, and `pr-notify` are not cloned. |
| `pull` | Switches every federation repo (deploy itself + all members) to `main` and pulls from GitHub (`--ff-only`; a dirty/diverged repo is skipped with a warning, and the target exits non-zero at the end if any repo was skipped). `infra-ui` is not a member and is not pulled. |
| `bundle` | Runs `make bundle` in every image-bearing member — `APP_DIRS` apps + vllm-service + data-plane (active profile) + open-webui-service (`OPENWEBUI_DIR`) + obs-plane (`OBS_DIR`) + edge-plane (`EDGE_DIR`) — then saves the `wait-healthy.sh` probe image (digest-pinned via `WAIT_PROBE_PIN`) as `wait-probe-image.tar.gz` in this repo. A member whose bundle for the current release already exists — clean tree, `.<slug>-version` recording the latest reachable tag, tarball present — is not rebuilt but switched to that tag (detached, unless HEAD already is the tag's commit), with a `>> <member>: bundle <ver> already present — switched to <ver>, skipping rebuild` line; `BUNDLE_FORCE=1` forces the full fan-out. Either way every member ends on its release tag — matching the tarballs for `copy-bundles.sh` — until `make pull` returns it to `main`; under `make -n` the switch is only reported. Every image-bearing member writes that record from the vendored `scripts/bundle-lib.sh` (`bundle_version` → `.<slug>-version`, gitignored), so the skip applies to all of them; a `*VERSION_OVERRIDE` in the environment disables it for that run. |
| `load` | `docker load` every `*.tar.gz` found under deploy + the member repos. |
| `prune` | Removes the federation images that no container uses — an image any container holds, running or stopped, stays — via `scripts/prune-images.sh`. By default: every image a member's compose projects built. Compose stamps `com.docker.compose.project=<name>` on each image it builds, and `<name>` is the `name:` of each standalone compose file of the member — `docker/compose.yaml` plus any `docker/compose.*.yaml` declaring its own `name:`, such as vllm-service's CPU-only shapes (`vllm-service-<shape>-only`); overlays declare none and are skipped — so every old release tag, superseded dev build and untagged rebuild leftover goes. `PRUNE_UPSTREAM=1` also removes every unused version of the upstream images the members pull (neo4j, qdrant, grafana, open-webui, litellm, …). Those carry no federation label, so they match by repository (each member's image list, read with its `.env`): an unused image of the same upstream repository pulled by another project goes too, while a reference outside the federation (say `mycorp/x:1`) keeps its image alive. Never touched: the health-probe image, other projects' images, containers, networks, volumes. No confirm prompt. Removed images come back with `make load`; `make prune bundle BUNDLE_FORCE=1` is a full rebuild (a plain `bundle` skips members whose tarball already exists, so nothing would be rebuilt). Preview: `docker image ls --filter label=com.docker.compose.project=<name>` (built) or `docker image ls <repo>` (upstream). A member whose compose file can't be read is skipped with a warning, and the target exits non-zero at the end. |

## Tier wiring

Why each optional tier sits where it does, and how it is gated:

- **obs tier** (`OBS_DIR`) — [2026-07-22-obs-tier-wiring-design.md](../2026-07-22-obs-tier-wiring-design.md).
- **edge tier** (`EDGE_DIR`) — [2026-07-24-edge-tier-wiring-design.md](../2026-07-24-edge-tier-wiring-design.md).
- **open-webui-service** (`OPENWEBUI_DIR`) — [ADR 0002](../decisions/0002-open-webui-lifecycle-member.md).

Why almost every target delegates to the member's own Makefile instead of
driving compose here is recorded in [CLAUDE.md](../../CLAUDE.md) § *The central
design split*.

## See also

- [releasing.md](releasing.md) — cutting the release you are bringing up.
- [airgap-transfer.md](airgap-transfer.md) — getting it onto an offline host.
