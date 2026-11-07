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
| [model-transfer.md](model-transfer.md) | `fetch-model.sh` / `pack-model.sh` / `unpack-model.sh` — downloading Hugging Face model weights and moving them between hosts |
| [runbooks/env-sync.md](runbooks/env-sync.md) | `make env` / `make env-check`: keeping every `.env` in step with its example, the report, shared values, first run and airgap adoption, restoring a backup, the conventions for member examples |
| [runbooks/audit.md](runbooks/audit.md) | The audit trail: setup, the daily signed anchor, the verifier's alerts, exports for a review, retention |
| [runbooks/backups.md](runbooks/backups.md) | Scheduled, age-encrypted data-plane backups: the key pair kept offline, the recipients file, the systemd timer with its stated purpose, moving the files off the host |
| [runbooks/host-audit.md](runbooks/host-audit.md) | One-time host procedure: auditd rules that name who edited the account file or a `.env`, read a backup, or ran `docker` by hand; keeping those records; clock sync |
| [runbooks/userns-remap.md](runbooks/userns-remap.md) | One-time host procedure: enabling `userns-remap` on the Docker daemon (ADR 0001) |
| [runbooks/volume-reown.md](runbooks/volume-reown.md) | One-time `chown` of external volumes to the non-root container users (ADR 0001) |
| [runbooks/upgrade-to-audit-trail.md](runbooks/upgrade-to-audit-trail.md) | One-time upgrade of a host still on the releases from before the audit trail: transfer, secrets, the window, checks and the way back |
| [runbooks/retire-chorus.md](runbooks/retire-chorus.md) | One-time removal of chorus and Neo4j from a host: the old `make down`, their images, volumes and backups, the checkout and its auditd watch, the leftover settings, the older data-plane tarballs |
| [hardening-migration.md](hardening-migration.md) | Why volume ownership is self-healing at every `up`, and what that replaced |

## Decisions

Architecture decision records. Federation-wide decisions that have no better
home are recorded here, since `deploy` is the layer that operates the whole
stack.

| Document | What it covers |
|---|---|
| [decisions/0001-container-engine-docker.md](decisions/0001-container-engine-docker.md) | Docker Engine stays the federation runtime; conformance effort goes into container/host hardening rather than a Podman migration |
| [decisions/0002-open-webui-lifecycle-member.md](decisions/0002-open-webui-lifecycle-member.md) | `open-webui-service` is a full lifecycle member, carried in its own `OPENWEBUI_DIR` variable rather than in `APP_DIRS` |
| [decisions/0003-federation-audit-trail.md](decisions/0003-federation-audit-trail.md) | A separate, verifiable audit trail: two log lanes, identity first, one hash-chained event library, the audit store in obs-plane, its access and integrity model, and the phased rollout |
| [decisions/0004-retire-chorus.md](decisions/0004-retire-chorus.md) | chorus and Neo4j leave the federation: chorus was never used in production, docint covers its use, and Neo4j Community cannot separate users; data-plane runs Qdrant alone |

## Reference

| Document | What it covers |
|---|---|
| [tech-stack.md](tech-stack.md) | Federation-wide tech-stack overview: every repo's languages, frameworks, images, networks, CI |
| [tech-stack.de.md](tech-stack.de.md) | German translation of the above |

Design history — the dated `YYYY-MM-DD-*` design and plan files — lives
alongside these in this directory; they record how a change was reasoned about
at the time and are not maintained as current reference. The retention period
across the federation, for one, is
[2026-11-05-retention-deadline-design.md](2026-11-05-retention-deadline-design.md);
its operating side is in [runbooks/bring-up.md](runbooks/bring-up.md#retention-periods)
and [runbooks/backups.md](runbooks/backups.md). The `.env` sync is
[superpowers/specs/2026-11-07-env-sync-design.md](superpowers/specs/2026-11-07-env-sync-design.md);
its operating side is [runbooks/env-sync.md](runbooks/env-sync.md).

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
- **Sibling repo paths** are written as seen from the repo root — the
  federation members are siblings of `deploy/`, so the top-level README refers
  to them as `../edge-plane/...`.
- Documentation is plain Markdown (GitHub Flavored). No build step is required.
