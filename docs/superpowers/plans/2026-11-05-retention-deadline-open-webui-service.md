# Retention deadline: open-webui-service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chats, notes and legacy uploads in Open WebUI are deleted the configured number of days after they were created (default 365), on by default, announced by a banner, with a grace period at switch-on.

**Architecture:** Everything happens in the existing stdlib retention sidecar (`retention/`), which already sweeps chats in `webui.db` in recorded `BEGIN IMMEDIATE` batches. The chat clock moves from `updated_at` to `created_at`; two new stores (notes, legacy files) follow the chat module's pattern; a persistent state file on the data volume carries the switch-on time for the grace period. Notes are switched off in compose; the banner is `WEBUI_BANNERS`.

**Tech Stack:** Python 3.12 stdlib (sqlite3), unittest, Docker Compose, the vendored audit library.

**Spec:** `deploy/docs/2026-11-05-retention-deadline-design.md` (sections "The rule" and "open-webui-service").

## Global Constraints

- Default period `365` days; `off` only for development, logged as a warning and stated in `config.audit`.
- Notice/grace lead time `RETENTION_NOTICE_DAYS`, default `30`.
- Nothing is deleted sooner than the lead time after the period took effect on the host (first run with it, or a change of the period).
- No extension path of any kind.
- Every deletion recorded as `delete` by the system before its transaction commits; a failed record rolls the batch back (existing `emit_deletion` pattern).
- Wording: "the configured retention period"; never a reason or legal basis.
- Stdlib only in `retention/`; the vendored `audit_event.py` / `audit_runtime.py` are never edited.
- One version bump for the repo in this wave: `VERSION` 0.9.1 → 0.10.0, in the last PR of the wave.
- Time units in the pinned image (v0.13.0): `chat.created_at` and `file.created_at` are seconds; `note.created_at` is **nanoseconds** (`models/notes.py` uses `time.time_ns()`).

## Review Focus

- A chat with `created_at` NULL but `updated_at` set (old rows): expires by `updated_at`; one with both NULL never expires and the dry run counts it as "no date".
- Changing `OPEN_WEBUI_CHAT_RETENTION_DAYS` from 365 to 180 restarts the grace period; nothing extra is deleted before 30 days after the change.
- The sidecar starting on a fresh host before Open WebUI created `webui.db`: no state written for stores that cannot run; no crash.
- A note chat whose note was swept is deleted at the next chat round, not left orphaned for a year.
- The state file is unreadable or corrupt: the sidecar refuses to sweep (fail closed) and reports `refused` in health, rather than restarting the grace period silently.

---

### Task 1: Grace-period state

**Files:**
- Create: `retention/retention_state.py`
- Test: `retention/tests/test_retention_state.py`

**Interfaces:**
- Produces: `RetentionState.load(path: Path) -> RetentionState`; `RetentionState.effective_since(store: str, period_days: int, now: float) -> float` (records `now` for a store whose period is new or changed, persists atomically, returns the recorded time); `def deletion_allowed(since: float, notice_days: int, now: float) -> bool` (`now >= since + notice_days * 86400`). State file: `<data_dir>/.retention-state.json`, mode 0600, `{"stores": {"chats": {"days": 365, "since": 1793...}}}`. A corrupt or unreadable file raises `StateRefused`.

- [ ] **Step 1: Write the failing tests** — `test_first_run_records_now`, `test_same_period_keeps_since`, `test_changed_period_restarts_since`, `test_deletion_waits_for_the_notice` (`deletion_allowed(since=0, notice_days=30, now=30*86400-1)` is False, at `30*86400` True), `test_corrupt_state_is_refused`, `test_write_is_atomic_and_private` (mode 0o600, written via temp file + `os.replace`).
- [ ] **Step 2: Run** `python3 -m unittest discover -s retention/tests -t retention -k retention_state` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `retention/retention_state.py` as above.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(retention): record when a retention period took effect`.

### Task 2: Chats expire by creation, on by default, after the grace period

**Files:**
- Modify: `retention/chat_retention.py` (`_EXPIRED`, `_CHANGED` → `_STARTED`, module docstring "A chat's age is its ``updated_at``" paragraph, note-chat handling in `plan_owner`)
- Modify: `retention/owui_retention.py` (`Settings.from_env`: `chat_days` default `DEFAULT_CHAT_DAYS = 365`; new `notice_days`; `Rounds._chats` consults Task 1; `config_criteria` adds `chat_retention_since`, `notice_days`; dry run says when deletion starts)
- Modify: `docker/compose.yaml` (`OPEN_WEBUI_CHAT_RETENTION_DAYS: ${OPEN_WEBUI_CHAT_RETENTION_DAYS:-365}`, add `RETENTION_NOTICE_DAYS: ${RETENTION_NOTICE_DAYS:-30}`), `.env.example`
- Test: `retention/tests/test_chat_retention.py`, `retention/tests/test_owui_retention.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `_EXPIRED = "(created_at < ? OR (created_at IS NULL AND updated_at < ?))"`; `_STARTED = "COALESCE(created_at, updated_at)"`; `Settings.notice_days: int` (env `RETENTION_NOTICE_DAYS`, 1–365, default 30); a note chat (`meta.internal` with `meta.note_id`) whose note no longer exists is due regardless of age.

- [ ] **Step 1: Write the failing tests** —
  - `test_age_is_the_creation` (replaces `test_age_is_the_last_change`): chat created 400 days ago, updated yesterday → deleted at 365.
  - `test_continued_chat_still_expires`: created 366 d, updated 0 d → deleted.
  - `test_no_creation_falls_back_to_last_change`, `test_no_dates_never_expires`.
  - `test_note_chat_without_its_note_is_due`.
  - `test_default_is_365_days` (`Settings.from_env({}).chat_days == 365`), `test_off_is_logged_and_recorded` (`config_criteria` has `chat_retention_days: None`, a warning logged).
  - `test_nothing_is_deleted_during_the_grace_period`: state since = now − 29 days, 400-day-old chat survives a round; at 30 days it goes.
  - `test_dry_run_names_the_first_deletion_date`.
- [ ] **Step 2: Run** `python3 -m unittest discover -s retention/tests -t retention` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the changes listed under Files; keep the existing re-check inside the write transaction.
- [ ] **Step 4: Run** the suite — Expected: PASS (all, including the existing WAL and schema tests).
- [ ] **Step 5: Commit** `feat(retention): chats expire a period after they were started, on by default`.

### Task 3: Notes off, existing notes swept

**Files:**
- Create: `retention/note_retention.py`
- Modify: `retention/owui_retention.py` (a `notes` store in `Rounds`, status, dry run, `config_criteria`)
- Modify: `retention/chat_retention.py` (`REQUIRED_COLUMNS` gains `note`: `id, user_id, created_at`; `pinned_note`: `note_id`; the schema guard's table list)
- Modify: `docker/compose.yaml` (`ENABLE_NOTES: false`, `USER_PERMISSIONS_FEATURES_NOTES: false`, comment: the notes router checks `notes.enable` itself, `routers/notes.py` `check_notes_access`)
- Test: `retention/tests/test_note_retention.py`; `retention/tests/synthetic.py` gains `Store.note(owner, *, created_days)`

**Interfaces:**
- Consumes: Task 1 (`"notes"` store), the chat module's `open_database`, `schema_problems`, `truncate_wal`, `emit_deletion`.
- Produces: `class NoteSweeper(emitter, database, days, *, busy_timeout_s, pause_s, max_batches, stop)` with `sweep(now) -> NoteSweep` (`notes: int`, `owners: int`, `batches: int`, `complete: bool`); deletes per note: `note`, `pinned_note` rows, comments on it (`comment.resource_type='note'`) with their reactions and notifications, and `access_grant` rows of `resource_type='note'`; record target kind `note`.

- [ ] **Step 1: Verify against the pinned image** that every route in `routers/notes.py` calls `check_notes_access`, and list what `models/notes.py` `delete_note_by_id` and its callers delete. Record the result in the module docstring.
- [ ] **Step 2: Write the failing tests** — `test_note_expires_by_its_creation_in_nanoseconds` (created 366 d ago as `int(t * 1e9)`), `test_young_note_stays`, `test_dependents_go_with_it` (pinned row, comment, reaction, grant), `test_record_names_notes_by_id_only`, `test_failed_record_rolls_back`, `test_grace_applies`.
- [ ] **Step 3: Run** the suite — Expected: new tests FAIL.
- [ ] **Step 4: Implement** `note_retention.py` following `chat_retention.py`'s batch, re-check, record-before-commit and WAL pattern.
- [ ] **Step 5: Run** the suite — Expected: PASS.
- [ ] **Step 6: Commit** `feat(retention): notes off; existing notes deleted a period after they were created`.

### Task 4: Legacy uploads swept

**Files:**
- Create: `retention/file_retention.py`
- Modify: `retention/owui_retention.py` (a `files` store), `retention/chat_retention.py` (`REQUIRED_COLUMNS`: `file`: `id, user_id, created_at, path`; `chat_file`: `file_id`; `knowledge_file`: `file_id`)
- Test: `retention/tests/test_file_retention.py`; `Store.file(owner, *, created_days, path)`

**Interfaces:**
- Consumes: Task 1 (`"files"` store), chat module helpers.
- Produces: `class FileSweeper(emitter, database, data_dir, days, ...)` with `sweep(now) -> FileSweep`; per file: the `file` row, `chat_file` and `knowledge_file` links, then the file on disk at `path` only when it resolves inside `<data_dir>/uploads` (no symlinks followed, like `audio_caches._OpenDirectory`), recorded before deletion; target kind `file`.

- [ ] **Step 1: Verify against the pinned image** where uploads and anything derived from them live (`models/files.py`, `routers/files.py`, the vector store). Whatever the sidecar cannot delete with stdlib (vector collections) is reported by the dry run and named in the runbook step of Task 5; it is not deleted.
- [ ] **Step 2: Write the failing tests** — `test_old_upload_goes_with_its_file_and_links`, `test_path_outside_uploads_is_refused_and_row_kept`, `test_symlinked_upload_is_not_followed`, `test_missing_file_on_disk_still_removes_the_row`, `test_grace_applies`.
- [ ] **Step 3: Run** — FAIL. **Step 4: Implement.** **Step 5: Run** — PASS.
- [ ] **Step 6: Commit** `feat(retention): uploads from before the intake was closed are deleted a period after they were made`.

### Task 5: Banner, docs, release

**Files:**
- Modify: `docker/compose.yaml` (`WEBUI_BANNERS`), `.env.example`, `CLAUDE.md` (Retention section, No content intake: notes), `README.md`, `VERSION` (0.10.0)
- Test: `retention/tests/test_compose_defaults.py` (new): parses `docker/compose.yaml` text for the defaults `OPEN_WEBUI_CHAT_RETENTION_DAYS:-365`, `RETENTION_NOTICE_DAYS:-30`, `ENABLE_NOTES: false`, and that `WEBUI_BANNERS` is valid JSON after substituting `365` with an `id`, `type: "info"`, `content` in English and `i18n.de.content` in German, `dismissible: true`, `timestamp`.

Banner copy (exact):
- en: "Chats are deleted automatically ${OPEN_WEBUI_CHAT_RETENTION_DAYS:-365} days after they were started."
- de: "Chats werden ${OPEN_WEBUI_CHAT_RETENTION_DAYS:-365} Tage nach ihrem Beginn automatisch gelöscht."

- [ ] **Step 1: Write the failing test** above. **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** compose, docs (including the runbook note: vector data of legacy uploads is cleared with Open WebUI's own *Reset vector storage* admin action, if Task 4 found any), `VERSION`.
- [ ] **Step 4: Gate:** `python3 -m unittest discover -s retention/tests -t retention`; the pinned-image run from CI (`docker run --rm --read-only --tmpfs /tmp --user 10001:10001 --network none ... python -m unittest discover -s /app/tests -t /app`); yamllint; `docker compose --env-file .env -f docker/compose.yaml config --quiet` with `.env` from `.env.example`; the schema job's three steps (migrate with the pinned image, compare the fixture, `owui_retention.py schema`). Expected: all pass.
- [ ] **Step 5: Commit** `feat: a retention period for chats, notes and uploads, announced in a banner; release v0.10.0`.
