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
make ps        # status across all tiers
make down      # reverse-order stop (never removes data volumes)
```

## Targets

| Target | What it does |
|---|---|
| `setup` | Delegates `make network volumes` to every tier (idempotent). |
| `up` | Inference → state → apps, each `compose up -d --no-build`, health-gated. |
| `down` | Apps → state → inference, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `bundle` | Delegates `make bundle` to every tier (build the airgap tarballs). |
| `load` | `docker load` every `*.tar.gz` found under the member repos. |

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

**Foreground vs detached `up`.** Several member `make up` targets are
**dev-foreground** (`docint`, `Nextext`, `translator`, `vllm-service` run
`compose up` without `-d`); others detach. A sequencer can't chain a foreground
`up`, so this layer **bypasses each repo's `make up`** and runs
`compose up -d --no-build` directly per tier, while still delegating the uniform
targets (`network`/`volumes`/`down`/`bundle`). If the members later grow a
detached production `up`, this can switch back to delegating.

**open-webui-service is not in `APP_DIRS`.** It kept a bespoke Makefile (it
skipped the `common.mk` rollout): `volume` is singular, it `pull`s a single
upstream image instead of building, and its `up -d` already self-creates its
network + volume. So it doesn't fit the uniform `setup`/`up` loop. It also
self-manages, which makes it easy to run on its own:

```bash
make -C ../open-webui-service network volume   # one-time
make -C ../open-webui-service up               # detached, self-contained
```

Fold it into the federation only if you also special-case its `volume`/`pull`
interface here.

## Not included (deliberately)

- **A federation `compose.yaml` with `include:`** — Compose can merge the member
  projects into one for a unified `ps`/`logs` pane, but the per-repo
  override/profile/`-only`-shape matrix makes that fiddly. The Make sequencing
  above is the spine; an `include:` overlay is a possible future convenience.
- **Multi-host orchestration** — this is single-host by design. If the
  deployment grows to several hosts, that's the trigger to reach for Ansible
  (per `infra/docs/2026-06-18-federation-orchestration-design.md`), not to
  expand this Makefile.
