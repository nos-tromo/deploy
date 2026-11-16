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
and waits for `prometheus:9090` on `data-net`, brings up `workbench` and waits
for `workbench-api:8000` on `workbench-net`, then brings up the apps, and finally brings up `edge-plane` (production `up`, in
both `up` and `up-dev`) and waits for `caddy:443` on `edge-net` — it is the last
tier up because it is the federation's public entry point, fronting everything
behind it.

All four external network seams (`inference-net`, `data-net`,
`workbench-net`, `edge-net`) are created by `make setup` before any tier
starts — every member's own `make network` creates the seams it joins, so an
app tier can never fail on a missing `edge-net` even though the edge tier
itself comes up last.

`down` is the exact reverse, and never passes `-v`. Only `data-plane`'s own
`make nuke` may destroy state.

## The workbench tier

workbench ([ADR 0005](../decisions/0005-workbench-shared-tool-tier.md)) is
the shared tool tier: its `api` on `workbench-net` and its `executor`, which
has no network. It comes up after obs and before the apps. Its own compose
file starts the `api` only once the `executor` is healthy, and the api opens
port 8000 only after its own start-up, so the gate on `workbench-api:8000`
passes once both have started. The api's `/healthz` needs no key:
`make health` in workbench asks it from inside the container. Open WebUI
starts without workbench; only its workbench tools need it. `WORKBENCH_DIR`
empty in `federation.env` skips the tier.

**The caller key.** The api refuses to start without an
`OPEN_WEBUI_WORKBENCH_KEY` of at least 32 characters (workbench
`docs/configuration.md`); the gate then times out after `WAIT_TIMEOUT` with a
refused connection. From workbench v0.2.0 the api starts with either
`OPEN_WEBUI_WORKBENCH_KEY` or `DOCINT_WORKBENCH_KEY` (at least 32 characters
each), refuses to start with neither, and `make env ENV_GENERATE=1` also writes
`DOCINT_WORKBENCH_KEY` into workbench's and docint's `.env` (group
`workbench-docint-key`). The same command generates `OPEN_WEBUI_WORKBENCH_KEY` into
workbench's and open-webui-service's `.env` (env-sync group `workbench-key`).
Until open-webui-service's example carries the key (its workbench release),
only one member of that group documents it, so the group is skipped and
nothing is generated. Then set the same value by hand in both `.env` files
(`openssl rand -hex 32`), or set `WORKBENCH_DIR=` empty.

**Host requirements.** The executor needs cgroup v2 and a cgroup namespace of
its own, Docker's default on cgroup v2. It refuses to start on cgroup v1,
under `cgroupns: host` (the daemon's `default-cgroupns-mode`), and on a
rootless daemon without the memory controller delegated to it. Check a host
once, before its first `make up`:

```bash
docker info --format '{{.CgroupVersion}}'     # must print 2
docker info --format '{{.SecurityOptions}}'   # lists name=cgroupns; name=rootless needs memory delegation
grep -s default-cgroupns-mode /etc/docker/daemon.json   # nothing, or "private"
```

A refusing executor stops the bring-up at the workbench tier; its log
(`make logs`) names the reason in one line. Under a user-namespaced daemon
(userns-remap, or rootless with the memory controller delegated) the executor
starts, but `/dev/zero` stays writable to jobs, with the consequence described
in [userns-remap.md](userns-remap.md#known-interactions).

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
| edge-plane | `EDGE_AUTH_RETENTION_DAYS` | `30` | Authelia's login records, that many days after they were recorded or ended: login attempts once `auth-audit` exported them to the audit trail, one-time codes, identity verifications, the TOTP history, ended bans (edge-plane `docs/audit.md#retention`) |
| each of the above | `RETENTION_NOTICE_DAYS` | `30` | — the notice before a deletion, and the grace a new or shorter period gives; changing it alone moves nothing |
| workbench | `WORKBENCH_RETENTION_DAYS` | `7` | Each workspace file and job record, that many days after it was made; no notice period (below) |

docint, open-webui-service, Nextext and edge-plane also accept `off`, for
development only, logged as a warning; data-plane does not. An invalid value
stops the app at startup, and makes data-plane refuse the backup run. The
first start of a release that brings a period, and switching it on again
after `off`, deletes nothing for the notice period.

edge-plane's period has **no notice period** (`RETENTION_NOTICE_DAYS` does
not apply): users have nothing to save from Authelia's login records, and
every login attempt is in the audit trail before it can be deleted. The
first start of edge-plane v0.16.0 therefore deletes the backlog older than
the period at once — exported attempts only. Deploy obs-plane v0.13.0 first
or together with it: its verifier must list `edge-plane/auth-retention`, or
that service's records are reported as `unlisted_source`.

A changed period never deletes sooner than announced: a longer one applies at
once, also to what was due under the shorter one but not yet deleted; a
shorter one gives the notice only to what it makes due sooner. Changing the
period back and forth postpones nothing. The design is
[2026-11-05-retention-deadline-design.md](../2026-11-05-retention-deadline-design.md);
each member's docs give the details.

workbench is the exception. It has no notice period and no `off`: a changed
`WORKBENCH_RETENTION_DAYS` applies to every file at the first sweep after a
restart, so a shorter one deletes at once what it makes due (workbench
`docs/retention.md`). Announce a change to the users first. Set it in
workbench's `.env`: `make env` copies it to open-webui-service, whose banner
names it ([env-sync.md](env-sync.md#shared-values)).

## Identity: edge secrets and second factors

Two one-time preparations belong to a host's setup, both described in
edge-plane's docs:

- **Edge secrets.** The gateway sends each app its own `X-Edge-Secret`,
  and the app trusts the `X-Auth-*` identity headers only when it matches
  (edge-plane `docs/identity-contract.md` § Edge secret). Generate one value
  per app with `openssl rand -hex 32`, and put it into edge-plane's
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
| `env` | Brings `federation.env` and every member's `.env` in step with its example and prints what changed; `ENV_GENERATE=1` also generates missing shared secrets. Writes the `.env` files directly, not through the members. See [env-sync.md](env-sync.md). |
| `env-check` | The same planner, writing nothing. Fails while a `.env` is behind, never synced, refused or in a shared conflict; `ENV_ALLOW_DRIFT=1` overrides. |
| `up` | Inference → state → obs → workbench → apps (incl. `open-webui-service`) → edge, each via the member's own `make up` (detached, `--no-build`), health-gated. The state tier comes up without published ports; the loopback-only dashboard port for SSH-tunnelled admin access opens only on demand, recorded, via data-plane's `make up-admin`. |
| `up-dev` | Same order + health gates as `up`, but the state + obs + workbench + app tiers come up via their own `make up-dev` (publishing host ports for local dev; workbench's api on the host's loopback only); inference and edge stay on production `up`. |
| `down` | Edge → apps (incl. `open-webui-service`) → workbench → obs → state → inference, via each repo's `make down`. Never `-v`. |
| `ps` / `logs` | Fan out across all tiers. |
| `clone` | Clones every federation member missing under `INFRA_ROOT`, from `$(GIT_REMOTE)/<dir>.git` (`GIT_REMOTE` defaults to the nos-tromo GitHub account; set it to an SSH prefix or an internal mirror in `federation.env`). Existing directories are skipped untouched — refreshing is `pull`'s job — so the target is idempotent. A failed clone warns and the loop continues, exiting non-zero at the end. Clones are never shallow, because members' `make bundle` needs reachable tags. `deploy` itself, `infra-ui`, and `pr-notify` are not cloned. |
| `pull` | Switches every federation repo (deploy itself + all members) to `main` and pulls from GitHub (`--ff-only`; a dirty/diverged repo is skipped with a warning, and the target exits non-zero at the end if any repo was skipped). `infra-ui` is not a member and is not pulled. |
| `bundle` | Runs `make bundle` in every image-bearing member — `APP_DIRS` apps + vllm-service + data-plane (active profile) + open-webui-service (`OPENWEBUI_DIR`) + obs-plane (`OBS_DIR`) + workbench (`WORKBENCH_DIR`) + edge-plane (`EDGE_DIR`) — then saves the `wait-healthy.sh` probe image (digest-pinned via `WAIT_PROBE_PIN`) as `wait-probe-image.tar.gz` in this repo. A member whose bundle for the current release already exists — clean tree, `.<slug>-version` recording the latest reachable tag, tarball present — is not rebuilt but switched to that tag (detached, unless HEAD already is the tag's commit), with a `>> <member>: bundle <ver> already present — switched to <ver>, skipping rebuild` line; `BUNDLE_FORCE=1` forces the full fan-out. Either way every member ends on its release tag — matching the tarballs for `copy-bundles.sh` — until `make pull` returns it to `main`; under `make -n` the switch is only reported. Every image-bearing member writes that record from the vendored `scripts/bundle-lib.sh` (`bundle_version` → `.<slug>-version`, gitignored), so the skip applies to all of them; a `*VERSION_OVERRIDE` in the environment disables it for that run. |
| `load` | `docker load` every `*.tar.gz` found under deploy + the member repos. |
| `prune` | Removes the federation images that no container uses — an image any container holds, running or stopped, stays — via `scripts/prune-images.sh`. By default: every image a member's compose projects built. Compose stamps `com.docker.compose.project=<name>` on each image it builds, and `<name>` is the `name:` of each standalone compose file of the member — `docker/compose.yaml` plus any `docker/compose.*.yaml` declaring its own `name:`, such as vllm-service's CPU-only shapes (`vllm-service-<shape>-only`); overlays declare none and are skipped — so every old release tag, superseded dev build and untagged rebuild leftover goes. `PRUNE_UPSTREAM=1` also removes every unused version of the upstream images the members pull (qdrant, grafana, open-webui, litellm, …). Those carry no federation label, so they match by repository (each member's image list, read with its `.env`): an unused image of the same upstream repository pulled by another project goes too, while a reference outside the federation (say `mycorp/x:1`) keeps its image alive. Never touched: the health-probe image, other projects' images, containers, networks, volumes. No confirm prompt. Removed images come back with `make load`; `make prune bundle BUNDLE_FORCE=1` is a full rebuild (a plain `bundle` skips members whose tarball already exists, so nothing would be rebuilt). Preview: `docker image ls --filter label=com.docker.compose.project=<name>` (built) or `docker image ls <repo>` (upstream). A member whose compose file can't be read is skipped with a warning, and the target exits non-zero at the end. |

`up` and `up-dev` run `env-check` first, before `setup` and before any tier
starts. `env` and `env-check` run `scripts/env_sync.py`, so `make up` needs
`python3` 3.9 or newer on the host (standard library only).

## Tier wiring

Why each optional tier sits where it does, and how it is gated:

- **obs tier** (`OBS_DIR`) — [2026-07-22-obs-tier-wiring-design.md](../2026-07-22-obs-tier-wiring-design.md).
- **workbench tier** (`WORKBENCH_DIR`) — [ADR 0005](../decisions/0005-workbench-shared-tool-tier.md);
  [The workbench tier](#the-workbench-tier) above.
- **edge tier** (`EDGE_DIR`) — [2026-07-24-edge-tier-wiring-design.md](../2026-07-24-edge-tier-wiring-design.md).
- **open-webui-service** (`OPENWEBUI_DIR`) — [ADR 0002](../decisions/0002-open-webui-lifecycle-member.md).

Why almost every target delegates to the member's own Makefile instead of
driving compose here is recorded in [CLAUDE.md](../../CLAUDE.md) § *The central
design split*.

## See also

- [releasing.md](releasing.md) — cutting the release you are bringing up.
- [airgap-transfer.md](airgap-transfer.md) — getting it onto an offline host.
