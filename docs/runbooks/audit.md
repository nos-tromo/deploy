# Runbook: the audit trail

How to set up, watch and use the federation's audit trail. The design and its
limits are in [ADR 0003](../decisions/0003-federation-audit-trail.md). The
store's own reference is obs-plane's `docs/audit.md`.

## Setup (once per host)

1. **Volumes and secrets.** obs-plane's `make volumes` creates the audit
   volumes along with the others (`make setup` here runs it). Generate
   `AUDIT_EDGE_SECRET` with edge-plane's `make secret` and put the same value
   into edge-plane's and obs-plane's `.env`; until both carry it, `/audit/`
   answers 403. If the applicable rules prescribe a period other than one
   year, set `AUDIT_RETENTION` in obs-plane's `.env` (Loki duration syntax,
   e.g. `8760h`). Choose the period before the trail holds data that must be
   kept.
2. **Who may read it.** In edge-plane's `authelia/users.yml`, add the group
   `audit` to the people who review data access, and only to them. `admins`
   does not grant it. Reading the trail is itself recorded.
3. **Signing key for anchors and exports.** Create an SSH signing key that
   never sits on the host for longer than signing takes:
   `ssh-keygen -t ed25519 -f audit-signing -C audit`. Keep the private key
   offline. Put its public key, with the signer identity you will use, into
   an `allowed_signers` file that travels with the anchors. Set
   `AUDIT_SIGNING_KEY` to the private key's path only while signing.
4. **Prerequisites from phase 1.** Every app's edge secret is set and logins
   use a second factor (edge-plane `docs/identity-contract.md`,
   `docs/user-accounts.md`). Without that, the "who" in the trail could be
   forged.
5. **Open WebUI's users at the router.** Generate one secret
   (`openssl rand -hex 32`) and put it into open-webui-service's `.env` as
   `OPEN_WEBUI_FORWARD_JWT_SECRET` and into vllm-service's `.env` as
   `AUDIT_OPENWEBUI_JWT_SECRET`, then restart both stacks. Open WebUI then
   signs a short-lived token naming the user on every model call, and the
   router records that user. Without it, the router's events for Open WebUI
   calls name no one. Use the value for nothing else; to rotate it, change it
   in both files and restart both stacks.
6. **Host-level records.** Set up auditd as in [host-audit.md](host-audit.md).
   It names who edited the account file or a `.env`, read a backup, or ran
   `docker` by hand — what no service can attribute.

`make up` brings the audit store up with the obs tier; nothing else is
sequenced differently.

### What writes to the trail

Hash-chained events come from the four apps' backends, vllm-service's router
(one `ai_call` per model call, naming Open WebUI's user when step 5 is done),
edge-plane's `auth-audit` (login attempts and account changes) and
data-plane's `admin-audit` (database shells, backups and restores).
obs-plane's verifier accepts chained lines only from these containers; adding
or renaming an emitter means adding its `project/service` pair (obs-plane
`docs/audit.md`, "Source binding"). The gateway's access log and Open WebUI's
own audit log come in as they are. Open WebUI keeps no documents: uploads,
knowledge bases and memories are off in open-webui-service.

Rules that follow from it:

- **Command-line runs go through the running backend.** The audited make
  targets (chorus `ingest`/`resolve`/`migrate`, docint `resolve`/`extract`/
  `topics`/`search-index`/…, Nextext `cli`) run with `docker compose exec`
  inside the backend, so their events land in its log; bring the stack up
  first. They pass the operator's host account as the actor, and
  `CASE_REF=` / `REASON=` as the stated purpose where an action needs one.
  A one-off container (`docker compose run --rm`) can exit before the log
  collector sees it, and its events would be lost without a trace.
  docint's `retention-report` stays a one-off container: it reads only the
  retention clock, records nothing, and can run before the backend is up.
- **Account changes need `auth-audit` up.** `make totp` refuses to change an
  account it cannot record. Hand edits of `authelia/users.yml` are recorded
  by `auth-audit` without an operator identity; the host's auditd records
  name who made them ([host-audit.md](host-audit.md)).
- **Queries and disclosures need a stated purpose.** chorus, docint and
  Nextext default to `AUDIT_REQUIRE_PURPOSE=query,disclose`; translator,
  which stores nothing, requires none unless set.
  - In the apps, the purpose bar in the header holds a case reference and a
    reason for the session; every request carries it, and a request that
    needs one and has none opens the bar.
  - Command-line runs that query or disclose need `CASE_REF=` or `REASON=`
    and stop with exit code 2 without one.
  - Nextext plays media through a short-lived grant bound to the stated
    purpose, since a player cannot send the header.
  - docint states its ingest's purpose when it fetches transcripts from
    Nextext: the user's if one was stated, else `docint ingest`.
  - To switch the requirement off for an app, set `AUDIT_REQUIRE_PURPOSE=`
    (empty) in its `.env`. The empty value is kept; only an unset variable
    gets the default.
- **Direct database access goes through data-plane's wrappers.**
  - `make db-shell` (Neo4j) and `make qdrant-shell` always need `CASE_REF=`
    or `REASON=`, and record when the session opened and when it closed.
  - `make backup` (`disclose`) follows `AUDIT_REQUIRE_PURPOSE`, so under the
    default it needs `REASON=`; the nightly timer passes one
    ([backups.md](backups.md)). Every file is age-encrypted to the public
    recipients in data-plane's `backup/recipients.txt`.
  - `make restore-neo4j DUMP=….age IDENTITY=…` and `make restore-qdrant
    COLLECTION=… SNAPSHOT=….age IDENTITY=…` (`modify`) decrypt with an identity
    given for that run only; they are recorded and need no purpose under the
    default.
  - Every wrapper refuses to start when `admin-audit` cannot record.
  - The Neo4j Browser and the Qdrant dashboard are not open by default:
    production `make up` publishes no data-plane ports. `make up-admin` in
    data-plane, with `CASE_REF=` or `REASON=`, opens 7474/7687/6333 on
    `127.0.0.1` for an SSH tunnel and records the opening. Closing them again
    is recorded too (data-plane README § Admin access). Who used them in that
    window is in the host's SSH logins.
  - A `docker exec` into a database container bypasses them; the host's
    auditd records name who did it.
- **Dev identities stay in dev.** Each app's `*_DEFAULT_IDENTITY` is set only
  by its dev overlay and is ignored while the app's edge secret is set.
  translator's `/translate` now answers 401 without an identity, like the
  other apps.

## Daily: anchor the previous day

The chains make altered or removed lines visible. The anchor is what makes
lines added after the fact visible, so take it every day:

```bash
cd obs-plane
make audit-anchor DAY=$(date -u -d yesterday +%F)   # writes audit-exports/anchor-<day>.json
# with AUDIT_SIGNING_KEY set, the target signs it (anchor-<day>.json.sig)
```

Move the anchor and its signature to the offline medium the same day. To check
an anchor later:

```bash
ssh-keygen -Y verify -f allowed_signers -I <signer identity> -n audit \
  -s anchor-<day>.json.sig < anchor-<day>.json
```

## Watch: alerts

Prometheus (obs-plane) evaluates the verifier's metrics:

| Alert | Meaning | What to do |
|---|---|---|
| `AuditChainProblem` | A chain defect was found: altered or removed lines, a gap, a repeat, a malformed line, an event claiming another service's name or continuing a chain from another container, lines of one chain from several containers when its start was not seen (`ambiguous_source`), a chain from a container that is not a listed emitter (`unlisted_source`), or a chain that stopped without a clean shutdown. obs-plane's `docs/audit.md` lists every kind. | Run `make audit-verify FROM= TO=` over the window to see which service, instance and sequence numbers are affected (never event content). A single `unterminated` after a crash or a forced restart is expected. Anything else needs an explanation. Compare against the signed anchors to tell a transport loss from tampering. |
| `AuditVerifierStale` | The verifier has not completed a run for several intervals. | Check the `audit-verifier` and `loki-audit` containers (`make ps`, `make logs S=audit-verifier`). |
| `AuditVerifierDown` | Prometheus cannot reach the verifier. | Same. |

## On request: export for a review

```bash
cd obs-plane
make audit-export FROM=2026-01-01T00:00:00Z TO=2026-02-01T00:00:00Z            # everything in range
make audit-export FROM=… TO=… SERVICE=docint                                     # one service
make audit-export FROM=… TO=… REF=<reference>                                    # lines mentioning a reference
```

The export is JSONL with a `.sha256`, signed like the anchors when
`AUDIT_SIGNING_KEY` is set. Hand over the file, its checksum and signature, and
the matching anchors. An export contains personal data: give it only to whoever
is entitled to it, and record that hand-over through your own procedure.

## Retention

The audit store deletes lines older than `AUDIT_RETENTION` by itself (Loki's
compactor). There is no API to delete individual lines. Changing the period
takes effect from the next compaction. Lowering it deletes older lines for
good, so decide first.
