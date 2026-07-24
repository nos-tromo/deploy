# nos-tromo deploy — federation lifecycle layer

The thin orchestration layer that brings the nos-tromo federation up and down on
a **single host**, in dependency order, health-gated. It is the one piece that
spans every member repo, so it lives in its own repo rather than inside any one
of them. It owns no services and no data — it sequences the members' own
`make`/compose lifecycles.

> **Status: scaffold.** The structure, ordering, and runbook are real; the
> health probes (`scripts/wait-healthy.sh` service:port targets) and the host
> profile must be validated against your actual deployment before you rely on
> `make up` unattended.

## On-host layout

The Makefile assumes the member repos sit as **siblings** of `deploy/` (the
`infra/` workspace layout). Point `INFRA_ROOT` elsewhere in `federation.env` if
your host differs.

```
<INFRA_ROOT>/
  vllm-service/      # inference tier (LiteLLM router + backends)
  data-plane/        # state tier (Neo4j, Qdrant + their volumes)
  obs-plane/         # observability tier (Prometheus, Grafana, Loki)
  chorus/ docint/ Nextext/ translator/ open-webui-service/   # app tier
  edge-plane/        # edge tier (Caddy gateway + Authelia — the entry point)
  deploy/            # this repo
```

## Bring-up order (load-bearing)

`inference (vllm-service) → state (data-plane) → obs (obs-plane) → apps → edge (edge-plane)`.
Each tier must be healthy before the next starts — the apps assume the router and
the databases are already reachable on `inference-net` / `data-net`. See
`../CLAUDE.md` for the invariant.

`make up` enforces this: it brings up `vllm-service`, waits for `vllm-router:4000`
on `inference-net`, brings up `data-plane`, waits for `neo4j:7687` + `qdrant:6333`
on `data-net`, brings up `obs-plane` and waits for `prometheus:9090` on `data-net`,
then brings up the apps, and finally brings up `edge-plane` (production `up`, in
both `up` and `up-dev`) and waits for `caddy:443` on `edge-net` — it is the last
tier up because it is the federation's public entry point, fronting everything
behind it.

All three external network seams (`inference-net`, `data-net`, `edge-net`)
are created by `make setup` before any tier starts — every member's own
`make network` creates the seams it joins, so an app tier can never fail
on a missing `edge-net` even though the edge tier itself comes up last.

## Quick start

```bash
cp federation.env.example federation.env   # then edit (apps on this host, profile)
make setup     # one-time: external networks + volumes for every tier
make up        # ordered, health-gated bring-up (detached)
make up-dev    # dev bring-up: state + obs + app tiers publish host ports (inference & edge stay production)
make ps        # status across all tiers
make down      # reverse-order stop (never removes data volumes)
```

## Targets

| Target | What it does |
|---|---|
| `setup` | Delegates `make network volumes` to every tier (idempotent). |
| `up` | Inference → state → obs → apps (incl. `open-webui-service`) → edge, each via the member's own `make up` (detached, `--no-build`), health-gated. |
| `up-dev` | Same order + health gates as `up`, but the state + obs + app tiers come up via their own `make up-dev` (publishing host ports for local dev); inference and edge stay on production `up`. |
| `down` | Edge → apps (incl. `open-webui-service`) → obs → state → inference, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `pull` | Switches every federation repo (deploy itself + all members) to `main` and pulls from GitHub (`--ff-only`; a dirty/diverged repo is skipped with a warning, and the target exits non-zero at the end if any repo was skipped). `infra-ui` is not a member and is not pulled. |
| `bundle` | Runs `make bundle` in every image-bearing member — `APP_DIRS` apps + vllm-service + data-plane (active profile) + open-webui-service (`OPENWEBUI_DIR`) + obs-plane (`OBS_DIR`) + edge-plane (`EDGE_DIR`). |
| `load` | `docker load` every `*.tar.gz` found under the member repos. |

## Releasing

Releases are identified by an **annotated Git tag** on `main`, minted
automatically on merge. `main` is the always-green integration trunk (GitHub
Flow: short-lived `feature/*` / `fix/*` branches → PR → CI → `main`); there is no
long-lived staging branch.

1. In a `release/vX.Y.Z` branch, bump the member's declared version — `pyproject.toml`
   `[project].version` (the Python apps + `vllm-service`) or the one-line `VERSION`
   file (`data-plane`, `open-webui-service`) — and, for the Python repos, run
   `uv lock` to sync the lockfile. PR → CI → merge to `main`.
2. On merge, the shared `release-tag` workflow (`nos-tromo/.github@v3`) reads the
   declared version and mints the annotated `vX.Y.Z` tag **automatically** — no
   manual `git tag`. It is idempotent (an unchanged version is a no-op) and refuses
   a version that decreased. Bumping the version in the release PR is the whole
   release action.
3. Bundle the tag: `make bundle` — each member builds from the latest annotated
   tag reachable from HEAD (it checks the tag out and restores your branch after),
   stamping its image `vX.Y.Z`. It refuses on a dirty tree or with no reachable
   tag, so a release artifact is always tag-versioned, never a dev `date+sha`. For
   pre-tag soak iteration, per-member `make bundle-dev` bundles the current working
   tree instead (never promoted).
4. Bring the tagged artifact up on a staging environment isolated from other
   workloads and exercise it end to end.
5. On success, promote the **same** artifact onward (see **Airgap flow** below).
   On failure, fix forward on `main`, tag the next patch (`vX.Y.Z+1`), and
   repeat — the failed candidate is never promoted.

## Airgap flow

```
build host (online)                 airgap host (offline)
──────────────────                  ─────────────────────
make bundle  ──▶ *.tar.gz  ──copy──▶  make load   (docker load all tarballs)
                                      make setup
                                      make up
```

Each member repo already produces its own versioned tarballs (`make bundle`,
sharing `scripts/bundle-lib.sh`); `make bundle` here just fans that out, and
`make load` loads them on the offline side. obs-plane's
`obs-plane-pulled-<version>.tar.gz` and edge-plane's
`edge-plane-pulled-<version>.tar.gz` are included in the fan-out. `wait-healthy.sh` uses a throwaway
`busybox` probe container — make sure that image is loaded on the airgap host
(or set `WAIT_PROBE_IMAGE`).

Once the federation is up, browsers reach it at `https://<EDGE_HOST>/` — the
client-side hosts-entry/DNS setup and CA trust needed to reach that URL are
documented in edge-plane's own README (see `../edge-plane/README.md`).

## Known integration points

**Delegated `up`** (was: foreground vs detached). Every member's `make up` is now
detached and `--no-build` — the apps via `common.mk` v3.2, `data-plane` /
`open-webui-service` via their bespoke Makefiles. So this layer **delegates
`make up`** per tier (with `PROFILE=$(DATA_PROFILE)` for `data-plane`), exactly as
it delegates `network`/`volumes`/`down`/`bundle`. `make up-dev` rides the same
delegation: the state + obs + app tiers come up via their detached `make up-dev` (host
ports published), while inference and the edge tier stay pinned to production `up` —
edge is never published in dev shape either, so a dev bring-up still fronts the stack
through Caddy exactly as production does. Only `ps`/`logs` still use the
compose helper directly — there is no uniform `ps` target, and `make logs`
follows with `-f`, which a sequencer can't chain.

**open-webui-service is folded in via `OPENWEBUI_DIR`, not `APP_DIRS`.** It is the
upstream chat UI — a pulled image with a bespoke Makefile (it skipped the
`common.mk` rollout) — so it is kept in its own variable rather than mixed into
the first-party `APP_DIRS`. But it is a full lifecycle member: `setup`, `up`,
`down`, `ps`, `logs`, and `bundle`/`load` all iterate `$(APP_DIRS) $(OPENWEBUI_DIR)`.
This works because it honors the same target contract as the apps — `.env`,
`docker/compose.yaml`, and the `network` / `volumes` / `down` / `bundle` targets
(its volume target was renamed from the singular `volume` to `volumes` to match).
It comes up in the app tier, attaching only to `inference-net` (like Nextext and
translator).

Set `OPENWEBUI_DIR` empty in `federation.env` to drop it from the federation
entirely. It still self-manages, so you can also run it standalone:

```bash
make -C ../open-webui-service network volumes   # one-time
make -C ../open-webui-service up                # detached, self-contained
```

**obs-plane is the obs tier, via `OBS_DIR`.** The observability plane
(Prometheus + Grafana + Loki; pulled images, bespoke Makefile) comes up
after state and before the apps, so app bring-up is observed. The health
gate probes `prometheus:9090` over `data-net` (prometheus carries its
service-name alias there); Grafana and Loki live on obs-plane's internal
network — use `make -C ../obs-plane health` for the deep check. In
production shape it publishes no host ports; `make up-dev` publishes
Grafana (see obs-plane's README). Set `OBS_DIR` empty in `federation.env`
to run without observability.

## Not included (deliberately)

- **A federation `compose.yaml` with `include:`** — Compose can merge the member
  projects into one for a unified `ps`/`logs` pane, but the per-repo
  override/profile/`-only`-shape matrix makes that fiddly. The Make sequencing
  above is the spine; an `include:` overlay is a possible future convenience.
- **Multi-host orchestration** — this is single-host by design. If the
  deployment grows to several hosts, that's the trigger to reach for Ansible
  (per `infra/docs/2026-06-18-federation-orchestration-design.md`), not to
  expand this Makefile.
