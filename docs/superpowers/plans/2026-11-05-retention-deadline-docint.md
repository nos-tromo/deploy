# Retention deadline: docint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every document expires `DOCUMENT_RETENTION_DAYS` (default 365) after its ingest completed; sessions, reports, extracts and topic runs never outlive the earliest document they draw on; owners see what is due 30 days ahead; a daily sweep deletes and records it. The idle-window collection retention (`COLLECTION_RETENTION`) is removed.

**Architecture:** A new sessions-store table indexes every document's deadline, written where ingest completes; every point also carries its ingest time in its payload, so a restored collection keeps its deadlines and the index is repaired from Qdrant. Derived items get an `expires_at` column (or sidecar field) that only ever goes down. A new per-document cascade in `RAG` removes one document's points, companions, docstore nodes, source file, manifest row, caches and artifacts. The existing daily sweeper in `api.py` is rebuilt around documents and derived items; the Dashboard card and sidebar hint show the owner's due items.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (sessions store, hand-written `_ensure_*` migrations), Qdrant (`qdrant_client`, llama-index), pytest + anyio, React 19 + TanStack Query + vitest, the vendored audit library.

**Spec:** `deploy/docs/2026-11-05-retention-deadline-design.md` (sections "The rule" and "docint"). Code facts below are from `docint` main at v2.10.1.

## Global Constraints

- `DOCUMENT_RETENTION_DAYS`: default `365`; whole days 1–3650 or `off`; `off` only for development, logged as a warning at startup and stated in `config.audit`. Any other value stops startup (it never falls back to a period or to off).
- `RETENTION_NOTICE_DAYS`: default `30`, whole days 1–365. It is both the notice lead time and the grace period.
- Grace: nothing is deleted sooner than `RETENTION_NOTICE_DAYS` after the period took effect (`retention_state.set_at`, rewritten only when the period changes).
- No extension: no route, flag or admin action moves a deadline later. Reads, searches, exports and re-ingests of identical content never touch a deadline.
- Derived `expires_at` only ever decreases.
- A document is keyed by (physical collection, file hash as `mark_completed` records it). Payload hashes that belong to it (transcript JSONL hash, `media_file_hash`, PDF `source_doc_id`) are listed with it, so the cascade can find every point via `docint/core/summary/units.py` `source_file_hash`.
- Every deletion recorded as `delete` by the system (`audit.SYSTEM_CONTEXT`) before it is carried out; a failed record stops that item's deletion (fail closed); references and counts only, never content or file names in criteria.
- Copy: "the configured retention period"; never a reason or legal basis. UI strings in `frontend/src/i18n/en.ts` and `de.ts` (parity test).
- `COLLECTION_RETENTION` is no longer read; if set, startup logs one warning naming `DOCUMENT_RETENTION_DAYS`.
- Never edit `docint/utils/audit_event.py` / `audit_runtime.py`.
- One version bump: `pyproject.toml` 2.10.1 → 2.11.0 plus the `uv.lock` entry (hand-edited), in the last PR of the wave. Local tests need `RESPONSE_LANGUAGE=en`.

## Review Focus

- A social export (`dossier.json`, many postings) and a video with its `.nextext.jsonl` transcript and keyframes: deleting the document removes every point that `source_file_hash` maps to it, including keyframe and posting images, and nothing of a neighbouring document.
- An image whose `_images` point is shared by two documents: deleting one document drops its occurrence and keeps the point for the other.
- Restoring a Qdrant snapshot taken before this release into a collection whose index rows were already swept: the restored documents get index rows with the `set_at` time (never the restore time) and fall due on schedule.
- A document due while an ingest job runs on its collection: skipped and retried next sweep; never half-deleted.
- A chat turn citing a document that is already gone (deleted between retrieval and persist): the turn's expiry falls back to the collection's oldest live document, not to "never".

---

### Task 1: Settings, the rule, and `config.audit`

**Files:**
- Modify: `docint/utils/env_cfg.py` (replace the block at l.3154-3196: `RetentionConfig(days: int | None, notice_days: int)` with property `enabled`; `load_retention_env() -> RetentionConfig`; delete `RETENTION_WINDOWS`, `RetentionWindow`)
- Modify: `docint/core/retention.py` (replace the month rule with day rules; keep `SweepReport` shape extended)
- Modify: `docint/core/state/retention_state.py` (`window` → `period_days` Integer, migration in `docint/core/state/base.py` `_ensure_retention_state_period`), `docint/core/state/collection_owner_manager.py` (`retention_window` / `record_retention_window` → `retention_period() -> RetentionPeriodState | None`, `record_retention_period(days: int | None, *, now: datetime) -> RetentionPeriodState`)
- Modify: `docint/core/audit.py` `open_auditor` (criteria gain `document_retention_days`, `retention_notice_days`, `retention_since`)
- Modify: `.env.example` (l.415), `docker/compose.yaml` if it passes `COLLECTION_RETENTION`
- Test: `tests/test_env_cfg_retention.py`, `tests/test_retention.py`, `tests/test_retention_state_migration.py` (new)

**Interfaces:**
- Produces (in `docint/core/retention.py`):
  - `document_expires_at(ingested_at: datetime, days: int, *, set_at: datetime, notice_days: int) -> datetime` = `max(ingested_at + days, set_at + notice_days)`; raises on naive datetimes.
  - `inherit(current: datetime | None, *sources: datetime | None) -> datetime | None` — the minimum of the non-None values; `None` only when all are None.
  - `is_due(deadline: datetime | None, now: datetime) -> bool`; `is_warning(deadline: datetime | None, now: datetime, notice_days: int) -> bool`.
  - `RetentionPeriodState(days: int | None, set_at: datetime)`.

- [ ] **Step 1: Write the failing tests** — `test_default_period_is_365_days_and_notice_30`, `test_off_is_explicit_and_warns`, `test_garbage_stops_startup` (`"12m"`, `"0"`, `"abc"` raise `ValueError`), `test_collection_retention_is_ignored_with_a_warning`, `test_deadline_is_ingest_plus_period`, `test_grace_after_switch_on` (ingested 400 d ago, set today → due in 30 d), `test_inherit_takes_the_earliest_and_never_raises`, `test_period_change_rewrites_set_at_same_period_keeps_it`, `test_legacy_window_row_migrates` (a `retention_state` row with `window='6m'` gets `period_days` and keeps no month semantics), `test_config_audit_states_the_period`.
- [ ] **Step 2: Run** `RESPONSE_LANGUAGE=en uv run pytest tests/test_env_cfg_retention.py tests/test_retention.py tests/test_retention_state_migration.py -q` — FAIL.
- [ ] **Step 3: Implement.** Remove `add_months`, `expires_at`, the activity `RetentionClock`; stop calling `_record_activity` (leave the `last_activity_at` column in place, unused, documented).
- [ ] **Step 4: Run** the same — PASS; then `uv run pytest -q` and fix the callers it breaks (api.py imports, `retention_report.py`) minimally so the suite is green; Task 6 rewrites them.
- [ ] **Step 5: Commit** `feat(retention): a fixed period in days from ingest, on by default; COLLECTION_RETENTION retired`.

### Task 2: The document index and the payload clock

**Files:**
- Create: `docint/core/state/document_retention.py` (ORM `DocumentRetention`, table `document_retention`), `docint/core/state/document_retention_manager.py`
- Modify: `docint/core/state/__init__.py` (register the model), `docint/core/state/base.py` (no ALTER needed for a new table; add an index on `expires_at`)
- Modify: `docint/core/rag.py` — in `_ingest_docs` at both completion sites (PDF lane after `manifest.mark_completed` l.8136; streaming lane l.8174) call the manager; in `_persist_node_batches` (l.5611) stamp `docint_ingested_at` (float epoch seconds, set once per ingest run per file hash) into each node's metadata before upsert; `_images` occurrences (`images_service.py` `_occurrence` l.860) gain `ingested_at`; create a keyword payload index on `file_hash` and on `media_file_hash` and a float index on `docint_ingested_at` next to the existing `create_payload_index` calls (rag.py:6286)
- Modify: `docint/cli/ingest.py` (CLI ingests write index rows too, with `owner=None`)
- Test: `tests/test_document_retention_manager.py`, `tests/test_rag_unit.py` (new section), `tests/test_document_retention_backfill.py`

**Interfaces:**
- Produces:
  - `DocumentRetention` columns: `physical` (PK part), `file_hash` (PK part), `owner` (nullable, indexed), `logical`, `filename`, `point_hashes` (JSON list of str), `ingested_at` (DateTime UTC), `expires_at` (DateTime UTC, indexed).
  - `DocumentRetentionManager.record_ingest(physical: str, logical: str, owner: str | None, file_hash: str, filename: str, point_hashes: Sequence[str], *, now: datetime, period: RetentionPeriodState) -> None` — inserts; an existing row is left unchanged (identical re-ingest does not reset).
  - `.due(now: datetime, *, limit: int) -> list[DocumentRetention]`, `.warning_for(owner: str | None, now: datetime, notice_days: int) -> list[DocumentRetention]`, `.oldest_live(physical: str) -> DocumentRetention | None`, `.expiry_of(physical: str, file_hashes: Iterable[str]) -> dict[str, datetime]`, `.delete(physical: str, file_hash: str) -> None`, `.repair_from_payload(physical, logical, owner, docs: Iterable[PayloadDocument], *, period) -> int` (rows for documents Qdrant holds and the index lacks; time = payload `docint_ingested_at`, else the manifest's `updated_at` for a completed row, else `period.set_at`; never `now`).
  - `PayloadDocument(file_hash: str, point_hashes: frozenset[str], filename: str, ingested_at: datetime | None)`, produced by `RAG.payload_documents(physical: str) -> list[PayloadDocument]` (one scroll over the main collection grouping by `source_file_hash`).
- Backfill (one-off, inside the migration step of `_make_session_maker`'s caller at startup, not in `base.py` since it needs Qdrant): for every owned and unowned collection, `repair_from_payload` with `ingested_at` = the manifest's `updated_at` for a completed row (never earlier than the real completion, so safe), else `set_at`.

- [ ] **Step 1: Write the failing tests** — `test_ingest_records_one_row_per_document`, `test_reingest_of_identical_content_keeps_the_clock`, `test_same_file_in_two_collections_has_two_clocks`, `test_payload_carries_the_ingest_time` (in-memory `QdrantClient(location=":memory:")`), `test_image_occurrence_carries_its_document_and_time`, `test_cli_ingest_records_an_unowned_row`, `test_repair_uses_payload_time_then_set_at_never_now`, `test_backfill_uses_manifest_completion_when_present`.
- [ ] **Step 2: Run** `RESPONSE_LANGUAGE=en uv run pytest tests/test_document_retention_manager.py tests/test_document_retention_backfill.py -q` — FAIL.
- [ ] **Step 3: Implement.** `point_hashes` = every distinct `source_file_hash` value among the points that file's ingest wrote (collected in `_persist_node_batches` per batch), always including `file_hash` itself.
- [ ] **Step 4: Run** — PASS; `uv run pytest tests/test_rag_unit.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(retention): every document's deadline, in the sessions store and on its points`.

### Task 3: Derived items inherit

**Files:**
- Modify: `docint/core/state/conversation.py`, `report.py` (add `expires_at` DateTime nullable), `docint/core/state/base.py` (`_ensure_derived_expiry_columns`), `docint/core/state/session_manager.py` (`_persist_turn` l.1700 lowers the conversation's `expires_at`), `docint/core/state/report_manager.py` (`add_item` 491 / `add_items` 545 / `set_collection_overview_snapshot` 414 lower the report's), `docint/core/api.py` (report item add: resolve `chunk_id` → `file_hash` via the point payload before `add_item`; extract job ~6690: sidecar `expires_at`), `docint/core/extract/store.py` (`write` takes `expires_at`; `list`/`get` return it), `docint/core/topics/store.py` (`save` writes `expires_at` into `run.json`; `load`/`active` return it)
- Test: `tests/test_derived_expiry.py` (new), additions to `tests/test_api_reports.py`, `tests/test_extract_store.py`, `tests/test_topics_store.py`

**Interfaces:**
- Consumes: Task 1 `inherit`; Task 2 `expiry_of`, `oldest_live`.
- Produces: `def source_expiry(rag: RAG, physical: str, file_hashes: Iterable[str]) -> datetime | None` in `docint/core/retention_sources.py` (new): the earliest expiry among the given hashes; hashes with no live row (already gone, unknown) fall back to `oldest_live(physical).expires_at`; no hashes at all → `oldest_live`. Rules, exactly:
  - turn: its citations' `file_hash`; a turn without citations, or with `scope_chunk_ids` or `topic_focus` set on the conversation → `oldest_live`.
  - report: each item's sources (`chat_answer` `sources[].file_hash`; `entity_finding` / `hate_speech_finding` `chunk_id` resolved server-side at add time; `summary` / `topics` items and `collection_overview_snapshot` → `oldest_live`).
  - extract, topic run → `oldest_live` at build time; an extract keeps `EXTRACT_RETENTION_DAYS` too (the earlier wins).
- Existing rows: the migration sets `expires_at` once at startup — sessions from their citations (else `oldest_live`), reports `oldest_live` of their collection, extracts and topic runs `oldest_live`; rows of collections without documents get `set_at + notice_days`.

- [ ] **Step 1: Write the failing tests** — `test_turn_inherits_its_cited_documents`, `test_uncited_turn_takes_the_oldest_document`, `test_scoped_or_focused_session_takes_the_oldest`, `test_later_turn_never_raises_the_expiry`, `test_cited_document_already_gone_falls_back_to_oldest`, `test_report_item_resolves_chunk_ids_to_documents`, `test_summary_item_and_overview_take_the_oldest`, `test_extract_takes_the_earlier_of_its_two_deadlines`, `test_topic_run_records_its_expiry`, `test_migration_stamps_existing_rows`.
- [ ] **Step 2: Run** `RESPONSE_LANGUAGE=en uv run pytest tests/test_derived_expiry.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it and `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(retention): sessions, reports, extracts and topic runs never outlive their sources`.

### Task 4: The per-document cascade

**Files:**
- Modify: `docint/core/rag.py` (new `delete_documents`; reuse `artifact_gc` and the existing helpers named below)
- Modify: `docint/core/ingest/images_service.py` (remove one document's occurrences; delete a point when none remain)
- Test: `tests/test_rag_delete_documents.py` (new; in-memory Qdrant, real `SQLiteKVStore`, real `IngestManifest`, tmp source dir)

**Interfaces:**
- Consumes: Task 2 `DocumentRetention` rows (for `point_hashes`, `filename`).
- Produces: `RAG.delete_documents(physical: str, documents: Sequence[DocumentRetention]) -> DocumentDeletion` with `DocumentDeletion(points: int, image_points: int, image_occurrences: int, nodes: int, source_files: int, artifacts: int, collection_empty: bool)`. Steps, in order, each idempotent:
  1. main collection: delete points whose `file_hash` or `media_file_hash` is in `point_hashes` (filter on the indexed fields; any remaining point whose `source_file_hash` is in the set is deleted by id after a scroll, for payloads that only carry `source_doc_id`);
  2. `_dockv` companion: the same filter;
  3. `_images`: drop the occurrences that name these documents (`source_doc_id`, `media_file_hash`, `posting_uuid` mapped via the main points scrolled in step 1); delete points left without occurrences;
  4. docstore: delete every node (including coarse parents) whose metadata `file_hash` / `source_file_hash` is in the set (`doc_store.delete_document(node_id, raise_error=False)`, as `verify_collection(repair=True)` does at rag.py:7694-7736);
  5. summary map cache entries `doc:{hash}` / `doc:name:{filename}`, then `_bump_summary_revision(physical)`;
  6. source files: the payload `file_path`s found in step 1 that resolve inside `{qdrant_src_dir}/{physical}/` (no symlink escapes);
  7. ingest manifest rows (`collection`, `file_hash`) — new `IngestManifest.forget(collection: str, file_hashes: Iterable[str]) -> int`;
  8. PDF artifacts by the `still_referenced` rule (`artifact_gc.py`), skipping in-flight hashes;
  9. `_entities`: delete the companion and enqueue the existing resolution stage for the collection, so no canonical entity or alias survives from a deleted document (until it completes, entity views show the collection as unresolved);
  10. invalidate `_index_cache`, `_query_engine_cache`, keyword readiness, `_invalidate_ner_cache(physical)`;
  11. `collection_empty` = no point left in the main collection.

- [ ] **Step 1: Write the failing tests** — `test_pdf_document_goes_with_points_nodes_file_manifest_and_artifacts`, `test_neighbour_document_is_untouched`, `test_video_with_transcript_and_keyframes_goes_whole`, `test_social_export_goes_with_posting_images`, `test_shared_image_keeps_the_other_documents_occurrence`, `test_shared_pdf_artifact_survives_while_another_collection_uses_it`, `test_source_path_outside_the_collection_is_not_removed`, `test_entities_are_dropped_and_resolution_is_queued`, `test_second_run_is_a_noop`, `test_last_document_reports_collection_empty`.
- [ ] **Step 2: Run** `RESPONSE_LANGUAGE=en uv run pytest tests/test_rag_delete_documents.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; `uv run pytest tests/test_rag_unit.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(rag): delete one document with everything that is only its`.

### Task 5: The sweep

**Files:**
- Modify: `docint/core/api.py` (lifespan l.~217-225: record the period, start the sweeper whenever `retention.enabled`; replace `_retention_sweep` l.322 and `_purge_if_still_expired` l.366; keep `_RETENTION_FIRST_SWEEP_S`, `_RETENTION_SWEEP_INTERVAL_S`, `run_retention_loop`, the busy check l.342, `_purge_guard`)
- Modify: `docint/core/retention.py` (`SweepReport` gains `documents`, `sessions`, `reports`, `extracts`, `topic_runs`, `collections`, `repaired`)
- Modify: `docint/core/audit_targets.py` if a `document` target for several hashes needs a helper
- Test: `tests/test_api_collections_ownership.py` (replace the window sweep tests l.681-880 with `_run_sweep` over the new rule), `tests/test_retention.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `async _retention_sweep(retention: RetentionConfig, state: RetentionPeriodState, jobs: IngestJobManager) -> SweepReport`, per run:
  1. repair the index from payloads for every collection (Task 2 `repair_from_payload`);
  2. for each collection with due documents and not busy: re-read the rows under `_purge_guard`, record `delete` `retention.document` (target kind `document`, ids `<logical>:<file_hash>`, container `<logical>`, criteria `period_days`, per-step counts), then `delete_documents`; if `collection_empty`, record `delete` `retention.collection` and run the existing `_purge_collection` (it also clears sessions, reports, PDFs and the ownership row);
  3. due sessions, reports (with their PDFs via `_discard_report_pdfs`), extracts, topic runs: record `delete` `retention.session` / `retention.report` / `retention.extract` / `retention.topic_run`, then the existing delete paths;
  4. log `Retention sweep complete | period=<d>d documents=… sessions=… reports=… extracts=… topic_runs=… collections=… repaired=… skipped_busy=… failed=…`.
  A failed record or step leaves that item for the next sweep; the others continue.

- [ ] **Step 1: Write the failing tests** — `test_due_document_is_deleted_and_recorded_before`, `test_not_due_during_the_grace_period`, `test_busy_collection_is_skipped`, `test_last_document_takes_the_collection_and_its_sessions`, `test_due_session_and_report_go_with_their_records`, `test_failed_record_deletes_nothing`, `test_restored_documents_without_rows_are_repaired_not_reset`, `test_unowned_collection_expires_too`, `test_off_starts_no_sweeper`.
- [ ] **Step 2: Run** `RESPONSE_LANGUAGE=en uv run pytest tests/test_api_collections_ownership.py tests/test_retention.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS, then `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(retention): the daily sweep deletes due documents and what derives from them`.

### Task 6: Notice API, `/config`, the operator report

**Files:**
- Modify: `docint/core/api.py` (`GET /collections/retention` l.2284 and models l.1459-1479 replaced; `/config` field l.1955/2143), `docint/cli/retention_report.py`, `Makefile` (l.144-149: `retention-report` takes `DAYS=` instead of `WINDOW=`), `pyproject.toml` entry unchanged
- Test: `tests/test_api_collections_ownership.py` (listing tests l.531-678 rewritten), `tests/test_retention_report_cli.py`

**Interfaces:**
- Produces:
  - `GET /collections/retention?all=false` → `RetentionOut{period_days: int | None, notice_days: int, items: list[RetentionItemOut]}`, `RetentionItemOut{kind: Literal["document","session","report"], collection: str, owner: str | None, id: str, name: str, expires_at: datetime, warning: bool}`; `name` is the file name, session title, or report title. Sorted by `expires_at`. Only the caller's items; `all=true` for admins, recorded as today (`admin` `collections.retention_all`). Reading never records activity.
  - `/config` → `document_retention_days: int | None`, `retention_notice_days: int` (replacing `collection_retention`).
  - `retention-report [--days N]`: per item OWNER / COLLECTION / KIND / NAME / INGESTED-OR-CREATED / DUE / NOTE, plus unowned collections; `--days` shows what that period would do, applying the grace exactly as switching it on now would; records nothing.

- [ ] **Step 1: Write the failing tests** — `test_owner_sees_only_their_due_items`, `test_warning_flags_items_within_the_notice`, `test_admin_all_is_recorded`, `test_listing_moves_no_clock`, `test_config_exposes_the_period`, `test_report_cli_lists_documents_sessions_reports`, `test_report_cli_hypothetical_days_applies_grace`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(retention): owners and operators see what falls due and when`.

### Task 7: Dashboard card and sidebar hint

**Files:**
- Modify: `frontend/src/api/types.ts` (l.836-862), `frontend/src/api/collections.ts:41`, `frontend/src/hooks/useRetention.ts`, `frontend/src/components/dashboard/RetentionCard.tsx`, `frontend/src/layout/Sidebar.tsx` (l.48, 65, 254-265), `frontend/src/i18n/en.ts` / `de.ts` (keys below)
- Create: `frontend/src/components/dashboard/RetentionCard.test.tsx`
- Test: also `frontend/src/routes/Dashboard.test.tsx:119-210`, `frontend/src/layout/Sidebar.test.tsx:753-812`

**Interfaces:**
- Consumes: Task 6 `RetentionOut`.
- Produces: the card lists the owner's items with `warning`, grouped by collection, each with kind, name and date (`SHOWN = 8`, then `retention_more`); renders nothing when `document_retention_days` is null; the sidebar count is items with `warning` outside the selected collection.
- Copy (exact; replace `retention_rule`, `retention_never`; keep the other keys' names):
  - `dashboard.retention_rule` en: "Documents are deleted {days} days after they were added. Chats, reports and extracts built from them are deleted with the earliest of their sources." de: "Dokumente werden {days} Tage nach dem Hinzufügen gelöscht. Chats, Reports und Extrakte, die darauf aufbauen, werden mit ihrer frühesten Quelle gelöscht."
  - `dashboard.retention_due` en: "Deleted on {date}" de: "Wird am {date} gelöscht"
  - `dashboard.retention_kind_document` / `_session` / `_report` en: "Document" / "Chat" / "Report"; de: "Dokument" / "Chat" / "Report"
  - `dashboard.retention_empty` en: "Nothing is due for deletion in the next {days} days." de: "In den nächsten {days} Tagen wird nichts gelöscht."

- [ ] **Step 1: Write the failing tests** — card: `renders the owner's due items grouped by collection`, `shows kind, name and date`, `renders nothing when retention is off`, `shows the empty state with the notice days`; sidebar: `counts due items outside the selected collection`; i18n parity passes.
- [ ] **Step 2: Run** `cd frontend && pnpm test -- RetentionCard Dashboard Sidebar i18n` — FAIL.
- [ ] **Step 3: Implement** within the existing design system (`@infra/ui` primitives, drawn icons only).
- [ ] **Step 4: Run** — PASS; `pnpm lint && pnpm build` — PASS.
- [ ] **Step 5: Commit** `feat(ui): the Dashboard shows what is deleted when`.

### Task 8: Docs and release

**Files:**
- Modify: `docs/retention.md` (rewritten: the rule, what counts as a document, inheritance, the sweep, the cascade, restore behaviour, upgrading, the report; "Not covered" updated — the deletion record now exists), `docs/configuration.md` (l.849-857), `docs/api-reference.md` (l.65, 146-154, 200-254), `docs/audit.md` (operation table l.80-81, target kinds, "Retention sweep" l.588-594), `docs/ui-guide.md` (l.69-81), `docs/cli-reference.md` (retention-report), `CLAUDE.md` where it names collection retention, `.env.example`, `pyproject.toml` 2.11.0, `uv.lock`
- Test: none new; the gate.

- [ ] **Step 1: Update the docs**; check every claim against the code (grep each env var, route, operation name).
- [ ] **Step 2: Gate:** `make verify` then `RESPONSE_LANGUAGE=en make test`. Expected: both pass.
- [ ] **Step 3: Commit** `docs(retention): the fixed retention period; release v2.11.0`.
