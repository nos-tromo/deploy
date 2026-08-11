# 0001 — Docker Engine as the federation container runtime

Status: accepted (2026-08-08)
Date: 2026-08-08

## Context

The federation runs twelve independent Compose projects on a **single host**,
joined by three external Docker networks (`inference-net`, `data-net`,
`edge-net`) on which services find each other by network alias. `deploy`
sequences their bring-up in tier order, health-gated. Nothing about that design
is engine-neutral by accident — it was built on Docker Compose and leans on
Compose semantics throughout.

Two pressures prompted a re-examination of that choice:

1. **BSI conformance is a stated requirement.** The relevant IT-Grundschutz
   building blocks are **SYS.1.6 (Containerisierung)** for the container
   layer, **SYS.1.3 (Server unter Linux und Unix)** for the host, and — only
   if the federation ever moves to Kubernetes — **APP.4.4 (Kubernetes)**.
   The recurring finding in German assessments of Docker deployments is that
   the daemon runs as root and membership in the `docker` group is
   root-equivalent, i.e. the container-management interface is inadequately
   restricted.
2. **Kubernetes may appear on the horizon.** Not required today, but plausible
   enough that the engine choice should not become an obstacle.

Podman is the obvious alternative: daemonless, rootless by default, and
frequently recommended precisely because it answers the root-daemon finding
by construction.

The decisive input arrived late in the discussion: **the production hosts run
Ubuntu 24.04 LTS (headless).** Had they been RHEL/Rocky/SLES, Podman would be
the vendor-supported engine and Docker CE a support-contract problem — that
fact alone would have flipped this decision. On Ubuntu it points the other way,
for reasons recorded under *Why not Podman*.

The second decisive input came from inventorying the federation's actual
posture rather than arguing about engines in the abstract. See *Implementation
notes* for the full survey; the summary is that **all 19 first-party images run
as root inside the container, with no dropped capabilities, no
`no-new-privileges`, and no read-only root filesystems anywhere in twelve
Compose files.** A rootless engine running root-inside containers with full
capabilities is not meaningfully more conformant than a rootful engine running
the same containers. The engine was never the binding constraint.

## Decision

**Keep Docker Engine as the federation container runtime.** Direct the
conformance effort at the container and host layers instead, where the actual
gap is:

- **Container hardening (engine-portable).** Non-root `USER` in all 19
  first-party Dockerfiles; `security_opt: [no-new-privileges:true]`,
  `cap_drop: [ALL]` plus explicit re-adds, and `read_only: true` with tmpfs
  mounts where the service tolerates it, across all twelve Compose projects.
  None of this work is wasted if the engine is ever revisited — it transfers
  unchanged.
- **`userns-remap` on the rootful daemon**, documented as the compensating
  control for the root-daemon finding. Container-root maps to an unprivileged
  host UID; the daemon stays root, so Ubuntu 24.04's unprivileged-userns
  restriction never enters the picture (see *Why not Podman*, point 2).
  Membership in the `docker` group remains an administrative privilege and is
  to be treated as such in the host's role model.
- **Remove the Docker socket from the observability plane.** `obs-plane`
  mounts `/var/run/docker.sock` read-only into Alloy for log discovery
  (`obs-plane/docker/compose.yaml`). Read-only or not, it hands the
  container-management API to a container and is the one directly citable
  SYS.1.6 finding in the tree. Replace with a restricted read-only socket
  proxy or file/journald-based discovery.
- **Host hardening under SYS.1.3**, using Ubuntu Pro's `usg` tooling to apply
  and report against a CIS profile. Ubuntu Pro also brings security coverage
  for `universe` packages and ESM through 2034 — relevant for airgapped hosts
  with multi-year field life and no upstream reachability.
- **Record the engine decision** (this document) so the reasoning survives
  the next time the question is raised.

The exact IT-Grundschutz control IDs are deliberately not enumerated here;
they must be mapped against the current IT-Grundschutz-Kompendium edition as
part of the conformance work rather than copied from memory into an ADR.

## Why not Podman

Not rejected on the merits — Podman's security model is genuinely better, and
on a RHEL-family host this decision would read differently. It is rejected
because on **this** platform, with **this** federation, the costs are concrete
and the benefit is obtainable by cheaper means.

1. **Podman is a second-class citizen on Ubuntu.** Noble ships Podman
   `4.9.3+ds1-1build2` from `universe` — community-maintained, outside
   standard LTS security coverage without Ubuntu Pro — and there is **no
   upstream apt repository for Ubuntu**; the Kubic/OBS builds were
   discontinued and upstream has an open request to provide a replacement.
   Podman 5.x, which is where most of the Compose-compatibility and
   rootless-networking improvements landed, is not reachable through the
   archive. Adopting Podman here means pinning an engine two major versions
   behind on a community-maintained package, and mirroring that `.deb` into
   the airgap. Docker's official Ubuntu repository is a first-class,
   mirrorable target with a vendor behind it. For hosts that sit offline for
   years, that supply-chain position matters more than the engine's security
   model.
2. **Ubuntu 24.04 restricts unprivileged user namespaces by default**
   (`kernel.apparmor_restrict_unprivileged_userns=1`) — the exact mechanism
   rootless containers depend on. It is workable on both sides (Docker's
   `docker-ce-rootless-extras` deb bundles the required AppArmor profile for
   rootlesskit; the packaged Podman carries its own), but it is live friction,
   and it makes `userns-remap` on a rootful daemon the lower-risk way to reach
   an equivalent audit position.
3. **AppArmor, not SELinux.** Podman's confinement and labelling story is
   strongest on SELinux. On Ubuntu both engines land on the AppArmor path, so
   another differentiator does not apply.
4. **Compose becomes a compatibility surface.** The realistic path is Docker
   Compose v2 driven against `podman.socket` in Docker-compat mode, and the
   federation's most load-bearing mechanisms sit exactly there: 22
   `depends_on: condition: service_healthy` gates (on which `deploy`'s entire
   health-gated bring-up rests), three *external* cross-project networks
   resolved by alias, external volumes, and `vllm-service`'s sixteen-file
   base/override/`-only` compose layout. All of it is "should work, must be
   proven" — a validation burden with no delivered feature at the end.
5. **GPU declarations do not translate.** `vllm-service` and `data-plane`
   both use `gpus: all` together with
   `deploy.resources.reservations.devices` (nvidia). Podman wants CDI
   (`nvidia.com/gpu=all`), so two repos change, plus the rootless + CDI
   caveats.
6. **Privileged ports.** `edge-plane` publishes `443`, `80` and `8443`.
   Rootless Podman cannot bind those without lowering
   `net.ipv4.ip_unprivileged_port_start`, socket activation, or running that
   project rootful — which returns part of the headline benefit at precisely
   the most exposed service.
7. **A partial migration is not available.** All twelve projects share three
   external networks on one host. Mixed engines means two disjoint network
   namespaces and no cross-project alias resolution — so this is an
   all-or-nothing switch across every repo's Makefile, bundle script, CI and
   documentation, in a single change window.

## Alternatives considered

- **Migrate the federation to Podman.** The strongest security posture per
  unit of configuration, and the right answer on a RHEL-family host. Rejected
  for the seven reasons above, of which points 1 and 7 are decisive: a
  two-major-versions-behind community package as the foundation of an
  airgapped deployment, adopted in one indivisible twelve-repo change.
- **Adopt Podman for a subset (e.g. `translator` as a canary).** Not
  possible in production — the shared external networks make mixed engines
  incoherent (point 7). A Podman spike remains valid as a *lab* exercise on a
  scratch VM, and is the prescribed evidence-gathering step should a reversal
  trigger fire.
- **Rootless Docker instead of `userns-remap`.** Reaches a similar audit
  position and is a supported configuration; the `docker-ce-rootless-extras`
  package even ships the AppArmor profile that makes it work on 24.04.
  Rejected as the default because it inherits the unprivileged-userns question
  rather than sidestepping it, and because rootless networking would need the
  same privileged-port treatment `edge-plane` requires under Podman.
  `userns-remap` gets container-root ≠ host-root with no change to the
  networking model.
- **Do nothing and argue the Docker posture as-is.** Rejected: with 19
  root-running images and full capabilities, there is no posture to argue.
- **Wait for Kubernetes and let it settle the question.** Rejected as a
  non-answer — see *Consequences* on why the engine choice is nearly
  orthogonal to a Kubernetes migration, and because the conformance
  obligation exists now.

## Consequences

- Positive: no migration risk. The bring-up ordering, health gating, alias
  discovery, GPU wiring, airgap bundles and twelve Makefiles keep working
  unchanged, and the conformance effort goes into changes that are visible to
  an assessor.
- Positive: the hardening work is engine-portable. If a reversal trigger
  fires, non-root images and dropped capabilities transfer to Podman intact —
  in fact they are a precondition for a *useful* Podman migration, so this is
  the correct first step under either future.
- Negative: the root-daemon finding is answered by a compensating control
  (`userns-remap` plus `docker` group discipline) rather than eliminated by
  construction. That is an argument to be made and documented at assessment
  time, not a property of the system. If an assessor rejects it, the fallback
  is a Podman migration with the hardening already done.
- Negative: `userns-remap` is not free. Remapped UIDs change ownership
  semantics on bind mounts and named volumes; every persistent volume in
  `data-plane`, the app stores, and the `huggingface-cache` volume must be
  re-owned or the containers lose access to their own data. This has to be
  rehearsed on a scratch host before it touches a real one, and it interacts
  with the non-root work below (both change the UID that touches the volume).
- Negative: Docker Desktop licensing on developer machines remains an open
  procurement question. It does not affect the headless production hosts —
  Docker Engine (Apache-2.0) is what runs there — but it is a cost item, not a
  conformance one, and should not be conflated with this decision.
- Neutral on Kubernetes. On a Kubernetes node the runtime is containerd or
  CRI-O; neither Docker nor Podman is present, so neither choice is an
  on-ramp. `podman kube generate` produces a sketch, not a production
  manifest. The real migration cost sits elsewhere — external networks become
  Services, external volumes become PVCs, the 22 `service_healthy` gates
  become probes and init containers, and `deploy`'s ordered bring-up becomes
  Helm/Argo ordering. What genuinely prepares for that is the hardening work
  in this decision (non-root, dropped capabilities, no socket mounts are all
  Pod Security Standards prerequisites), not the engine.
- Reversal trigger — any one of these reopens the decision:
  1. Production hosts move to a RHEL-family or SLES distribution.
  2. An assessor rejects `userns-remap` + group discipline as a compensating
     control for the root-daemon finding.
  3. Ubuntu ships a supported, current Podman (main-component, or an official
     upstream apt repository), removing the supply-chain objection.
  4. Docker licensing terms change in a way that affects Engine on servers.
  On a trigger: run the lab spike described above, then migrate wholesale in
  one window. The hardening from this decision carries over.

## Implementation notes (verified 2026-08-08)

Established by survey of the workspace on the date above, so a revisit starts
from facts rather than recollection. Counts exclude a stale `docint` git
worktree that duplicates two Dockerfiles.

**Current posture — the gap this decision addresses:**

| Finding | Extent |
|---|---|
| Dockerfiles with a non-root `USER` | 0 of 19 |
| Compose services with `no-new-privileges` / `cap_drop` / `read_only` | 0 |
| Compose services with a `user:` directive | 0 |
| Docker socket mounted into a container | 1 (Alloy, read-only) |
| `depends_on: service_healthy` gates | 22 |

First-party images by repo: `chorus` 2, `docint` 2, `Nextext` 2, `translator`
2 (backend + frontend each), `vllm-service` 11 (the model-server sidecars).

**Four concrete blockers for the non-root work**, each needing its own
treatment — this is why the hardening is a real change and not a sweep:

1. **The `huggingface-cache` volume is mounted at
   `/root/.cache/huggingface/hub`** across `vllm-service`'s per-model compose
   files. A non-root `USER` requires re-pathing that mount *and* re-owning the
   existing named volume — which on an airgapped host already holds
   transferred model weights (`scripts/pack-model.sh` /
   `scripts/unpack-model.sh`, runbook in `docs/model-transfer.md`). Sequence
   this deliberately or a hardening PR destroys a host's model cache.
2. **The four frontend images are `nginx:1.27-alpine` with `EXPOSE 80`**,
   using the official image's `/etc/nginx/templates/` entrypoint mechanism.
   Non-root means either the `nginx-unprivileged` variant (listens on 8080) or
   re-pathing pid/cache directories and changing the listen port. Either way
   the upstream port changes, so **`edge-plane`'s Caddyfile must move in the
   same wave** — its four `reverse_proxy <app>-frontend:80` lines
   (`edge-plane/caddy/Caddyfile`, the `/chorus/`, `/docint/`, `/nextext/`,
   `/translator/` site blocks) are the one cross-repo coupling in the
   hardening plan.
3. **Backends are straightforward.** All four app backends and the
   `vllm-service` sidecars run uvicorn on `:8000` (unprivileged), so a
   non-root `USER` is mostly an ownership question on `/app` and any writable
   paths.
4. **Caddy is the exception to `cap_drop: [ALL]`** — it binds `:443`/`:8443`
   inside the container and needs `NET_BIND_SERVICE` re-added explicitly.

**Not yet verified:** whether the pulled upstream images (Prometheus, Grafana,
Loki, Qdrant, Neo4j, Open WebUI, Authelia) already drop privileges by default.
Several are believed to; each must be confirmed per image during the hardening
pass rather than assumed, and pinned with an explicit `user:` where the
upstream default is root.

**External sources** for the Ubuntu-specific claims in *Why not Podman*:

- Podman 4.9.3 in noble/universe:
  [UbuntuUpdates — podman (noble)](https://www.ubuntuupdates.org/package/core/noble/universe/updates/podman)
- No upstream apt repository for Ubuntu:
  [containers/podman#28209](https://github.com/containers/podman/issues/28209),
  [podman discussion #27327](https://github.com/podman-container-tools/podman/discussions/27327)
- 24.04 unprivileged-userns restriction and the rootless workaround:
  [Ubuntu — Understanding AppArmor User Namespace Restriction](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007),
  [moby/moby#47480](https://github.com/moby/moby/issues/47480)

**Estimated change for the follow-on hardening**, one PR per repo per the
federation release workflow: 19 Dockerfiles, 12 Compose projects, one
`obs-plane` socket replacement, one coordinated `edge-plane` + frontends port
wave, and the host-level `userns-remap` runbook in `deploy/README.md`. The
volume re-ownership rehearsal, not the file edits, is the long pole.
