# Ghostwriter vision, stage 1: images and PDFs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ghostwriter v0.3.0 takes pictures (pick, drop, paste a screenshot) straight into the user's message, gives PDFs page facts, and shows the model the images workbench's tools return. With vision off, a model that cannot see, or a workbench without `view_file`, it behaves exactly as v0.2.0.

**Architecture:**
- Attachments gain `kind`, `status`, `render` and `failure` (schema 2). An `AttachmentProcessor` (asyncio tasks in the one process) calls workbench v0.7.0's `POST /files/prepare` for each image and PDF and stores the manifest; the SPA follows the status by long-poll.
- Each round's messages carry internal `image_ref` parts. `fit` counts them by `estimate_image_tokens`, never by characters. `materialise` swaps them for base64 data URLs only just before `ModelClient.stream`. Renditions are fetched from workbench per message and held in memory for that message only.
- `Vision` decides whether images are offered: `GHOSTWRITER_VISION`, Ollama's `/api/show`, and workbench listing `view_file`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, httpx and anyio (`MockTransport` in tests), the OpenAI SDK, loguru; React 19, react-query, `@infra/ui` v0.26.0, vitest.

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§2 anti-goals, §6 spikes, §7.3, §7.4) and `deploy/docs/decisions/0009-ghostwriter-sees-images-and-media.md`. Contracts consumed exactly:
- `deploy/docs/superpowers/plans/2026-11-20-ghostwriter-vision-workbench-v0.7.md` § Contract for Ghostwriter;
- `deploy/docs/superpowers/plans/2026-11-20-ghostwriter-vision-infra-ui.md` Tasks 2–4 (`AttachmentTile`, `ImageLightbox`, the v0.26.0 pin).

Every path below is relative to the `ghostwriter` repo root unless it starts with `deploy/`. Read `CLAUDE.md` ("What changes together", Invariants, Testing) before Task 1.

## Global Constraints

- Branch `feat/vision-stage1` from a fresh `main`; one PR. `pyproject.toml` `version = "0.3.0"` and `uv lock` in Task 12 only; the tag is minted on merge.
- Python 3.11 only; ruff with Google docstrings (`D`) and annotations (`ANN`); pyrefly strict; 120-character lines. `make verify` and `make test` green before every push (`git add` new files first).
- One process, one uvicorn worker: the processor's tasks, the turn registry and the audit chain live in it. Never add workers.
- Audit fail-closed: an action whose event cannot be written does not happen. References only: ids, counts, kinds, tokens.
- Logs hold ids, counts, statuses and tokens (loguru `{}` placeholders); never a file name, a label, text, a data URL or image bytes.
- No file bytes stored: no spool, no copy in SQLite. Image bytes live in memory for one message (`ImageBank`). Data URLs exist only in the list handed to `ModelClient.stream`, never in a log, the store, `compact`, `tool_steps` or the audit.
- Every file lookup is by `(owner, chat_id, id)`; every workbench call carries that chat's `X-Session-Id` (`CallFor`).
- workbench v0.7.0 lists `view_file` and attaches images only to an MCP call that carries `X-Accept-Images: 1`. The discovery call (`system_call`) always sends it; a turn's calls send it exactly when `Vision.images_offered` is true. Without it workbench answers as v0.6.0 did (no `view_file`, no images, the old `read_document` text).
- Nothing is fetched at runtime. Inference goes only to `OPENAI_API_BASE` (the Ollama probe uses the same host).
- Vendored files are never edited here: `ghostwriter/utils/audit_*.py`, `make/common.mk`, `scripts/bundle-lib.sh`, `frontend/eslint.config.js`, the `[tool.ruff]`/`[tool.pyrefly]` sections.
- Test data is invented (`tests/fakes.py` conventions, `TINY_JPEG`/`TINY_PNG`, names such as `harbour-photo.jpg`, `quay-scan.pdf`). No absolute local path in any committed file or message.
- Spec values: image suffixes `.png .jpg .jpeg .webp .gif .bmp .tif .tiff .heic .heif` (never `.svg`); `GHOSTWRITER_MAX_IMAGES` 8; `GHOSTWRITER_IMAGE_TOKENS` unset = `ceil(w/32)*ceil(h/32)` per image, a number = that per image (280 documented for gemma); with a fixed cost, pages and images whose long side > 1024 go as 2 strips; tool images ≤ 8 and ≤ 3 MiB per answer, each ≤ 1 MiB; a rendition < 768 KiB; long-poll `wait` ≤ 20 s; processor semaphore 2.
- UI strings in `en.ts` and `de.ts` (German "Sie"); `i18n.test.ts` keeps parity.
- Commit messages conventional (`feat(store): …`), no real data.

## Review Focus

1. **A chat deleted while its picture is being prepared.** Expect the job cancelled, no write to the gone row, no error-level log, and the folder deleted. Test in Task 5.
2. **The backend restarted while attachments were processing.** Expect them prepared again at start (prepare is idempotent), never a message stuck behind 409. Test in Task 5.
3. **Two screenshots pasted in the same second.** Expect two attachments, `screenshot-…-HHMMSS.png` and `screenshot-…-HHMMSS-2.png`, not one name twice in the chat's folder. Test in Task 10.
4. **A workbench v0.7 `read_document` that returns images while the model cannot see.** Expect no image part anywhere, `view_file` not offered, and the tool text saying the images could not be shown. Test in Task 7.
5. **A follow-up after an attached picture expired, or whose rendition is gone.** Expect the attachment line "; expired" with nothing fetched, or a "could not be loaded" note, and the answer going on. Test in Task 7.

---

### Task 1: Vision settings

**Files:**
- Modify: `ghostwriter/config.py` (`Settings` after `context_tokens`; `load_settings`), `ghostwriter/audit.py` (`_operating_mode` gains `"vision": settings.vision`), `.env.example` (Inference, after `GHOSTWRITER_CONTEXT_TOKENS`), `docker/compose.yaml` (backend `environment`), `docs/configuration.md`, `tests/conftest.py` (`make_settings`)
- Test: `tests/test_config.py`, `tests/test_scaffold.py`

**Interfaces:**
- Produces: `Settings.vision: bool`, `Settings.max_images: int`, `Settings.image_tokens: int | None`; `VISION_ENV = "GHOSTWRITER_VISION"`, `MAX_IMAGES_ENV = "GHOSTWRITER_MAX_IMAGES"`, `IMAGE_TOKENS_ENV = "GHOSTWRITER_IMAGE_TOKENS"`, `MAX_IMAGES_LIMIT: Final = 32`, `IMAGE_TOKENS_LIMIT: Final = 10_000`.
- `make_settings` sets `vision=False` unless overridden, so every existing test keeps v0.2.0 behaviour; vision tests pass `vision=True`. Update its docstring.

- [ ] **Step 0: Branch.** `git fetch origin && git switch main && git pull --ff-only && git switch -c feat/vision-stage1`.
- [ ] **Step 1: Write the failing tests.**
  - `test_defaults_match_the_contract` gains `(s.vision, s.max_images, s.image_tokens) == (True, 8, None)`.
  - `test_vision_is_on_or_off`: `"off"` → False, `"ON"` → True; `"maybe"` raises `ValueError` matching `GHOSTWRITER_VISION must be on or off.`
  - `test_max_images_is_a_whole_number_from_1_to_32`: `"4"` → 4; `"0"`, `"33"`, `"x"` raise `GHOSTWRITER_MAX_IMAGES must be a whole number from 1 to 32.`
  - `test_image_tokens_is_unset_or_a_fixed_cost`: unset → None, `"280"` → 280; `"0"`, `"10001"`, `"many"` raise `GHOSTWRITER_IMAGE_TOKENS must be a whole number of tokens from 1 to 10000, or unset.`
  - `test_compose_matches_the_contract` gains `GHOSTWRITER_VISION: ${GHOSTWRITER_VISION:-on}`, `GHOSTWRITER_MAX_IMAGES: ${GHOSTWRITER_MAX_IMAGES:-8}` and `GHOSTWRITER_IMAGE_TOKENS: ${GHOSTWRITER_IMAGE_TOKENS:-}` in the backend block.
  - `test_env_example_documents_vision`: the lines `# GHOSTWRITER_VISION=on`, `# GHOSTWRITER_MAX_IMAGES=8`, `# GHOSTWRITER_IMAGE_TOKENS=` are present (the < 80 check already runs).
- [ ] **Step 2: Run** `uv run pytest tests/test_config.py tests/test_scaffold.py -q`. Expected: the new asserts FAIL.
- [ ] **Step 3: Implement** `_vision`, `_max_images`, `_image_tokens` (shape of `_speech_input` and `_context_tokens`). `.env.example` text, verbatim:

```
# Pictures: on lets users attach images, which go to the chat model with
# their message, and shows the model pages that have no text. Off, a model
# without vision, or a workbench before 0.7.0 behaves as before.
# GHOSTWRITER_VISION=on

# The most images one request carries; at most vllm-service's
# CHAT_LIMIT_MM_PER_PROMPT.
# GHOSTWRITER_MAX_IMAGES=8

# Tokens one image costs the model. Unset: one per 32x32 pixels (Qwen). A
# number: a fixed cost per image (280 for gemma); pages then go as two
# strips.
# GHOSTWRITER_IMAGE_TOKENS=
```
  `docs/configuration.md`: three rows after `GHOSTWRITER_CONTEXT_TOKENS`, saying the same, and that `GHOSTWRITER_MAX_IMAGES` must not exceed `CHAT_LIMIT_MM_PER_PROMPT`.
- [ ] **Step 4: Run** `uv run pytest -q` and `python3 -B ../deploy/scripts/check_env_examples.py .`. Expected: PASS; the env check clean.
- [ ] **Step 5: Commit** `feat(config): GHOSTWRITER_VISION, GHOSTWRITER_MAX_IMAGES and GHOSTWRITER_IMAGE_TOKENS`

### Task 2: Attachments get a kind, a status and a manifest (schema 2)

**Files:**
- Modify: `ghostwriter/models.py` (`Attachment`), `ghostwriter/db.py` (`SCHEMA_VERSION = 2`, `MIGRATIONS[1]`), `ghostwriter/schemas.py`, `ghostwriter/store.py`, `ghostwriter/retention.py` (`SweepReport.manifests`, `_sweep_contents`)
- Test: `tests/test_db.py`, `tests/test_store.py`, `tests/test_retention.py`

**Interfaces:**
- Produces:
  - `schemas.AttachmentKind = Literal["document", "image", "media"]` (`media` reserved for stage 3), `AttachmentStatus = Literal["processing", "ready", "failed"]`; `AttachmentOut.kind`, `AttachmentOut.status` after `mime`.
  - `Attachment.kind` `String(8)`, `server_default="document"`; `status` `String(10)`, `server_default="ready"`, `index=True`; `render` `Text` nullable; `failure` `String(40)` nullable; `CheckConstraint`s `ck_attachments_kind`, `ck_attachments_status`.
  - `MIGRATIONS[1]`, one transaction:
    ```sql
    ALTER TABLE attachments ADD COLUMN kind VARCHAR(8) NOT NULL DEFAULT 'document'
        CHECK (kind IN ('document', 'image', 'media'));
    ALTER TABLE attachments ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT 'ready'
        CHECK (status IN ('processing', 'ready', 'failed'));
    ALTER TABLE attachments ADD COLUMN render TEXT;
    ALTER TABLE attachments ADD COLUMN failure VARCHAR(40);
    CREATE INDEX ix_attachments_status ON attachments (status);
    ```
  - `STORED_MANIFEST_KEYS: Final = ("kind", "total", "pages_without_text", "renditions", "frames")`; a rendition keeps `n`, `width`, `height`, `mime` only (never `path`, `hint`, `bytes`).
  - `@dataclass(frozen=True) class AttachmentRef`: `id`, `chat_id`, `owner`, `file_id`, `path`, `name`, `kind: AttachmentKind`, `status: AttachmentStatus`, `manifest: Mapping[str, Any] | None` (`None` once the file period passed), `created_at`, `expired: bool`.
  - `ChatStore.add_attachment(..., kind: str = "document", status: str = "ready")`.
  - `ChatStore.find_attachment(owner, chat_id, attachment_id) -> AttachmentRef | None`: through the owner's chat only.
  - `ChatStore.attachment_out(owner, chat_id, attachment_id) -> AttachmentOut | None`.
  - `ChatStore.settle_attachment(attachment_id, *, status: Literal["ready", "failed"], manifest: Mapping[str, Any] | None, failure: str | None) -> bool`: only a `processing` row changes; `False` when the row is gone or settled.
  - `ChatStore.settle_processing(failure: str) -> int`: every `processing` row, images `failed`, others `ready`.
  - `ChatStore.pending_statuses(chat_id) -> dict[str, AttachmentStatus]`.
  - `ChatStore.processing_attachments() -> list[AttachmentRef]`, across chats, oldest first.
  - `due_contents(now, limit) -> tuple[list[str], list[str], list[str]]`: the third list holds attachments whose file period has passed and whose `render` is set. `empty_contents(step_ids, result_ids, attachment_ids: Sequence[str] = ())` sets `render = NULL`. A manifest is derived from the file, so it never outlives it (spec §2).
  - `_sweep_contents` records the third list as `delete` / `retention.tool_content`, target kind `attachment`, before emptying; `SweepReport.manifests: int = 0`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_db.py`: the fresh-file test expects version 2; the newer-file test writes `user_version = 3`; the migrations test patches `SCHEMA_VERSION` to 4 and `MIGRATIONS` to `{2: to_3, 3: to_4}` (`ran == [3, 4]`, version 3).
  - `test_a_version_1_file_gets_kinds_and_statuses`: create the v1 file by hand (`chats` via `Base.metadata.tables["chats"].create`, then the v1 `attachments` DDL below), one chat and one attachment row, `PRAGMA user_version = 1`; `init_db`. The row reads `kind == "document"`, `status == "ready"`, `render is None`, `failure is None`; version 2; an insert with `kind = 'video'` raises `IntegrityError`; `ix_attachments_status` is listed by `inspect(engine).get_indexes("attachments")`.
    ```sql
    CREATE TABLE attachments (id VARCHAR(36) NOT NULL PRIMARY KEY,
      chat_id VARCHAR(36) NOT NULL REFERENCES chats (id) ON DELETE CASCADE,
      message_id VARCHAR(36), workbench_file_id VARCHAR(32) NOT NULL, path VARCHAR(1024) NOT NULL,
      name VARCHAR(255) NOT NULL, size INTEGER NOT NULL, mime VARCHAR(127) NOT NULL, created_at DATETIME NOT NULL)
    ```
  - `tests/test_store.py`:
    - `test_an_attachment_carries_its_kind_and_status`: `add_attachment(..., kind="image", status="processing")` serves both; `chat_detail` too.
    - `test_a_settled_manifest_keeps_metadata_only`: settling with `{"path", "kind", "total", "pages_without_text", "renditions": [{"n", "width", "height", "mime", "bytes"}], "frames", "hint"}` stores exactly `STORED_MANIFEST_KEYS`, renditions with `{"n", "width", "height", "mime"}`.
    - `test_settling_is_once_and_only_from_processing`: a second settle and a settle of a `ready` row return `False`.
    - `test_an_attachment_is_found_only_through_its_owners_chat`: another owner's or another chat's lookup is `None`.
    - `test_pending_statuses_name_each_waiting_attachment`.
    - `test_processing_attachments_are_listed_across_chats_with_their_owner`.
    - `test_stranded_processing_rows_settle`: an image becomes `failed` with the token, a PDF `ready`.
    - `test_a_manifest_expires_with_its_file`: past the file period `find_attachment(...).manifest is None`, `due_contents` names the id, and `empty_contents` empties `render`.
  - `tests/test_retention.py`: `test_expired_manifests_are_recorded_then_emptied`: one `delete` / `retention.tool_content` event, target `attachment` [id], then `render` is NULL; `report.manifests == 1`.
- [ ] **Step 2: Run** `uv run pytest tests/test_db.py tests/test_store.py tests/test_retention.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces. Existing exact-JSON asserts on attachments in `tests/` gain `"kind": "document", "status": "ready"`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(store): attachments carry a kind, a status and their manifest (schema 2)`

### Task 3: The workbench client keeps images, prepares, and fetches renditions

**Files:**
- Modify: `ghostwriter/workbench.py` (`ToolResult` :182, `_decode` :552, `view` :1037, new methods), `tests/fakes.py`
- Test: `tests/test_workbench.py`

**Interfaces:**
- Produces in `workbench.py`:
  - `MAX_IMAGE_BYTES = 1024 * 1024`, `MAX_IMAGES_BYTES = 3 * 1024 * 1024`, `MAX_TOOL_IMAGES = 8`, `MAX_RENDITION_BYTES = 768 * 1024`, `PREPARE_TIMEOUT_S = 270.0` (workbench's 120 s queue + its 120 s wall + 30 s), `RENDITION_TIMEOUT_S = 30.0`, `IMAGE_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}`.
  - `@dataclass(frozen=True) class ToolImage`: `label: str`, `width: int`, `height: int`, `mime: str`, `data: bytes = field(repr=False)`.
  - `ToolResult.images: tuple[ToolImage, ...] = ()`.
  - `@dataclass(frozen=True) class Rendition`: `n`, `width`, `height`, `mime`. `@dataclass(frozen=True) class Manifest`: `kind: Literal["image", "pdf", "none"]`, `total: int`, `pages_without_text: tuple[int, ...]`, `renditions: tuple[Rendition, ...]`, `frames: int | None`; `as_stored(self) -> dict[str, Any]` (the five stored keys).
  - `async def WorkbenchClient.prepare(self, *, call: CallFor, path: str, strips: int = 1) -> Manifest`: `POST /files/prepare?path=` (and `&strips=k` only when `k > 1`: workbench then also makes a picture's strips on its prepare gate), no body, `PREPARE_TIMEOUT_S`. A path outside the chat's folder is `WorkbenchNotFound` unsent; 404 `WorkbenchNotFound`; 507 `StorageFull`; 422 `WorkbenchUnavailable(<token>)`, the token being the `detail` text before its first `:` when it matches `_TOKEN` (`unreadable_file`, `unsupported_format`, `read_timeout`), else `http:422`; other statuses `http:<status>`; a malformed manifest `bad_answer`.
  - `view(self, *, call, path, rendition: int | None = None, strips: int = 1, strip: int | None = None)`: the parameters go only with `rendition`; `strip` only when `strips > 1`.
  - `@dataclass(frozen=True) class RenditionRequest`: `path: str`, `n: int = 1`, `strips: int = 1`, `strip: int = 1`.
  - `def WorkbenchClient.fetch_renditions(self, wanted: Sequence[RenditionRequest], *, call: CallFor) -> list[tuple[str, bytes] | None]`: synchronous (the turn's thread), never raises. One `_detached` exchange within `RENDITION_TIMEOUT_S`, one `AsyncClient`, all requests at once in an anyio task group. An answer counts only if it is 200, `image/jpeg` or `image/png`, under `MAX_RENDITION_BYTES` (read as a stream, cut past it) and has its signature; otherwise `None`. One log line with counts.
  - `_decode`: image blocks of `content[1:]` (`type == "image"`, `mimeType` in `IMAGE_SIGNATURES`, `base64.b64decode(data, validate=True)`, signature, ≤ `MAX_IMAGE_BYTES`) pair by position with `structuredContent.shown[i]` (`label` a string ≤ 100, `width`/`height` ints 1–4096). Keeping stops at `MAX_TOOL_IMAGES` or past `MAX_IMAGES_BYTES`. Dropped blocks give one warning with their count. `text` is `content[0].text` as before.
- Produces in `tests/fakes.py`:
  - `TINY_JPEG = b"\xff\xd8\xff\xe0" + bytes(60)`, `TINY_PNG = b"\x89PNG\r\n\x1a\n" + bytes(56)`.
  - `image_block(data: bytes, mime: str = "image/jpeg") -> dict`, and `view_result(folder, shown: list[dict], blocks: list[dict]) -> dict`: `content [text, *blocks]`, and `structuredContent` with the contract's eight keys and `outputs: []`.
  - `CONTRACT_TOOLS` gains `view_file` (`{"path": string, "part": string, "strips": integer}`), and `read_document` gains `"strips": {"type": "integer"}`; the docstrings say ten tools.
  - `FakeDoor`:
    - `_ROUTES` gains `("POST", "/files/prepare")`; `answers["/files/prepare"] = None` answers from `_prepare`.
    - `manifests: dict[str, dict]` answers by path first. Otherwise a stored file of the chat gets its manifest by suffix: an image `kind image`, `total 1`, one rendition 64 × 48 `image/jpeg`; `.pdf` `total 3`, `pages_without_text [2]`, renditions 1–3 at 1131 × 1600; anything else `kind none`. Another chat's path, or an unknown one, is 404.
    - `/files/view` with `rendition` serves `TINY_JPEG` (strip `i` of `k > 1`: `TINY_JPEG + bytes([i])`) as `image/jpeg`; 404 outside `1..total`.

- [ ] **Step 1: Write the failing tests** (`tests/test_workbench.py`):
  - `test_a_view_file_answer_keeps_its_images_with_their_labels`: two blocks → `images[i].label`, `width`, `height`, `mime`, `data == TINY_JPEG`; `text` unchanged.
  - `test_image_blocks_that_are_not_images_are_dropped`, parametrized: bad base64; `image/gif`; `TINY_PNG` under `image/jpeg`; 1 MiB + 1 byte; no `shown` entry. Each gives `images == ()`, the text intact, one warning without the label.
  - `test_at_most_8_images_and_3_mib_are_kept`: 10 blocks give 8; four of 900 KiB give 3.
  - `test_prepare_answers_the_manifest`: `POST /files/prepare` with `path` and the chat's session; `Manifest.kind == "pdf"`; `as_stored()` has the five keys.
  - `test_prepare_maps_workbench_refusals`, parametrized: 404 → `WorkbenchNotFound`; 422 `{"detail": "unreadable_file: damaged"}` → token `unreadable_file`; 503 → `http:503`; 507 → `StorageFull`.
  - `test_prepare_never_asks_outside_the_chat`: nothing is sent.
  - `test_a_rendition_view_carries_its_parameters`: `rendition=2, strips=2, strip=1` gives those query parameters; without `rendition`, only `path`.
  - `test_renditions_are_fetched_together_and_checked`: three wanted, the door 404 for one and `TINY_PNG` under `image/jpeg` for another, give `[("image/jpeg", TINY_JPEG), None, None]`. With `delays["/files/view"] = 0.3` the call takes under 0.8 s.
  - `test_fetch_renditions_never_raises`: a transport that raises gives `[None, None]`.
  - `test_every_request_carries_the_session_purpose_and_request_id` gains `prepare` and `fetch_renditions`.
  - `test_the_log_holds_no_image_bytes` (`log_lines`): no base64 of `TINY_JPEG`, no label.
- [ ] **Step 2: Run** `uv run pytest tests/test_workbench.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `prepare` and `view` follow `_upload`'s `_rest_errors` shape.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(workbench): keep tool images, prepare attachments and fetch renditions`

### Task 4: When images are offered

**Files:**
- Create: `ghostwriter/vision.py`, `tests/test_vision.py`
- Modify: `ghostwriter/upload.py`, `ghostwriter/api/meta.py`, `ghostwriter/main.py` (`create_app(..., inference_transport=None)`, `app.state.vision`, the probe task)
- Test: `tests/test_upload.py`, `tests/test_api_meta.py`

**Interfaces:**
- `upload.py`:
  - `IMAGE_TYPES: Final[dict[str, str]]`: `.png` `image/png`, `.jpg`/`.jpeg` `image/jpeg`, `.webp` `image/webp`, `.gif` `image/gif`, `.bmp` `image/bmp`, `.tif`/`.tiff` `image/tiff`, `.heic` `image/heic`, `.heif` `image/heif`.
  - `PREPARED_SUFFIXES: Final = frozenset({".pdf", *IMAGE_TYPES})`; stage 2 adds the Office suffixes here.
  - `attachment_type(name, *, images: bool = False) -> str | None`; `attachment_kind(name) -> Literal["document", "image"]`; `needs_prepare(name) -> bool`.
- `vision.py`:
  - `VIEW_TOOL: Final = "view_file"`, `PROBE_TIMEOUT_S: Final = 5.0`.
  - `def system_call() -> CallFor`: `CallFor("ghostwriter:system", "capability", forward_headers(RequestContext(SYSTEM_ACTOR, None, uuid4().hex)) | {"X-Accept-Images": "1"})`. workbench reads no principal on discovery; the header asks what it would offer a caller that takes images.
  - `ACCEPT_IMAGES: Final = {"X-Accept-Images": "1"}`: merged into a turn's `CallFor` headers exactly when `images_offered` is true (Task 7 builds the turn's call with it); test that a turn with vision off sends no such header.
  - `class Vision`:
    - `__init__(self, settings: Settings)`; `model_sees: bool` starts as `settings.vision`; `probed: threading.Event` is set when a probe ended (tests wait on it).
    - `def images_offered(self, workbench: WorkbenchClient | None) -> bool`: `model_sees`, a workbench, and `VIEW_TOOL` in `workbench.discover(call=system_call())`. Discovery is the cached, single-flight one, so it blocks at most once per cache window; call it from a thread.
    - `async def probe(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None`: see below.
- `meta.ConfigOut` gains `image_types: list[str]` (sorted; empty unless offered), `max_images: int`, `vision: bool` (images offered). While offered, `attachment_types` includes the image types.
- `main.py`: `app.state.vision = Vision(settings)`. With `settings.vision` and provider `ollama`, the lifespan starts `asyncio.create_task(vision.probe(transport=inference_transport))` and cancels it at shutdown, as it does the sweep.

`probe`, the one place the Ollama rule lives:

```python
base = settings.openai_api_base.removesuffix("/v1")
POST f"{base}/api/show", json={"model": settings.text_model}, Authorization Bearer key, trust_env=False, PROBE_TIMEOUT_S
answer 200 JSON with "capabilities" a list of strings:
    "vision" missing  -> model_sees = False; logger.warning("The chat model lists no vision capability; images are not offered.")
    "vision" present  -> nothing changes
anything else (status, transport error, not JSON, no list) -> nothing changes;
    logger.warning("The chat model's capabilities could not be read ({}); images stay as configured.", token)
finally: probed.set()
```

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_upload.py`:
    - `test_images_are_attachable_only_when_offered`: `attachment_type("photo.HEIC")` is None, `(…, images=True)` is `image/heic`; `.svg` is None either way.
    - `test_kinds_and_preparation_follow_the_extension`: `quay-scan.pdf` → document, prepared; `deck.pptx` → document, not prepared; `harbour-photo.jpg` → image, prepared; `notes.txt` → document, not prepared.
  - `tests/test_vision.py` (a `WorkbenchClient` over `httpx.MockTransport(FakeDoor())`):
    - `test_images_are_offered_with_vision_a_seeing_model_and_view_file`: True. Without `view_file` in `tools_result`, False. `vision=False` gives False with no request sent; workbench `None` gives False.
    - `test_the_offer_reads_a_cached_discovery`: two calls; `door.methods() == ["server/discover", "tools/list"]`.
    - `test_ollama_without_vision_turns_images_off`: base `http://ollama.test:11434/v1`. The request goes to `http://ollama.test:11434/api/show` with `{"model": <text_model>}`. Capabilities `["completion", "tools"]` give `model_sees is False` and a warning (`loguru_caplog`).
    - `test_ollama_with_vision_keeps_images_on`.
    - `test_a_failed_probe_keeps_the_setting`, parametrized: 500, `httpx.ConnectError`, not JSON, no `capabilities`. Each keeps `model_sees is True` and sets `probed`.
    - `test_no_probe_on_vllm`: an app with provider `vllm` never calls the inference transport.
  - `tests/test_api_meta.py`:
    - `test_config_reports_what_the_spa_needs` gains `"image_types": [], "max_images": 8, "vision": False`.
    - `test_config_offers_image_types_with_vision`: `vision=True` and a `FakeDoor` give the 10 image suffixes sorted in `image_types` and in `attachment_types`, and `vision: True`.
    - `test_config_offers_no_images_against_a_workbench_without_view_file`.
    - `test_config_offers_no_images_once_ollama_says_the_model_cannot_see` (wait on `app.state.vision.probed`).
- [ ] **Step 2: Run** `uv run pytest tests/test_upload.py tests/test_vision.py tests/test_api_meta.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(vision): offer images only with a seeing model and a workbench that lists view_file`

### Task 5: The attachment processor and its routes

**Files:**
- Create: `ghostwriter/processing.py`, `tests/test_processing.py`
- Modify: `ghostwriter/api/files.py`, `ghostwriter/api/messages.py` (`send_message` :497), `ghostwriter/api/chats.py` (`delete_chat` :181), `ghostwriter/retention.py` (`Sweeper(..., processor=None)`, `_delete_chats`), `ghostwriter/main.py` (lifespan), `docs/audit.md`
- Test: `tests/test_api_files.py`, `tests/test_api_messages.py`

**Interfaces:**
- `processing.py`:
  - `PREPARE_SLOTS: Final = 2`, `PREPARE_RETRY_S: Final = 10.0`, `MAX_WAIT_S: Final = 20`, `PREPARE_RECIPIENT: Final = "workbench:prepare"`.
  - `@dataclass(frozen=True) class PrepareJob`: `attachment_id`, `chat_id`, `owner`, `path`, `kind: Literal["document", "image"]`, `op: Operation | None`. `None` means a system operation is opened, as at start.
  - `class AttachmentProcessor(store: ChatStore, workbench: WorkbenchClient, emitter: AuditEmitter)`:
    - `submit(job) -> None`: event-loop thread only; a second submit of one id is a no-op.
    - `async wait(attachment_id, timeout_s) -> None`: until its task ends. Without a task it sleeps `timeout_s`, since the status cannot change without one.
    - `cancel_chat(chat_id) -> int`; `requeue() -> int` (`store.processing_attachments()`, `op=None`); `async aclose() -> None`.
  - A job, under `asyncio.Semaphore(PREPARE_SLOTS)`:
    1. `store.mark_files(chat_id)`. `ChatNotFound` ends it quietly.
    2. Record the hand-over: `op.child("disclose", "chat.attachment", target=attachment [id], recipient=PREPARE_RECIPIENT, criteria={"kind": kind})`. With `op=None` it is the primary of `system_operation(emitter, "disclose", "chat.attachment")`, container the chat, criteria `{"kind", "requeued": True}`. An `AuditWriteError` sends nothing and settles as a failure with token `audit`.
    3. `workbench.prepare(...)`; `http:503` or `timeout` is tried once more after `PREPARE_RETRY_S`. For an image whose manifest gives `strips_for_image(width, height, fixed=settings.image_tokens is not None) == 2`, `prepare(..., strips=2)` follows once (idempotent), so the first turn finds the strips made and runs no job on the user's turn; its failure leaves the image `ready` (the turn makes the strips on demand).
    4. Success: `settle_attachment(ready, manifest.as_stored())`. Failure: an `error` child `{"reason": token}`, then an image is `failed` (failure = the token) and a document `ready` without a manifest (its text still reads).
    5. A cancel settles nothing.
    Store and audit calls run in `anyio.to_thread.run_sync`. Logs carry the attachment id, kind and token only.
- `api/files.py`:
  - The upload: `offered = await run_in_threadpool(vision.images_offered, workbench)`; `_checked_name(raw, images=offered)`; `kind = attachment_kind(name)`; `status = "processing" if offered and needs_prepare(name) else "ready"`; `add_attachment(..., kind=kind, status=status)`. While processing: `processor.submit(PrepareJob(..., op=op))`.
  - `GET /{chat_id}/attachments/{attachment_id}`, `wait: int = Query(0, ge=0, le=MAX_WAIT_S)`, op `audited("query", ATTACHMENT)`. 404 `Attachment not found.` unless `find_attachment` finds it. Waits while processing, reads again, records target `attachment` [id] with criteria `{"status": status}`, answers `AttachmentOut`.
  - `GET /{chat_id}/attachments/{attachment_id}/preview`, `page: int = Query(1, ge=1, le=10_000)`, op `audited("disclose", "file.preview")`:
    - 404 when not found, `failed`, without a manifest, or `page > total`; 410 when expired; 409 `attachment_processing`.
    - Records target `file` [file_id], criteria `{"page": page}`, recipient `preview`, then streams `workbench.view(call, path, rendition=page)` through `FileStream`.
    - `WorkbenchNotFound` is an `error` child and 410; `WorkbenchUnavailable` an `error` child and 502.
- `api/messages.py`: `PROCESSING: Final = "Wait until the attached files are ready."`, `PROCESSING_CODE: Final = "attachment_processing"`. `send_message` reads `store.pending_statuses(chat_id)`: an unknown id is 400 as today; any `processing` id is 409 `{detail, code}` before anything is recorded. A `failed` one is sent and named as unreadable (Task 6).
- `chats.delete_chat`: after `op.record`, `anyio.from_thread.run_sync(processor.cancel_chat, chat_id)` when a processor exists; `Sweeper._delete_chats` cancels before its folder delete.
- `main.py` lifespan: with workbench on, `app.state.processor = AttachmentProcessor(...)` and `requeue()` after `apply_periods`; without, `app.state.processor = None` and `store.settle_processing("unavailable")`. `await processor.aclose()` at shutdown, before the engine goes.

- [ ] **Step 1: Write the failing tests** (`tests/test_api_files.py` with `make_settings(tmp_path, vision=True)` unless named; `tests/test_processing.py` for the processor alone):
  - `test_other_types_are_refused_with_415` stays as it is (vision off: `image.png` is 415).
  - `test_an_image_is_prepared_after_it_is_attached`:
    - the 201 body has `kind "image"` and `status "processing"`;
    - `GET …/attachments/{id}?wait=20` answers `ready`;
    - the door saw `POST /files/prepare` with the upload's path;
    - the events, in order: `collect`, `disclose` (`workbench:upload`), `disclose` (`workbench:prepare`, criteria `kind`).
  - `test_a_pdf_gets_its_manifest_and_a_word_file_none`: the PDF goes `processing` → `ready`, `find_attachment(...).manifest["total"] == 3`; a `.docx` is `ready` at once with no prepare request.
  - `test_an_unreadable_image_fails_and_a_pdf_stays_ready`: the door answers 422 `unreadable_file: damaged`. The image ends `failed`, the PDF `ready`; each has an `error` child with reason `unreadable_file`.
  - `test_a_busy_prepare_is_tried_once_more`: 503 then the default (`PREPARE_RETRY_S` patched to 0) ends `ready`.
  - `test_the_status_route_is_the_owners_and_the_chats`: another user, another chat id and an unknown id are each 404 and record nothing.
  - `test_the_long_poll_returns_when_ready_or_after_its_wait`: prepare delayed 0.5 s; `wait=20` answers `ready` in under 5 s; `wait=0` answers `processing` at once; `wait=21` is 422.
  - `test_a_preview_streams_the_rendition_with_the_hardening`: 200 `image/jpeg`, `FILE_HEADERS`, body `TINY_JPEG`; the door saw `rendition=1`; one `disclose` / `file.preview` with `page: 1`.
  - `test_a_preview_is_found_only_through_its_chat`: another user or chat is 404 with no view request.
  - `test_a_preview_of_a_processing_failed_or_expired_attachment`: 409 code `attachment_processing` / 404 / 410.
  - `test_a_pdf_page_past_its_total_is_404`.
  - `test_a_chat_deleted_while_preparing_cancels_the_job` (Review Focus 1): prepare delayed 2 s; `DELETE` is 204 at once; no task is left; no error-level log (`loguru_caplog`); `/files/chat` was sent.
  - `test_processing_attachments_are_prepared_again_at_start` (Review Focus 2): a `processing` row written before start, its manifest in `door.manifests[path]`, becomes `ready`. The primary is `disclose` / `chat.attachment` by the system, with `requeued: true`.
  - `test_without_workbench_stranded_rows_settle`: `workbench_api_key=None`; the image is `failed`, the PDF `ready`.
  - `tests/test_processing.py` (a store, a `WorkbenchClient` over `FakeDoor`, an emitter on `CapturedAuditStream`):
    - `test_a_second_submit_of_one_attachment_is_a_no_op`: one prepare request.
    - `test_at_most_two_prepares_run_at_once`: three jobs with prepare delayed 0.3 s never have more than two requests in flight.
    - `test_an_unrecorded_hand_over_sends_nothing`: with the stream failing, no prepare request; the image is `failed` with `audit`.
    - `test_a_cancelled_job_settles_nothing`: `cancel_chat` during the delay leaves the row `processing` and returns 1.
  - `tests/test_api_messages.py`: `test_a_message_waits_for_its_attachments`. A `processing` attachment is 409 `{"detail": "Wait until the attached files are ready.", "code": "attachment_processing"}`, with no event, no stored message and no model call. A `failed` one is taken.
- [ ] **Step 2: Run** `uv run pytest tests/test_api_files.py tests/test_processing.py tests/test_api_messages.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**. In `docs/audit.md`'s catalogue:
  - the attachment row gains the `disclose` `chat.attachment` (`workbench:prepare`) child, with its `error` child;
  - new rows for `GET …/attachments/{id}` (`query` `chat.attachment`, target `attachment` [id], criteria `status`), `GET …/attachments/{id}/preview` (`disclose` `file.preview` (`preview`), target `file` [id], `error` child on failure) and "start-up: a `processing` attachment" (`disclose` `chat.attachment` as the system, `requeued`);
  - the sweep's `retention.tool_content` row names target `attachment` (manifests).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(files): prepare images and PDFs in the background, with status and preview routes`

### Task 6: Images in the context: parts, estimates, the budget

**Files:**
- Create: `ghostwriter/agent/images.py`, `tests/test_agent_images.py`
- Modify: `ghostwriter/agent/context.py` (`PastTurn`, `user_content`, `_Size`, `fit`, `_replayed`), `ghostwriter/store.py` (`_prompt_text`, `user_turn`, `past_turns`), `ghostwriter/agent/loop.py` (the `user_turn` call site only)
- Test: `tests/test_agent_context.py`

**Interfaces:**
- `agent/images.py`:
  - `STRIP_LONG_SIDE: Final = 1024`, `TOKEN_SIDE: Final = 32`.
  - `@dataclass(frozen=True) class AttachedImage`: `file_id`, `path`, `name`, `width`, `height` (rendition 1).
  - `@dataclass(frozen=True) class ImageBudget`: `fixed: int | None`, `max_images: int`.
  - `ImageBank = dict[str, tuple[str, bytes]]` (key → mime, bytes).
  - `estimate_image_tokens(width: int, height: int, *, fixed: int | None) -> int`: `fixed`, else `ceil(w / 32) * ceil(h / 32)`.
  - `strips_for_image(width, height, *, fixed) -> int`: 2 when `fixed` is set and the long side is > 1024, else 1.
  - `strips_for_path(path, *, fixed, known: Mapping[str, tuple[int, int]]) -> int | None`: `None` without `fixed`; 2 for `.pdf`; for an image suffix `strips_for_image` on `known[path]`, or 1 when unknown; otherwise `None`.
  - A ref part: `{"type": "image_ref", "key", "origin": "attachment" | "tool", "label", "width", "height"}`, plus `path`, `n`, `strip`, `strips` for an attachment. It never leaves the process.
  - `attachment_parts(images: Sequence[AttachedImage], *, fixed: int | None) -> list[dict[str, Any]]`: one ref per strip, key `att:{file_id}:{strip}/{strips}`, label `name` or `"{name}, strip {i} of {k}"`.
  - `tool_image_message(groups: Sequence[tuple[str, str, Sequence[ToolImage]]]) -> tuple[dict[str, Any], ImageBank]`: groups of `(tool, call_id, images)` give one `role: user` message, per group a `LEGEND` text part then its refs (key `tool:{call_id}:{i}`), and the bank entries.
  - Texts, verbatim:
    - `LEGEND = "[Images returned by {tool} for call {call_id}: {labels}. Not a message from the user.]"` (labels joined by `"; "`)
    - `LABEL = "[Image: {label}]"`
    - `RETIRED = "[Image: {label}; not shown again to make room, view it again with view_file]"`
    - `OVER_CAP = "[Image: {label}; not shown, at most {max} images go to the model at once; view_file shows it]"`
    - `NOT_RESENT = "[Image: {label}; not shown again, view_file shows it]"`
    - `NOT_LOADED = "[Image: {label}; could not be loaded, view_file shows it]"`
    - `LEFT_OUT = "[Image: {label}; left out, the model did not take images]"`
  - `refs(messages) -> list[dict[str, Any]]`; `note(ref, template, **values) -> dict[str, Any]` (a text part).
  - `materialise(messages, bank) -> tuple[list[dict[str, Any]], int]`: a new list. Each ref becomes `LABEL` and `{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}`, or `NOT_LOADED` when its key is not in the bank. Returns the image count; the input is unchanged.
  - `without_images(messages) -> list[dict[str, Any]]`: every ref becomes `LEFT_OUT`.
  - `retire_tool_images(current: list[dict[str, Any]], bank: ImageBank, *, keep: int) -> None`: tool refs older than the newest `keep` become `RETIRED`, in place, and leave the bank.
- `context.py`:
  - `PastTurn.images: tuple[AttachedImage, ...] = ()` (last field).
  - `user_content(text, attachments, *, path_of, facts_of: Callable[[AttachmentOut], str | None] = lambda _: None) -> str`. A `failed` attachment reads `[Attached: {name}; could not be read]`; facts go inside the brackets: `[Attached: {name} ({path}, {size} bytes; {facts})]`.
  - `attachment_facts(kind: str, manifest: Mapping[str, Any] | None, *, seen: bool) -> str | None`:
    - an image: `image 2048×1536`, plus `; not shown: this model does not take images` when not `seen`;
    - a PDF: `PDF, 12 pages` or `PDF, 1 page`, plus `; pages 3, 7 have no text` or `; page 3 has no text`;
    - else `None`.
  - `fit(system, tools, past, current, *, budget=PROMPT_TOKENS, images: ImageBudget | None = None)`. `_Size` counts each ref as its `LABEL` text plus `estimate_image_tokens`, and keeps the image count. `_Size.replace(message, index, part)` keeps all three in step.
- `store.py`:
  - `@dataclass(frozen=True) class UserTurn`: `text: str`, `images: tuple[AttachedImage, ...]`, `position: int`.
  - `user_turn(chat_id, message_id, *, seen: bool = False) -> UserTurn`; `past_turns(..., seen: bool = False)` fills `PastTurn.images`.
  - Images are listed for `kind image`, `status ready`, a manifest with rendition 1, and not expired.

`fit`, its order (the code only where the order is new; clearing and cutting stay as they are):

```python
kept = [system_message, *(_copied(m) for m in current)]          # list contents copied: current never changes
size = _Size(kept, images)
def over() -> bool:
    return size.tokens() + fixed > budget or (images is not None and size.images > images.max_images)
if images is not None:
    for message, i in _refs_of(kept, origin="tool"):            # the current turn's tool images, oldest first
        if not over(): break
        size.replace(message, i, note(message["content"][i], RETIRED))
    for message, i in reversed(_refs_of(kept[1:2], origin="attachment")):   # the question's images, last first
        if size.images <= images.max_images: break
        size.replace(message, i, note(message["content"][i], OVER_CAP, max=images.max_images))
# then, unchanged: clear tool results oldest first; then cut the current turn's arguments; then the warning
for turn in reversed(past):                                     # newest first, as today
    modes = ["none"] if images is None or not turn.images else ["images", "notes"]
    for mode in modes:
        messages = _replayed(turn, mode=mode, images=images)    # images: refs; notes: NOT_RESENT lines; none: text
        if fits(messages) and (mode != "images" or size.images + len(refs(messages)) <= images.max_images):
            add(messages); break
    else:
        break                                                   # nothing of this turn fits: stop, as today
```

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_images.py`:
    - `test_an_image_costs_its_fixed_price_or_one_token_per_32_pixels`: `(2048, 1536, None) == 3072`, `(1600, 1131, None) == 1800`, `(100, 10, None) == 4`, `(2048, 1536, 280) == 280`.
    - `test_strips_only_for_a_fixed_cost`:
      - `strips_for_image(2048, 1536, fixed=None) == 1`, `(…, fixed=280) == 2`, `(1024, 768, fixed=280) == 1`;
      - `strips_for_path("/workspace/c/quay-scan.PDF", fixed=280, known={}) == 2`;
      - a `.png` known at 1600 × 1200 gives 2, unknown 1; `fixed=None` gives `None`; `.xlsx` gives `None`.
    - `test_attachment_parts_label_each_strip`: two refs, keys `att:F:1/2` and `att:F:2/2`, labels `harbour-photo.jpg, strip 1 of 2`.
    - `test_tool_images_go_into_one_user_message_with_a_legend`: the exact `LEGEND` text for `view_file` / `call_0` with two labels, then the refs in order; the bank holds both.
    - `test_materialise_swaps_refs_for_data_urls_and_notes_for_missing_ones`: the URL is `"data:image/jpeg;base64," + base64.b64encode(TINY_JPEG).decode()`, preceded by `[Image: …]`. A key missing from the bank gives `NOT_LOADED`. The count is right, and `copy.deepcopy(input) == input` afterwards.
    - `test_without_images_leaves_only_notes`; `test_retire_tool_images_keeps_the_newest`.
  - `tests/test_agent_context.py`:
    - `test_the_attachment_line_carries_facts`: an image, an unseen image, a PDF with and without text-less pages, a 1-page PDF, a failed and an expired attachment, each with its exact line.
    - `test_an_image_counts_its_estimate_against_the_budget`: `fixed=280`. Two tool refs, with a budget that fits the text plus 280 but not plus 560: the older becomes `RETIRED` and the newer stays.
    - `test_tool_images_retire_oldest_first_before_results_are_cleared`: a budget short by one image clears no result; short by more retires every tool image, then results are `CLEARED`.
    - `test_the_image_cap_holds_across_the_request`: `max_images=3`, the question with 2 images and 2 tool images: the oldest tool image is retired. A past turn with an image replays with `NOT_RESENT`.
    - `test_earlier_turns_replay_with_their_images_while_budget_and_cap_allow`: the newest replays with its image refs, the next with notes, the oldest is left out.
    - `test_without_an_image_budget_nothing_changes`: every existing `fit` test passes untouched (run them).
    - `test_the_store_lists_ready_images_of_a_turn`: a ready image gives `AttachedImage(width=64, height=48)`; a processing, failed or expired image, or a PDF, gives none.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_images.py tests/test_agent_context.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `loop.py` takes `UserTurn` (`text`, `position`) and nothing more in this task.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): image parts, their estimate and their place in the budget`

### Task 7: The turn sends images

**Files:**
- Modify: `ghostwriter/agent/loop.py`, `ghostwriter/agent/tools.py`, `ghostwriter/agent/prompt.py`, `ghostwriter/api/messages.py` (`_frame`, `OperationTurnAudit.model_called`), `ghostwriter/main.py` (`AgentLoop(..., vision=app.state.vision)`), `tests/fakes.py` (`RecordingAudit`, `FakeWorkbench`)
- Test: `tests/test_agent_loop.py`, `tests/test_api_messages.py`

**Interfaces:**
- `tools.py`:
  - `HIDDEN_ARGUMENTS: Final = frozenset({"strips"})`; `function_tools(tools, *, hidden=HIDDEN_ARGUMENTS)` leaves them out of `properties` and `required` (copies, never the cached schema).
  - `STRIPPED_TOOLS: Final = frozenset({"view_file", "read_document"})`.
  - `SHOWN: Final = "[{n} images were shown]"`; `NOT_SHOWN: Final = "[{n} images could not be shown: this model does not take images]"`.
  - `tool_message(outcome, *, images_seen: bool = True)` appends the line after a newline when `outcome.images`. The store keeps exactly this text.
- `prompt.py`: `VISION: Final`, verbatim: `"Images the user attached are in their message: look at them. Read a document with read_document first; pages without text come back as images in the same answer. For how something looks (layout, design, charts, handwriting, photos), look with view_file, at most 4 pages a call. Images of earlier messages are not sent again: use view_file to see one again."`. `system_prompt(..., vision: bool = False)` appends it after the folder sentence, only with a folder.
- `loop.py`:
  - `AgentLoop(..., vision: Vision | None = None)`; `REFUSED_IMAGE_STATUSES: Final = frozenset({400, 413, 422})`.
  - `@dataclass(frozen=True) class NoticeEvent`: `code: Literal["images_dropped"]`, added to `TurnEvent`.
  - `TurnAudit.model_called(*, round, done, outcome, images: int = 0)`.
- `messages.py`: `NoticeEvent` → `sse_frame("notice", {"code": event.code})`; `chat.answer` criteria gain `"images": images`. The module docstring lists the frame.
- `fakes.py`:
  - `RecordingAudit.images: list[int]`.
  - `FakeWorkbench.fetch_renditions(wanted, *, call)` answers `("image/jpeg", TINY_JPEG)` for each, `None` for paths in `missing: set[str]`, and records `fetched`.

The turn, as the loop now runs it:
1. `sees = vision is not None and vision.model_sees`; `budget_images = ImageBudget(settings.image_tokens, settings.max_images) if sees else None`.
2. The tools offered: discovered, `view_file` left out unless `sees`, `strips` hidden.
3. The question: `store.user_turn(..., seen=sees)`. Its content is `[{"type": "text", "text": text}, *attachment_parts(images, fixed=…)]` with images and `sees`, else the text string. `known` sizes come from the question's and earlier turns' `AttachedImage`s.
4. Each round: `fit(..., images=budget_images)`. Attachment refs not in the bank and not tried yet are fetched in one `fetch_renditions`. Then `materialise`, then `stream_round(wire, …)`.
5. A round that raises `openai.APIStatusError` with a status in `REFUSED_IMAGE_STATUSES`, carried images, and showed no text:
   - record `model_called(error, images=n)` and yield `NoticeEvent("images_dropped")` once;
   - run the same round again with `materialise(without_images(fitted), bank)`; every later round of the turn goes the same way;
   - a second failure is the model error, as today.
6. `view_file` and `read_document` arguments: while `sees`, `strips = strips_for_path(path, fixed, known)` is set when not `None`, else removed. Without `sees`, it is removed. Whatever the model sent for it is dropped.
7. After a round's tool messages: while `sees` and any `ToolResult` has images, `tool_image_message` is appended to `current` and the bank updated. Then `retire_tool_images(current, bank, keep=settings.max_images)` runs.
8. The tool message and the stored step are `tool_message(outcome, images_seen=sees)`.
9. Each round records `images` = what `materialise` counted. The bank is a local of the run: it goes when the message's answer ends.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_loop.py`. `LoopEnv.run` gains `vision: bool = False` (builds `Vision` from `dataclasses.replace(settings, vision=True)`) and `attach: Sequence[tuple[str, dict]] = ()`, names and manifests settled `ready` before the message binds them:
    - `test_an_attached_image_goes_into_the_users_message`: the last user message's content is the text part (the line holds `image 64×48`), `[Image: harbour-photo.jpg]`, and one `image_url` with the data URL. One rendition request (`n 1`, `strips 1`). The tools sent include `view_file`, and neither its schema nor `read_document`'s has `strips`.
    - `test_a_fixed_image_cost_sends_two_strips_of_a_large_image`: `image_tokens=280`, a 2048 × 1536 manifest. Two `image_url` parts, labelled `… strip 1 of 2` and `… strip 2 of 2`, fetched with `strips=2`, `strip=1, 2`.
    - `test_tool_images_follow_the_tool_messages_in_one_user_message`. One round: `view_file` answers 2 images and `read_document` 1. Round 2 sends `assistant(tool_calls)`, `tool`, `tool`, then one `user` message: the `view_file` legend, its 2 images, the `read_document` legend, its image. The tool messages end with `[2 images were shown]` and `[1 images were shown]`. The stored steps hold the same text and no base64.
    - `test_ghostwriter_sets_strips_and_the_model_cannot`: `image_tokens=280` and the model sending `view_file {"path": ".../quay-scan.pdf", "strips": 3}` sends `strips: 2`. `read_document` of an `.xlsx` sends no `strips`. Without `image_tokens`, no `strips`.
    - `test_a_follow_up_replays_tool_steps_as_text_and_attached_images_again`. Turn 2 sends turn 1's question with its `image_url`, and turn 1's tool message as text only. Turn 1's legend message is gone.
    - `test_without_vision_view_file_is_not_offered_and_tool_images_are_named` (Review Focus 4): `vision=False` and `read_document` answering one image. No `image_url` is sent; the tool message ends with `[1 images could not be shown: this model does not take images]`; no `view_file` and no `strips` are sent.
    - `test_an_expired_or_missing_image_is_noted_and_the_turn_goes_on` (Review Focus 5). A rendition the workbench misses gives the `NOT_LOADED` note and an answer. An expired attachment gives `; expired` with no fetch.
    - `test_a_refused_image_round_is_tried_once_without_images`. Round 1 raises `openai.BadRequestError("image refused", response=httpx2.Response(400, request=httpx2.Request("POST", "http://router.test/v1/chat/completions")), body=None)`.
      - One `NoticeEvent`.
      - The second call's messages hold `LEFT_OUT` and no `image_url`.
      - `audit.log[:2] == ["model:1:error", "model:1:ok"]` and `audit.images[:2] == [1, 0]`.
    - `test_a_second_refusal_is_the_model_error`; `test_a_refusal_without_images_is_not_retried` (one call, `ErrorEvent("model")`).
    - `test_the_prompt_tells_the_model_how_to_look_only_with_vision`: `VISION` is in the system prompt with vision and tools, and not otherwise.
    - `test_tool_images_past_the_cap_are_retired`: `max_images=2` and three rounds of one `view_file` image each. Round 4 sends 2 `image_url` parts and one `RETIRED` note.
  - `tests/test_api_messages.py`, an app with `vision=True`, an image attachment prepared through the route:
    - `test_a_refused_image_round_streams_a_notice`: the frames hold `notice {"code": "images_dropped"}` before the first `token`. The `chat.answer` events carry `images: 1`, then `images: 0`.
    - `test_no_data_url_reaches_the_log_the_store_or_the_audit`: a loguru sink at `DEBUG` added in the test, the audit stream, and the bytes of the SQLite file and its `-wal`. None holds `data:image` or `base64.b64encode(TINY_JPEG)`, while `fake_openai.calls[0]["messages"]` does.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_loop.py tests/test_api_messages.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `_answer` readable: a `_round` helper owns steps 4–5, and `_tool_images` owns step 7.
- [ ] **Step 4: Run** `uv run pytest -q`, then `make verify`. Expected: PASS, green.
- [ ] **Step 5: Commit** `feat(agent): attached and tool images reach the model, within the budget and the cap`

### Task 8: Pin `@infra/ui` v0.26.0

**Files:**
- Modify: `frontend/package.json` (`@infra/ui`), `frontend/pnpm-lock.yaml`

**Interfaces:**
- Consumes, from the infra-ui plan: `AttachmentTile`, `AttachmentTileProps`, `AttachmentKind`, `AttachmentTileStatus`, `ImageLightbox`, `ImageLightboxProps`, `FileIcon`.

- [ ] **Step 1: Resolve the pin.** Run `git ls-remote https://github.com/nos-tromo/infra-ui 'refs/tags/v0.26.0^{}'`. Expected: one line `<40-hex>	refs/tags/v0.26.0^{}`. **If there is no line, stop and report to the lead: the infra-ui release has not merged, and Tasks 9–11 wait for it.**
- [ ] **Step 2: Bump.** Set `"@infra/ui": "https://codeload.github.com/nos-tromo/infra-ui/tar.gz/<that SHA>"`, then `cd frontend && pnpm install`.
- [ ] **Step 3: Check.** `cd frontend && pnpm lint && pnpm build && pnpm test`, then `grep -c 'AttachmentTile\|ImageLightbox' node_modules/@infra/ui/dist/index.d.ts`. Expected: green, and a count ≥ 2.
- [ ] **Step 4: Commit** `build(frontend): pin @infra/ui v0.26.0 for the attachment tile and the lightbox`

### Task 9: The SPA's types, routes, previews and the notice frame

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/chats.ts`, `frontend/src/hooks/chatStreams.tsx`, `frontend/src/components/ResultCard.tsx` (imports the moved helpers), `frontend/src/lib/format.ts`, `frontend/src/test/fixtures.tsx`, `frontend/src/components/Conversation.tsx` (`REFUSAL_KEYS`, the notice line), `frontend/src/i18n/en.ts`, `de.ts`
- Create: `frontend/src/hooks/useObjectUrl.ts` (moved from `ResultCard.tsx`, with `saveBlob`), `frontend/src/hooks/usePreview.ts`, `frontend/src/lib/format.test.ts`
- Test: `frontend/src/hooks/useChatStream.test.tsx`, `frontend/src/components/Conversation.test.tsx`

**Interfaces:**
- `types.ts`:
  - `AttachmentOut` gains `kind: 'document' | 'image' | 'media'` and `status: 'processing' | 'ready' | 'failed'`.
  - `AppConfig` gains `image_types: string[]`, `max_images: number`, `vision: boolean`.
  - `export type NoticeCode = 'images_dropped'`; `export interface NoticeFrame { code: NoticeCode }`.
- `chats.ts`:
  - `getAttachment(chatId, attachmentId, waitS: number, signal?) => Promise<AttachmentOut>` (`?wait=`).
  - `attachmentPreview(chatId, attachmentId, page = 1, signal?) => Promise<Blob>` (`…/preview?page=`; errors as `fileBlob`'s).
- `useObjectUrl(blob: Blob | undefined): string | null` and `saveBlob(blob, name)`, unchanged, now exported from `hooks/useObjectUrl.ts`.
- `usePreview(chatId: string, attachment: AttachmentOut, enabled: boolean): { url: string | null; gone: boolean }`: react-query key `['preview', chatId, attachment.id]`, `staleTime: Infinity`, `gcTime: 60_000`, `retry: false`; enabled only for `kind image`, `status ready` and not expired.
- `format.ts`: `screenshotName(at: Date, taken: ReadonlySet<string>): string`. Local time, `screenshot-YYYYMMDD-HHMMSS.png`; when taken, `-2`, `-3`, … before `.png`.
- `chatStreams.tsx`:
  - `DraftAnswer.notices: NoticeCode[]`: `applyFrame` adds a known code once.
  - `StreamState.notices: NoticeCode[]` (`IDLE` has `[]`): an ended stream with notices is kept in `ended`, as a failure is, until the next one.
- `Conversation.tsx`:
  - `REFUSAL_KEYS.attachment_processing = 'chat.attachment_processing'`;
  - a `NoticeLine` (`role="status"`, `text-sm text-muted-foreground`, `chat.images_dropped`) under the draft, or after the messages from `stream.notices`.
- i18n (en / de), exact:
  - `chat.images_dropped`: `The model did not take the pictures; it answered without them.` / `Das Modell hat die Bilder nicht angenommen und ohne sie geantwortet.`
  - `chat.attachment_processing`: `Wait until the attached files are ready.` / `Warten Sie, bis die angehängten Dateien bereit sind.`
- `fixtures.tsx`: `CONFIG` gains `image_types: [], max_images: 8, vision: false`. `VISION_CONFIG` adds `.png`, `.jpg` and `.heic` to `attachment_types` and to `image_types`, with `vision: true`. `attachment(...)` helpers gain `kind: 'document', status: 'ready'`.

- [ ] **Step 1: Write the failing tests.**
  - `lib/format.test.ts`: `screenshotName(new Date(2026, 10, 20, 9, 5, 7), new Set())` is `screenshot-20261120-090507.png`; with that name taken, `…-090507-2.png`; with both taken, `-3`.
  - `useChatStream.test.tsx`: `it('keeps an images_dropped notice with its answer until the next one starts')`. A `notice` frame gives `draft.notices == ['images_dropped']` once (sent twice). After `final`, `notices` stays; a new `send` clears it. An unknown code is ignored.
  - `Conversation.test.tsx`: `it('says when the model answered without the pictures')` and `it('names a refusal while files are prepared')` (a 409 `attachment_processing`).
- [ ] **Step 2: Run** `cd frontend && pnpm test src/lib src/hooks/useChatStream.test.tsx src/components/Conversation.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** `pnpm typecheck` names every fixture still missing the new fields; fill them.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test`. Expected: PASS; `i18n.test.ts` parity green.
- [ ] **Step 5: Commit** `feat(frontend): attachment kinds and statuses, previews, and the images_dropped notice`

### Task 10: The composer: tiles, thumbnails, processing, screenshots

**Files:**
- Modify: `frontend/src/hooks/useAttachments.ts`, `frontend/src/components/Composer.tsx`, `frontend/src/i18n/en.ts`, `de.ts`
- Rename and rewrite: `frontend/src/components/AttachmentChips.tsx` → `frontend/src/components/AttachmentTiles.tsx` (`git mv`)
- Test: `frontend/src/components/Composer.test.tsx`

**Interfaces:**
- `useAttachments`:
  - `PendingFile` gains `kind: 'document' | 'image'` (by `config.image_types`) and `localUrl?: string`. That is an object URL made on `add` for `.png .jpg .jpeg .gif .webp .bmp` only, revoked on `dismiss` and on unmount.
  - `status` gains `'processing'`: after an upload answering `processing`, and for a waiting attachment the chat lists as `processing`.
  - A followed attachment polls `getAttachment(chat, id, 20)` until `ready` or `failed` (then `done` with the new attachment). A network error waits 2 s and tries again; a 404 settles `error: 'upload'`; unmount aborts. Each id is followed once.
- `AttachmentTiles({ chatId, pending, maxImages, onDismiss })`:
  - a `flex flex-wrap gap-2` row of `AttachmentTile size="sm"` above `ChatComposer`'s pill, never inside it;
  - status by `tileStatus(file)`: uploading → `uploading`; processing → `processing`; `error`, or a `failed` attachment → `failed`; expired → `expired`; else `ready`;
  - `thumbnailUrl` is `localUrl`, else `usePreview(...).url` (HEIC, TIFF, a reload);
  - `detail` is `formatSize`; `onRemove` only for `error`;
  - `onOpen` for an image with a picture, opening one `ImageLightbox` (`closeLabel` `lightbox.close`);
  - the error lines stay as today's `role="alert"` paragraphs.
- `Composer`:
  - `busy = pending.some(f => f.status === 'uploading' || f.status === 'processing')` disables Send, and `composer.processing_hint` shows below the tiles while any file is processing.
  - More waiting images than `config.max_images` shows `composer.images_cap`.
  - Paste: with text that is not a pasted file's name, images are never attached (only other attachable files are). A lone PNG with no text is attached as `screenshotName(new Date(), <names pending>)`. Everything else as today.
- i18n (en / de), exact:
  - `composer.processing`: `Preparing {name}…` / `{name} wird vorbereitet…`
  - `composer.processing_hint`: `Sending waits until the attached files are ready.` / `Das Senden wartet, bis die angehängten Dateien bereit sind.`
  - `composer.unreadable`: `{name} could not be read; the model will be told.` / `{name} konnte nicht gelesen werden; das Modell erfährt es.`
  - `composer.images_cap`: `At most {max} images go to the model with one message; the others are only named.` / `Mit einer Nachricht gehen höchstens {max} Bilder an das Modell; die übrigen werden nur genannt.`
  - `lightbox.close`: `Close` / `Schließen`

- [ ] **Step 1: Write the failing tests** (`Composer.test.tsx`, `VISION_CONFIG`, `URL.createObjectURL` stubbed to `blob:local-<n>` as `ResultCard.test.tsx` does):
  - `it('shows a dropped picture at once, prepares it, and sends once it is ready')`:
    - drop `harbour-photo.png`; its tile's `img` has `src` `blob:local-1` at once;
    - the upload answers `processing`; `GET …/attachments/a1?wait=20` answers `processing`, then `ready`;
    - Send is disabled with the hint until `ready`, then sends `attachment_ids: ['a1']`.
  - `it('picks pictures through the paperclip')`: the picker's `accept` includes `.png`; a change event uploads.
  - `it('fetches the thumbnail of a HEIC photo once it is ready')`: no local URL. After `ready`, `GET …/attachments/a1/preview?page=1` carries the purpose header, and the `img` shows its object URL.
  - `it('marks a picture that could not be read, and still sends it')`: a `failed` tile, `composer.unreadable`, Send enabled.
  - `it('warns when more pictures wait than go to the model')`: `max_images: 2` and three pictures.
  - `it('opens a thumbnail in the lightbox and closes it with Escape')`.
  - `it('follows a waiting attachment the chat lists as processing')`: the poll starts with no upload.
  - `it('attaches a lone pasted screenshot under a dated name, and two in one second apart')` (Review Focus 3): `vi.setSystemTime(new Date(2026, 10, 20, 9, 5, 7))`; two pastes upload `screenshot-20261120-090507.png` and `screenshot-20261120-090507-2.png`.
  - `it('keeps text that comes with a picture as text, also with pictures on')`: the existing rich paste under `VISION_CONFIG` is not prevented and uploads nothing.
  - The existing paste, drop and refusal tests pass unchanged under `CONFIG`.
- [ ] **Step 2: Run** `cd frontend && pnpm test src/components/Composer.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm build && pnpm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(frontend): picture tiles in the composer, prepared before they are sent`

### Task 11: The bubble's pictures and the trail

**Files:**
- Create: `frontend/src/components/AttachmentImage.tsx`
- Modify: `frontend/src/components/MessageView.tsx` (`UserMessage`, `PendingUser` gains `chatId`), `frontend/src/components/Conversation.tsx` (passes `chatId` to `PendingUser`), `frontend/src/components/Trail.tsx` (`TOOL_LABELS`), `frontend/src/i18n/en.ts`, `de.ts`
- Test: `frontend/src/components/Conversation.test.tsx`, `frontend/src/components/Trail.test.tsx`

**Interfaces:**
- `AttachmentImage({ chatId, attachment })`:
  - an `AttachmentTile size="md" kind="image"`; `status` `expired` when expired, `failed` when failed, else `ready`;
  - `thumbnailUrl` from `usePreview`, fetched with the purpose as `apiFetch` sends it, revoked by `useObjectUrl`;
  - `onOpen` opens `ImageLightbox` with `onDownload` saving the original (`fileBlob(chatId, attachment.file_id)` and `saveBlob`) and `downloadLabel` `result.download`.
- `UserMessage({ chatId, message })`: images in a `flex flex-wrap gap-2` grid of `AttachmentImage`; other attachments keep today's chip.
- `TOOL_LABELS` gains `['view_file', { named: 'trail.view_file', unnamed: 'trail.view_file_unnamed' }]`. i18n: `trail.view_file` `Looked at {detail}` / `{detail} angesehen`; `trail.view_file_unnamed` `Looked at a file` / `Eine Datei angesehen`.

- [ ] **Step 1: Write the failing tests.**
  - `Conversation.test.tsx`:
    - `it('shows a message's pictures as a grid, fetched with the purpose, and opens one')`: `GET …/attachments/a1/preview?page=1` with `X-Audit-Purpose` when one is stated. The tile's `img` has the object URL; a click opens the dialog named `harbour-photo.jpg`; Escape closes it; unmount revokes the URL.
    - `it('marks an expired picture without fetching it')`.
    - `it('shows the picture of a message still being sent')` (`PendingUser`).
  - `Trail.test.tsx`: `it('names view_file steps')`: `Looked at quay-scan.pdf`, and `Looked at a file` once expired.
- [ ] **Step 2: Run** `cd frontend && pnpm test src/components/Conversation.test.tsx src/components/Trail.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm build && pnpm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(frontend): pictures in the user's bubble with a lightbox, and view_file in the trail`

### Task 12: Docs and release

**Files:**
- Modify: `docs/data-flow.md`, `docs/retention.md`, `docs/configuration.md` (caps), `docs/audit.md` (check Task 5's rows), `CLAUDE.md`, `README.md`, `pyproject.toml` (`version = "0.3.0"`), `uv.lock`, `tests/test_version.py` (`"0.3.0"`), `tests/test_api_meta.py` (`"version": "0.3.0"`)

- [ ] **Step 1: Docs.**
  - `docs/data-flow.md`:
    - the `attachments` row gains `kind`, `status`, `render` (page count, pages without text, render sizes; emptied at the file period) and `failure` (a token);
    - "What is never stored" gains image bytes and data URLs: held in memory for one message;
    - "Where data goes" gains:
      - the router: attached images and pages or pictures a tool showed, as `image_url` data URLs of workbench's renders, metadata removed by workbench, held for one message;
      - workbench `POST /files/prepare` and `GET /files/view?rendition=`;
      - Ollama `/api/show` on the router's host (the model name only).
  - `docs/retention.md`: nothing new is kept but the manifest, which goes at the file period. Renders are workbench's derived files, deleted with their source there.
  - `docs/configuration.md` caps table: `MAX_RENDITION_BYTES` 768 KiB, `MAX_IMAGE_BYTES` / `MAX_IMAGES_BYTES` / `MAX_TOOL_IMAGES` 1 MiB / 3 MiB / 8, `PREPARE_TIMEOUT_S` 270, `RENDITION_TIMEOUT_S` 30, `PREPARE_SLOTS` 2, the long-poll's 20 s.
  - `CLAUDE.md`:
    - the module map gains `ghostwriter/vision.py`, `ghostwriter/processing.py` and `ghostwriter/agent/images.py`, and `frontend/src/components/AttachmentTiles.tsx` / `AttachmentImage.tsx`;
    - "A message, end to end" step 4 names image parts, the bank and `materialise`, and step 5 the `notice` frame;
    - "What changes together" gains: an image cap → `vision.py` / `images.py` and vllm-service's `CHAT_LIMIT_MM_PER_PROMPT`; the schema is version 2;
    - the invariants are unchanged: still no file bytes kept.
  - `README.md`: one line that pictures can be attached.
- [ ] **Step 2: Version.** Bump to `0.3.0`, `uv lock`, then `uv run pytest tests/test_version.py tests/test_api_meta.py -q`. Expected: PASS.
- [ ] **Step 3: Gates.** `make verify && make test`. Expected: green; paste the summary lines into the PR.
- [ ] **Step 4: Commit** `docs: pictures, prepared attachments and the image budget` and `chore(release): 0.3.0`.
- [ ] **Step 5: Push and PR.** `git push -u origin feat/vision-stage1`; a PR to `main` with an **Operators** line: "0.3.0 sends pictures to the chat model. It needs workbench 0.7.0 with `view_file` listed; without it, nothing changes. Keep vllm-service's `CHAT_LIMIT_MM_PER_PROMPT` ≥ `GHOSTWRITER_MAX_IMAGES` (8). For gemma set `GHOSTWRITER_IMAGE_TOKENS=280`." Confirm CI ran on the last commit. Merging is the owner's call.

---

## Self-review

- **Spec coverage (§7.3, §7.4, stage 1):**
  - kinds, statuses, manifest, migration: Task 2;
  - the processor, the hand-over records, long-poll, preview, 409: Task 5;
  - the image line facts, image parts, the estimate, retirement, replay, the cap: Task 6;
  - tool images in a synthetic user message, hidden strips, the vLLM-style retry with `notice`, `images` in `ai_call`, the prompt: Task 7;
  - the capability (setting, Ollama, `view_file`): Task 4;
  - the UI (tiles, thumbnails, lightbox, paste, processing, cap warning, bubble, trail, notice): Tasks 9–11.
- **Left for later stages, with their extension points:** Office via `PREPARED_SUFFIXES` and `attachment_facts` (slides); media via `AttachmentKind "media"`, `PrepareJob.kind` (a Nextext step before prepare) and the tile's `video`/`audio` kinds.
- **Decisions this plan makes where the spec is silent:**
  - `/api/config` and the upload learn `view_file` from the cached discovery with a system `CallFor`;
  - tests default `vision=False`;
  - a `failed` image is sent and named as unreadable;
  - a label text part precedes each image;
  - the manifest is emptied at the file period;
  - the retry applies to any provider (Ollama is caught by the probe first).
- **Proportion:** code appears only where tests do not pin the order: `probe`, `fit`'s retirement order, the migration SQL, and the copy the spec fixes. The rest is signatures, values and test assertions.
