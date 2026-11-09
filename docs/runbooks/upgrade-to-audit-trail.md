# Runbook: upgrading a deployment from before the audit trail

A one-time procedure for a host that still runs the releases from before the
audit trail (docint before v2.7.0) and moves to the current ones in a single
maintenance window. The transfer itself follows the
[README](../../README.md#airgap-flow) and
[airgap-transfer.md](airgap-transfer.md); this runbook adds what the jump
across the audit-trail releases needs on top. The same window also runs
[retire-chorus.md](retire-chorus.md), at the steps named below: the current
releases no longer carry what it removes.

## What moves

The release each member ran before the audit trail, and the release this
runbook targets. Check what a host actually runs in each member's
`.<slug>-version` file (open-webui-service: `.open-webui-version`).

| Member | Before | Target |
|---|---|---|
| edge-plane | v0.8.1 | v0.12.0 |
| obs-plane | v0.6.6 | v0.12.0 |
| data-plane | v0.3.7 | v0.9.0 |
| vllm-service | v0.6.0 | v0.8.1 |
| docint | v2.6.0 | v2.12.0 |
| Nextext | v1.12.12 | v1.17.0 |
| translator | v1.5.11 | v1.8.1 |
| open-webui-service | v0.4.4 | v0.11.0 |
| deploy | v0.3.5 | v0.7.0 |

Upgrade every member in the same window. `make up` starts the gateway last,
so no app enforces its edge secret while the gateway does not send it yet;
upgrading members one by one opens exactly that window (and others: Nextext
v1.15+ refuses docint's job polls without a stated purpose unless docint is
v2.10+).

## A. On the build host

1. Steps A1 and A2 of
   [retire-chorus.md](retire-chorus.md#a-on-the-build-host): `make pull` and
   `make bundle`, which leave every member on its release tag; then
   data-plane's older tarballs go.
2. `scripts/copy-bundles.sh <dest>` into an empty `<dest>`, right after,
   before any member leaves its tag. From deploy v0.5.2 on it also ships
   open-webui-service's `retention/` and `VERSION`, which the retention
   sidecar mounts.
3. Download the `age` package for the host's distribution
   (`apt-get download age`, `dnf download age`): backups now encrypt with it,
   and it is not part of any bundle. The same goes for `python3`, 3.9 or newer,
   where the host lacks it: `make env`, and so `make up`, now need it.
4. Optional: to pin model revisions (`*_MODEL_REVISION` in vllm-service), note
   the revision `fetch-model.sh` prints for each model transferred
   ([model-transfer.md](../model-transfer.md)). A pinned revision missing from
   the cache stops that service and the router.

## B. On the host, while the old releases still run

1. **Back up with the old releases:**
   - `make backup` in data-plane (plaintext under `backup/snapshots/`); move
     the files to the offline medium. They are a safety copy of the data, not
     a way back: the new data-plane never restores plaintext backups, and
     data-plane stays on its new release ([Going back](#going-back)). They
     hold Qdrant's collections and Neo4j's dump; C4 deletes Neo4j's volumes
     and then this dump, the last copy of its data
     ([retire-chorus.md](retire-chorus.md), steps B3 and B4).
   - A copy of the volumes `sessions-storage` (docint) and `open-webui-data`
     (Open WebUI v0.13.0 migrates its database on first start): the way back
     for those two apps.
2. **Generate the secrets.** `openssl rand -hex 32`, once per value: every
   value its own, at least 32 characters.

   | Value | edge-plane `.env` | Counterpart, same value |
   |---|---|---|
   | `DOCINT_EDGE_SECRET` | yes | docint `.env`, same name |
   | `NEXTEXT_EDGE_SECRET` | yes | Nextext `.env` |
   | `TRANSLATOR_EDGE_SECRET` | yes | translator `.env` |
   | `OPEN_WEBUI_EDGE_SECRET` | yes | open-webui-service `.env` |
   | `AUDIT_EDGE_SECRET` (required; no quotes, `$` or spaces) | yes | obs-plane `.env` — obs-plane does not start without it |
   | `GRAFANA_EDGE_SECRET` (optional) | yes | obs-plane `.env` |
   | docint's service token | — | Nextext `NEXTEXT_SERVICE_TOKENS=docint=<token>`, docint `NEXTEXT_API_KEY=<token>` |
   | Open WebUI's user token secret | — | open-webui-service `OPEN_WEBUI_FORWARD_JWT_SECRET`, vllm-service `AUDIT_OPENWEBUI_JWT_SECRET` |
   | `QDRANT_API_KEY` (now required by data-plane) | — | data-plane and docint; keep the existing value if the host has one |

3. **Backup encryption.** Create an age key pair offline and put the public
   key into data-plane's `backup/recipients.txt`; the file and every directory
   above it must belong to root or the backup account and be writable by
   their owner only ([backups.md](backups.md)). Install `age` on the host.
4. **Signing key for the daily anchors**, created offline
   ([audit.md](audit.md), step 3).
5. **Retention periods.** Decide them now; the defaults apply when unset.
   `AUDIT_RETENTION` (obs-plane, `8760h`) must be right before the trail holds
   data — lowering it later deletes older lines. The others are listed in
   [bring-up.md](bring-up.md#retention-periods).
6. **Just before the window:** no Nextext transcription and no docint ingest
   may be running. Nextext keeps jobs in memory, and docint is known to
   Nextext as `service:docint` from now on, so it would not see older jobs.

## C. The window

1. `make down`, with the old files
   ([retire-chorus.md](retire-chorus.md#b-on-the-host-in-the-window), step B1).
2. **Record the old examples**
   ([env-sync.md](env-sync.md#first-run-on-a-host), airgap adoption). Replace
   deploy's files with the transfer set's, and only deploy's. Take `chorus` out
   of `APP_DIRS` in `federation.env` (retire-chorus step B6 asks for it
   anyway), so `make env` neither rewrites chorus' `.env` nor keeps a copy of
   it. Then run `make env`, against the members' still-old examples: it stores
   the example each `.env` is in step with, which is what lets step 6 follow
   untouched defaults and carry renames.
3. Replace the member files with the transfer set. Leave every `.env`,
   edge-plane's `authelia/users.yml` and `certs/`, and data-plane's `backup/`
   as they are.
4. Steps B2 to B6 of
   [retire-chorus.md](retire-chorus.md#b-on-the-host-in-the-window), then
   delete data-plane's older tarballs as in its step B7.
5. `make load`.
6. **Edit every `.env`:** run `make env` again
   ([env-sync.md](env-sync.md)); then enter the values from B2 that it reports
   as unset or placeholders.
   - edge-plane: `EDGE_AUTH_POLICY=one_factor` for now. Logins otherwise need
     a TOTP code, and no user has one yet.
   - Remove what is no longer read: docint `COLLECTION_RETENTION`,
     `SOCIAL_TIMESTAMP_LINK_ENABLED`, `SOCIAL_TEXT_LINK_ENABLED`.
   - Leave `AUDIT_REQUIRE_PURPOSE` unset (docint and Nextext then require a
     purpose for `query` and `disclose`) and every `*_DEFAULT_IDENTITY`
     empty.
7. In `authelia/users.yml`, give the group `audit` to the people who review
   data access, and only to them; `admins` does not grant it.
8. `make setup`. It creates the new external volumes (`edge-audit-state`,
   `loki-audit-data`, `grafana-audit-data`, `audit-verifier-state`); run it
   after `make load`, since obs-plane needs its python image to set their
   ownership.
9. `make up`.
10. **Second factors.** `make totp U=<user>` in edge-plane for every user,
    handed over in person (edge-plane `docs/user-accounts.md`). Then set
    `EDGE_AUTH_POLICY=two_factor` and run `make up` in edge-plane; a restart
    alone keeps the old environment.

## D. Checks

- `make health` in every member: edge-plane reports `auth-audit: exporting`,
  obs-plane checks the audit store and that its guard answers `403` without
  the secret, data-plane checks `admin-audit`.
- `make audit-verify FROM=<window start> TO=<now>` in obs-plane: no
  `unlisted_source`. A single `unterminated` per restarted service is
  expected; so is a burst of events from `auth-audit` exporting the existing
  login history.
- Through the gateway: logins need the TOTP code; docint and Nextext ask for
  a case reference or reason in their purpose bar; `/audit/` opens for
  the `audit` group only. A request that bypasses the gateway gets `401`.
- What retention will delete, after the 30-day grace that starts now: docint
  `make retention-report` (and the Dashboard card), open-webui-service
  `make retention-dry-run`, Nextext `make cli-clean LEGACY=1 DRY=1`.
- A test backup: `make backup REASON="upgrade check"` in data-plane writes
  `*.age` files.
- The checks in [retire-chorus.md](retire-chorus.md#c-checks).
- Set up the nightly backup timer ([backups.md](backups.md)), the host's
  auditd rules ([host-audit.md](host-audit.md)) and, from tomorrow, the daily
  anchor ([audit.md](audit.md#daily-anchor-the-previous-day)).

## E. Afterwards

- Delete data-plane's plaintext backups from B1 once the new releases have
  proven themselves; until then they are a safety copy of Qdrant's data.
- Nextext: remove CLI leftovers from before the upgrade with
  `make cli-clean LEGACY=1`; CLI inputs go to `/tmp/nextext-cli/` from now on.
- open-webui-service: vectors of uploads from before the upgrade stay in its
  vector store until an admin runs *Reset vector storage*.

## What users notice

- Logins need a second factor.
- docint and Nextext show a purpose bar; requests that read or hand out data
  need a case reference or reason.
- docint shows what is deleted when; Open WebUI shows a banner with its
  retention period, and uploads, knowledge bases and notes are off.
- translator needs a login.

## Going back

Retention deletes nothing for 30 days. deploy and data-plane stay on v0.7.0
and v0.9.0 or later: their releases before v0.6.0 and v0.9.0 need Neo4j and
chorus, which [retire-chorus.md](retire-chorus.md) removes with their data. Older
data-plane would try to pull the neo4j image, and older deploy waits on
`neo4j:7687` and runs `make` in chorus' checkout. deploy v0.6.0 itself works,
but has no `make env`. Within those 30 days the
other members' images and `.env` can be reverted, with two limits:

- Open WebUI needs its volume copy from B1 (database migration).
- What reached the audit trail stays there.
