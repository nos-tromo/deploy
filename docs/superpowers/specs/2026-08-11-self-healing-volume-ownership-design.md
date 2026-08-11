# Design: federation-wide self-healing volume ownership

Date: 2026-08-11
Status: approved (supersedes the manual-runbook shape of ADR 0001 / PR #27)

## Problem

The hardening wave moves every first-party container to the non-root `app`
user (uid `10001`), but external volumes keep whatever ownership they were
created with — root, on a fresh `docker volume create`, or on hosts populated
before the wave. PR #27 documented a manual, per-host migration for
`huggingface-cache` only (`make migrate-cache` in vllm-service). The
federation has more affected volumes, and a manual runbook per volume:

- doesn't scale (one command per volume per host),
- silently regresses when a volume is deleted and re-created later,
- drifts: docint already solved this differently, with a boot-time one-shot.

## Decision

Adopt docint's `volume-permissions` one-shot pattern in **every** member
whose uid-10001 containers mount volumes read-write. Ownership becomes a
side effect of `make up` — no manual migration step exists anywhere.
`make migrate-cache` in vllm-service is **removed**, not deprecated: one
mechanism, zero drift.

## Scope

| Member | Volumes | Change |
|---|---|---|
| docint | docling-cache, huggingface-cache (shared), sessions-storage, source-preview-cache, pipeline-storage | none — already canonical (`ollama-cache` is deliberately excluded there: ollama is a third-party image with its own uid) |
| vllm-service | huggingface-cache | add one-shot to main compose **and** each of the seven `*-only` compose shapes; remove `migrate-cache` |
| chorus | chorus-state | add one-shot |
| Nextext | nltk-cache, spacy-cache, tmp-jobs | add one-shot |
| open-webui-service | open-webui-data | add one-shot (reuses the pulled open-webui image, which defaults to root) |
| translator | none | none |
| data-plane, obs-plane, edge-plane | third-party uids; hardening deferred | **out of scope** — chowning these volumes to 10001 would break them |

The docint one-shot already covers the shared `huggingface-cache`, but docint
boots in the app tier — after vllm — so vllm-service needs its own one-shot
for the first hardened boot to work in tier order.

## The canonical shape

Copied from docint's `docker/compose.yaml` verbatim, mounts adjusted per
member. One `volume-permissions` service per compose entrypoint whose
services mount affected volumes read-write:

- `image:` the member's own image (no new airgap import),
- `user: "0:0"`, `read_only: true`, `security_opt: [no-new-privileges:true]`,
  `network_mode: none`, `restart: "no"`,
- entrypoint:
  `find <mountpoints> -xdev ! -user 10001 -exec chown -h 10001:10001 {} +`
  — find roots are the mountpoints themselves (parent dirs live on the
  read-only image rootfs); the find form touches only wrong-owner entries,
  so a healthy boot over a multi-GB volume writes nothing,
- every service mounting those volumes gains
  `depends_on: volume-permissions: condition: service_completed_successfully`.

docint's copy is the reference implementation; reviewers of future members
diff against it rather than inventing variants.

## Snapshot story

`migrate-cache`'s mandatory tar snapshot goes away with the target. chown is
metadata-only and the find form is idempotent, so the snapshot was
belt-and-braces for a manual sudo operation, not for this. The deploy runbook
keeps an **optional** snapshot snippet (plain tar of the huggingface-cache
mountpoint) for cautious airgap operators before their first hardened boot.

## deploy changes (this repo)

- Rewrite `docs/hardening-migration.md` from manual runbook to "how
  self-healing works": keep the symptom section (the "Ignoring corrupted
  tree cache file ... Permission denied" diagnostic), procedure becomes
  "just `make up`", add the optional snapshot snippet, the scope table
  above, and name the canonical compose shape.
- Update the README pointer text to match.
- No Makefile or CLAUDE.md changes — nothing behavioral changes in deploy.

## Member changes (sibling repos, separate PRs)

Four independent PRs: vllm-service (add one-shots, remove `migrate-cache`
target + its help text), chorus, Nextext, open-webui-service (add one-shot).
docint unchanged.

## Rollout & verification

Member PRs land first (independent of each other), deploy doc PR last,
referencing them. Per member: `docker compose config` renders; on a host
with a deliberately root-owned volume, `make up` brings the stack healthy,
`find <mountpoint> ! -uid 10001` prints nothing, and vLLM logs show no
tree-cache permission spam. deploy's gate stays lint-only (shellcheck,
`make -n ps`, yamllint).

## Out of scope

- Any `user:`/`read_only:` hardening of data-plane, obs-plane, edge-plane.
- Sharing the compose fragment mechanically (compose has no `common.mk`
  equivalent; copy drift is accepted and bounded by the named reference).
- Model-weight transfer (`pack-model.sh`/`unpack-model.sh` already chown to
  the destination owner and need no change).
