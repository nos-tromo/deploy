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
  chorus/ docint/ Nextext/ translator/ open-webui-service/   # app tier
  deploy/            # this repo
```

## Bring-up order (load-bearing)

`inference (vllm-service) → state (data-plane) → apps`. Each tier must be healthy
before the next starts — the apps assume the router and the databases are already
reachable on `inference-net` / `data-net`. See `../CLAUDE.md` for the invariant.

`make up` enforces this: it brings up `vllm-service`, waits for `vllm-router:4000`
on `inference-net`, brings up `data-plane`, waits for `neo4j:7687` + `qdrant:6333`
on `data-net`, then brings up the apps.

## Quick start

```bash
cp federation.env.example federation.env   # then edit (apps on this host, profile)
make setup     # one-time: external networks + volumes for every tier
make up        # ordered, health-gated bring-up (detached)
make up-dev    # dev bring-up: state + app tiers publish host ports (inference stays production)
make ps        # status across all tiers
make down      # reverse-order stop (never removes data volumes)
```

## Targets

| Target | What it does |
|---|---|
| `setup` | Delegates `make network volumes` to every tier (idempotent). |
| `up` | Inference → state → apps (incl. `open-webui-service`), each via the member's own `make up` (detached, `--no-build`), health-gated. |
| `up-dev` | Same order + health gates as `up`, but the state + app tiers come up via their own `make up-dev` (publishing host ports for local dev); inference stays on production `up`. |
| `down` | Apps (incl. `open-webui-service`) → state → inference, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `bundle` | Runs `make bundle` in every image-bearing member — `APP_DIRS` apps + vllm-service + data-plane (active profile) + open-webui-service (`OPENWEBUI_DIR`). |
| `load` | `docker load` every `*.tar.gz` found under the member repos. |

## Releasing

Releases are cut from an **annotated Git tag** on `main`. `main` is the
always-green integration trunk (GitHub Flow: short-lived `feature/*` / `fix/*`
branches → PR → CI → `main`); there is no long-lived staging branch.

1. Ensure `main` is green and carries the changes to ship.
2. Tag the release: `git tag -a vX.Y.Z -m "vX.Y.Z"` and push the tag.
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
`make load` loads them on the offline side. `wait-healthy.sh` uses a throwaway
`busybox` probe container — make sure that image is loaded on the airgap host
(or set `WAIT_PROBE_IMAGE`).

## Known integration points

**Delegated `up`** (was: foreground vs detached). Every member's `make up` is now
detached and `--no-build` — the apps via `common.mk` v3.2, `data-plane` /
`open-webui-service` via their bespoke Makefiles. So this layer **delegates
`make up`** per tier (with `PROFILE=$(DATA_PROFILE)` for `data-plane`), exactly as
it delegates `network`/`volumes`/`down`/`bundle`. `make up-dev` rides the same
delegation: the state + app tiers come up via their detached `make up-dev` (host
ports published), while inference stays pinned to production `up`. Only `ps`/`logs` still use the
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

## Not included (deliberately)

- **A federation `compose.yaml` with `include:`** — Compose can merge the member
  projects into one for a unified `ps`/`logs` pane, but the per-repo
  override/profile/`-only`-shape matrix makes that fiddly. The Make sequencing
  above is the spine; an `include:` overlay is a possible future convenience.
- **Multi-host orchestration** — this is single-host by design. If the
  deployment grows to several hosts, that's the trigger to reach for Ansible
  (per `infra/docs/2026-06-18-federation-orchestration-design.md`), not to
  expand this Makefile.
