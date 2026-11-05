# Retention deadline: Nextext Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** What `make cli` leaves on the `tmp-jobs` scratch volume is deleted the configured number of days after it was written (default 365), recorded; `make cli-clean` deletes it now; each run says when its outputs go.

**Architecture:** `make cli` runs in `/tmp/nextext-cli/`. A new module `nextext/api/cli_files.py` scans that directory (and the legacy `/tmp/output/`) and deletes expired entries; the backend runs it at startup and daily from the lifespan, recording through the job manager's audit sink. A new console script `nextext-cli-clean` does the on-demand delete with the operator as actor, emitting like `nextext-cli`.

**Tech Stack:** Python 3.12, FastAPI lifespan, pytest, the vendored audit library, Docker Compose, Make.

**Spec:** `deploy/docs/2026-11-05-retention-deadline-design.md` (sections "The rule" and "Nextext").

## Global Constraints

- `NEXTEXT_CLI_RETENTION_DAYS` default `365`; `off` only for development (warning + `config.audit`).
- `RETENTION_NOTICE_DAYS` default `30`: nothing written before the rule took effect is deleted sooner than that after it took effect.
- No extension path.
- Every deletion recorded as `delete`; fail closed: a failed record stops the sweep before the files go.
- Env read in `nextext/utils/env_cfg.py` (the repo's one env module).
- One version bump: `pyproject.toml` 1.15.1 → 1.16.0 (and the `uv.lock` entry, hand-edited), in the last PR of the wave.
- Never sweep `/tmp/nextext-jobs` (the job manager's own) or `/tmp/matplotlib`; never follow symlinks.

## Review Focus

- A CLI run in progress while the daily sweep runs: files of a run younger than the period are never touched (age is the run directory's own time, not a child's).
- `/tmp/nextext-cli` missing on a fresh volume: the sweep is a no-op, the CLI creates it.
- An entry the backend cannot delete (permissions): recorded as `error`, the rest still go.
- `make cli-clean LEGACY=1` on a `/tmp` that holds the audit stream lock file: the lock file is never removed.
- Two backends on the same volume: the sweep runs only where the job manager holds its directory lock (`JobManager._lock_root`).

---

### Task 1: Settings and the CLI's working directory

**Files:**
- Modify: `nextext/utils/env_cfg.py` (`load_cli_retention_env() -> CliRetentionConfig` with `days: int | None`, `notice_days: int`), `Makefile` (`cli` target: `exec -w /tmp/nextext-cli` after `mkdir -p` via `sh -c`, comment updated), `docker/compose.yaml` (`NEXTEXT_CLI_RETENTION_DAYS: ${NEXTEXT_CLI_RETENTION_DAYS:-365}`, `RETENTION_NOTICE_DAYS: ${RETENTION_NOTICE_DAYS:-30}` on the backend), `.env.example`
- Modify: `nextext/cli.py` (after the last output is written, print to stderr: "Outputs are deleted on <YYYY-MM-DD> (retention period <N> days)." in the run's `RESPONSE_LANGUAGE`; nothing when off)
- Test: `tests/test_env_cfg.py`, `tests/test_cli.py`, `tests/test_makefile.py` (or the repo's existing Makefile/compose test)

**Interfaces:**
- Produces: `CLI_ROOT = Path("/tmp/nextext-cli")` and `LEGACY_OUTPUT = Path("/tmp/output")` in `nextext/api/cli_files.py` (created in Task 2; Task 1 only references the path string in the Makefile); `CliRetentionConfig(days: int | None, notice_days: int)`.

- [ ] **Step 1: Write the failing tests** — `test_cli_retention_defaults` (365, 30), `test_cli_retention_off`, `test_cli_retention_rejects_garbage` (non-integer → `ValueError` at startup, not a silent default), `test_cli_prints_its_deletion_date` (en and de), `test_make_cli_runs_in_its_own_directory` (`-w /tmp/nextext-cli` in the target).
- [ ] **Step 2: Run** `uv run pytest tests/test_env_cfg.py tests/test_cli.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(cli): runs in a directory of their own and say when their outputs are deleted`.

### Task 2: The sweep

**Files:**
- Create: `nextext/api/cli_files.py`
- Modify: `nextext/api/main.py` (lifespan: run `CliFileSweeper.sweep` after `job_manager.start()` and then every 24 h in a background task, cancelled at shutdown before the chain closes), `nextext/api/jobs.py` (expose the audit sink to the sweeper; the sweep runs only while `_lock_root()` holds)
- Test: `tests/test_cli_files.py`

**Interfaces:**
- Consumes: Task 1's config; `AuditSink` from `nextext/api/jobs.py`.
- Produces: `class CliFileSweeper(root: Path = CLI_ROOT, legacy: Path = LEGACY_OUTPUT, *, days: int, notice_days: int, state: Path = CLI_ROOT / ".retention-since")` with `sweep(now: float, sink: AuditSink) -> CliSweep` (`removed: list[str]`, `failed: list[str]`); and `scan(now: float) -> list[CliEntry]` (`name`, `path`, `written_at`, `due_at`) used by the dry run and by `nextext-cli-clean`. An entry is a direct child of `root` or of `legacy`; its time is its own `st_mtime` (directories: the newest mtime anywhere below, so a run still writing is young). Records: `delete` / `cli.sweep`, by the system, target kind `cli_file`, ids = entry names (continuation above 200).

- [ ] **Step 1: Write the failing tests** — `test_expired_run_directory_is_removed_and_recorded`, `test_young_run_stays`, `test_a_run_still_writing_is_young` (old dir, new child), `test_legacy_output_is_swept_too`, `test_grace_before_the_rule_took_effect` (state written now; a 400-day-old entry survives until now + 30 d), `test_symlink_is_not_followed`, `test_job_directory_is_never_touched`, `test_record_failure_deletes_nothing`, `test_off_does_nothing`.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli_files.py -q` — FAIL.
- [ ] **Step 3: Implement** `cli_files.py`; wire it into the lifespan.
- [ ] **Step 4: Run** the file, then `uv run pytest -q` — PASS.
- [ ] **Step 5: Commit** `feat(api): delete what CLI runs left once the retention period has passed`.

### Task 3: `make cli-clean`, docs, release

**Files:**
- Create: `nextext/cli_clean.py` (console script `nextext-cli-clean = "nextext.cli_clean:main"` in `pyproject.toml`)
- Modify: `Makefile` (`cli-clean` target: `exec backend nextext-cli-clean $(if $(RUN),--run $(RUN)) $(if $(LEGACY),--legacy)` with `AUDIT_CLI_USER` like `cli`), `docs/cli.md`, `docs/configuration.md`, `CLAUDE.md` (the `make cli` and env sections), `README.md` if it shows `make cli`, `pyproject.toml` version 1.16.0, `uv.lock` entry
- Test: `tests/test_cli_clean.py`

**Interfaces:**
- Consumes: Task 2 `CliFileSweeper.scan`, the CLI's emitter setup in `nextext/cli.py` (`load_audit_env`, `emitter_factory`, `emit_config_event`).
- Produces: `main(argv: list[str] | None = None) -> int`: without options removes everything under `CLI_ROOT` and `LEGACY_OUTPUT`; `--run NAME` one entry; `--legacy` also removes regular files directly in `/tmp` except names starting with `.` and the fixed keep-list `{"audit-stream.lock"}` (plus any other file the backend image writes there, found in Step 1). Records `delete` / `cli.clean`, actor the operator (`AUDIT_CLI_USER`, via `cli`), before deleting; exit 0 on success, 1 when a record or a deletion failed.

- [ ] **Step 1: Verify** in the backend image which regular files exist directly in `/tmp` during normal operation (`docker compose exec backend ls -la /tmp`), and set the keep-list from it.
- [ ] **Step 2: Write the failing tests** — `test_clean_all`, `test_clean_one_run`, `test_legacy_keeps_the_lock_file_and_dotfiles`, `test_unknown_run_exits_1_and_records_nothing`, `test_clean_is_recorded_with_the_operator`.
- [ ] **Step 3: Run** — FAIL. **Step 4: Implement**, docs, version. **Step 5: Run** — PASS.
- [ ] **Step 6: Gate:** `make verify` then `make test`. Expected: both pass.
- [ ] **Step 7: Commit** `feat(cli): make cli-clean deletes a run's files now, recorded; release v1.16.0`.
