# Retention deadline: one fixed period, announced, never extended

Status: design, 2026-11-05. Scope: docint, open-webui-service, Nextext,
data-plane, plus deploy's runbooks. chorus is out of scope of this design.

## Intent

The federation is an analysis workspace, not a place where data is kept. What
someone brings into an app stays there for a fixed, configured period and is
then deleted. Anything that must be kept longer belongs in other systems
outside the federation, so a deadline here is never extended. The person who
owns the data sees, in the app itself, what will be deleted and when, early
enough to move what they still need.

## The rule

1. **Deadline.** Every stored item expires a fixed period after it was stored.
   The default is 365 days. Using, opening, searching or exporting an item
   never moves its deadline.
2. **No extension.** Nothing in this design lets anyone keep an item past its
   deadline: no button, no flag, no admin override.
3. **Derived data inherits.** Anything derived from stored items (chat
   answers, summaries, reports, extracts, topic runs, indexes) expires no later
   than the earliest-expiring item it draws on. Where an app cannot tell which
   items something draws on, the oldest item it could have drawn on counts.
4. **Notice.** From a configured lead time before the deadline (default 30
   days), the owner sees in the app what will be deleted and on which date.
   There is no other channel (no mail): the hosts are airgapped.
5. **On by default.** The deadline applies out of the box. An explicit `off`
   exists for development only; it is logged as a warning and stated in the
   audit chain's `config.audit` event.
6. **Grace at switch-on.** Nothing is deleted sooner than the notice lead time
   after the rule took effect on a host (first start of the new release, or a
   change of the period). So every owner gets a full notice period, also for
   data that is already older than the deadline when this ships.
7. **Recorded.** Every deletion is recorded in the audit trail as `delete`, by
   the system, with references and counts, never content. A deletion whose
   record cannot be written does not happen (fail closed), as everywhere in
   the federation.

Wording in code, UI and docs speaks of "the configured retention period" and
never names a reason or a legal basis for it.

## Goals

- One rule across the apps that store user data, with the same defaults.
- The deadline travels with the data: a restore from backup cannot bring an
  item back without its deadline.
- Owners are told in time, in the app they use.
- Operators can see beforehand what a sweep will delete (dry-run report), as
  with the existing sweepers.

## Non-goals

- A review workflow with outcomes ("keep", "extend", reason). There is none.
- Per-item deadlines chosen by users.
- Notices outside the apps (mail, chat bots).
- Host-level redundancy (mirrored disks, replicated storage). That is the
  host's concern and not a backup in the sense of this design.

## Anti-goals (would be a failure even if it works)

- Any path by which activity keeps data alive (the current docint rule,
  "12 months after the last activity", is exactly that and goes).
- Derived data that outlives its source, e.g. a report written in month 11
  still showing a document's content in month 20.
- Deleting data without the notice period on the day this ships.
- A restore that resurrects expired data, or restored data that never expires.

## Constraints

- Airgap: nothing fetched at runtime; no external notification service.
- The audit library and its conventions (hash chain, fail closed, references
  not content, continuation events above 200 ids, `config.audit` criteria).
- Each repo keeps its own release; per the federation's release rule this is
  one wave with one version bump per repo (docint, open-webui-service,
  Nextext, data-plane). Docs ride along on the same branch.
- UI text in English and German (`RESPONSE_LANGUAGE`), as the apps already do.
- Open WebUI is an upstream image; it cannot be changed, only configured,
  guarded (edge-guard) and cleaned from outside (the retention sidecar).

## docint

docint has collection retention today (`COLLECTION_RETENTION`, off by default):
a collection falls due a number of months after its last activity, the
Dashboard flags it 30 days ahead, and a daily sweep deletes the whole
collection with everything attached. This design replaces that rule.

### The clock

- **Per document, per collection.** A document's clock starts when its ingest
  into a collection completes. The same file in two collections has two
  clocks.
- **Re-ingesting identical content does not reset the clock** while the
  document exists. Once it has been deleted, a new ingest is a new document
  with a new clock.
- **Where the clock lives.** Each Qdrant point of the document (and its
  companions `_images`, `_entities`) carries the ingest time in its payload,
  so a restored collection still knows its deadlines. The sessions store keeps
  an index (collection, owner, file hash, file name, ingested, expires) for
  listing notices quickly. When the two disagree, or the index lacks a
  document Qdrant holds, the payload wins and the index is repaired.
- **Existing documents.** At upgrade, a document's ingest time is taken from
  the ingest manifest's completion time where the manifest has it, else the
  time the rule took effect on the host (rule 6, recorded once). Never a
  guessed earlier time.
- **Points without a payload clock found later** (a restore from a backup
  taken before this release): the index row's ingest time if the index still
  has one, else the manifest's, else the time the rule took effect, never the
  restore time. So restored data never gets a fresh clock, and data from
  before the release expires no later than everything else from before it.
- **Collections without an owner** (CLI-ingested, pre-ownership) expire like
  any other; no one is notified, and the operator report lists them.

### Derived data

Each derived item gets an `expires_at` when it is created, and every later
change that adds sources lowers it to the earliest expiry among the new
sources; it is never raised.

| Item | Sources it inherits from |
|---|---|
| Chat turn | the documents its citations name; a turn without citations, or one that used collection-wide context (rolling summary, topic focus, scope), the collection's oldest live document at that time |
| Chat session | the earliest of its turns; its rolling summary counts as collection-wide |
| Report | the earliest of its items (an item's own sources where known, else the collection's oldest live document); a collection overview snapshot counts as collection-wide |
| Stored extract | collection-wide (it keeps its own shorter `EXTRACT_RETENTION_DAYS` as well) |
| Topic run | collection-wide |
| Summary cache, collection overview cache | collection-wide; invalidated when any document they drew on is deleted |
| Full-text index, entity data, pipeline artifacts, docstore nodes, source file | part of the document itself; deleted in the document's cascade |

A derived item that expires is deleted whole. There is no partial redaction.

### The sweep

- Runs inside the backend, as today: a few minutes after startup, then daily.
- **Due documents** are deleted with a new per-document cascade: the
  document's points in the collection and its companions, its full-text index
  entries, entity data drawn only from it, its source file, docstore nodes,
  ingest manifest row, and pipeline artifacts nothing else uses (the existing
  sharing rule for artifacts applies). A collection whose last document goes
  is deleted with the existing collection cascade.
- **Due derived items** are deleted with their existing delete paths
  (session, report, extract, topic run).
- The existing safeguards stay: skip what a running job works on (retry next
  sweep), delete-in-progress returns `409`, a failed step leaves the item for
  the next sweep, every step is idempotent.

### Notice

- The Dashboard's *Automatic deletion* card and the sidebar hint show, for the
  signed-in owner, every document, session and report due within the lead
  time, with its date; grouped by collection.
- `GET /collections/retention` (or a successor) returns the same for the
  caller; admins can pass `all=true` as today (recorded as `admin`).
- Wording names the date and that the item is deleted then; it does not
  suggest it can be kept.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DOCUMENT_RETENTION_DAYS` | `365` | Days from ingest to deletion. `off` only for development. |
| `RETENTION_NOTICE_DAYS` | `30` | Lead time of the notice and the grace period at switch-on. |
| `COLLECTION_RETENTION` | removed | No longer read; a set value logs a warning at startup. |

### Audit

`delete` events as the system: one per swept document batch (target
`document` refs as `<collection>:<file hash>`; criteria counts per kind of
derived data), and the existing `session.delete`, `report.delete`,
`extract.delete` and `collection.delete` shapes for derived items, with
operation names marking them as retention (`retention.*`). The `config.audit`
event states the period, the lead time and when the rule took effect.

## open-webui-service

- **Chats.** `OPEN_WEBUI_CHAT_RETENTION_DAYS` defaults to `365` (today: empty,
  off). The clock is a chat's `created_at`, no longer `updated_at`: a chat that
  is continued keeps its oldest messages, so it expires a year after it began.
  Internal chats go with their root chat as today; note chats follow their
  note.
- **Notes.** Open WebUI's Notes feature stores user content and no sweeper
  covers it. It is switched off (`ENABLE_NOTES=false`,
  `USER_PERMISSIONS_FEATURES_NOTES=false`), in line with the existing "no
  content intake" rule; if the pinned image does not enforce the flag
  server-side, edge-guard refuses note writes like the other intake routes.
  Existing notes are swept at `created_at` plus the period.
- **Legacy uploads.** Files uploaded before uploads were turned off (the
  `file` table and the files under the data volume) are swept at `created_at`
  plus the period, with whatever the pinned image derived from them.
- **Notice.** A per-chat notice is not possible without changing the upstream
  UI. Instead a banner (`WEBUI_BANNERS`, English and German via its `i18n`
  field) states that chats are deleted the configured number of days after
  they were started. The banner text takes the period from the same variable.
- **Grace at switch-on** as in the rule: the sidecar records when the period
  took effect and deletes nothing sooner than the lead time after it.
- The audio cache sweep (60 minutes) stays as it is.

## Nextext

The backend keeps jobs in memory only; the gap is `make cli`, whose inputs
and outputs stay on the `tmp-jobs` scratch volume until removed by hand, with
no record.

- `make cli` works in a directory of its own on the scratch volume
  (`/tmp/nextext-cli/`); the docs tell the operator to copy inputs there.
- The backend sweeps that directory at startup and daily: anything older than
  `NEXTEXT_CLI_RETENTION_DAYS` (default `365`) is deleted and recorded
  (`delete`, by the system).
- `make cli-clean` deletes the CLI's files now (all, or one run's), recorded
  with the operator as actor.
- Each CLI run ends by printing the date its outputs will be deleted; that is
  the notice to the person who ran it.
- **Files from before this release** (inputs copied to `/tmp`, outputs under
  `/tmp/output/`): the sweep also covers `/tmp/output/` by the same age rule,
  with the grace of rule 6. Top-level input files in `/tmp` are not swept
  automatically, since `/tmp` also holds the backend's own files
  (`/tmp/nextext-jobs`, `/tmp/matplotlib`, its lock files); the startup log
  and the dry run list them, and `make cli-clean LEGACY=1` removes the regular
  files directly in `/tmp` except a fixed list of the backend's own, recorded.

## data-plane

- After every successful backup, backups older than `BACKUP_RETENTION_DAYS`
  (default `30`, at most `365`) are deleted: encrypted Neo4j dumps and Qdrant
  snapshots alike (the prune is about backups, whichever app the store
  serves), and plaintext leftovers of earlier versions by the same age rule.
  The age is read from the timestamp in the file name. The backup just
  written is never deleted.
- Grace (rule 6) applies: the first prune on a host deletes nothing written
  before the release sooner than the lead time after the release's first
  backup, so an operator can still deal with plaintext leftovers.
- A file the backup account cannot delete (plaintext dumps written by a
  container as root) is not an error of the backup: the prune names it, says
  to remove it as root, and records it as not deleted.
- Off-host copies are backups too and follow the same period. The repo cannot
  reach them, so the runbooks make pruning them at the same age an operator
  duty, replacing the current 90-day suggestion for off-host copies.
- `make backup-prune` does the same on demand.
- Each pruning is recorded (`delete`, operation `backup.prune`, the files by
  name, counts), attributed like the backup that triggered it.
- The deadline travels with the data (docint section), so a restored Qdrant
  collection's expired documents fall due at docint's next sweep; the restore
  runbook says to let that sweep run before users work on restored data.

## deploy

- `docs/runbooks/backups.md`: the 30-day pruning, that backups are
  short-lived restore points, not an archive, and that off-host copies are
  pruned at the same age by the operator.
- The federation's docs index points at this design.

## Rollout

- One wave: docint, open-webui-service, Nextext and data-plane each get one
  release with this change. No cross-repo version coupling.
- Each repo's dry-run report shows what the new rule will delete and when,
  before the release is deployed.
- Because of the grace period, the first deletions on a host happen no sooner
  than the lead time after deployment.

## Left to the builder

- Table and column names, the exact payload key, and how the index is
  repaired from Qdrant.
- How the per-document cascade is split into functions, and batch sizes.
- How the notice is laid out in the Dashboard, within the existing design
  system.
- Operation names and criteria keys of the new audit events, within the
  federation's conventions.
- Whether `RETENTION_NOTICE_DAYS` is one variable per repo or named per repo;
  defaults as above either way.
- What else the pinned Open WebUI image derives from legacy uploads, verified
  against its source.

## Out of scope

- chorus.
- translator (stores nothing).
- obs-plane: ops logs keep 30 days; the audit store keeps one year and holds
  references, not content.
- The federation audit trail's own retention.
