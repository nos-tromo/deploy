# Ghostwriter vision, stage 3: video and audio through Nextext — Ghostwriter v0.6.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ghostwriter v0.6.0 takes video and audio attachments.
- It streams each clip from workbench to Nextext for one job, streams the transcript and keyframes back into workbench as derived files of the clip, and deletes the job at once.
- The model reads the clip's facts in the attachment line, its transcript with `read_document`, and its frames with `view_file`.
- Without Nextext, or against a workbench before 0.9.0, Ghostwriter behaves exactly as v0.5.0.

**Architecture:**
- **`ghostwriter/nextext.py`** holds two pieces:
  - `NextextClient`: create, poll, artifact, delete and list over httpx, with Nextext's Bearer service token;
  - `NextextHealth`: a background probe of Nextext's `/health`, and the offer, which also needs workbench 0.9.
- **`AttachmentProcessor`** gains a media step before the prepare, on slots of its own, with the Nextext delete in a shielded `finally`. It owns the set of live Nextext job ids.
- **`reconcile`** deletes Ghostwriter's Nextext jobs that no task owns. It runs once at start, in the background, before any clip may create a job, and again in the daily sweep.
- **workbench v0.9's media manifest** settles the attachment; the facts line and workbench's tools do the rest.

**Builds on:** Ghostwriter v0.5.0 (stage 2), merged, as `2026-11-21-ghostwriter-vision-app-v0.5.md` plans it: `workbench.Kind`/`Unit`, `unit` in `Manifest` and `STORED_MANIFEST_KEYS`, `attachment_facts(..., name)`, the 390 s `PREPARE_TIMEOUT_S`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, httpx and anyio (`MockTransport` in tests), loguru; React 19, react-query, `@infra/ui` v0.26 (`AttachmentTile` kinds `video` and `audio`), vitest.

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§2 anti-goals, §5.3, §7.3 Media, §7.5, §8 stage 3, §9, §10 check 7) and `deploy/docs/decisions/0009-ghostwriter-sees-images-and-media.md`. Contracts consumed exactly:
- `deploy/docs/superpowers/plans/2026-11-21-ghostwriter-vision-workbench-v0.9.md` § Contract for Ghostwriter;
- Nextext's API under `/api/v1`: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}/artifacts/{name}`, `DELETE /jobs/{id}`, `GET /health` (Nextext `nextext/api/routes/jobs.py`, `routes/health.py`, `schemas.py`);
- `deploy/docs/superpowers/plans/2026-11-21-ghostwriter-vision-federation-media.md` (the `nextext-ghostwriter-token` group, which deploy 0.14.0 needs).

Every path below is relative to the `ghostwriter` repo root unless it starts with `deploy/`. Read `CLAUDE.md` ("What changes together", Invariants, Testing) before Task 1.

## Decisions

The lead's calls (the owner may correct them):
- **E0: spike S3 gates the stage.**
  - Task 0 is a checklist run on the GPU staging host (Whisper and diarization), not code. Its clips: 30 s with speech and on-screen text, audio only, a silent video, 50 MiB.
  - Record per clip: the job time (queue included), the artifacts produced (present or 404, sizes), `skipped` / `skip_reason_code`; then `/health` with Whisper down, what a `DELETE` of a running job does, and whether `GET /jobs` lists only the caller's jobs.
  - Stop rule: if an answer contradicts a contract of this plan, stop and report before Task 1.
- **E5: Ghostwriter v0.6.0.**
  - Settings: `NEXTEXT_API_BASE` (compose default `http://nextext-backend:8000/api/v1`; empty is off), `NEXTEXT_API_KEY` (at least 32 characters, through `_secret`), `NEXTEXT_TIMEOUT`, the whole job budget in seconds (default 900: upload, queue, run, downloads). Per-request timeouts are constants.
  - `NextextClient` (async httpx, `trust_env=False`, Bearer, `forward_headers`):
    - `create`: a hand-built multipart, field `file` named `media.<ext>` and field `options`;
    - `poll`: terminal statuses `completed`, `failed`, `cancelled`, `interrupted`; a gone job counts as failed;
    - `artifact`: a stream, capped at 8 MiB for the jsonl and 32 MiB for the zip; 404 gives `None`;
    - `delete` (404 is fine), `list_jobs`, and a health probe on `/health`, HTTP 200 only, like `Vision.probe`: it never blocks start and probes again after a failure, with backoff.
  - The offer:
    - media is offered only while the base and the key are set and the probe is healthy;
    - `upload.MEDIA_TYPES` holds the 11 suffixes, and `attachment_kind` gives `media`;
    - the 50 MiB cap is unchanged; `/api/config` gains `media_types` and `media: bool`.
  - Processing, between the hand-over and the prepare:
    - an audit `disclose` child, recipient `nextext:transcribe` (criteria kind, bytes);
    - `workbench.view` streamed into `create`, with no spool, then polling within `NEXTEXT_TIMEOUT`, then each artifact streamed into `workbench.derived`;
    - in a shielded `finally`: `delete` and an audit `delete` child (`chat.nextext`, the job id as a reference only); then the prepare.
    - Failure tokens: `nextext_unavailable`, `nextext_failed`, `undecodable_media`, `timeout`. `skipped` with `vad_no_speech` is no failure: the clip is `ready` and the facts say "no speech".
  - Reconcile, at start (before `processor.requeue()`) and in the daily sweep (before `_sweep_chats`): `list_jobs()`, then delete every Ghostwriter job that no running task owns and whose `created_at` is older than `NEXTEXT_TIMEOUT` + 60 s. It is recorded as a system `delete` / `chat.nextext` with counts only.
  - The facts line: `video 02:14, 8 frames; transcript`, `audio 31:07; transcript`, `video 00:40; no speech`. The model reads the transcript with `read_document` and looks at frames with `view_file`; a prompt sentence on clips goes out with vision only.
  - UI: `video`/`audio` tiles by suffix (server kind `media`); a "Transcribing …" hint, since processing takes longer; no lightbox and no thumbnail for clips, the tile's icon only; en/de.
  - Docs and release: audit rows, data flow, retention, version 0.6.0, the Operators notes.

Calls made in this plan:
1. **`poll` reads `GET /jobs` and picks its job.** The list item carries `status`, `error_code`, `skipped` and `skip_reason_code`. A completed `GET /jobs/{id}` carries the whole result, the transcript included, which Ghostwriter must not hold. A job missing from the list is gone, and fails with `nextext_failed`.
2. **`keyframes_max` is 19, not 20:** workbench keeps at most 19 frames, since one prepare job has 20 outputs and one is the transcript (v0.9 plan, call 2).
3. **Clips have their own `MEDIA_SLOTS = 2`.** A clip never holds a prepare slot while it is at Nextext, so a picture attached meanwhile is ready as fast as in v0.5.0. The `NEXTEXT_TIMEOUT` budget starts when the clip's job takes its media slot.
4. **The start reconcile does not block start-up.** It runs in the background as soon as the probe passes (at most `START_WAIT_S`, 120 s). Every clip's `create` waits for it, so at start it deletes every job listed, with no grace window: no task of this process can own one yet. The sweep's reconcile spares owned ids and jobs younger than `NEXTEXT_TIMEOUT` + 60 s.
5. **Media is offered only against a workbench whose discovery names version 0.9.0 or later.**
6. **A clip is processed with or without vision**, since the transcript is text. Its frames reach the model only through `view_file`, that is, with vision.
7. **An artifact over its cap is left out.** The cap is checked on the declared length first, then on the stream. The clip is `ready` with what remains, logged with the token `artifact_too_large`.
8. **A clip requeued at start runs the whole round trip again**, and workbench replaces its artifacts.
9. **A cancelled clip job still deletes its Nextext job and records that delete**, shielded. It is the one thing a cancelled job still writes.
10. **The user's bubble keeps today's chip for a clip.**

## Global Constraints

- Branch `feat/vision-stage3` from a `main` that holds v0.5.0; one PR, opened after Task 0's checklist passed. `pyproject.toml` `version = "0.6.0"` and `uv lock` in Task 9 only; the tag is minted on merge.
- Python 3.11 only; ruff with Google docstrings (`D`) and annotations (`ANN`); pyrefly strict; 120-character lines. `make verify` and `make test` green before every push (`git add` new files first).
- One process, one uvicorn worker. The processor's tasks, the owned job ids, the probe and the audit chain live in it.
- Audit fail-closed: a hand-over to Nextext that cannot be recorded is not sent, and a reconcile that cannot be recorded deletes nothing. Events hold references only: ids, counts, kinds, tokens.
- Logs hold ids, counts, statuses and tokens (loguru `{}` placeholders). Never a file name, a transcript line, a Nextext error text or a key.
- No file bytes kept, none spooled, none read whole: the clip streams workbench → Nextext chunk by chunk, and each artifact streams Nextext → workbench the same way.
- Nextext sees the clip as `media.<ext>` only, never the user's file name.
- Every workbench call carries the chat's `X-Session-Id` (`CallFor`). Every Nextext call carries `Authorization: Bearer <NEXTEXT_API_KEY>`, `X-Audit-Purpose` and `X-Request-ID` (`forward_headers`).
- R15: frames reach the model only through `view_file` with `X-Accept-Images: 1`, sent exactly when images are offered, as in v0.3.0.
- Nothing is fetched at runtime. Nextext is reached only at `NEXTEXT_API_BASE` on `inference-net`; there is no network change.
- Vendored files are never edited here: `ghostwriter/utils/audit_*.py`, `make/common.mk`, `scripts/bundle-lib.sh`, `frontend/eslint.config.js`, the `[tool.ruff]`/`[tool.pyrefly]` sections.
- Test data is invented: names such as `harbour-tour.mp4` and `voice-memo.m4a`, bytes such as `b"\x00\x00\x00\x18ftypmp42" + bytes(200)`, and fakes in `tests/fakes.py`. No absolute local path in any committed file or message.
- Spec values:
  - the media suffixes `.mp4 .mov .m4v .webm .mkv .mp3 .m4a .wav .ogg .opus .flac`, with the types of workbench's `renders.MEDIA_TYPES`;
  - a 50 MiB cap; `NEXTEXT_TIMEOUT` 900; the artifact caps jsonl 8 MiB, zip 32 MiB;
  - the Nextext options exactly `{"keyframes": true, "visual_context": false, "summarization": false, "keyframes_max": 19}`;
  - the token at least 32 characters.
- UI strings in `en.ts` and `de.ts` (German "Sie"); `i18n.test.ts` keeps parity.
- Commit messages conventional, no real data.

## Review Focus

1. **Nextext restarts while a clip is processing** (its jobs live in memory only). Expect the job missing from `GET /jobs` to fail the clip with `nextext_failed` at the next poll, not after 15 minutes; the delete's 404 is fine and is recorded. Test in Task 5.
2. **The chat is deleted while its clip is at Nextext.** Expect:
   - the task cancelled, with nothing written to the gone attachment and no error-level log;
   - the Nextext job still deleted, and its `delete` / `chat.nextext` recorded.

   Test in Task 5.
3. **A clip with no speech** (`skipped`, `vad_no_speech`, `docint.jsonl` 404). Expect `ready` and the line `video 00:40, 3 frames; no speech`, never `failed`. Tests in Tasks 5 and 7.
4. **A picture attached while two clips transcribe.** Expect it ready within its usual prepare, not after the clips. Test in Task 5.
5. **Ghostwriter restarts with a clip processing.** Expect:
   - the orphaned Nextext job deleted by the start reconcile;
   - the requeued clip's new job never deleted by that reconcile, and the clip ready.

   Test in Task 6.

---

### Task 0: Spike S3 on the staging host (a checklist, no code)

**Files:** none committed. Results go into the PR description and to the lead.

The clips are synthetic, made on the host at test time and deleted afterwards. Speech is a text-to-speech tool (for example `espeak-ng`) reading invented sentences; on-screen text is ffmpeg's `drawtext`; pictures are `lavfi` test sources. No recording of a person.

- [ ] **Step 1: Prepare.**
  - A Nextext ≥ 1.20 with Whisper and diarization reachable, and a `ghostwriter=<token>` entry in its `NEXTEXT_SERVICE_TOKENS` (`openssl rand -hex 32`).
  - A shell in a container on `inference-net` with `curl`.
  - Four clips:
    - A: 30 s, 1080p, with speech and burned-in text;
    - B: audio only, `.m4a`, 60 s of speech;
    - C: a silent video, 20 s;
    - D: 1080p with speech, about 50 MiB.
  - E: a 4K version of A, for the zip size.
- [ ] **Step 2: Run each clip** as Ghostwriter will.
  - The request: `POST /api/v1/jobs`, chunked multipart with `options` = `{"keyframes":true,"visual_context":false,"summarization":false,"keyframes_max":19}` and `file` = `media.<ext>`, `Authorization: Bearer`, `X-Audit-Purpose: ghostwriter chat`, `X-Request-ID`.
  - Then poll `GET /api/v1/jobs` every 2 s, fetch both artifacts, and `DELETE`.
  - Record per clip:
    - `created_at`, `started_at` and `finished_at` (queue and run time); `status`, `error_code`, `skipped`, `skip_reason_code`;
    - `docint.jsonl` (status, bytes, the keys of its first line); `keyframes.zip` (status, bytes, member names, the largest frame's size and pixel dimensions, whether `manifest.json` is there).
- [ ] **Step 3: Probe the edges.**
  - Clip D with a docint ingest job queued first: the queue time.
  - `GET /api/v1/health` with Whisper stopped: is it still 200?
  - `DELETE` of a running job: its answer; does the pipeline go on? Is the concurrency slot freed (a second job starts at once)? Is the upload removed?
  - `GET /api/v1/jobs` with docint's token while a Ghostwriter job runs: is only the caller's job listed?
  - A chunked upload with no `Content-Length`: accepted?
- [ ] **Step 4: Apply the stop rule.** Stop and report to the lead before Task 1 if any of these holds:
  - an artifact name or the `manifest.json` shape differs from v0.9's contract;
  - a member is not `frame_NNN.jpg` or `manifest.json`;
  - a list item lacks `status`, `error_code`, `skipped`, `skip_reason_code` or `created_at`;
  - clip D's `keyframes.zip` passes 32 MiB, or its `docint.jsonl` 8 MiB;
  - clip D takes more than 900 s from `created_at` to completed with an idle queue;
  - `GET /jobs` lists another caller's jobs;
  - a chunked upload is refused;
  - `DELETE` of a running job answers anything but 204.

  Clip E passing 32 MiB is no stop: the plan leaves its frames out (call 7). Record it.

### Task 1: Nextext settings

**Files:**
- Modify:
  - `ghostwriter/config.py` (`Settings`, after `workbench_timeout_s`; `load_settings`), `ghostwriter/audit.py` (`_operating_mode` gains `"media": settings.nextext_enabled`);
  - `.env.example` (a `--- Nextext ---` block after `--- workbench ---`), `docker/compose.yaml` (the backend's `environment`), `docs/configuration.md`, `tests/conftest.py` (`make_settings`'s docstring).
- Test: `tests/test_config.py`, `tests/test_scaffold.py`

**Interfaces:**
- Produces:
  - `Settings.nextext_api_base: str` (`""` is off), `Settings.nextext_api_key: str | None = field(repr=False)`, `Settings.nextext_timeout_s: float`; the property `nextext_enabled`, true when both base and key are set.
  - `NEXTEXT_BASE_ENV = "NEXTEXT_API_BASE"`, `NEXTEXT_KEY_ENV = "NEXTEXT_API_KEY"`, `NEXTEXT_TIMEOUT_ENV = "NEXTEXT_TIMEOUT"`.
  - `_nextext_base(environ)`: `(_get(...) or "").rstrip("/")`. There is no default in code, since compose sets it.
  - `_nextext_timeout(environ) -> float`: default 900. A value that is not a number from 60 to 86400 raises `ValueError("NEXTEXT_TIMEOUT must be a number of seconds from 60 to 86400.")`.
- `make_settings` leaves Nextext off (no key in its environment); media tests pass `nextext_api_base="http://nextext.test/api/v1", nextext_api_key=NEXTEXT_KEY`.

- [ ] **Step 0: Branch.** `git fetch origin && git switch main && git pull --ff-only && git switch -c feat/vision-stage3`.
- [ ] **Step 1: Write the failing tests.**
  - `test_defaults_match_the_contract` gains `(s.nextext_api_base, s.nextext_api_key, s.nextext_timeout_s, s.nextext_enabled) == ("", None, 900.0, False)`.
  - `test_nextext_is_on_with_a_base_and_a_key`: a trailing `/` is stripped; a key of 31 characters raises `NEXTEXT_API_KEY must be at least 32 characters long.`; `repr(settings)` holds no key.
  - `test_nextext_timeout_is_seconds_from_60_to_86400`: `"1800"` gives 1800.0; `"59"`, `"x"`, `"inf"` and `"86401"` raise.
  - `test_compose_matches_the_contract` gains `NEXTEXT_API_BASE: ${NEXTEXT_API_BASE-http://nextext-backend:8000/api/v1}` (colon-less, so an empty value turns clips off), `NEXTEXT_API_KEY: ${NEXTEXT_API_KEY:-}` and `NEXTEXT_TIMEOUT: ${NEXTEXT_TIMEOUT:-900}`.
  - `test_env_example_documents_nextext`: the lines `# [secret] [shared: Nextext NEXTEXT_SERVICE_TOKENS]`, `# NEXTEXT_API_KEY=`, `# NEXTEXT_API_BASE=http://nextext-backend:8000/api/v1` and `# NEXTEXT_TIMEOUT=900` are present.
- [ ] **Step 2: Run** `uv run pytest tests/test_config.py tests/test_scaffold.py -q`. Expected: the new asserts FAIL.
- [ ] **Step 3: Implement.** The `.env.example` text, verbatim:

```
# --- Nextext ---

# Nextext transcribes attached video and audio and takes a video's frames.
# The key is the same value as Ghostwriter's entry in Nextext's
# NEXTEXT_SERVICE_TOKENS (ghostwriter=<token>); make env fills both in.
# Unset, video and audio are not offered; a key under 32 characters stops
# startup.
# [secret] [shared: Nextext NEXTEXT_SERVICE_TOKENS]
# NEXTEXT_API_KEY=

# Nextext's API on inference-net. Empty turns video and audio off.
# NEXTEXT_API_BASE=http://nextext-backend:8000/api/v1

# The longest one clip may take at Nextext, in seconds: the upload, the
# wait behind other jobs, the transcription and the downloads.
# NEXTEXT_TIMEOUT=900
```

  `docs/configuration.md`: three rows, saying the same; that `NEXTEXT_JOB_CONCURRENCY` (Nextext's, 1) queues clips behind docint's ingest within this budget; and that clips also need workbench 0.9.0.
- [ ] **Step 4: Run** `uv run pytest -q` and `python3 -B ../deploy/scripts/check_env_examples.py .` (deploy 0.14.0). Expected: PASS, and the check clean.
- [ ] **Step 5: Commit** `feat(config): NEXTEXT_API_BASE, NEXTEXT_API_KEY and NEXTEXT_TIMEOUT`

### Task 2: The Nextext client

**Files:**
- Create: `ghostwriter/nextext.py`, `tests/test_nextext.py`
- Modify: `tests/fakes.py` (`FakeNextext`)

**Interfaces:**
- Produces in `nextext.py`:
  - The constants (`Final`):
    - `MEDIA_NAME = "media"`, `JOB_OPTIONS` (the Global Constraints' value), `JSONL = "docint.jsonl"`, `KEYFRAMES = "keyframes.zip"`, `ARTIFACT_CAPS = {JSONL: 8 * 1024 * 1024, KEYFRAMES: 32 * 1024 * 1024}`;
    - `TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})`;
    - per request: `CREATE_READ_S = 120.0`, `CALL_S = 10.0` (list, delete), `ARTIFACT_READ_S = 60.0`, `HEALTH_S = 5.0`; `LIST_MAX_BYTES = 1024 * 1024`;
    - `_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")`.
  - `class NextextUnavailable(Exception)`, whose `token` is one of `unreachable`, `timeout`, `http:<status>`, `bad_answer`. `class ArtifactTooLarge(Exception)`.
  - `@dataclass(frozen=True) class NextextJob`: `job_id: str`, `status: str` (Nextext's six, or `gone`), `error_code: str | None`, `skipped: bool`, `skip_reason_code: str | None`, `created_at: datetime` (aware, UTC).
  - `class NextextClient(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None)`; a new `AsyncClient` per call, `trust_env=False`, connect 3 s, write 10 s:
    - `async create(self, chunks: AsyncIterator[bytes], *, ext: str, mime: str, headers: Mapping[str, str]) -> str`: `POST {base}/jobs`, chunked, the layout below. Past `MAX_ATTACHMENT_BYTES` it raises `UploadTooLarge` before sending more. `201` with a valid `job_id` gives the id; else `NextextUnavailable`.
    - `async list_jobs(self, *, headers) -> list[NextextJob]`: `GET {base}/jobs`, its body at most `LIST_MAX_BYTES`; an item that does not read is left out, a body that does not read is `bad_answer`.
    - `async poll(self, job_id, *, headers) -> NextextJob`: its item of `list_jobs`, or `NextextJob(job_id, "gone", None, False, None, <now>)`.
    - `artifact(self, job_id: str, name: str, *, headers) -> AbstractAsyncContextManager[AsyncIterator[bytes] | None]`:
      - `404` gives `None`;
      - `200` gives the raw chunks; it raises `ArtifactTooLarge` when the declared `Content-Length` passes `ARTIFACT_CAPS[name]`, before the body is read, or before the chunk that passes it;
      - any other answer raises `NextextUnavailable`.
    - `async delete(self, job_id, *, headers) -> bool`: `204` or `404` gives `True`, anything else `False` (logged by its token). Never raises but a cancel.
    - `async healthy(self) -> bool`: `GET {base}/health` without the key, `True` only for `200`; never raises.

The `POST /jobs` body (the layout is fixed here):

```python
boundary = secrets.token_hex(16)
yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"options\"\r\n"
       f"Content-Type: application/json\r\n\r\n{json.dumps(JOB_OPTIONS, separators=(',', ':'))}\r\n").encode()
yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{MEDIA_NAME}{ext}\"\r\n"
       f"Content-Type: {mime}\r\n\r\n").encode()
async for chunk in chunks:   # counted against MAX_ATTACHMENT_BYTES as workbench._multipart counts
    yield chunk
yield f"\r\n--{boundary}--\r\n".encode()
```

`ext` is the clip's lower-cased suffix, one of `MEDIA_TYPES`; the header is `Content-Type: multipart/form-data; boundary=…`, with no `Content-Length`.

- `tests/fakes.py`, `FakeNextext`, an `httpx.MockTransport` handler:
  - `statuses: list[str]`, one per poll, the last repeated; `error_code`, `skipped`, `skip_reason_code`;
  - `artifacts: dict[str, bytes | int]`, where an int is a status; `declared: dict[str, int]`, a `Content-Length` to claim;
  - `create_status: int = 201`, `list_status: int = 200`, `health: int = 200`, `vanish_after: int | None` (the polls before the job leaves the list), `listed: list[dict]` (other jobs, as list items);
  - it records `requests`, `uploads` (the headers and the whole body, for assertions only), `deleted: list[str]` and `events: list[str]` (`"create"`, `"list"`, `"delete:<id>"`, in order).
  - Every id and value in it is invented.

- [ ] **Step 1: Write the failing tests** (`tests/test_nextext.py`):
  - `test_create_streams_the_clip_under_a_neutral_name`:
    - the body parsed with `python_multipart` has `options` exactly `JOB_OPTIONS`, and `file` with filename `media.mp4`, type `video/mp4` and the chunks' bytes;
    - there is no `Content-Length`; the Bearer key, the purpose and the request id are sent;
    - the test's attachment name `harbour-tour.mp4` appears nowhere in the request.
  - `test_create_maps_refusals`: 401 gives `http:401`; 503; a transport error gives `unreachable`; 201 without `job_id`, or with `job_id` `"../x"`, gives `bad_answer`.
  - `test_poll_reads_the_list_and_never_a_result`: the only request is `GET /jobs`, and the fields map; a job not listed gives `status == "gone"`.
  - `test_interrupted_is_terminal`.
  - `test_an_artifact_streams_within_its_cap`:
    - the chunks are equal; 404 gives `None`;
    - a declared `Content-Length` over the cap raises `ArtifactTooLarge`, and the fake's body generator is never started;
    - an undeclared body over the cap raises before its last chunk.
  - `test_delete_is_fine_when_the_job_is_gone`: 404 gives `True`; 500 gives `False`, with a log line holding the token alone.
  - `test_health_is_200_only`: 200 gives `True`; 503 and a transport error give `False`; no key is sent.
  - `test_the_log_holds_no_names_texts_or_keys` (`loguru_caplog`).
- [ ] **Step 2: Run** `uv run pytest tests/test_nextext.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; the error mapping follows `workbench._rest_errors`' shape.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(nextext): a client for one job per clip, streamed and capped`

### Task 3: When clips are offered, and the upload takes them

**Files:**
- Modify:
  - `ghostwriter/nextext.py` (`NextextHealth`, `serves_media`), `ghostwriter/upload.py` (`MEDIA_TYPES`, `AUDIO_SUFFIXES`, `attachment_type`, `attachment_kind`, `media_kind`);
  - `ghostwriter/api/files.py` (`_checked_name` `:128`; the upload `:240-298`), `ghostwriter/api/meta.py` (`ConfigOut`, `config`);
  - `ghostwriter/main.py` (`create_app(..., nextext_transport=None)`, `app.state.nextext`, `app.state.media`, the probe task), `tests/fakes.py` (`FakeDoor.server_version`, default `"0.3.0"` as today).
- Test: `tests/test_upload.py`, `tests/test_nextext.py`, `tests/test_api_meta.py`, `tests/test_api_files.py`

**Interfaces:**
- `upload.py`:
  - `MEDIA_TYPES: Final[dict[str, str]]`: the 11 suffixes with workbench's `renders.MEDIA_TYPES` types; `AUDIO_SUFFIXES: Final = frozenset({".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac"})`.
  - `attachment_type(name, *, images: bool = False, media: bool = False) -> str | None`.
  - `attachment_kind(name) -> Literal["document", "image", "media"]`; `media_kind(name) -> Literal["video", "audio"]`.
- `nextext.py`:
  - `MEDIA_WORKBENCH: Final = (0, 9)`, `PROBE_FIRST_S: Final = 5.0`, `PROBE_MAX_S: Final = 300.0`.
  - `serves_media(version: str) -> bool`: `X.Y[.Z…]` with `(X, Y) >= MEDIA_WORKBENCH`; anything else is `False`.
  - `class NextextHealth(client: NextextClient)`:
    - `healthy: bool`, false at first; `checked: asyncio.Event`, set after each probe;
    - `async run(self) -> None`: probe; when healthy, wait until `failed()`; when not, sleep and probe again, `PROBE_FIRST_S` doubling to `PROBE_MAX_S`;
    - `failed(self) -> None`: `healthy = False`, and it wakes `run`;
    - `async wait_healthy(self, timeout_s: float) -> bool`;
    - `offered(self, workbench: WorkbenchClient | None) -> bool`, for a thread: `healthy`, a workbench, and `serves_media(workbench.discover(call=system_call(), images=False).server_version)` from the cached discovery.
- `main.py`:
  - With `settings.nextext_enabled`: `app.state.nextext = NextextClient(settings, transport=nextext_transport)` and `app.state.media = NextextHealth(app.state.nextext)`; the lifespan starts `asyncio.create_task(media.run())` and cancels it at shutdown with the others.
  - Otherwise both are `None`.
- `meta.ConfigOut` gains `media_types: list[str]` (sorted, empty unless offered) and `media: bool`; while offered, `attachment_types` includes `MEDIA_TYPES`.
- The upload:
  - `media = await run_in_threadpool(_media_offered, request)`; `_checked_name(raw, images=offered, media=media)`;
  - `status` is `"processing"` when `kind == "media"`, or when `offered and needs_prepare(name)`;
  - `PrepareJob(..., kind, op, size=stored.size)`, Task 5's field.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_upload.py`:
    - `test_clips_are_attachable_only_when_offered`: `attachment_type("harbour-tour.MP4")` is `None`; `(…, media=True)` gives `video/mp4`; `voice-memo.opus` gives `audio/opus`; `.avi` is `None` either way.
    - `test_a_clip_is_media`: `attachment_kind("voice-memo.m4a") == "media"`; `media_kind` gives `audio` for `.m4a` and `video` for `.webm`.
  - `tests/test_nextext.py`:
    - `test_serves_media`: `"0.9.0"`, `"0.10.1"` and `"1.0"` give `True`; `"0.8.3"`, `""` and `"dev"` give `False`.
    - `test_the_probe_marks_nextext_healthy_on_200`.
    - `test_a_failed_probe_tries_again_with_backoff`: with `asyncio.sleep` patched, the waits are 5, 10, 20, … capped at 300.
    - `test_a_failure_wakes_the_probe`.
  - `tests/test_api_meta.py`:
    - `test_config_reports_what_the_spa_needs` gains `"media_types": [], "media": False`.
    - `test_config_offers_clips_with_nextext_and_workbench_0_9`: `FakeDoor(server_version="0.9.0")` and a healthy `FakeNextext` (wait on `checked`) give `media: True` and the 11 suffixes sorted, also in `attachment_types`.
    - `test_no_clips_against_workbench_0_8` (`"0.8.0"`); `test_no_clips_while_nextext_is_down` (`health=503`); `test_no_clips_without_a_key`.
    - `test_a_hanging_nextext_never_holds_start_up`: a transport that never answers; `/api/config` answers `media: False` at once.
  - `tests/test_api_files.py`:
    - `test_a_clip_is_processing_once_attached`: 201 with `kind "media"` and `status "processing"`; workbench got `video/mp4`; the processor got a `PrepareJob` of kind `media` with its size.
    - `test_a_clip_is_415_without_nextext`.
- [ ] **Step 2: Run** `uv run pytest tests/test_upload.py tests/test_nextext.py tests/test_api_meta.py tests/test_api_files.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(files): offer video and audio while Nextext is healthy and workbench takes clips`

### Task 4: The workbench client sends artifacts and reads a clip's manifest

**Files:**
- Modify:
  - `ghostwriter/workbench.py`: new `derived`; `_KINDS` `:118`; `Manifest` `:315`; `_manifest`;
  - `ghostwriter/store.py:60` (`STORED_MANIFEST_KEYS`), `tests/fakes.py` (`FakeDoor`: `/files/derived`, the media manifest).
- Test: `tests/test_workbench.py`, `tests/test_store.py`

**Interfaces:**
- Produces in `workbench.py`:
  - `DerivedRole = Literal["nextext_jsonl", "nextext_zip"]`, `DERIVED_TYPES: Final = {"nextext_jsonl": "application/x-ndjson", "nextext_zip": "application/zip"}`, `DERIVED_TIMEOUT_S: Final = 150.0`.
  - `async def WorkbenchClient.derived(self, *, call: CallFor, source: str, role: DerivedRole, chunks: AsyncIterator[bytes]) -> str`:
    - `POST /files/derived?source=&role=`, the body streamed as `content=chunks`, its `Content-Type` by role, `Accept-Encoding: identity`.
    - A source outside the chat's folder is `WorkbenchNotFound`, unsent.
    - `200` with an `id` of 32 hex digits gives the id. `404` is `WorkbenchNotFound`; `413` is `WorkbenchUnavailable("http:413")`; `422` its token; `507` is `StorageFull`; anything else `http:<status>`.
  - v0.5's `Kind` and `_KINDS` gain `"media"`, and its `Unit` gains `"frame"` (v0.5 reads an unknown unit as `bad_answer`). `Manifest` gains `duration_s: float | None = None` and `transcript: bool = False`, after v0.5's `unit`.
  - `_manifest` refuses (giving `bad_answer`):
    - a `duration_s` that is not `null` or a non-negative finite number;
    - a `transcript` that is not a bool;
    - kind `media` without unit `frame`.

    A manifest without the two keys reads with their defaults. `as_stored` adds both.
- `STORED_MANIFEST_KEYS` gains `"duration_s"` and `"transcript"`.
- `FakeDoor`:
  - `_ROUTES` gains `("POST", "/files/derived")`. It records `derived: list[tuple[str, str, bytes]]` (source, role, body) and answers `{"id", "role", "bytes", "replaced": None}`; `answers["/files/derived"]` scripts a status.
  - For a media path `_prepare` answers from what was posted:
    - a jsonl gives `transcript: true` and `duration_s: 91.0`;
    - a zip gives `total 3` and renditions 1–3 at 1280 × 720;
    - neither gives `total 0`.
  - `unit` is `frame`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_workbench.py`:
    - `test_derived_streams_an_artifact_into_the_clips_folder`: the parameters, the type by role, the body's bytes, the chat's session; the id is answered.
    - `test_derived_maps_refusals`, parametrized: 404, 413, 422 `unreadable_file: …`, 507.
    - `test_derived_never_asks_outside_the_chat`.
    - `test_a_media_manifest_is_read`: kind `media`, unit `frame`, `total 3`, `duration_s 91.0`, `transcript True`; `as_stored()` holds both keys.
    - `test_a_bad_media_manifest_is_bad_answer`, parametrized: `duration_s: "x"`, `duration_s: -1`, `transcript: 1`, kind `media` with unit `page`.
    - `test_an_older_manifest_reads_with_the_defaults`.
  - `tests/test_store.py::test_a_settled_media_manifest_keeps_its_facts`.
  - `test_every_request_carries_the_session_purpose_and_request_id` gains `derived`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workbench.py tests/test_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; `derived` follows `_upload`'s shape.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(workbench): stream Nextext's artifacts in and read a clip's manifest`

### Task 5: The processor's media step

**Files:**
- Modify:
  - `ghostwriter/processing.py`: `PrepareJob` `:67`, `_requeued` `:88`, `__init__` `:121`, `_run` `:261`, `_prepare` `:282`, `_settle_failed` `:466`; new `_transcribe`;
  - `ghostwriter/store.py` (`settle_processing`: media goes `failed`, as a picture does), `ghostwriter/main.py` (the processor gets `nextext`, `health` and `nextext_timeout_s`; `processor.start_media()` after `requeue()`), `docs/audit.md`.
- Create: `tests/test_processing_media.py`
- Test: `tests/test_api_files.py`, `tests/test_api_messages.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `PrepareJob.kind: Literal["document", "image", "media"]`; `PrepareJob.size: int | None = None`, the last field, after v0.5.0's `background`; a requeued job has none. A clip is never `background`: Send waits for its transcript, for without it the model has nothing to read.
  - The constants (`Final`):
    - `MEDIA_SLOTS = 2`, `NEXTEXT_RECIPIENT = "nextext:transcribe"`, `NEXTEXT_OPERATION = "chat.nextext"`;
    - `POLL_S = 2.0`, `POLL_SLOW_S = 5.0`, `POLL_SLOW_AFTER_S = 60.0`, `START_WAIT_S = 120.0`.
  - The tokens: `nextext_unavailable` (`unreachable`, a per-request `timeout`, `http:401`, `http:403`, `http:5xx` from `create`, `list_jobs` or `artifact`; it also calls `health.failed()`), `nextext_failed` (any other `http:`, `failed`, `cancelled`, `interrupted`, `gone`), `undecodable_media` (`failed` with that `error_code`), `timeout` (the budget spent).
  - `AttachmentProcessor(store, workbench, emitter, *, image_tokens=None, nextext: NextextClient | None = None, health: NextextHealth | None = None, nextext_timeout_s: float = 900.0)`; `owned_jobs(self) -> frozenset[str]`.
  - The gate: `_reconciled: asyncio.Event`, and `start_media(self) -> None`, which in this task sets it at once. Task 6 makes it run the start reconcile first. The lifespan calls it right after `requeue()`, and the tests call it.

The media job, in this order (the tests pin each step; the order itself is decided here):

```text
async with media_slots:                                   # never a prepare slot while at Nextext
    mark_files                                            # ChatNotFound: a quiet end
    await reconciled, at most START_WAIT_S                # Task 6's gate: no create before the start reconcile
    nextext op: disclose chat.attachment, recipient NEXTEXT_RECIPIENT, criteria {"kind": "media"} (+ "bytes" when size)
                                                          # as the upload's child, or the system's primary when requeued;
                                                          # unrecorded: nothing is sent, failed "audit"
    no nextext client: token "nextext_unavailable"
    deadline = monotonic() + nextext_timeout_s; job_id = None
    try:
        async with workbench.view(call, path) as clip:    # the bytes pass through, never read whole
            job_id = await nextext.create(clip.aiter_raw(), ext, mime, headers)
        owned.add(job_id)
        job = poll every POLL_S (POLL_SLOW_S after POLL_SLOW_AFTER_S) until status in TERMINAL or "gone"
                                                          # deadline passed first: token "timeout"
        completed (skipped or not): hand-over disclose chat.attachment -> workbench:prepare, a child of the
            nextext op (never a second system primary), then for
            (JSONL, "nextext_jsonl"), (KEYFRAMES, "nextext_zip"):
                async with nextext.artifact(job_id, name) as body:
                    body is not None: await workbench.derived(call, source=path, role=role, chunks=body)
                ArtifactTooLarge: left out, logged "artifact_too_large"
        otherwise: the token of its status
    finally, under anyio.CancelScope(shield=True), at most CALL_S:
        if job_id: deleted = await nextext.delete(job_id)
                   nextext op child: delete chat.nextext, target job [job_id], criteria {"deleted": deleted},
                   outcome ok or error; owned.discard(job_id)
token: an error child (disclose chat.attachment, NEXTEXT_RECIPIENT, {"reason": token}), then failed(token)
async with slots: _prepared(job, op) as for a document; its token settles the clip failed
```

- [ ] **Step 1: Write the failing tests** (`tests/test_processing_media.py`; a store, a `WorkbenchClient` over `FakeDoor(server_version="0.9.0")`, a `NextextClient` over `FakeNextext`, an emitter on `CapturedAuditStream`; `POLL_S` patched to 0.01).
  - The round trip:
    - `test_a_clip_goes_to_nextext_and_its_artifacts_to_workbench`:
      - the clip is `ready`, and its stored manifest has `transcript: true`;
      - Nextext saw one upload, `media.mp4`, whose bytes are the clip's in FakeDoor;
      - workbench saw `derived` for the jsonl, then the zip, with Nextext's bytes;
      - `FakeNextext.deleted == [job id]`;
      - the events, in order: `disclose` (`nextext:transcribe`, `kind: media`, `bytes`), `disclose` (`workbench:prepare`), `delete` / `chat.nextext` (target `job` [id], `deleted: true`).
    - `test_the_clip_passes_through_chunk_by_chunk`: FakeDoor's view yields 8 chunks and logs `view:<i>` before each; FakeNextext logs `nextext:<i>` as each arrives; the log interleaves (`view:1`, `nextext:1`, `view:2`, …).
  - What Nextext answers:
    - `test_a_clip_without_speech_is_ready` (Review Focus 3): `skipped`, `vad_no_speech`, `docint.jsonl` 404; `derived` holds the zip alone, and the manifest has `transcript: false`.
    - `test_audio_has_no_keyframes`: `keyframes.zip` 404; the jsonl alone.
    - `test_nextext_failures_settle_the_clip_failed`, parametrized:
      - `["failed"]` with `undecodable_media` gives `undecodable_media`;
      - `["failed"]` with `internal`, `["interrupted"]` and `["cancelled"]` give `nextext_failed`;
      - `create_status=503` gives `nextext_unavailable`, and `health.healthy` turns false.
      - Each: `failed` with that token and an error child with that reason; the job is deleted when one was created.
    - `test_a_vanished_job_fails_at_the_next_poll` (Review Focus 1): `vanish_after=1` gives `nextext_failed` within 3 polls; the delete answers 404 and is recorded with `deleted: true`.
    - `test_the_budget_ends_a_slow_job`: `["running"]` forever with `nextext_timeout_s=0.2` gives `timeout`, and the job is deleted.
    - `test_an_oversized_keyframes_zip_is_left_out`: `declared[KEYFRAMES] = 32 MiB + 1`; `derived` holds the jsonl alone; `ready`; a log line with `artifact_too_large`.
  - Cancels and records:
    - `test_a_chat_deleted_mid_job_still_deletes_the_nextext_job` (Review Focus 2): `cancel_chat` while polling. The row is still `processing` and nothing settled it; the Nextext `DELETE` was sent and `delete` / `chat.nextext` recorded; no error-level log (`loguru_caplog`).
    - `test_an_unrecorded_hand_over_sends_nothing_to_nextext`: `unrecorded_child(monkeypatch, "chat.attachment")`; no Nextext request; `failed` with `audit`.
  - Slots and start:
    - `test_a_picture_is_prepared_while_clips_transcribe` (Review Focus 4): two clips held at `running`, then a picture submitted; the picture is `ready` while both clips are still `processing`.
    - `test_a_requeued_clip_runs_again`: a `processing` clip row at start, through `requeue()`, ends `ready`; its hand-over is the system's primary, `requeued: true`.
    - `test_without_nextext_a_clip_fails_as_unavailable`.
  - Logs: `test_the_log_holds_no_names`: no `harbour-tour`, no transcript text.
  - `tests/test_store.py::test_stranded_processing_rows_settle` gains a clip, which goes `failed`.
  - `tests/test_api_messages.py::test_a_message_waits_for_its_attachments` gains a `processing` clip, which is 409.
- [ ] **Step 2: Run** `uv run pytest tests/test_processing_media.py tests/test_store.py tests/test_api_messages.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; `_transcribe` owns the round trip, and `_prepare` calls it for kind `media`. `docs/audit.md`:
  - the attachment row gains the `disclose` `chat.attachment` (`nextext:transcribe`) child, with its `error` child, and the `delete` `chat.nextext` child (target `job` [id], `deleted`);
  - "start-up: a `processing` attachment" covers clips.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(processing): a clip goes to Nextext for one job, its artifacts to workbench, the job deleted at once`

### Task 6: Orphaned Nextext jobs are deleted, at start and daily

**Files:**
- Modify:
  - `ghostwriter/processing.py` (`reconcile`; `start_media` runs it);
  - `ghostwriter/retention.py` (`Sweeper.sweep` `:136`, before `_sweep_chats`; `SweepReport.nextext_jobs`);
  - `docs/audit.md`, `docs/retention.md`.
- Create: `tests/test_reconcile.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: Task 5's owned ids and media gate; `NextextHealth.wait_healthy`.
- Produces:
  - `RECONCILE_EXTRA_S: Final = 60.0`.
  - `async def AttachmentProcessor.reconcile(self, *, grace_s: float | None) -> int`: the deleted count. A `NextextUnavailable` is logged by its token and gives 0. An `AuditWriteError` propagates, with nothing deleted.
  - `def AttachmentProcessor.start_media(self) -> None`, Task 5's, now one task:
    - no Nextext: `_reconciled` is set at once;
    - otherwise `await health.wait_healthy(START_WAIT_S)`, and when healthy `reconcile(grace_s=None)`, an `AuditWriteError` logged; in a `finally`, `_reconciled` is set.
  - In the sweep, with a processor and Nextext: `report.nextext_jobs = await processor.reconcile(grace_s=settings.nextext_timeout_s + RECONCILE_EXTRA_S)`, before `_sweep_chats`. The summary log line gains the count.

The rule (the grace is decided here):

```python
jobs = await nextext.list_jobs(headers=system_headers)            # forward_headers of a system context
owned = self.owned_jobs()
cutoff = None if grace_s is None else utcnow() - timedelta(seconds=grace_s)
stale = [j for j in jobs if j.job_id not in owned and (cutoff is None or j.created_at <= cutoff)]
if not stale:
    return 0
op = system_operation(emitter, "delete", NEXTEXT_OPERATION)
op.record(target=Target(kind="job", count=len(stale)), criteria={"listed": len(jobs), "at_start": grace_s is None})
deleted = [await nextext.delete(j.job_id, headers=system_headers) for j in stale].count(True)
if deleted < len(stale):
    op.child("delete", NEXTEXT_OPERATION, target=Target(kind="job", count=len(stale) - deleted), outcome="error")
return deleted
```

At start no task of this process can own a job, since every clip's `create` waits for `_reconciled`, so `grace_s=None` deletes every job listed, whatever Nextext's clock says. In the sweep, a live task's job is at most `NEXTEXT_TIMEOUT` old; the 60 s more covers a `create` whose answer has not come back yet, and clock skew.

- [ ] **Step 1: Write the failing tests** (`tests/test_reconcile.py`, the fakes of Task 5).
  - `test_the_start_reconcile_deletes_every_job_listed`:
    - three listed jobs: two `created_at` hours ago, one "now" plus 30 s (Nextext's clock ahead);
    - all three are deleted;
    - one system `delete` / `chat.nextext` event: target `job`, count 3, no ids, `at_start: true`.
  - `test_no_clip_creates_a_job_before_the_start_reconcile` (Review Focus 5): a `processing` clip at start and an orphan listed.
    - `FakeNextext.events` begins `["list", "delete:<orphan>", "create", …]`.
    - The clip ends `ready`; its own job is deleted once, by its `finally`, never by the reconcile.
  - `test_the_sweep_spares_owned_and_young_jobs`: an owned id (a clip held `running`), a job made 10 s ago and one made 2 h ago; only the 2 h one is deleted (grace 960 s).
  - `test_an_unrecorded_reconcile_deletes_nothing`: a failing stream raises `AuditWriteError` and sends no `DELETE`.
  - `test_a_down_nextext_reconciles_nothing_and_clips_go_on`: `list_status=503` gives 0; `_reconciled` is set; the clip then fails `nextext_unavailable`.
  - `test_the_start_reconcile_waits_for_the_probe`: health 503 first, then 200; the list comes after the first healthy probe.
  - `tests/test_retention.py::test_the_sweep_reconciles_before_it_deletes_chats`: the `chat.nextext` event comes before the sweep's chat deletions, and `report.nextext_jobs == 1`.
- [ ] **Step 2: Run** `uv run pytest tests/test_reconcile.py tests/test_retention.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, then the docs:
  - `docs/audit.md`: a row "start-up and the daily sweep: Ghostwriter's Nextext jobs no clip owns", `delete` `chat.nextext` as the system, target `job` (count), criteria `listed`, `at_start`, with an `error` child for those not deleted;
  - `docs/retention.md`: a copy of a clip lives at Nextext for one job; a crash leaves it until the next start or the next daily sweep.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(processing): orphaned Nextext jobs are deleted at start and in the daily sweep, recorded first`

### Task 7: What the model is told of a clip

**Files:**
- Modify: `ghostwriter/agent/context.py` (`attachment_facts`, a `media` branch beside v0.5's `office` one), `ghostwriter/agent/loop.py` (`system_prompt(..., media=…)`), `ghostwriter/agent/prompt.py` (`MEDIA`)
- Test: `tests/test_agent_context.py`, `tests/test_agent_loop.py`

**Interfaces:**
- `context.py`:
  - `attachment_facts(kind, manifest, *, seen, name)`, v0.5's signature (its call site already passes the name). Kind `media` with a manifest gives a line; without one, `None`.
  - The line: `{video|audio}` (from `media_kind(name)`), then ` {duration}` when `duration_s` is set, as `mm:ss`, or `h:mm:ss` from one hour, in whole seconds.
  - For a video: `, {N} frames` (`, 1 frame`), or `, no frames` when `total` is 0; after the count, ` not shown to the model` when not `seen` (`UNSEEN_PICTURE`).
  - Then `; transcript` or `; no speech`.
- `prompt.py`: `MEDIA: Final`, verbatim: `"A video or audio file the user attached comes with a transcript: read it with read_document. A video's frames are pictures of it over time: look at them with view_file ('frame 3', 'frames 2-5', 'at 01:24')."`. `system_prompt(..., media: bool = False)` appends it after `VISION`, only with vision, tools and `media`.
- `loop.py`: `media` is `app.state.media` offered at the turn (the cached offer). `strips_for_path` already gives `None` for a clip, so no `strips` is sent.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_context.py`:
    - `test_a_clips_line_says_what_it_holds`, each line exact:
      - `video 02:14, 8 frames; transcript` (134.2 s, total 8, a transcript, seen);
      - `video 00:40, 3 frames; no speech` (Review Focus 3);
      - `video 02:14, no frames; transcript`;
      - `audio 31:07; transcript` (1867 s, `voice-memo.m4a`);
      - `audio 1:02:03; no speech`;
      - `video 02:14, 8 frames not shown to the model; transcript` (not seen);
      - `video, 1 frame; transcript` (no duration);
      - `None` without a manifest.
    - `test_a_clip_line_rides_the_attachment_line`: `[Attached: harbour-tour.mp4 (/workspace/<chat>/harbour-tour.mp4, 1234 bytes; video 02:14, 8 frames; transcript)]`.
  - `tests/test_agent_loop.py`:
    - `test_the_prompt_tells_the_model_of_clips_only_with_vision_and_media`.
    - `test_view_file_on_a_clip_sends_no_strips`: `image_tokens=280`, `view_file {"path": ".../harbour-tour.mp4", "part": "at 00:04"}` sends no `strips`.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_context.py tests/test_agent_loop.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): a clip's duration, frames and transcript in its attachment line`

### Task 8: The SPA's clip tiles

**Files:**
- Modify:
  - `frontend/src/api/types.ts` (`AppConfig`), `frontend/src/lib/format.ts` (`AUDIO_EXTENSIONS`, `clipKind`), `frontend/src/hooks/useAttachments.ts` (`PendingFile.kind` `:20`, the kind at `:94` and `:229`);
  - `frontend/src/components/AttachmentTiles.tsx` (the kind `:62-72`, the status label `:52`), `frontend/src/components/Composer.tsx` (the hint);
  - `frontend/src/test/fixtures.tsx` (`CONFIG`, `MEDIA_CONFIG`), `frontend/src/i18n/en.ts`, `de.ts`.
- Test: `frontend/src/components/Composer.test.tsx`, `frontend/src/lib/format.test.ts`

**Interfaces:**
- `AppConfig` gains `media_types: string[]` and `media: boolean`.
- `format.ts`: `AUDIO_EXTENSIONS: ReadonlySet<string>`, the six audio suffixes of `upload.AUDIO_SUFFIXES`; `clipKind(name: string): 'video' | 'audio'`.
- `PendingFile.kind: 'document' | 'image' | 'video' | 'audio'`, from the name for a file in `config.media_types`, and from the attachment (`kind === 'media'`) for one the chat lists. A clip gets no `localUrl` and no preview.
- `AttachmentTiles`:
  - a clip's tile is `kind={file.kind}` with no `thumbnailUrl` and no `onOpen`;
  - while processing, its label is `composer.transcribing`.
- `Composer`: while a clip is processing, `composer.transcribing_hint` shows instead of `composer.processing_hint`.
- i18n (en / de), exact:
  - `composer.transcribing`: `Transcribing {name}…` / `{name} wird transkribiert…`
  - `composer.transcribing_hint`: `Video and audio take longer: they are transcribed before they are sent.` / `Video und Audio dauern länger, weil sie vor dem Senden transkribiert werden.`
- `fixtures.tsx`: `CONFIG` gains `media_types: [], media: false`. `MEDIA_CONFIG` adds `.mp4`, `.m4a` and `.webm` to `attachment_types` and to `media_types`, with `media: true`.

- [ ] **Step 1: Write the failing tests.**
  - `format.test.ts`: `clipKind('voice-memo.M4A') === 'audio'`, `clipKind('harbour-tour.mkv') === 'video'`.
  - `Composer.test.tsx` (`MEDIA_CONFIG`):
    - `it('shows a dropped clip as a video tile while it is transcribed, and sends once it is ready')`:
      - drop `harbour-tour.mp4`; its tile has no `img`, and the label is `Transcribing harbour-tour.mp4…`;
      - the transcribing hint shows; Send is disabled until `GET …/attachments/a1?wait=20` answers `ready`, then it sends `attachment_ids: ['a1']`.
    - `it('shows an audio file as an audio tile')`.
    - `it('opens no lightbox for a clip')`: a click on the tile opens no dialog.
    - `it('follows a clip the chat lists as processing')`.
    - `it('offers no clips without Nextext')`: under `CONFIG` the picker's `accept` lacks `.mp4`, and a dropped `.mp4` is refused as today.
- [ ] **Step 2: Run** `cd frontend && pnpm test src/lib/format.test.ts src/components/Composer.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** `pnpm typecheck` names every fixture still missing a field.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm build && pnpm test`. Expected: PASS; `i18n.test.ts` parity green.
- [ ] **Step 5: Commit** `feat(frontend): video and audio tiles, transcribed before they are sent`

### Task 9: Docs and release

**Files:**
- Modify: `docs/data-flow.md`, `docs/retention.md` (check Task 6's lines), `docs/configuration.md` (caps), `docs/audit.md` (check Tasks 5–6's rows), `CLAUDE.md`, `README.md`, `pyproject.toml` (`version = "0.6.0"`), `uv.lock`, `tests/test_version.py` (`"0.6.0"`), `tests/test_api_meta.py` (`"version": "0.6.0"`)

- [ ] **Step 1: Docs.**
  - `docs/data-flow.md`, "Where data goes" gains Nextext:
    - the clip, streamed from workbench under the name `media.<ext>`, for one job;
    - `docint.jsonl` and `keyframes.zip` streamed back into workbench as derived files of the clip;
    - the job deleted at once, or at the next start or daily sweep after a crash.
  - `docs/data-flow.md`, "What is never stored" gains: clip bytes and artifacts pass through memory chunk by chunk.
  - `docs/configuration.md`, the caps table: `MEDIA_SLOTS` 2, `POLL_S` 2 s and 5 s after 60 s, the artifact caps 8 MiB and 32 MiB, `START_WAIT_S` 120, `keyframes_max` 19.
  - `CLAUDE.md`:
    - the module map gains `ghostwriter/nextext.py`, and "Where it fits" gains "Talks to Nextext `nextext-backend:8000` on `inference-net`, service `service:ghostwriter`";
    - the invariants: "A clip streams to Nextext for one job, the one copy of a file outside workbench (ADR 0009); every other rule stands";
    - "A message, end to end" step 4 names the media step;
    - "What changes together" gains two pairs: the media suffix table, with workbench's `renders.MEDIA_TYPES`; `JOB_OPTIONS["keyframes_max"]`, with workbench's `MEDIA_FRAMES_MAX`.
  - `README.md`: one line, video and audio with Nextext.
- [ ] **Step 2: Version.** Bump to `0.6.0`, `uv lock`, then `uv run pytest tests/test_version.py tests/test_api_meta.py -q`. Expected: PASS.
- [ ] **Step 3: Gates.** `make verify && make test`. Expected: green; paste the summary lines into the PR, with Task 0's results.
- [ ] **Step 4: Commit** `docs: video and audio through Nextext` and `chore(release): 0.6.0`.
- [ ] **Step 5: Push and PR.** `git push -u origin feat/vision-stage3`; a PR to `main`.
  - **Operators** line: "0.6.0 takes video and audio once Nextext is set. It needs workbench 0.9.0 and deploy 0.14.0. Run `make env ENV_GENERATE=1`: it writes Ghostwriter's `NEXTEXT_API_KEY` and appends `ghostwriter=<token>` to Nextext's `NEXTEXT_SERVICE_TOKENS`. Recreate both backends while Nextext has no job in flight. Nextext's `NEXTEXT_JOB_CONCURRENCY` (1) queues clips behind docint's ingest, within `NEXTEXT_TIMEOUT` (900 s)."
  - Confirm CI ran on the last commit. Merging is the owner's call.

---

## Self-review

- **Spec coverage.** Spec §7.3 Media asks for these, each with its task:
  - the Bearer token, purpose and request id: Tasks 2, 5;
  - `GET /files/view` streamed into `POST /jobs` under `media.<ext>`, polled, both artifacts into `POST /files/derived`, `DELETE` in a shielded `finally`: Task 5;
  - Ghostwriter's own orphaned jobs, at start and in the daily sweep: Task 6;
  - media offered only with base, key and a passing probe: Task 3.
  - Elsewhere in the spec:
    - §7.3 Attachments (the `nextext:transcribe` and `delete` records): Task 5;
    - §7.3 The turn (the facts line): Task 7;
    - §7.4 (tiles and icons): Task 8;
    - §5.3 (no spool, delete at once, derived files expire with the clip, no media without Nextext, 50 MiB): Tasks 3, 5 and workbench v0.9;
    - §10 check 7: Tasks 5–6, then the staging run.
- **Type consistency:**
  - `NextextJob`, `NextextClient`, `ArtifactTooLarge` and `JOB_OPTIONS` are Task 2's;
  - `NextextHealth.offered`, `wait_healthy` and `failed` are Task 3's;
  - `WorkbenchClient.derived` and `Manifest.duration_s`/`transcript` are Task 4's;
  - `owned_jobs`, `_reconciled` and `NEXTEXT_OPERATION` are Task 5's, used in Task 6;
  - `media_kind` is Task 3's, used in Task 7.
- **Proportion:** code appears only for the multipart layout, the media step's order, the reconcile rule and the copy the plan fixes (`.env.example`, i18n, `MEDIA`). The rest is signatures, values and assertions.
