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
long-lived staging branch.

`deploy` itself is versioned the same way: a one-line `VERSION` file read by the
same `release-tag` workflow, minting the tag on merge to `main`.

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
   tree instead (never promoted). Re-running `make bundle` is idempotent per
   member: one already bundled at its current tag (per its `.<slug>-version`
   file, the same record `copy-bundles.sh` checks for skew) is skipped, so a
   partially failed fan-out can be re-run without rebuilding the members that
   succeeded. `BUNDLE_FORCE=1` rebuilds everything.
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
`edge-plane-pulled-<version>.tar.gz` are included in the fan-out.
The fan-out skips members already bundled at their current release tag (see
the `bundle` row above). Two caveats: the version record carries no profile,
so after switching `DATA_PROFILE` force a data-plane rebuild with
`BUNDLE_FORCE=1`; setting a `*VERSION_OVERRIDE` disables the skip
automatically for that run.
`wait-healthy.sh` uses a throwaway `busybox` probe container; `make bundle`
saves that image too (`wait-probe-image.tar.gz` in this repo, pulled by the
digest in `WAIT_PROBE_PIN` and tagged `WAIT_PROBE_IMAGE`), and `make load`
restores it — so the health gates work offline without any extra step. If you
swap the probe image, override `WAIT_PROBE_IMAGE` and `WAIT_PROBE_PIN`
together in `federation.env`.

The **copy** step is `scripts/copy-bundles.sh <dest-dir>`: it collects, per
member, the image tarballs plus everything the airgap host needs beside them —
compose files, Makefile (+ vendored `make/`), version files, `.env.example`,
and the config directories the containers mount from the repo — into one
destination (e.g. a mounted USB stick). Member paths are derived from the
script's location (siblings of `deploy/`; override with `INFRA_ROOT=...`).
Secrets stay behind by design: edge-plane's `authelia/users.yml`, `certs/`,
and every real `.env` are never copied — they are provisioned fresh on the
airgap side.

The script also refuses to assemble a skewed transfer set. Member bundles are
built from the latest release tag, while the repo files copied beside them
come from the working tree — so a member that has moved past its last release
(new commits since the tag, or uncommitted changes) would hand the airgap
host compose files referencing images its tarball doesn't contain, and the
first `make up` there tries to pull from the internet. Each member's
`.<slug>-version` file records what its bundle was built from (release tag or
`<date>-<sha>`); on mismatch the run lists the skewed members and exits
non-zero. Re-run that member's `make bundle` (tagging a new release first if
the changes should ship), or force with `COPY_BUNDLES_ALLOW_SKEW=1`.

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
documented in edge-plane's own README (see `../edge-plane/README.md`).

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

**edge-plane is the edge tier, via `EDGE_DIR`.** The gateway (Caddy +
Authelia; pulled image, bespoke Makefile), like inference, is pinned to
production `up` in both modes — its production shape already publishes
the entry ports, so the dev overlay only adds a repo-local echo container
rather than changing what's exposed. It comes up last (gated on
`caddy:443` over `edge-net`) and goes down first, since it is the
federation's public entry point fronting everything behind it. Set
`EDGE_DIR` empty in `federation.env` to run without the gateway. Client
prerequisites — `EDGE_HOST` resolution and CA trust — are documented in
`../edge-plane/README.md`.

## Decisions

Architecture decision records live in `docs/decisions/`. Federation-wide
decisions that have no better home (host platform, container engine) are
recorded here, since `deploy` is the layer that operates the whole stack:

- `docs/decisions/0001-container-engine-docker.md` — Docker Engine stays the
  federation runtime; conformance effort goes into container/host hardening
  (`userns-remap`, non-root images, socket removal) instead of a Podman
  migration.

## Not included (deliberately)

- **A federation `compose.yaml` with `include:`** — Compose can merge the member
  projects into one for a unified `ps`/`logs` pane, but the per-repo
  override/profile/`-only`-shape matrix makes that fiddly. The Make sequencing
  above is the spine; an `include:` overlay is a possible future convenience.
- **Multi-host orchestration** — this is single-host by design. If the
  deployment grows to several hosts, that's the trigger to reach for Ansible
  (per `infra/docs/2026-06-18-federation-orchestration-design.md`), not to
  expand this Makefile.
