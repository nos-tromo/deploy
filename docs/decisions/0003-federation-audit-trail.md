# 0003 — A separate, verifiable audit trail for the federation

Status: accepted (2026-11-03)
Date: 2026-11-03

## Context

The federation's apps process personal data: social-media material in
chorus, documents in docint, recordings and transcripts in Nextext, free text
in translator and Open WebUI. Data-protection rules for such processing expect
a record of who collected, changed, queried, disclosed, combined or erased
which data, when, and for what stated purpose. They also expect that record
to be kept for a defined period and then deleted, and to be producible for a
review on request. Where results are machine-generated, it must be possible to
trace which model and which evidence produced them.

An audit of the federation found nothing that met this. Each repo logged in
its own way to container stdout, and Alloy shipped all of it to the
operational Loki. That store keeps 30 days, its readers are the operators, and
it offers no integrity guarantee. chorus had a partial audit table, without a
purpose field, with no enforced retention, and with triggers its own process
could drop. The other apps had none. Operational logs also carried personal
data (query strings, submitted values, upstream error bodies). And the
identity every record would name could be forged: the backends trusted
identity headers from any container on the shared networks.

## Decision

**Two lanes.** Operational logs stay short-lived, are scrubbed of personal
data at the source and redacted in the collector, and remain the operators'
tool. Audit records travel in a lane of their own, with their own store,
retention, readership and integrity checks.

**Identity first.** Each app trusts the gateway's identity headers only with
its own edge secret, and so do obs-plane's two Grafanas, through a guard in
front of each. Direct service calls use service tokens in a separate
principal namespace. Logins need a second factor (edge-plane v0.9.0 and the
app releases of the same round). Without that, every "who" in the trail
could be forged.

**One event format, one library.** `audit_event.py`, canonical in the
`.github` hub and vendored verbatim with a drift check, writes one JSON line
per event to stdout, marked `"stream": "audit"`. Each process writes one hash
chain (`seq`, `prev_hash`, `hash`), with start, heartbeat and shutdown
events. Events carry references, counts, bounded search criteria and the
stated purpose, never content. The same module verifies chains.

**Transport through the existing collector.** Alloy routes lines carrying
the audit marker, the gateway's access log and Open WebUI's built-in audit
records to the audit store, and drops them from the operational stream.
Records of the last two kinds carry no chain, so Alloy accepts them only
from the gateway's and Open WebUI's own containers. The pulled third-party
components can only write to stdout, so this path works for every producer
without a new network seam. Losses in transport (a container removed before
collection) show up as sequence gaps.

**Store, access, retention.** obs-plane runs a second Loki (`loki-audit`) on
its own external volume:
- the deletion API is off;
- retention is the compactor's `AUDIT_RETENTION`, which defaults to one year
  and is set to whatever the applicable rules prescribe;
- only the collector writes to it; the viewer and the verifier reach it
  through a read-only proxy that passes queries and nothing else, so no
  reader can add, change or remove lines;
- a separate Grafana (`grafana-audit`) reads it, behind the gateway at
  `/audit/`, for the group `audit` only. Its guard refuses every request
  that does not carry the gateway's edge secret;
- operators in `admins` do not see it;
- every read of the trail is itself in the gateway's access log, and so in
  the trail.

**Integrity.** A verifier in obs-plane re-checks the chains continuously and
binds each event to its source. An event's `service` must match the compose
project Docker reported for its container, and a chain must stay in the
container it started in. It reports defects to Prometheus, including chains
that went silent without a shutdown, and alerts fire on them. Daily anchors
(counts, a digest over the day's lines, chain heads) are signed with a key
kept off the host and stored offline, as is any export produced for a
review.

**Rollout in phases**, each a set of independent, CI-green PRs:
0. Scrub operational logs.
1. Identity and correlation.
2. Library and audit lane.
3. Instrument every app, including the purpose of queries and disclosures
   and model provenance.
4. Enforce deletion.

## Alternatives considered

- **Use the operational Loki with longer retention.** Rejected. It would
  give operators the trail, keep personal data far longer than operations
  need, and offer no integrity.
- **A database in data-plane that apps write to directly.** Rejected. It
  needs a new network seam or widens `data-net` to apps that hold no data,
  puts credentials into every app, and cannot take records from the pulled
  third-party components, which only write to stdout.
- **A signature or HMAC per service.** Rejected for now. The key would sit
  where an attacker able to forge lines (root on the host, or the compromised
  service) can read it. Source binding in the collector and off-host anchors
  cover the same threats without key distribution.

## Consequences

- Every app that handles personal data vendors `audit_event.py` and emits
  through it. CI fails if a copy drifts.
- The integrity guarantee has a stated limit: whoever controls the host or the
  audit store can fabricate records for the current day until that day's
  anchor is signed off the host. Anchors must therefore be taken and moved
  offline every day.
- Third-party producers (the gateway's access log, Open WebUI) are not
  hash-chained. The collector accepts their records only from their own
  containers, and the anchors protect them afterwards.
- The legal classification, the retention period, the purpose of each
  processing and the impact assessment are decided and documented by the
  operator, outside these repositories. The repositories provide the
  mechanism and the settings.

## Amendment: rules from the instrumentation (phase 3)

Decided while the services were instrumented:

- **A second shared module.** `audit_runtime.py` (canonical next to
  `audit_event.py`, vendored and drift-checked the same way) holds what every
  emitter must do alike. It writes to the container's main stdout under one
  container-wide lock and reads the stated purpose (`X-Audit-Purpose`: a
  form-encoded case reference and reason). It also parses
  `AUDIT_REQUIRE_PURPOSE`, keeps only sane request ids, and splits long id
  lists instead of cutting them.
- **Command-line runs go through the long-running container.** A one-off
  container can exit before the collector discovers it, and its lines would
  then be lost without a trace. Audited command-line work therefore runs with
  `docker compose exec` in the service's running container and writes into
  its log.
- **Listed emitters.** The verifier accepts chained lines only from the
  federation's emitters, by compose project and service. A sibling
  container of an emitter cannot write a chain in its name.
- **Dev identities cannot stand in for verified ones.** A service ignores its
  dev default identity while its edge secret is set.
- **Fail closed.** An operation whose event cannot be written fails and
  returns no data. Where the component allows it, the next call is refused
  until the trail is writable again.
- **Open WebUI stores no documents.** Its edge guard refuses uploads,
  knowledge bases and retrieval processing, and memories are off. Personal
  data therefore accumulates only in services that record their processing.

## Amendment: the stated purpose and the remaining sources (phase 3, second part)

- **Queries and disclosures state a purpose.** chorus, docint and Nextext
  require one for `query` and `disclose` by default
  (`AUDIT_REQUIRE_PURPOSE=query,disclose`, written colon-less in compose, so
  an empty value switches it off). translator stores nothing and leaves it
  to the operator. Each SPA's purpose bar holds a case reference and a
  reason for the session and sends them with every request; a refused
  request opens the bar and is sent once more. The four apps use one wording,
  a declined prompt says what is missing, and a refused query reloads once a
  purpose is stated.
- **What a browser cannot decorate goes through `fetch` or a grant.**
  Downloads are fetched files. A media player cannot send a header, so
  Nextext issues a short-lived playback grant, bound on the server to the
  caller, the stated purpose and the request; the streams it opens are
  recorded with that purpose.
- **Machine clients state a purpose too.** docint's media ingest through
  Nextext sends the purpose of the docint request that started it, or
  `docint ingest` when none was stated: the ingest is a collection, which
  needs none of its own.
- **Open WebUI's users reach the router only signed.** With a dedicated
  secret, Open WebUI signs a short-lived token naming the user on every model
  call, and the router records that user. The secret itself switches the
  forwarding on, so the user's details never travel as plain headers.
- **The served revision is the recorded one.** Each model launch loads the
  commit its revision variable names, and the router records the same value,
  pass-through backends included. A pinned commit missing from the cache
  stops the service instead of serving other weights.
- **Direct database access is recorded.** data-plane's `admin-audit` records
  database shells, which always need a purpose, as well as backups and
  restores. Each wrapper refuses to start when it cannot record.
- **The host covers what no service sees.** auditd names who edits the account
  file or a member's `.env`, reads a backup, or runs `docker` by hand
  (`docs/runbooks/host-audit.md`).
