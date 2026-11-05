# Retention deadline: data-plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backups are short-lived restore points: after every successful backup, backups older than `BACKUP_RETENTION_DAYS` (default 30, at most 365) are deleted and recorded; `make backup-prune` does it on demand.

**Architecture:** A new script `scripts/backup_prune.sh` lists `BACKUP_DIR`, decides by the UTC timestamp in each file name, and deletes; it runs under the existing `scripts/audit-admin.sh` wrapper as a new kind `prune` (action `delete`), so admin-audit admits and records it exactly like backups. `make backup-neo4j` / `backup-qdrant` call it after their backup succeeded.

**Tech Stack:** Bash, Make, Python 3.12 stdlib (`audit/admin_audit.py`), unittest.

**Spec:** `deploy/docs/2026-11-05-retention-deadline-design.md` (sections "The rule" and "data-plane").

## Global Constraints

- `BACKUP_RETENTION_DAYS` default `30`, whole days 1–365; any other value refuses the backup target before anything runs.
- Grace: nothing written before the first prune on a host is deleted sooner than `BACKUP_RETENTION_DAYS` after that first prune (state file `$(BACKUP_DIR)/.prune-since`, mode 0600).
- The backup just written is never deleted; pruning runs only after a successful backup.
- Encrypted and plaintext backups of both stores are pruned by the same age rule: `neo4j-<YYYYmmddTHHMMSSZ>.dump[.age]`, `qdrant-<collection>-<YYYYmmddTHHMMSSZ>[-n].snapshot[.age]`. A name without a parsable stamp is never deleted; the prune names it.
- A file the running account cannot delete is reported (`ERROR ... remove it as root`) and recorded as not deleted; it does not fail the backup.
- Records: `delete`, operation `backup.prune.neo4j` / `backup.prune.qdrant`, target kind `file`, ids = file names (continuation above 200), criteria `period_days`, `cutoff`, `deleted`, `not_deleted`; attributed like the backup that triggered it.
- shellcheck-clean; portable between GNU and BSD userland (date parsing via `TZ=UTC` arithmetic in bash, not `date -d`).
- One version bump: `VERSION` 0.6.1 → 0.7.0, last PR of the wave.

## Review Focus

- `BACKUP_DIR` holds `.part` files from an interrupted backup: never deleted by the prune (they are the backup scripts' own) and named.
- A Qdrant collection name containing a date-like string (`qdrant-2026-docs-...`): the stamp is taken from the last `-<stamp>` before the suffix, not the first match.
- A clock set back (stamp in the future): file kept, warning.
- Prune run as a different account than the backups (files owned by another user): reported, recorded as not deleted, exit 0.
- The `.prune-since` file deleted by hand: the grace restarts (state recreated); documented.

---

### Task 1: admin-audit knows the prune

**Files:**
- Modify: `audit/admin_audit.py` (`KIND_ACTIONS["prune"] = "delete"`; `operation_of("prune", store)` → `backup.prune.<store>`; the fields `prune` accepts: `period_days`, `cutoff`, `deleted`, `not_deleted`, repeatable `file`; `REQUIRED_FACTS[("prune", store)] = ("period_days", "cutoff", "deleted")`; no purpose required unless `AUDIT_REQUIRE_PURPOSE` lists `delete`)
- Test: `audit/tests/test_admin_audit.py` (`PruneRecordTest`)

**Interfaces:**
- Produces: `begin prune <store>` / `end prune <store> --status N period_days=.. cutoff=.. deleted=.. not_deleted=.. file=<name>...` accepted by `scripts/audit-admin.sh` unchanged.

- [ ] **Step 1: Write the failing tests** — `test_prune_is_a_deletion_of_files`, `test_long_file_list_continues_in_id_events`, `test_success_must_state_counts`, `test_prune_follows_require_purpose` (`delete` listed → refused without a purpose).
- [ ] **Step 2: Run** `python3 -m unittest discover -s audit/tests -t audit -k Prune` — FAIL.
- [ ] **Step 3: Implement.** **Step 4: Run** — PASS. **Step 5: Commit** `feat(audit): record the pruning of backups as a deletion`.

### Task 2: `scripts/backup_prune.sh`

**Files:**
- Create: `scripts/backup_prune.sh`
- Test: `audit/tests/test_admin_audit.py` (`BackupPruneTest`, using the existing `AgeStandIn`-style temp dirs)

**Interfaces:**
- Consumes: `backup_dir`, `file_size` from `scripts/backup_age.sh` (sourced).
- Produces: `BACKUP_RETENTION_DAYS=<n> BACKUP_DIR=<dir> AUDIT_FACTS=<file> scripts/backup_prune.sh <neo4j|qdrant> [--now <epoch>]` (the `--now` option for tests only); writes the facts above; exit 0 when it ran (even with files it could not delete), 2 on a bad setting.

- [ ] **Step 1: Write the failing tests** — `test_old_backups_of_the_store_go` (31-day-old `.age` of neo4j goes, qdrant's stays when pruning neo4j), `test_plaintext_leftovers_by_the_same_rule`, `test_the_backup_just_written_never_goes` (stamp = now, period 1 day: kept) and `test_an_old_only_backup_still_goes` (`make backup-prune` with a single 40-day-old file deletes it: no backup outlives the period), `test_grace_on_first_run` (`.prune-since` written; a 100-day-old file stays until since + 30 d), `test_part_files_and_unparsable_names_stay_and_are_named`, `test_last_stamp_in_the_name_counts`, `test_future_stamp_is_kept_with_a_warning`, `test_undeletable_file_is_reported_not_failed` (chmod the dir read-only for the test, skip as root), `test_bad_period_refuses` (`0`, `366`, `abc`).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** on macOS and in the Linux container used for the recipients hardening (bash 5.2, GNU coreutils, non-root) — PASS both.
- [ ] **Step 5: Commit** `feat(backup): delete backups older than the retention period after each backup`.

### Task 3: Make targets, docs, release

**Files:**
- Modify: `Makefile` (`BACKUP_RETENTION_DAYS ?= 30`; after the `AUDITED backup <store>` line in `backup-neo4j` / `backup-qdrant`: `@$(AUDITED) prune <store> -- ./scripts/backup_prune.sh <store>`; new `backup-prune: ` running both stores; `help` text), `.env.example`, `backup/README.md` (Cadence: local *and* off-host copies kept 30 days, the off-host pruning is the operator's; Where backups land; Restore: let docint's sweep run before users work on restored data), `README.md` if it lists targets, `CLAUDE.md`, `VERSION` 0.7.0
- Test: `audit/tests/test_admin_audit.py` (`MakefileTest`: `test_a_backup_prunes_after_it_succeeded`, `test_a_failed_backup_prunes_nothing`, `test_backup_prune_target`)

- [ ] **Step 1: Write the failing tests.** **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** targets and docs; replace the README's 90-day off-host suggestion with the 30-day rule.
- [ ] **Step 4: Gate:** shellcheck `scripts/*.sh`; yamllint; the compose-config job's loop; `python3 -m unittest discover -s audit/tests -t audit` on macOS, in the pinned python image (read-only, uid 65534) and in the Linux container (bash 5.2, GNU coreutils, make, non-root). Expected: all pass.
- [ ] **Step 5: Commit** `feat(backup): backups are kept 30 days by default; release v0.7.0`.
