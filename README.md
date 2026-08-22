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

On a bare host you do not have to create that layout by hand — clone `deploy`
first, then `make clone` fills in every member repo beside it (see **Quick
start**).

## Bring-up order (load-bearing)

`inference (vllm-service) → state (data-plane) → obs (obs-plane) → apps → edge (edge-plane)`.
Each tier must be healthy before the next starts — the apps assume the router and
the databases are already reachable on `inference-net` / `data-net`. See
`../CLAUDE.md` for the invariant.

`make up` enforces this, health-gating each tier before starting the next, and
`make setup` creates all three network seams up front so no tier can fail on a
missing network. For the per-tier probes and the reverse-order `down`, see
[bring-up.md](docs/runbooks/bring-up.md#what-make-up-enforces).

## Quick start

```bash
cp federation.env.example federation.env   # then edit (GIT_REMOTE, INFRA_ROOT, apps, profile)
make clone     # bare host: clone every missing member repo under INFRA_ROOT
make setup     # one-time: external networks + volumes for every tier
make up        # ordered, health-gated bring-up (detached)
make up-dev    # dev bring-up: state + obs + app tiers publish host ports (inference & edge stay production)
make ps        # status across all tiers
make down      # reverse-order stop (never removes data volumes)
```

## Targets

`make help` prints this list plus the apps configured on this host. Full detail
for every target — flags, skip rules, exit codes — is in
[bring-up.md](docs/runbooks/bring-up.md#targets).

| Target | What it does |
|---|---|
| `setup` | Delegates `make network volumes` to every tier (idempotent). |
| `up` | Ordered, health-gated bring-up via each member's own `make up` (detached, `--no-build`). |
| `up-dev` | Same order + gates, but state + obs + apps publish host ports; inference and edge stay production. |
| `down` | Reverse order, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `clone` | Clone every missing member repo under `INFRA_ROOT` from `$(GIT_REMOTE)/<dir>.git` (never shallow). |
| `pull` | Switch every federation repo to `main` and `git pull --ff-only`. |
| `bundle` | Build every image-bearing member's airgap tarball(s) + the health-probe image; skips members already bundled at their current tag. |
| `load` | `docker load` every `*.tar.gz` found under deploy + the member repos. |

## Releasing

Releases are identified by an **annotated Git tag** on `main`, minted
automatically on merge. `main` is the always-green integration trunk (GitHub
Flow: short-lived `feature/*` / `fix/*` branches → PR → CI → `main`); there is no
long-lived staging branch. Bumping the declared version in the release PR is the
whole release action — the shared `release-tag` workflow does the rest, then
`make bundle` builds that tag and the same artifact soaks on staging before
promotion. Step-by-step: [releasing.md](docs/runbooks/releasing.md).

## Airgap flow

```
build host (online)                 airgap host (offline)
──────────────────                  ─────────────────────
make bundle  ──▶ *.tar.gz  ──copy──▶  make load   (docker load all tarballs)
                                      make setup
                                      make up
```

The copy step is `scripts/copy-bundles.sh <dest-dir>`, which also refuses to
assemble a version-skewed transfer set. See
[airgap-transfer.md](docs/runbooks/airgap-transfer.md) for what each step
moves, the probe-image handling, and the skew guard.

### What else must travel

`bundle`/`load` move only the **images** — the inference tier also needs its
**model weights** on the offline host. `scripts/pack-model.sh` /
`scripts/unpack-model.sh` tar a Hugging Face model out of the
`huggingface-cache` Docker volume on the online host and restore it into the
volume on the airgap host; see `docs/model-transfer.md` for the runbook.

Volume ownership for the hardened (uid 10001) members is self-healing: each
member's compose file ships a `volume-permissions` one-shot that fixes
wrong-owner entries at every `up` — no manual migration step, also not on
hosts populated before the hardening wave. See `docs/hardening-migration.md`.

Once the federation is up, browsers reach it at `https://<EDGE_HOST>/` — the
client-side hosts-entry/DNS setup and CA trust needed to reach that URL are
documented in edge-plane's own TLS runbook (see
`../edge-plane/docs/tls-runbook.md`).

On a brand-new host, the edge tier aborts `make up` with a clear message
until `edge-plane/authelia/users.yml` has been provisioned from its
template (see edge-plane's README quickstart).

## Hardening runbooks (ADR 0001)

The container-hardening releases (non-root images, `cap_drop`, read-only
rootfs — see `docs/decisions/0001-container-engine-docker.md`) require two
host-side, one-time procedures, documented as runbooks:

- `docs/runbooks/userns-remap.md` — enabling `userns-remap` on the daemon
  (the compensating control for the root-daemon finding). Changes the
  Docker storage root: images/volumes must be re-provisioned.
- `docs/runbooks/volume-reown.md` — per-repo table of external volumes
  that need a one-time `chown` to the new container users, with the
  snapshot-first rule and the fresh-volume trap.

Rehearse both together on a scratch host before staging/production; the
`huggingface-cache` re-own is the long pole.

## Known integration points

- **Delegated `up`** — almost every target delegates to the member's own
  Makefile rather than driving compose here; only `ps`/`logs` and the git
  targets are driven directly. The rationale is in
  [CLAUDE.md](CLAUDE.md) § *The central design split*.
- **obs tier** (`OBS_DIR`) — after state, before the apps; gated on
  `prometheus:9090`. [Design](docs/2026-07-22-obs-tier-wiring-design.md).
- **edge tier** (`EDGE_DIR`) — last up, first down; gated on `caddy:443`,
  production-pinned in both modes. [Design](docs/2026-07-24-edge-tier-wiring-design.md).
- **open-webui-service** (`OPENWEBUI_DIR`) — a full lifecycle member kept out of
  `APP_DIRS`. [ADR 0002](docs/decisions/0002-open-webui-lifecycle-member.md).

Each is disabled by setting its variable empty in `federation.env`.

## Decisions

Architecture decision records live in `docs/decisions/`. Federation-wide
decisions that have no better home (host platform, container engine) are
recorded here, since `deploy` is the layer that operates the whole stack:

- `0001-container-engine-docker.md` — Docker Engine stays the federation
  runtime; conformance effort goes into container/host hardening instead of a
  Podman migration.
- `0002-open-webui-lifecycle-member.md` — `open-webui-service` is a full
  lifecycle member, carried in `OPENWEBUI_DIR` rather than `APP_DIRS`.

## Documentation

[`docs/README.md`](docs/README.md) indexes everything in `docs/`: the runbooks
above, the decision records, and the federation-wide tech-stack overview
([`docs/tech-stack.md`](docs/tech-stack.md) — German:
[`docs/tech-stack.de.md`](docs/tech-stack.de.md)). Dated `YYYY-MM-DD-*` files
are design history, not current reference.

## Not included (deliberately)

- **A federation `compose.yaml` with `include:`** — Compose can merge the member
  projects into one for a unified `ps`/`logs` pane, but the per-repo
  override/profile/`-only`-shape matrix makes that fiddly. The Make sequencing
  above is the spine; an `include:` overlay is a possible future convenience.
- **Multi-host orchestration** — this is single-host by design. If the
  deployment grows to several hosts, that's the trigger to reach for Ansible
  (per `infra/docs/2026-06-18-federation-orchestration-design.md`), not to
  expand this Makefile.
