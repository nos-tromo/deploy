# deploy documentation

This directory holds the reference material for **deploy**, the nos-tromo
federation lifecycle layer. It complements the top-level
[`README.md`](../README.md) (which focuses on what the layer is, the on-host
layout, and the quick start) with runbooks, decision records, and the
federation-wide tech-stack overview.

## Runbooks

Step-by-step procedures, in the order an operator meets them.

| Document | What it covers |
|---|---|
| [runbooks/bring-up.md](runbooks/bring-up.md) | What `make up` does tier by tier, the health probes, and the full make-target reference |
| [runbooks/releasing.md](runbooks/releasing.md) | The release ritual: version bump → auto-minted tag → `make bundle` → staging soak → promote |
| [runbooks/airgap-transfer.md](runbooks/airgap-transfer.md) | Moving a release to an offline host: `bundle`/`load`, `copy-bundles.sh`, the version-skew guard |
| [model-transfer.md](model-transfer.md) | `pack-model.sh` / `unpack-model.sh` — moving Hugging Face model weights between hosts |
| [runbooks/userns-remap.md](runbooks/userns-remap.md) | One-time host procedure: enabling `userns-remap` on the Docker daemon (ADR 0001) |
| [runbooks/volume-reown.md](runbooks/volume-reown.md) | One-time `chown` of external volumes to the non-root container users (ADR 0001) |
| [hardening-migration.md](hardening-migration.md) | Why volume ownership is self-healing at every `up`, and what that replaced |

## Decisions

Architecture decision records. Federation-wide decisions that have no better
home are recorded here, since `deploy` is the layer that operates the whole
stack.

| Document | What it covers |
|---|---|
| [decisions/0001-container-engine-docker.md](decisions/0001-container-engine-docker.md) | Docker Engine stays the federation runtime; conformance effort goes into container/host hardening rather than a Podman migration |
| [decisions/0002-open-webui-lifecycle-member.md](decisions/0002-open-webui-lifecycle-member.md) | `open-webui-service` is a full lifecycle member, carried in its own `OPENWEBUI_DIR` variable rather than in `APP_DIRS` |

## Reference

| Document | What it covers |
|---|---|
| [tech-stack.md](tech-stack.md) | Federation-wide tech-stack overview: every repo's languages, frameworks, images, networks, CI |
| [tech-stack.de.md](tech-stack.de.md) | German translation of the above |

Design history — the dated `YYYY-MM-DD-*` design and plan files — lives
alongside these in this directory; they record how a change was reasoned about
at the time and are not maintained as current reference.

## Who this is for

- **Operators bringing the federation up on a host** — start with the
  top-level [`README.md`](../README.md) quick start, then
  [runbooks/bring-up.md](runbooks/bring-up.md).
- **Operators shipping a release to an airgapped host** —
  [runbooks/releasing.md](runbooks/releasing.md), then
  [runbooks/airgap-transfer.md](runbooks/airgap-transfer.md) and
  [model-transfer.md](model-transfer.md).
- **Host administrators preparing a new machine** —
  [decisions/0001-container-engine-docker.md](decisions/0001-container-engine-docker.md)
  for the posture, then the two one-time procedures
  [runbooks/userns-remap.md](runbooks/userns-remap.md) and
  [runbooks/volume-reown.md](runbooks/volume-reown.md), rehearsed together.
- **Anyone editing the Makefile** — read
  [`CLAUDE.md`](../CLAUDE.md) § *The central design split* first; it is the
  design detail behind every target.

## Conventions used in these docs

- **Runbooks** are named `runbooks/<topic>.md` and open with a one-line
  statement of what they are for and a link back to the section of the
  top-level README that summarizes them.
- **Decision records** are numbered `decisions/NNNN-<slug>.md` and follow
  Context / Decision / Alternatives considered / Consequences.
- **Member repo paths** are written relative to `INFRA_ROOT` (for example
  `../edge-plane/README.md`) — the federation members are siblings of
  `deploy/`.
- Documentation is plain Markdown (GitHub Flavored). No build step is required.
