# Runbook: bring-up and the make targets

What `make up` actually does tier by tier, and the full reference for every
target. The [README](../../README.md#bring-up-order-load-bearing) states the
order and why it is load-bearing; this is the detail behind it.

## What `make up` enforces

`make up` enforces this: it brings up `vllm-service`, waits for `vllm-router:4000`
on `inference-net`, brings up `data-plane` in its plain production shape (no
published ports; an admin opens the loopback-only Qdrant dashboard port on
demand with data-plane's recorded `make up-admin` — see data-plane's README
§ Admin access), waits for `qdrant:6333` on `data-net`, brings up `obs-plane`
and waits for `prometheus:9090` on `data-net`, then brings up the apps, and finally brings up `edge-plane` (production `up`, in
both `up` and `up-dev`) and waits for `caddy:443` on `edge-net` — it is the last
tier up because it is the federation's public entry point, fronting everything
behind it.

All three external network seams (`inference-net`, `data-net`, `edge-net`)
are created by `make setup` before any tier starts — every member's own
`make network` creates the seams it joins, so an app tier can never fail
on a missing `edge-net` even though the edge tier itself comes up last.

`down` is the exact reverse, and never passes `-v`. Only `data-plane`'s own
`make nuke` may destroy state.

## Retention periods

Stored data is deleted after the configured retention period, on by default;
each member applies it to what it keeps and lists what falls due in the app
for the notice period first. The settings, in each member's `.env`:

| Member | Setting | Default | What it deletes |
|---|---|---|---|
| docint | `DOCUMENT_RETENTION_DAYS` | `365` | Each document, that many days after its ingest; chats, reports, extracts and topic runs built from it no later |
| open-webui-service | `OPEN_WEBUI_CHAT_RETENTION_DAYS` | `365` | Each chat, that many days after it was started, with its files |
| Nextext | `NEXTEXT_CLI_RETENTION_DAYS` | `365` | Command-line inputs and results, that many days after they were stored |
| data-plane | `BACKUP_RETENTION_DAYS` | `30` | Backups, that many days after they were taken ([backups.md](backups.md)) |
| each of the above | `RETENTION_NOTICE_DAYS` | `30` | — the notice before a deletion, and the grace a new or shorter period gives; changing it alone moves nothing |

docint, open-webui-service and Nextext also accept `off`, for development only,
logged as a warning; data-plane does not. An invalid value stops the app at
startup, and makes data-plane refuse the backup run. The first start of a
release that brings a period, and switching it on again after `off`, deletes
nothing for the notice period.

A changed period never deletes sooner than announced: a longer one applies at
once, also to what was due under the shorter one but not yet deleted; a
shorter one gives the notice only to what it makes due sooner. Changing the
period back and forth postpones nothing. The design is
[2026-11-05-retention-deadline-design.md](../2026-11-05-retention-deadline-design.md);
each member's docs give the details.

## Identity: edge secrets and second factors

Two one-time preparations belong to a host's setup, both described in
edge-plane's docs:

- **Edge secrets.** The gateway sends each app its own `X-Edge-Secret`,
  and the app trusts the `X-Auth-*` identity headers only when it matches
  (edge-plane `docs/identity-contract.md` § Edge secret). Generate one value
  per app with edge-plane's `make secret`, and put it into edge-plane's
  `.env` and the app's `.env` under the same name: `DOCINT_EDGE_SECRET`,
  `NEXTEXT_EDGE_SECRET`, `TRANSLATOR_EDGE_SECRET`, `OPEN_WEBUI_EDGE_SECRET`.
  obs-plane's two Grafanas work the same way, with the values in obs-plane's
  `.env`: `GRAFANA_EDGE_SECRET` (optional)
  and `AUDIT_EDGE_SECRET` (required: without it the audit-trail viewer
  refuses every request). The one service-to-service caller, docint →
  Nextext, uses a token instead: the same value in Nextext's
  `NEXTEXT_SERVICE_TOKENS` (as `docint=<token>`) and docint's
  `NEXTEXT_API_KEY`. `make up` starts the gateway last, so an app that
  enforces its secret never waits on a gateway that does not send it yet.
- **Second factors.** Logins need a password and a TOTP code, and TOTP is
  provisioned by an admin: `make totp U=<user>` in edge-plane for every
  user, before running with `EDGE_AUTH_POLICY=two_factor` (the default).
  edge-plane `docs/user-accounts.md` § Second factor explains why users
  cannot enrol through the portal.

## Targets

`make help` prints the same list, plus the apps configured on this host.

| Target | What it does |
|---|---|
| `setup` | Delegates `make network volumes` to every tier (idempotent). |
| `up` | Inference → state → obs → apps (incl. `open-webui-service`) → edge, each via the member's own `make up` (detached, `--no-build`), health-gated. The state tier comes up without published ports; the loopback-only dashboard port for SSH-tunnelled admin access opens only on demand, recorded, via data-plane's `make up-admin`. |
| `up-dev` | Same order + health gates as `up`, but the state + obs + app tiers come up via their own `make up-dev` (publishing host ports for local dev); inference and edge stay on production `up`. |
| `down` | Edge → apps (incl. `open-webui-service`) → obs → state → inference, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `clone` | Clones every federation member missing under `INFRA_ROOT`, from `$(GIT_REMOTE)/<dir>.git` (`GIT_REMOTE` defaults to the nos-tromo GitHub account; set it to an SSH prefix or an internal mirror in `federation.env`). Existing directories are skipped untouched — refreshing is `pull`'s job — so the target is idempotent. A failed clone warns and the loop continues, exiting non-zero at the end. Clones are never shallow, because members' `make bundle` needs reachable tags. `deploy` itself, `infra-ui`, and `pr-notify` are not cloned. |
| `pull` | Switches every federation repo (deploy itself + all members) to `main` and pulls from GitHub (`--ff-only`; a dirty/diverged repo is skipped with a warning, and the target exits non-zero at the end if any repo was skipped). `infra-ui` is not a member and is not pulled. |
| `bundle` | Runs `make bundle` in every image-bearing member — `APP_DIRS` apps + vllm-service + data-plane (active profile) + open-webui-service (`OPENWEBUI_DIR`) + obs-plane (`OBS_DIR`) + edge-plane (`EDGE_DIR`) — then saves the `wait-healthy.sh` probe image (digest-pinned via `WAIT_PROBE_PIN`) as `wait-probe-image.tar.gz` in this repo. A member whose bundle for the current release already exists — clean tree, `.<slug>-version` recording the latest reachable tag, tarball present — is not rebuilt but switched to that tag (detached, unless HEAD already is the tag's commit), with a `>> <member>: bundle <ver> already present — switched to <ver>, skipping rebuild` line; `BUNDLE_FORCE=1` forces the full fan-out. Either way every member ends on its release tag — matching the tarballs for `copy-bundles.sh` — until `make pull` returns it to `main`; under `make -n` the switch is only reported. Every image-bearing member writes that record from the vendored `scripts/bundle-lib.sh` (`bundle_version` → `.<slug>-version`, gitignored), so the skip applies to all of them; a `*VERSION_OVERRIDE` in the environment disables it for that run. |
| `load` | `docker load` every `*.tar.gz` found under deploy + the member repos. |
| `prune` | Removes the federation images that no container uses — an image any container holds, running or stopped, stays — via `scripts/prune-images.sh`. By default: every image a member's compose projects built. Compose stamps `com.docker.compose.project=<name>` on each image it builds, and `<name>` is the `name:` of each standalone compose file of the member — `docker/compose.yaml` plus any `docker/compose.*.yaml` declaring its own `name:`, such as vllm-service's CPU-only shapes (`vllm-service-<shape>-only`); overlays declare none and are skipped — so every old release tag, superseded dev build and untagged rebuild leftover goes. `PRUNE_UPSTREAM=1` also removes every unused version of the upstream images the members pull (qdrant, grafana, open-webui, litellm, …). Those carry no federation label, so they match by repository (each member's image list, read with its `.env`): an unused image of the same upstream repository pulled by another project goes too, while a reference outside the federation (say `mycorp/x:1`) keeps its image alive. Never touched: the health-probe image, other projects' images, containers, networks, volumes. No confirm prompt. Removed images come back with `make load`; `make prune bundle BUNDLE_FORCE=1` is a full rebuild (a plain `bundle` skips members whose tarball already exists, so nothing would be rebuilt). Preview: `docker image ls --filter label=com.docker.compose.project=<name>` (built) or `docker image ls <repo>` (upstream). A member whose compose file can't be read is skipped with a warning, and the target exits non-zero at the end. |

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
