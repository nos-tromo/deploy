# Ghostwriter vision, stage 1: workbench v0.7.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** workbench renders images and PDF pages in its executor, keeps the renders as derived files of their source, and shows them to Ghostwriter: a prepare route with a manifest, rendition views, the MCP tool `view_file`, and `read_document` that returns images of pages without text.

**Architecture:** A new `derived` table and a reserved `.derived/<source-id>/` folder in each chat folder hold the renders, bound to their source's creation time. A new executor runner, `prepare_file`, decodes with Pillow (HEIC through pi-heif) and pypdfium2 and writes the renders to `out/`; the queue registers them as derived rows (never `files` rows) after a `modify` / `file.prepare` event. The api reads renders from disk for image blocks and serves them; it never decodes.

**Tech Stack:** Python 3.12, FastAPI, pydantic, SQLite; executor: Pillow, pi-heif (decode only), pypdfium2; tests: pytest, reportlab, pillow-heif (dev group only).

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§2 anti-goals, §6 spikes, §7.2 workbench) and `deploy/docs/decisions/0009-ghostwriter-sees-images-and-media.md`.

Every path below is relative to the `workbench` repo root unless it starts with `deploy/`.

## Global Constraints

- Branch `feat/vision-stage1`; GitHub Flow, one PR. `pyproject.toml` version `0.7.0` and `uv lock` in this PR; the tag is minted on merge, never by hand. Workflow `uses:` stay SHA-pinned (no workflow change is planned).
- Python `>=3.12,<3.13`; ruff with Google docstrings (`D`) and annotations (`ANN`); pyrefly strict; 120-character lines. `make verify` and `make test` green before every push (`git add` new files first: pre-commit sees tracked files only). `make test-executor-root` and `make test-isolation` green before the PR is opened.
- The executor imports nothing of `workbench.api`. Rules both sides use live in `workbench/renders.py`, which imports no imaging library.
- The api never decodes a file: it imports neither `PIL`, `pypdfium2` nor `pi_heif` (its image has none of them). It checks a render's signature and size only.
- Fail closed: a render becomes visible only after its `modify` / `file.prepare` event; an image leaves the api only after its `disclose` / `file.view`, `query` / `tool.view_file` or `query` / `tool.read_document` event names the source and each render.
- Logs and events: ids, counts, codes and exception types only; never a file name, a path, a page's text or an image byte. Errors are short fixed texts.
- Airgap: every library comes from `uv.lock` into the executor image; nothing is fetched at runtime.
- Test files are built at test time with invented content (`tests/fixtures/`); no binary fixture is committed; GPS values are invented. No absolute local path in any committed file.
- Goldens in `tests/api/golden/` are edited by hand, never regenerated.
- Spec values: images at most 2048 px on the long side and 2.4 MP; JPEG q90 4:4:4, or PNG for PNG/GIF/BMP sources when it fits; every render under 768 KiB; sources over 120 MP refused; PDF pages at 1600 px long side, JPEG q85, the first 20 at prepare, later ones on demand; strips 1–3 along the long side with 6 % overlap; `view_file` at most 8 images and 2.5 MiB raw; `read_document` at most 4 images; derived files at most 64 MiB per source, counted in the user's quota; one prepare job at a time per caller, within its slots.
- Roles: `image` and `page` now; `pdf`, `frame`, `transcript`, `nextext_jsonl`, `nextext_zip` reserved in the table's `CHECK` for stages 2 and 3.
- Commit messages: conventional (`feat(store): …`), no real data.

## Review Focus

1. **A phone photo in an unusual colour mode** (CMYK JPEG from a print export, 16-bit grey PNG from a scanner, palette PNG with a transparent colour). Expect the colours of the source, not an inverted, white or black picture. Test in Task 4.
2. **An extension that lies**: a PNG saved as `.jpg`, a text file named `.png`. Expect the first decoded by its content and the second `422 unreadable_file` with nothing stored. Tests in Tasks 4 and 8.
3. **A password-protected or damaged PDF.** Expect prepare `422 unreadable_file`, no derived row, and `read_document` answering exactly as before. Tests in Tasks 5 and 10.
4. **Two prepares of one file at once** (Ghostwriter retries, or two tabs). Expect one job, one set of renders, no duplicate row and no `error` event. Test in Task 7.
5. **A long PDF past its 64 MiB of renders.** Expect a page past the cap to answer `507` on the route and a hint in `view_file`, never a 500, and the pages already rendered still served. Tests in Tasks 7 and 9.

## Contract for Ghostwriter (what the Ghostwriter plan consumes)

Headers on every route: `Authorization: Bearer <GHOSTWRITER_WORKBENCH_KEY>`, `X-User-Id`, `X-Session-Id`, `X-Audit-Purpose`, `X-Request-ID`.

**Amended after the final review (rulings R15, R16):** the MCP door lists `view_file` and attaches images (`read_document` pages and pictures) only to a call that carries `X-Accept-Images: 1`; without it Ghostwriter's key is answered as in v0.6.0 (no `view_file`, no images, the v0.6.0 `read_document` text), so an older Ghostwriter next to v0.7.0 is never told it sees images it drops. `POST /files/prepare` also takes `strips` (1–3, default 1): with `k > 1` on a picture it makes the picture's `k` strips on the prepare gate as well.

**`POST /files/prepare?path=<virtual path>[&strips=<k>]`**, no body. Idempotent: when the renders exist it answers from the rows, with no job and no event. `200`:

```json
{"path": "/workspace/chat-1/scan.pdf", "kind": "pdf", "total": 24, "pages_without_text": [3, 7],
 "renditions": [{"n": 1, "width": 1131, "height": 1600, "mime": "image/jpeg", "bytes": 182311}],
 "frames": null, "hint": null}
```

All seven keys are always present. `kind` is `image`, `pdf` or `none` (any other file, answered at once, `total: 0`, `renditions: []`). `renditions` lists the whole renders that exist now (pages 1–20 after prepare, more as later pages are viewed), `n` 1-based; an image has one, `n: 1`. `frames` is the frame count of a multi-frame image (only the first is shown), else `null`. Statuses: `400` bad path or session; `404 not found` (no such file, another chat, a `.derived` path); `422` `unreadable_file: …`, `unsupported_format: …`, `read_timeout: …`, `the file could not be processed: …`; `428` no purpose; `503 busy, retry later` (prepare waits up to 120 s for its turn); `507` quota, storage full, or `the renders of this file are at their limit`. The text before the first `:` of a `422` detail (`unreadable_file`, `unsupported_format`, `read_timeout`) is a stable token callers may branch on; a test in Task 8 pins the three. A client timeout must cover 120 s of queue, the job wall (`GHOSTWRITER_WORKBENCH_WALL_S`, 120) and 30 s.

**`GET /files/view?path=<p>&rendition=<n>[&strips=<k>&strip=<i>]`**. Without `rendition` the route is unchanged. With it: `200` with `image/jpeg` or `image/png`, `Content-Length`, `Content-Disposition: inline; filename*=…` and the view hardening headers. `k` is 1–3 (default 1); `i` is 1–`k` and required when `k > 1`. A page not yet rendered, or strips not yet cut, are made by a job on the user's own turn (5 s wait, then `503`). `404` for `n` outside `1..total` or a file of kind `none`; `422` for a bad `strips`/`strip` pair; otherwise as prepare.

**MCP `view_file`** (workspace door): input `{"path": str (1–1024), "part": str | null, "strips": int 1–3 | null}`. `structuredContent`: `{"path", "kind", "total", "shown": [{"label", "n", "width", "height", "mime"}], "truncated", "next_part", "hint", "outputs": []}`. `content`: `[text, *image blocks]`, each block `{"type": "image", "data": <base64>, "mimeType": "image/jpeg"|"image/png", "annotations": {"audience": ["assistant"]}}`; at most 8 blocks and 2.5 MiB raw. **Image block `i` (in `content[1:]`) is `shown[i]`, in order, always**; a test in Task 9 pins it. Errors as every tool's: `isError` with reason `refused` (`not found`, `no_such_part: …`, `unreadable_file: …`), `wait_timeout` / `queue_full` / `executor_busy`, `purpose_required`, `invalid_arguments`.

**MCP `read_document`**: input gains `strips` (int 1–3 or null; Ghostwriter sets it and hides it from the model). Output gains `shown` (same items as above). On a PDF it attaches the renders of `pages_without_text` inside its window as image blocks (at most 4, 2.5 MiB); on a picture file it answers `kind: "image"`, `total_parts: 1`, `pages_without_text: [1]` and the image.

---

### Task 1: Shared render rules

**Files:**
- Create: `workbench/renders.py`, `tests/test_renders.py`

**Interfaces:**
- Produces (all `Final`):
  - `IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"})`, `PDF_SUFFIX = ".pdf"`.
  - `Kind = Literal["image", "pdf", "none"]`; `kind_of(name: str) -> Kind` by lower-cased suffix. Stage 2 adds `office` here.
  - `SOURCE_MAX_PIXELS = 120_000_000`, `IMAGE_LONG_SIDE = 2048`, `IMAGE_MAX_PIXELS = 2_400_000`, `PAGE_LONG_SIDE = 1600`, `IMAGE_QUALITY = 90`, `PAGE_QUALITY = 85`, `QUALITY_FLOOR = 50`, `RENDER_MAX_BYTES = 768 * 1024` (every render is strictly smaller), `FIRST_PAGES = 20`, `PAGES_CHECKED_MAX = 1000`, `RENDERS_PER_JOB = 20` (the supervisor's `MAX_OUTPUTS`), `MAX_STRIPS = 3`, `STRIP_OVERLAP = 0.06`, `DERIVED_MAX_BYTES = 64 * 1024 * 1024`.
  - `SIGNATURES: Mapping[str, bytes] = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}`; `signature_ok(data: bytes, mime: str) -> bool`.
  - `target_size(width: int, height: int, *, long_side: int, max_pixels: int | None = None) -> tuple[int, int]`: scale `s = min(1, long_side / max(w, h), sqrt(max_pixels / (w * h)))`, each side `max(1, int(side * s + 1e-6))` (the epsilon keeps 1754 × 1600/1754 at 1600). Never enlarges.
  - `strip_spans(length: int, strips: int) -> list[tuple[int, int]]` (algorithm below).
  - `render_name(n: int, strip: int, strips: int, mime: str) -> str`: `p0003.jpg`, `p0003-s1of2.png`.
  - `class RenderEntry(BaseModel)` (`extra="forbid"`): `name: str`, `role: Literal["image", "page"]`, `n: int` (≥ 1), `strip: int = 1`, `strips: int = 1` (each 1–3, `strip <= strips`), `width: int`, `height: int` (each 1–`IMAGE_LONG_SIDE`), `mime: Literal["image/jpeg", "image/png"]`.
  - `class RenderFacts(BaseModel)` (`extra="forbid"`): `kind: Literal["image", "pdf"]`, `total: int` (≥ 1), `pages_without_text: list[int]` (at most `PAGES_CHECKED_MAX`), `frames: int | None` (≥ 2 when set), `hint: str | None` (≤ 300).
  - `class RenderAnswer(RenderFacts)`: `renders: list[RenderEntry]` (at most `RENDERS_PER_JOB`). This is the runner's whole answer.

`strip_spans`, integer arithmetic only:

```python
if strips == 1:
    return [(0, length)]
overlap = round(STRIP_OVERLAP * length)
size = -(-(length + (strips - 1) * overlap) // strips)          # ceil
starts = [i * (length - size) // (strips - 1) for i in range(strips)]
return [(start, start + size) for start in starts]
```

- [ ] **Step 1: Write the failing tests** (`tests/test_renders.py`):
  - `strip_spans(1600, 2) == [(0, 848), (752, 1600)]`; `strip_spans(1600, 3) == [(0, 598), (501, 1099), (1002, 1600)]`; `strip_spans(1600, 1) == [(0, 1600)]`; for `length` in `(10, 999, 1601, 2048)` and `k` in `(2, 3)`: first start 0, last end `length`, each neighbour overlap ≥ `round(0.06 * length)`.
  - `target_size(4032, 3024, long_side=2048, max_pixels=2_400_000)`: long side ≤ 2048, product ≤ 2.4 M, ratio within 1 %. `target_size(100, 50, long_side=2048, max_pixels=2_400_000) == (100, 50)`. `target_size(10000, 100, long_side=2048) == (2048, 20)`. `target_size(1240, 1754, long_side=1600) == (1131, 1600)`.
  - `kind_of("Scan.PDF") == "pdf"`, `kind_of("photo.HEIC") == "image"`, `kind_of("deck.pptx") == "none"`, `kind_of("noext") == "none"`.
  - `render_name(3, 1, 2, "image/png") == "p0003-s1of2.png"`, `render_name(1, 1, 1, "image/jpeg") == "p0001.jpg"`.
  - `RenderEntry` refuses `strip=3, strips=2`, `width=0`, `width=2049`, `mime="image/gif"` and an extra key; `RenderFacts` refuses `frames=1`.
  - `signature_ok(b"\xff\xd8\xff\xe0…", "image/jpeg")`; a PNG signature under `image/jpeg` is refused.
- [ ] **Step 2: Run** `uv run pytest tests/test_renders.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `workbench/renders.py` as in Interfaces; model it on `workbench/decks.py` (module docstring says both the api and the executor read it).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(renders): the bounds of a render, shared by the api and the executor`

### Task 2: Derived files in the store

**Files:**
- Modify: `workbench/layout.py` (`DERIVED: Final = ".derived"`), `workbench/models.py`, `workbench/api/store.py` (`_EXTRA_TYPES` :89, `_SCHEMA` :127, `_check_new_name` :327, `_locate` :462, `list_dir` :900, `usage` :1042, `remove` :1081, `delete_chat` :1117, `sweep_user` :1188, `summarize_due` :1410)
- Test: `tests/api/test_store.py`

**Interfaces:**
- Consumes: `renders.DERIVED_MAX_BYTES` (Task 1).
- Produces:
  - `models.DerivedRole = Literal["image", "page", "pdf", "frame", "transcript", "nextext_jsonl", "nextext_zip"]`.
  - `@dataclass(frozen=True) class DerivedSpec: role: DerivedRole; ordinal: int; mime: str; width: int | None; height: int | None; meta: Mapping[str, object]`.
  - `@dataclass(frozen=True) class DerivedRecord: id; source_id; caller; user_id; chat_id; role: DerivedRole; ordinal: int; name: str; size: int; mime: str; width: int | None; height: int | None; meta: Mapping[str, object]; created_at: float`, with properties `relative_path -> f"{DERIVED}/{source_id}/{name}"`, `strip -> int` and `strips -> int` (from `meta`, default 1).
  - Table (in `_SCHEMA`, `CREATE TABLE IF NOT EXISTS`, so an existing database gains it):
    ```sql
    CREATE TABLE IF NOT EXISTS derived (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL, caller TEXT NOT NULL, user_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('image','page','pdf','frame','transcript','nextext_jsonl','nextext_zip')),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0), name TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL,
        width INTEGER, height INTEGER, meta TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS derived_source ON derived (source_id);
    CREATE INDEX IF NOT EXISTS derived_created ON derived (created_at);
    CREATE INDEX IF NOT EXISTS derived_owner ON derived (caller, user_id, chat_id);
    ```
  - `class RendersFull(StoreError)`, message `the renders of this file are at their limit`.
  - `Store.register_derived(self, source: FileRecord, staged: Path, spec: DerivedSpec, *, file_id: str, quota_bytes: int, cap_bytes: int = DERIVED_MAX_BYTES) -> DerivedRecord`. Under the store's lock: the source row must still exist for its owner and chat (else `NotFound`, `staged` stays); the chat must not be held (`_refuse_deleted`); the user's usage plus the size must fit `quota_bytes` (`QuotaExceeded`); the source's derived bytes plus the size must fit `cap_bytes` (`RendersFull`); no row of the source may have the same role, ordinal, `strip` and `strips` (`Conflict`). The file goes to `<chat>/.derived/<source-id>/<file_id><ext>` (`.jpg` / `.png` by mime; another mime is `PathError`), folders 0700, file 0600, renamed then inserted as `_place` does. `name` is `<file_id><ext>`, `created_at` is `source.created_at`. `staged` checks are those of `register_output` (regular file, no link, id of 32 hex digits).
  - `Store.derived_of(self, source: FileRecord) -> list[DerivedRecord]`, the source's rows for its own owner and chat, by role, ordinal, `strips`, `strip`.
  - `Store.derived_for(self, sources: Sequence[FileRecord]) -> list[DerivedRecord]`, every row of those sources.
  - `Store.open_derived(self, rec: DerivedRecord) -> BinaryIO`, as `open_file` opens (no link, regular file, else `NotFound`).
  - `Store.remove(self, record: FileRecord, derived: Sequence[DerivedRecord] = ()) -> None`: under the lock, unlink each named derived file, then the source file, then delete the named derived rows and the source row in one transaction; empty `.derived/<source-id>/` and `.derived/` go. Derived rows not named stay (retention's stray pass takes them, recorded).
  - `Store.remove_derived(self, rec: DerivedRecord) -> None`, one render's file and row.
  - `Store.due_derived(self, cutoff: float, limit: int = 200, *, after: tuple[float, str] | None = None) -> list[DerivedRecord]`: rows with `created_at < cutoff`, or whose source row is gone, in `(created_at, id)` order.
  - `delete_chat` calls `record(ids, …)` with the file ids, then the chat's derived ids, and deletes the derived rows in the same transaction as the file and job rows. It still returns the number of files.
  - `usage` adds the user's derived sizes.
  - `summarize_due` returns a `derived` entry too.
  - Reserved name: `_check_new_name` refuses `DERIVED` (`PathError("invalid file name")`), so no upload or output takes it. `_locate` raises `NotFound("not found")` for any path whose segments below the chat id include `DERIVED`, for every principal: `get`, `open_file`, `list_dir`, `add_upload`'s folder and `mkdir` never reach it. `list_dir` of a chat folder leaves `.derived` out.
  - `sweep_user`: the known names include each derived row's `relative_path`; an orphan's id is taken from `<id>.part` or from a `.derived/<source-id>/<id>.<ext>` name.
  - `_EXTRA_TYPES` gains `.webp` `image/webp`, `.bmp` `image/bmp`, `.tif`/`.tiff` `image/tiff`, `.heic` `image/heic`, `.heif` `image/heif`.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_store.py`; `spec()` builds a `DerivedSpec("page", n, "image/jpeg", 10, 20, {})`, `staged()` writes bytes into `staging/<job>/`):
  - `test_derived_files_live_under_their_source_and_carry_its_time`: the file is at `.derived/<source id>/<file_id>.jpg` below the chat folder, mode 0600; `rec.created_at == source.created_at` with the clock moved on; `derived_of(source) == [rec]`.
  - `test_a_derived_file_needs_its_source`: after `store.remove(source)`, `register_derived` raises `NotFound` and `staged` is still there.
  - `test_a_deleted_chat_takes_no_derived_file`: after `delete_chat`, `NotFound`.
  - `test_a_source_holds_at_most_its_cap_of_renders`: `cap_bytes=10`, an 8-byte render then a 4-byte one raises `RendersFull`.
  - `test_renders_count_against_the_quota`: `usage` grows by the render's size; a render past `quota_bytes` raises `QuotaExceeded`.
  - `test_the_same_render_is_refused_twice`: same role, ordinal and `{"strip": 1, "strips": 2}` raises `Conflict`; `{"strip": 2, "strips": 2}` is kept.
  - `test_the_reserved_name_is_refused`: an upload named `.derived` and `register_output(..., ".derived", ...)` raise `PathError`; `get`, `open_file`, `list_dir` and `add_upload` on `/workspace/chat-1/.derived/<id>/<name>` or `/workspace/chat-1/.derived` raise `NotFound`, for a confined and an unconfined principal.
  - `test_derived_files_are_hidden`: `list_dir("/workspace/chat-1")` has no `.derived` entry; `files_in_chat` has only the source.
  - `test_a_chat_delete_names_and_takes_its_derived_files`: `record` gets `[source id, derived id]` in that order; no derived row and no folder remain.
  - `test_remove_takes_only_the_named_derived_files`: two renders, one named; the other row stays and `due_derived(0)` lists it (source gone).
  - `test_the_orphan_sweep_keeps_derived_files_and_names_orphans_by_id`: a render with its row stays; a file `.derived/<sid>/<32 hex>.jpg` without a row is removed and `record` receives that id.
  - `test_summarize_due_counts_derived_files`: `summarize_due(db, cutoff)["derived"].due == 1`.
  - `test_image_types_are_known`: `_mime` of `a.heic`, `a.heif`, `a.webp`, `a.bmp`, `a.tif` as in Interfaces.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_store.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** as in Interfaces. `meta` is stored with `json.dumps(meta, sort_keys=True, separators=(",", ":"))`.
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS (`test_chat_delete.py` included; adjust its id-list assertions only where a test now has renders).
- [ ] **Step 5: Commit** `feat(store): derived files bound to their source, in a reserved folder`

### Task 3: Retention of derived files

**Files:**
- Modify: `workbench/api/retention.py` (`KINDS` :89, `sweep_once`, `_delete` :335; new `_sweep_derived`), `docs/retention.md`
- Test: `tests/api/test_retention.py`

**Interfaces:**
- Consumes: `Store.derived_for`, `Store.remove(record, derived)`, `Store.due_derived`, `Store.remove_derived`, `summarize_due`'s `derived` (Task 2).
- Produces:
  - `KINDS = ("upload", "output", "derived")`; the dry run prints a `derived:` line like the others.
  - `_delete(batch, …)`: reads `store.derived_for(batch)` first; its event names the batch's file ids, then their derived ids; criteria gain `"derived": <count>` (`count` stays the number of files). Each file goes with `store.remove(rec, derived=<its renders>)`. A file that could not go is recorded again as `error` with its renders' ids.
  - `_sweep_derived(self, cutoff: float, stop: threading.Event, report: SweepReport) -> None`, run after `_sweep_files`: pages `due_derived`, batches by caller and user, records each batch first as `delete` / `retention.sweep` with criteria `{"rule": "derived", "period_days", "cutoff", "caller", "owner", "count"}` and the derived ids, then `remove_derived` each; failures are recorded again as `error`. Renders count in `report.deleted` and `report.errors`.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_retention.py`, with the file's own sweeper and clock fixtures):
  - `test_a_due_file_takes_its_renders_in_the_same_record`: a due PDF with two renders gives one `retention.sweep` event whose target ids are `[pdf id, r1 id, r2 id]`, criteria `derived: 2`, `count: 1`; afterwards no row and no file of either.
  - `test_renders_of_a_kept_file_are_kept`.
  - `test_a_render_whose_source_is_gone_is_swept_and_recorded`: a stray row gives an event with `rule: derived` and its id, before the file goes.
  - `test_a_record_that_cannot_be_written_deletes_no_render`: with the audit write failing, `exit_process` is called and both the render and its source remain.
  - `test_the_dry_run_counts_renders`: the output has the line `  derived: 2 due now, aged 8.0 days; 0 kept`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_retention.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**; in `docs/retention.md` § The rules, add: renders go with their source, in its record; strays and due renders are swept with `rule: derived`; renders without a row are orphans named by the id their name carries; the dry run has a `derived` line.
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(retention): renders go with their source, and strays are swept, each recorded first`

### Task 4: Image libraries and image normalisation in the executor

**Files:**
- Modify: `pyproject.toml` (`executor` extra: `pillow`, `pi-heif>=1.4.0`, `pypdfium2`; `dev` group: `pillow-heif`), `uv.lock`
- Create: `workbench/executor/render/__init__.py`, `workbench/executor/render/images.py`, `workbench/executor/render/encode.py`, `tests/fixtures/make_images.py`, `tests/executor/test_render.py`
- Modify: `tests/executor/test_supervisor.py` (one root case)

**Interfaces:**
- Consumes: `renders.target_size`, `renders.strip_spans`, the constants (Task 1); `readers.UnreadableFile`, `readers.DAMAGED`, `readers.TOO_BIG`.
- Produces:
  - `@dataclass(frozen=True) class Normalised: image: Image.Image; lossless: bool; frames: int`.
  - `images.normalise(path: Path, *, long_side: int = IMAGE_LONG_SIDE, max_pixels: int = IMAGE_MAX_PIXELS) -> Normalised`. Raises `UnreadableFile` (not a picture of a known kind, damaged, over the source cap, out of memory). `lossless` is true when the decoded format is PNG, GIF or BMP.
  - `encode.encode(image: Image.Image, *, lossless: bool, quality: int, subsampling: int) -> tuple[bytes, str]` returns the bytes and their mime, always under `RENDER_MAX_BYTES`, with no metadata; raises `UnreadableFile` if nothing fits.
  - `encode.cut(image: Image.Image, strips: int) -> list[Image.Image]`: crops along the long side by `strip_spans` (horizontal bands for a portrait page, vertical for a landscape one).
  - `make_images.py`: `make_photo(path, *, size=(64, 48), orientation=6, gps=True) -> Path` (left half red, right half blue; EXIF orientation and a GPS IFD with invented coordinates), `make_alpha_png(path) -> Path` (a fully transparent pixel at (0, 0)), `make_noise(path, size, fmt) -> Path`, `make_gif(path, frames=3) -> Path` (frame 0 red, others green), `make_bomb_png(path, width=12000, height=12000) -> Path` (a valid IHDR and a tiny IDAT, written with `struct` and `zlib` like `make_fixtures._png`), `make_cmyk_jpeg(path) -> Path` (CMYK `(0, 255, 255, 0)`), `make_png16(path) -> Path` (mode `I;16`, value 32768), `make_heic(path) -> Path` (via `pillow_heif.from_pillow(...).save`, without registering an opener).

`normalise`, in this order (memory: nothing large is copied before the resize):
1. `pi_heif.register_heif_opener(decode_threads=1, thumbnails=False, depth_images=False, aux_images=False)` once per process; one decoder thread keeps a job within its `nproc` and CPU limits.
2. `Image.open(path, formats=["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF", "HEIF"])`, under `warnings.catch_warnings()` with `DecompressionBombWarning` as an error and `Image.MAX_IMAGE_PIXELS = SOURCE_MAX_PIXELS`; also refuse `width * height > SOURCE_MAX_PIXELS` before loading. Message: `the picture is larger than 120 megapixels`.
3. `frames = getattr(img, "n_frames", 1)`; `img.seek(0)`. Read the EXIF orientation (tag `0x0112`) now.
4. JPEG: `img.draft("RGB", target)` with `target = target_size(w, h, …)`. Then load.
5. Convert the mode: `I;16*`/`I` scaled to 8-bit grey; `P`/`PA`/`LA` with transparency to `RGBA`; CMYK and anything else to RGB through its ICC profile when it has one (`ImageCms.profileToProfile` to sRGB; a broken profile falls back to `convert("RGB")`), else `convert("RGB")`.
6. Resize to `target_size(…)` with `Image.Resampling.LANCZOS` and `reducing_gap=3.0`.
7. Apply the orientation from step 3 (`ImageOps.exif_transpose` semantics, on the small image).
8. Composite `RGBA` on white; the result is `RGB`.
Every library failure in 2–8 is `UnreadableFile(DAMAGED)`, `MemoryError` is `UnreadableFile(TOO_BIG)`.

`encode`: a fresh copy whose `info` is emptied is saved, with no `exif`, `icc_profile` or `xmp` argument. `lossless`: PNG (`optimize=False`) if under the cap. Otherwise JPEG with `subsampling` at `quality`, then 10 lower each step down to `QUALITY_FLOOR`; then the image is scaled by 0.75 and the ladder runs again, at most 3 times.

- [ ] **Step 1: Add the libraries.** `uv add --optional executor pillow "pi-heif>=1.4.0" pypdfium2` and `uv add --group dev pillow-heif`. pi-heif is the decode-only build (libheif and libde265, LGPL-3.0); pillow-heif also bundles the x265 encoder (GPL-2.0), so it stays in the dev group, which only the `test` image stage carries. Both have cp312 manylinux wheels for x86_64 and aarch64; pypdfium2 ships `py3-none` manylinux wheels for both.
- [ ] **Step 2: Smoke-test the libraries in a slot** before anything is built on them. Add `test_as_root_the_image_libraries_load_in_a_slot` to `tests/executor/test_supervisor.py` (`ROOT_ON_LINUX`, `_runner`, production limits `memory_mb=1536`): the job body imports `pi_heif`, `pypdfium2` and `PIL`, decodes a HEIC written by the test (skip that part without `pillow_heif`), opens a two-page PDF from `make_fixtures.make_pdf` with `pypdfium2.PdfDocument`, renders page 1 at scale 2 and counts its characters, and puts the sizes in `result`. Run `make test-executor-root`. Expected: PASS in both shapes. **If pdfium cannot start under `RLIMIT_AS` 1536 MB or the seccomp filter, stop and report to the lead with the error type: the plan needs another renderer.**
- [ ] **Step 3: Write the failing tests** (`tests/executor/test_render.py`):
  - `test_a_rotated_photo_comes_out_upright_without_its_metadata`: `make_photo` (64 × 48, orientation 6, GPS) gives a 48 × 64 image whose top-centre pixel is red and bottom-centre blue (each channel within 40); the encoded bytes contain neither `b"Exif"` nor `b"GPS"` nor `b"http://ns.adobe.com/xap"`; `Image.open(out).getexif()` is empty and `info` has no `exif`, `icc_profile` or `xmp`.
  - `test_alpha_lies_on_white`: pixel (0, 0) is within 2 of `(255, 255, 255)`.
  - `test_a_large_photo_is_scaled_within_the_bounds`: a 4032 × 3024 noise JPEG gives long side ≤ 2048, `w * h <= 2_400_000`, JPEG, under 768 KiB.
  - `test_a_screenshot_stays_png_when_it_fits` (flat 1200 × 800 PNG gives `image/png`) and `test_a_png_that_does_not_fit_becomes_jpeg` (noise 2000 × 1000 PNG gives `image/jpeg`).
  - `test_every_render_is_under_768_kib`: noise RGB at 2048 × 1172 encodes under the cap at q90 4:4:4.
  - `test_only_the_first_frame_of_a_gif_is_shown`: `frames == 3`, the pixel is red.
  - `test_a_heic_photo_is_decoded`: `pytest.importorskip("pillow_heif")`; 64 × 48 out.
  - `test_a_decompression_bomb_is_unreadable`: `make_bomb_png` raises `UnreadableFile` naming 120 megapixels, within 2 s.
  - `test_a_text_file_named_png_is_unreadable` and `test_a_png_named_jpg_is_read_by_its_content` (Review Focus 2).
  - `test_unusual_colour_modes_keep_their_colours` (Review Focus 1): CMYK `(0, 255, 255, 0)` gives red within 40 per channel; `I;16` 32768 gives grey 128 ± 2; a palette PNG with a transparent index gives white there.
  - `test_cut_makes_bands_along_the_long_side`: a 1131 × 1600 image cut in 2 gives two 1131 × 848 images; a 1600 × 1131 image gives two 848 × 1131.
- [ ] **Step 4: Run** `uv run pytest tests/executor/test_render.py -q`. Expected: FAIL.
- [ ] **Step 5: Implement** `images.py` and `encode.py` as above.
- [ ] **Step 6: Run** the same command. Expected: PASS.
- [ ] **Step 7: Commit** `feat(executor): decode, orient and encode pictures without their metadata` (with `pyproject.toml` and `uv.lock`).

### Task 5: PDF pages, strips and the `prepare_file` runner

**Files:**
- Create: `workbench/executor/render/pages.py`, `workbench/executor/runners/render.py`
- Modify: `workbench/executor/runners/__init__.py` (`RUNNERS["prepare_file"]`), `workbench/api/jobs.py:233` (`TOOLS` gains `prepare_file`), `workbench/executor/runners/documents.py:83` (`_hint`, PDF sentence)
- Test: `tests/executor/test_render.py`, `tests/executor/test_supervisor.py`

**Interfaces:**
- Consumes: Task 1, Task 4; `readers.set_deadline`, `readers.check`, `readers.ReadTimeout`; `runners.documents.error`; `protocol.check_input_path`; the PDF reader's `_ENCRYPTED` text.
- Produces:
  - `pages.open_pdf(path: Path) -> Any` (`pypdfium2.PdfDocument`); a password-protected file is `UnreadableFile("the PDF is password-protected (encrypted)")`, other failures `UnreadableFile(DAMAGED)`, no pages `UnreadableFile("the PDF has no pages")`.
  - `pages.pages_without_text(pdf: Any, limit: int) -> tuple[list[int], bool]`: page numbers up to `limit` whose text page holds nothing but whitespace (`get_textpage().get_text_range().strip() == ""`), and whether pages were left unchecked. Each page and text page is closed; `readers.check()` runs between pages.
  - `pages.render_page(pdf: Any, n: int) -> Image.Image`: RGB, `scale = PAGE_LONG_SIDE / max(width_pt, height_pt)`, the page's own rotation applied.
  - Runner `prepare_file(args: dict[str, Any], ctx: JobContext) -> dict[str, Any]`:
    - args: `path` (the source, relative to the chat folder), `pages: list[int] | None`, `strips: int` (1–3), `have: list[int]` (whole pages already rendered; skipped when `strips == 1`), `budget_s: float`.
    - `pages: None` means pages 1..`min(total, FIRST_PAGES)`; an image accepts only `None` or `[1]`.
    - Answer: a `RenderAnswer` as JSON; every entry names one file it wrote into `ctx.out_dir` (`render_name`), role `image` (n 1) or `page`. Strips are cut from a fresh render of the page (or the normalised image), so no render is decoded twice and no JPEG generation is lost.
    - `hint`: `only the first of {frames} frames is shown` for a multi-frame image; `pages after {PAGES_CHECKED_MAX} were not checked for text` when unchecked pages remain; `pages without text are listed up to page {n}` when the list was cut so the answer stays within `ctx.max_chars`.
    - Errors (`documents.error`): `unreadable_file`, `unsupported_format` (a suffix of kind `none`), `no_such_part` (`this document has {total} pages`, or `a picture has one page`), `read_timeout` (budget spent), `file_not_found`.
    - Pages are JPEG at `PAGE_QUALITY` with 4:2:0 subsampling; images at `IMAGE_QUALITY` with 4:4:4.
  - `documents._hint` for a PDF: `{pages …} of those looked at have no text layer (scans or pictures); do not treat them as blank.` The slide sentence keeps its words without "read_document does no OCR".

- [ ] **Step 1: Write the failing tests** (`tests/executor/test_render.py`, `Job`-style context as in `tests/executor/test_templates.py`):
  - `test_a_pdf_is_counted_and_its_first_pages_rendered`: `make_pdf` with 22 pages, page 2 `None`: `total == 22`, `pages_without_text == [2]`, 20 entries `p0001.jpg`…`p0020.jpg` with long side 1600, each file present, JPEG, under the cap, `RenderAnswer.model_validate(answer)` passes.
  - `test_later_pages_render_on_demand`: `pages=[21, 22]` gives two entries, n 21 and 22.
  - `test_have_skips_pages_already_rendered`: `pages=None, have=[1, 2, 3]` gives n 4..20.
  - `test_strips_of_a_page`: `pages=[2], strips=2` gives `p0002-s1of2.jpg` and `p0002-s2of2.jpg`, heights from `strip_spans(height, 2)`, same width.
  - `test_an_image_has_one_render`: `make_photo` gives `kind == "image"`, `total == 1`, one entry n 1 role `image`, `frames is None`.
  - `test_a_page_past_the_end_is_no_such_part`; `test_an_encrypted_pdf_is_unreadable` (`make_pdf(password="x")`) and a truncated PDF (`make_fixtures.make_truncated`) is too, with nothing in `out/` (Review Focus 3).
  - `test_a_long_pdf_is_checked_up_to_its_limit`: with `PAGES_CHECKED_MAX` patched to 5 on a 7-page PDF, the hint names page 5.
  - `test_the_answer_fits_its_limit`: 60 pages without text and `max_chars=300` give a cut list and the hint.
  - `test_the_answer_names_no_path`: `json.dumps(answer)` holds neither `/workspace` nor the job folder.
  - `test_a_word_file_is_unsupported`.
  - Root, in `tests/executor/test_supervisor.py`: `test_as_root_a_photo_and_a_scan_are_prepared_in_a_slot`: the real entry point, `tool="prepare_file"`, production limits; a `make_photo` JPEG and a 3-page PDF each give `ok`, staged outputs whose names match the answer's entries, and a HEIC too when `pillow_heif` is there.
- [ ] **Step 2: Run** `uv run pytest tests/executor/test_render.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `pages.py`, the runner (deadline from `budget_s`, errors as `read_document`'s runner maps them), the registrations and the `_hint` sentence.
- [ ] **Step 4: Run** `uv run pytest tests/executor -q`, then `make test-executor-root`. Expected: PASS; both shapes green.
- [ ] **Step 5: Commit** `feat(executor): prepare_file renders pictures and PDF pages, and cuts strips`

### Task 6: The queue: render jobs, the prepare gate, a lock per source

**Files:**
- Modify: `workbench/api/jobs.py` (`_turn` :611, `_admit` :673, `_discard` :1015, `_keep` :1085, `_register` :1120; `PNG_SIGNATURE` :99), `workbench/api/tools_mcp.py` (`CallJobs`)
- Test: `tests/api/test_jobs.py`

**Interfaces:**
- Consumes: `Store.register_derived`, `remove_derived`, `RendersFull`, `DerivedSpec`, `DerivedRecord` (Task 2); `RenderAnswer`, `RenderEntry`, `RENDER_MAX_BYTES`, `signature_ok`, `SIGNATURES` (Task 1).
- Produces:
  - `PREPARE_QUEUE_WAIT_S: Final = 120`: one prepare takes 1–6 s, so 120 s covers a burst of some 20–100 attachments across all users behind the one prepare slot; longer would only hold HTTP requests open behind a stuck queue. It equals Ghostwriter's default wall, so the worst wait is one stuck job.
  - `PREPARE_WAITING_MAX: Final = 32`, `PNG_SIGNATURE = SIGNATURES["image/png"]`.
  - `Gate = Literal["user", "prepare"]`.
  - `JobQueue.submit_renders(self, p: Principal, source: FileRecord, args: dict[str, Any], keeper: Keeper, *, call: CallLimits, gate: Gate = "user") -> tuple[JobRecord, JobResult, list[DerivedRecord]]`: tool `prepare_file`, input `[source.name]`; its outputs become derived rows, never files.
  - `JobQueue.source_lock(self, source_id: str, wait_s: float) -> AbstractAsyncContextManager[None]`: one `asyncio.Lock` per source id, dropped when no one holds or waits; `Busy("wait_timeout")` after `wait_s`.
  - `CallJobs.submit_renders(self, p, source, args, keeper)` (its call limits, gate `user`; it never adds to `outputs()`) and `CallJobs.source_lock(self, source_id)` (with `call.wait_s`).
- The prepare gate (`_turn(p, call, *, gate)`):
  - Gate `prepare` replaces the user's gate with the caller's prepare gate: a `_UserGate(1)` kept in its own dict by caller name, so no user id can share it.
  - It still takes the caller's gate (counted within the caller's slots) and a global slot, in that order.
  - Its waiting calls count only against `PREPARE_WAITING_MAX`, never against `queue_max`, a user's share or the caller's share; one more is `queue_full`.
- Registration: `_keep`, `_register` and `_discard` are made generic over the record type (`R`), taking `place: Callable[[Path, OutputFile, str], R]` and `drop: Callable[[R], None]`; `submit` passes `register_output` / `remove`, `submit_renders` its own. For each output the keeper chose, the render's `place`:
  1. finds the answer's one `RenderEntry` with that name (none, or an answer that is no `RenderAnswer`: `invalid`);
  2. reads the first 8 bytes and the size of the staged file (`_read_regular`), refusing a wrong signature or a size ≥ `RENDER_MAX_BYTES` (`invalid`);
  3. calls `register_derived` with `DerivedSpec(entry.role, entry.n, entry.mime, entry.width, entry.height, meta)`, `meta` being `{"strip": i, "strips": k}` for a strip, and for the whole render with n 1 also `{"facts": <the answer's RenderFacts as JSON>}`.
  Lost reasons gain `full` (`RendersFull`) and `duplicate` (`Conflict`); each lost id is recorded again through `keeper.again`, as for files.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_jobs.py`, with its `FakeClient` and a scripted answer staging small JPEGs):
  - `test_a_prepare_never_takes_a_users_turn`: with `jobs_per_user=1` and the user's interactive job held running, a prepare for the same user starts at once.
  - `test_one_prepare_runs_at_a_time_across_users`: user B's prepare starts only after user A's ends.
  - `test_prepare_counts_within_the_callers_slots`: caller slots 2 with two interactive jobs running, a prepare waits; with a prepare running, one interactive job runs and a second waits.
  - `test_waiting_prepares_do_not_fill_the_queue`: 32 prepares waiting, an interactive call of another user still runs; the 33rd prepare is `queue_full`.
  - `test_a_prepare_waits_up_to_its_call_wait`: `wait_timeout` after the call's `wait_s`.
  - `test_renders_are_recorded_before_they_are_kept`: the keeper sees the planned ids before any derived row exists; afterwards `derived_of(source)` has them, the head render's `meta["facts"]["total"]` is set, and `store.files_in_chat` has only the source.
  - `test_a_render_without_its_entry_or_signature_is_not_kept`: an unnamed output and a PNG staged as `image/jpeg` are not kept, and `again` names their ids.
  - `test_a_render_of_a_deleted_chat_is_not_kept`: the chat deleted while the job runs gives no derived row.
  - `test_the_source_lock_serialises_and_times_out`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_jobs.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS (every existing queue test unchanged).
- [ ] **Step 5: Commit** `feat(jobs): render jobs keep derived files; prepares queue on a gate of their own`

### Task 7: The api's renders: prepared, made on demand, read back

**Files:**
- Create: `workbench/api/renders.py`, `tests/api/test_renders.py`
- Modify: `workbench/api/audit.py` (`OPERATIONS["prepare"] = ("modify", "file.prepare")`), `workbench/api/tools.py` (`ShownImage`), `docs/audit.md` (the `prepare` row)

**Interfaces:**
- Consumes: Tasks 1, 2 and 6; `tools.OutputKeeper`, `tools.judged`, `tools._readable`, `tools._payload`.
- Produces:
  - `tools.ShownImage(BaseModel)`: `label: str` (≤ 100), `n: int`, `width: int`, `height: int`, `mime: Literal["image/jpeg", "image/png"]`; one-line field descriptions (they are frozen in the goldens).
  - In `renders.py`: `MAX_VIEW_IMAGES: Final = 8`, `MAX_READ_IMAGES: Final = 4`, `MAX_SHOWN_BYTES: Final = 2_621_440` (2.5 MiB raw; base64 stays under Ghostwriter's 4 MiB answer cap).
  - `class Rendition(BaseModel)`: `n`, `width`, `height`, `mime`, `bytes: int`. `class Manifest(BaseModel)`: `path`, `kind: Literal["image", "pdf", "none"]`, `total`, `pages_without_text: list[int]`, `renditions: list[Rendition]`, `frames: int | None`, `hint: str | None`.
  - `@dataclass(frozen=True) class WithBlocks: answer: BaseModel; blocks: list[dict[str, Any]]`.
  - `class RenderJobs(Protocol)` with `submit_renders` and `source_lock(source_id)` as `CallJobs` has them; `@dataclass(frozen=True) class RouteJobs: queue: JobQueue; call: CallLimits; gate: Gate`, which implements it for the file routes.
  - `manifest_of(source: FileRecord, rows: Sequence[DerivedRecord]) -> Manifest | None`: `None` without a whole render n 1 carrying `facts`.
  - `async def prepared(p: Principal, source: FileRecord, *, settings: Settings, store: Store, audit: AuditLog, jobs: RenderJobs) -> Manifest`: kind `none` answers `Manifest(kind="none", total=0, …, hint="This kind of file is not shown as pictures; read it with read_document.")` with no job. Otherwise, under `jobs.source_lock(source.id)`: the manifest from the rows, or `audit.check("prepare", p)` and a job with `pages: None`, `strips: 1`, `have` the whole pages present, `budget_s = max(1, settings.job_wall_s * 3 // 4)`.
    - The keeper is `OutputKeeper(audit, p, "prepare", [source.id], decide)`: `decide` keeps every planned id of an `ok` answer that `_readable(RenderAnswer, …)`; criteria `{"renders": n, "strips": k, "on_demand": bool, "outputs": n}` plus `judged`'s.
    - A refusal goes through `_payload` (its code's status).
    - When the head render could not be kept, it raises the store error of the loss: `QuotaExceeded`, `RendersFull` or `StorageFull` (all 507), else a 500.
  - `async def renders_for(p, source, manifest: Manifest, pages: Sequence[int], strips: int, *, settings, store, audit, jobs) -> list[DerivedRecord]`: the renders of those pages at that strip count, by page then strip. Missing ones are made by one job per at most `RENDERS_PER_JOB // strips` pages, under the source lock, rows checked again inside it. A page the store could not keep is left out. Busy and runner refusals propagate.
  - `read_render(store: Store, rec: DerivedRecord) -> bytes | None`: the whole file if its signature matches `rec.mime` and it is under `RENDER_MAX_BYTES`; else `None`, logged by the error type alone.
  - `image_block(mime: str, data: bytes) -> dict[str, Any]`: `{"type": "image", "data": b64, "mimeType": mime, "annotations": {"audience": ["assistant"]}}`.
  - `label(kind: Literal["image", "pdf"], n: int, strip: int, strips: int) -> str`: `image`, `image, strip 1 of 2`, `page 3`, `page 3, strip 2 of 3`. `shown_image(kind, rec) -> ShownImage`.
  - `page_window(part: str | None, total: int, *, default_last: int) -> tuple[int, int]`: `None` is `1..min(default_last, total)`; `page N` or `N` is N alone; `pages N-M` or `N-M` is N..min(M, total). Anything else, or N outside 1..total, raises `HTTPException(422, "no_such_part: …")` with the grammar or `this document has {total} pages`.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_renders.py`, `Local` executor and helpers from `tests/api/test_tools_templates.py`):
  - `test_prepare_runs_once_and_then_answers_from_the_rows`: the second `prepared` sends no request and writes no event.
  - `test_a_manifest_names_pages_without_text_and_renditions`: a 3-page PDF with page 2 `None` gives `total 3`, `pages_without_text [2]`, renditions n 1..3 with sizes equal to the files'. One event `modify` / `file.prepare` whose target ids are the source then the three renders.
  - `test_a_word_file_is_kind_none_without_a_job`.
  - `test_missing_pages_and_strips_are_made_on_demand`: 22 pages; `renders_for([21], 1)` makes one job; `renders_for([2], 2)` gives two renders in strip order; asking again makes none.
  - `test_two_prepares_at_once_run_one_job` (Review Focus 4): `asyncio.gather` of two `prepared` calls over a slow `Local` gives one request, one set of rows and no `error` event.
  - `test_a_source_at_its_render_cap_says_so` (Review Focus 5): `DERIVED_MAX_BYTES` patched low, `renders_for` past it returns without the page; `prepared` on a fresh source whose head cannot be kept raises `RendersFull`.
  - `test_a_render_whose_bytes_are_wrong_is_not_read`: a render file overwritten with text gives `read_render(...) is None`.
  - `test_page_window_grammar`: `(None, 10, 4) → (1, 4)`, `("page 3", 10) → (3, 3)`, `("pages 2-5", 10) → (2, 5)`, `("pages 8-20", 10) → (8, 10)`, `("page 11", 10)` → 422 `no_such_part`, `("slide x", 10)` → 422.
  - `test_the_image_block_is_for_the_assistant`.
  - `test_the_api_imports_no_image_library`: a subprocess `python -I -c "import workbench.api.main, sys; print(sorted(m for m in ('PIL','pypdfium2','pi_heif') if m in sys.modules))"` prints `[]`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_renders.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; add to `docs/audit.md` § Every event key the row for `prepare`: `modify` / `file.prepare`, target the source and every render kept, criteria `renders`, `strips`, `on_demand`, `outputs: 0` on an `error`, written before any render is visible.
- [ ] **Step 4: Run** `uv run pytest tests/api tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): renders are prepared once, made on demand and read back checked`

### Task 8: The routes: prepare, and rendition views

**Files:**
- Modify: `workbench/api/files.py`, `workbench/api/auth.py:57` (`PREPARE_PATH = "/files/prepare"` in `FILE_PATHS`), `workbench/api/deps.py` (`JobsDep`, `CallerDep`), `workbench/api/errors.py` (docstring :11; `(RendersFull, 507, None)`), `docs/permissions.md` § File routes
- Test: `tests/api/test_files_renders.py` (new), `tests/api/test_files_ghostwriter.py` (`…_to_its_four_paths` becomes `…_to_its_five_paths`)

**Interfaces:**
- Consumes: Task 7.
- Produces:
  - `POST /files/prepare` → `prepare_file(path: str, p, caller, settings, store, audit, jobs) -> dict[str, object]`, `audit.check("prepare", p)` first, then `prepared(…, jobs=RouteJobs(queue, CallLimits(wall, PREPARE_QUEUE_WAIT_S, caller.slots), "prepare"))` with `wall = min(caller.wall_s or settings.job_wall_s, settings.job_wall_s)`; answers `Manifest.model_dump(mode="json")` (all keys, nulls kept).
  - `GET /files/view` gains `rendition: int | None = Query(None, ge=1)`, `strips: int = Query(1, ge=1, le=3)`, `strip: int | None = Query(None, ge=1, le=3)` and becomes `async`.
    - Without `rendition` it runs today's body unchanged in the thread pool.
    - With it: `strips > 1` needs `strip` and `strip <= strips`, and `strips == 1` needs `strip` absent or 1 (else `HTTPException(422, "strip is 1 to strips")`).
    - `audit.check("view", p)`; the manifest through `prepared` on the user's own turn (`RouteJobs(queue, CallLimits(wall, MCP_QUEUE_WAIT_S, caller.slots), "user")`).
    - Kind `none` or `rendition > total` is `NotFound`. `renders_for([rendition], strips)` picks the render; a page that could not be kept raises `RendersFull`.
    - `read_render` returning `None` is a 500 `internal error`.
    - Records `view` naming `[source.id, render.id]`, then answers `Response(data, media_type=render.mime)` with `Content-Disposition: inline; filename*=UTF-8''<quoted stem>-<slug><ext>`, the slug `page-3` or `page-3-strip-1-of-2` (an image whole: the stem alone).
  - `JobsDep` raises `Busy("executor_busy")` when `app.state.jobs` is `None`. `BodyLimit` and `ViewHardening` need no change: prepare has no body, so `REQUEST_MAX` bounds it, and renditions are on `VIEW_PATH`.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_files_renders.py`, app with a `Local` executor, headers `HG` plus `PURPOSE`):
  - `test_prepare_answers_the_manifest_and_records_a_modify`: keys exactly `{"path", "kind", "total", "pages_without_text", "renditions", "frames", "hint"}`; each rendition's keys `{"n", "width", "height", "mime", "bytes"}`; the event carries the purpose and request id.
  - `test_prepare_again_answers_from_the_renders_without_a_job_or_event`.
  - `test_prepare_of_a_word_file_is_kind_none_without_a_job`.
  - `test_prepare_of_a_text_file_named_png_is_422_and_stores_nothing` (Review Focus 2): `detail` starts `unreadable_file:`, no derived row, one `error` event with `refused: unreadable_file`.
  - `test_prepare_needs_a_purpose` (428, `denied`); `test_prepare_of_another_chats_file_is_404`; `test_prepare_has_no_body` (a declared 3 MiB body is 413 `request too large`).
  - `test_a_rendition_is_served_with_the_view_hardening`: `image/jpeg`, the three `FILE_HEADERS`, `inline`; one `disclose` / `file.view` naming the source and the render.
  - `test_a_rendition_past_the_first_pages_is_rendered_on_demand`: page 21 of 22 makes one job; asking again makes none.
  - `test_strips_are_cut_once_and_served`: `strips=2&strip=1`, then `strip=2`, make one job; the two bodies differ.
  - `test_bad_strip_pairs_are_422`: `strip=2` alone, `strips=2` alone, `strips=2&strip=3`.
  - `test_prepare_422_details_start_with_their_token`: a text file named `.png` gives a detail starting `unreadable_file:`, a password-protected PDF the same, a patched runner timeout `read_timeout:`; the token is the text before the first `:`.
  - `test_a_rendition_out_of_range_or_of_a_word_file_is_404`, with the hardening headers on the 404.
  - `test_without_rendition_the_view_is_as_before`: same bytes, headers and single event as before; no job.
  - `test_a_derived_path_is_not_found`: `GET /files/view?path=/workspace/chat-1/.derived/<id>/<name>` is 404; `GET /files/list` of the chat has no `.derived`.
  - `test_a_page_past_the_render_cap_is_507` (Review Focus 5).
  - `test_a_busy_queue_is_503`: a `Local` with `status="busy"`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_files_renders.py tests/api/test_files_ghostwriter.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; in `docs/permissions.md` add the prepare row (reads the file in the executor; writes renders under `.derived/`; `modify` / `file.prepare`; keeps the renders until the source goes) and widen the view row (a rendition: one render, `disclose` / `file.view` naming the source and the render; may render first).
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(files): POST /files/prepare and rendition views for Ghostwriter`

### Task 9: The `view_file` tool

**Files:**
- Create: `workbench/api/tools_views.py`, `tests/api/test_tools_views.py`
- Modify:
  - `workbench/api/tool_table.py` (a row between `search_files` and `write_document`), `workbench/api/tools_mcp.py:247` (`workspace_run` unpacks `WithBlocks`), `workbench/api/audit.py` (`OPERATIONS["view_file"] = ("query", "tool.view_file")`).
  - `workbench/config.py:32`, `docker/compose.yaml:111`, `.env.example:81` (`view_file` in the default list).
  - `tests/callers.py`, `tests/test_compose_defaults.py:47` (`TOOLS_LIST`), `tests/test_docs.py:40` (`== 10`), `tests/api/test_tools_golden.py:3` (docstring: ten tools), both `tests/api/golden/ghostwriter-*.json`.
  - `docs/permissions.md` (matrix row; "nine" → "ten" at :16 and :57), `docs/audit.md` (the `view_file` row), `docs/configuration.md:34` ("the ten workspace tools", and: "Since 0.7.0 the default includes `view_file`; a list set explicitly must add it, or Ghostwriter offers no images.").

**Interfaces:**
- Consumes: Task 7 (`prepared`, `renders_for`, `read_render`, `image_block`, `shown_image`, `page_window`, `WithBlocks`, the caps), `tools.ShownImage`, `tools._within`, `tools._capped`.
- Produces:
  - `class ViewFileRequest(BaseModel)`: `path: str` (1–1024, described as `read_document`'s), `part: str | None` (`json_schema_extra={"type": "string"}`; "'page 3' shows page 3, 'pages 2-5' pages 2 to 5; left out: the picture, or pages 1-4"), `strips: int | None` (1–3, `json_schema_extra={"type": "integer"}`, "Leave it out.").
  - `class ViewedFile(BaseModel)`: `path`, `kind: Literal["image", "pdf", "none"]`, `total: int`, `shown: list[ShownImage]`, `truncated: bool`, `next_part: str | None`, `hint: str | None` (≤ 2000).
  - `async def view_file(body: ViewFileRequest, p: Principal, settings: Settings, store: Store, audit: AuditLog, jobs: CallJobs) -> ViewedFile | WithBlocks` (`CallJobs` imported under `TYPE_CHECKING`, as `tools.py` does: `tools_mcp` imports the table, which imports this module):
    1. `store.get`, then `prepared`.
    2. Kind `none`: `shown []` and the manifest's hint.
    3. Otherwise the window (`page_window(part, total, default_last=4)`; an image accepts `None`, `page 1`, `1`).
    4. `renders_for` the first `MAX_VIEW_IMAGES // k` pages of it.
    5. Take whole pages in order while the count stays ≤ 8 and the bytes ≤ 2.5 MiB; the first page that does not fit ends the answer with `truncated: true` and `next_part = "pages {n}-{last}"` (`"page {n}"` when `n == last`).
    6. A render `read_render` refuses, or a page not kept, is skipped and named in the hint (`page 7 could not be shown`); a page past the cap gets `the renders of this file are at their limit; pages after 6 cannot be shown`.
    7. Record `view_file` naming the source and each render shown, criteria `{"images": n, "strips": k, "paged": part is not None}`, before returning.
    Every call that finds its file is recorded, kind `none` too (`images: 0`).
  - `workspace_run`: an answer that is `WithBlocks` gives `ToolOutcome(structured, answer.blocks, text)`, built from `answer.answer` as today.
  - Description (about 800 characters), saying: it shows a picture or pages of a file of this conversation as images for you to see, metadata removed; pictures (PNG, JPEG, WebP, GIF, BMP, TIFF, HEIC) and PDF pages, the part grammar above; use it for what text cannot tell (layout, design, charts, handwriting, photos, scans) and to look at a chart or picture a tool made; at most 8 images, then call again with `next_part`; Word, PowerPoint and spreadsheets are not shown yet, read them with `read_document`; never describe an image you have not looked at.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_tools_views.py`, MCP calls as in `tests/api/test_tools_templates.py`):
  - `test_view_file_shows_an_image_to_the_assistant`: `content[0]` is text, `content[1]` an image block with `audience ["assistant"]`, its data decodes to a JPEG under 768 KiB; `structuredContent` keys exactly `{"path", "kind", "total", "shown", "truncated", "next_part", "hint", "outputs"}`, `outputs == []`, `shown[0]["label"] == "image"`.
  - `test_view_file_shows_pages_1_to_4_by_default` (6-page PDF).
  - `test_view_file_caps_at_8_images`: pages 1-12 give 8 blocks, `truncated`, `next_part == "pages 9-12"`.
  - `test_view_file_caps_at_2_5_mib`: renders patched to about 700 KiB each give 3 blocks and a raw sum ≤ 2 621 440.
  - `test_strips_come_as_k_images_per_page`: `part="page 2", strips=2` gives labels `page 2, strip 1 of 2` and `page 2, strip 2 of 2`.
  - `test_image_blocks_follow_shown_in_order`: pages 1-3 of a PDF whose pages differ; for each `i`, the bytes of `content[1 + i]` are the render named by `shown[i]` (same `n`, width and height).
  - `test_view_file_shows_a_chart_run_python_made`: an output PNG of a `run_python` call is shown.
  - `test_view_file_on_a_word_file_says_read_it`: kind `none`, one content block, `images: 0` recorded.
  - `test_view_file_is_recorded_before_the_images_go`: the event's ids are `[source, *shown render ids]`; with `broken_audit` the call fails and no image block is sent.
  - `test_a_tampered_render_is_skipped_with_a_hint`; `test_a_page_past_the_render_cap_is_named_in_the_hint` (Review Focus 5).
  - `test_view_file_of_another_chat_or_a_derived_path_is_not_found` (`refused`, `not found`).
  - `test_the_schema_stays_small`: `len(json.dumps(input_schema)) + len(description) <= 3000`.
  - `test_ghostwriter_is_offered_view_file`: `tools/list` has ten names, sorted; docint's has `run_python` alone.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_tools_views.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, then the registrations; add the `view_file` entry to both goldens by hand (copy the served JSON once after reading it line by line); add the matrix row and the audit row.
- [ ] **Step 4: Run** `uv run pytest -q` and `python3 -B ../deploy/scripts/check_env_examples.py .`. Expected: PASS; the env check clean.
- [ ] **Step 5: Commit** `feat(api): view_file shows pictures and pages to the model`

### Task 10: `read_document` returns images of pages without text

**Files:**
- Modify: `workbench/api/tools.py` (`ReadDocumentRequest.strips`, `DocumentRead.shown`, the `pages_without_text` field description, module docstring; `read_document` moves out), `workbench/api/tools_views.py` (`read_document` moves here, since it now shows pages; `renders` imports `tools`, so `tools` cannot import `renders`), `workbench/api/tool_table.py:96` and `:116` (descriptions), `workbench/api/mcp_wire.py:58` (`WORKSPACE_INSTRUCTIONS`), both ghostwriter goldens, `docs/permissions.md` § What the model is told, `docs/audit.md` (`read_document` criteria `images`)
- Modify: `tests/api/test_files_ghostwriter.py:26` (import `read_document` from `tools_views`)
- Test: `tests/api/test_tools_documents.py`

**Interfaces:**
- Consumes: Task 7, Task 9's module.
- Produces:
  - `ReadDocumentRequest.strips: int | None` (1–3, "Leave it out."); `DocumentRead.shown: list[ShownImage] = []`, described "Pages without text shown as images with this answer."; `pages_without_text` described "Pages or slides looked at that hold no text (scans or pictures); never treat them as blank."
  - `tools_views.read_document(body, p, settings, store, audit, jobs) -> DocumentRead | WithBlocks`:
    - **A picture file** (`kind_of == "image"`): no read job. `prepared`, then its one render (or strips). Answer `DocumentRead(kind="image", parts=[], total_parts=1, pages_without_text=[1], truncated=False, shown=[…], hint="This is a picture: it comes with this answer for you to see.")`.
    - **Otherwise** today's job and `_fit_read`. A refusal is recorded and raised as today. For an `ok` answer of a PDF with `pages_without_text`, the window rule below runs, then `_within(answer, limit)`.
    - The read is recorded once, after the images are settled and before returning: `read_document` naming the file then each render shown, criteria `{"paged": …, "images": n}`.
    - `Busy`, a runner refusal or a cap while making renders never fails the read. It answers with the text, `shown []` and the hint `the pages without text could not be shown now; view_file shows them`.
    - Kinds `none` (Word, PowerPoint…) attach nothing in stage 1.
  - Descriptions:
    - `read_document`: "Readable" gains "pictures (PNG, JPEG, WebP, GIF, BMP, TIFF, HEIC), which come back as the image". The OCR sentence becomes: "pages_without_text lists pages or slides with no text layer (scans, pictures); never treat them as blank. Such PDF pages come back as images in the same answer (shown names them): read them from the images. To look at any page, use view_file."
    - `run_python`'s installed list gains `pillow, pypdfium2`.
    - `WORKSPACE_INSTRUCTIONS` ends "…comes back as a reference with its path and id, never as bytes; pages and pictures come back as images for you to see."

The window rule (the cut is not determined by the tests alone):

```python
k = body.strips or 1
cut_at = page_of(answer.next_part) if answer.truncated else None      # "page N" -> N, else None
candidates = [n for n in answer.pages_without_text if cut_at is None or n < cut_at]
wanted = candidates[: max(1, MAX_READ_IMAGES // k)]                    # no later page can fit by count
made = await renders_for(p, rec, manifest, wanted, k, ...)
shown: list[tuple[DerivedRecord, bytes]] = []
used = 0
for n in candidates:
    page = None if n not in wanted else [
        (r, data) for r in made if r.ordinal == n and (data := read_render(store, r)) is not None
    ]                                                                   # [] when it could not be made
    size = sum(len(data) for _, data in page or [])
    if shown and (page is None or len(shown) + len(page) > MAX_READ_IMAGES or used + size > MAX_SHOWN_BYTES):
        answer.parts = [part for part in answer.parts if page_of(part.label) < n]
        answer.truncated, answer.next_part = True, f"page {n}"
        break
    shown += page or []
    used += size
```

The first page always goes (`if shown and …`), and one page is at most 3 images of under 768 KiB, so paging always moves on. A page in `wanted` whose render could not be made is skipped and named in the hint. The hint gains `Pages 2, 5 come with this answer as images.`

- [ ] **Step 1: Write the failing tests** (`tests/api/test_tools_documents.py`, `Local` executor):
  - `test_a_scanned_page_comes_back_as_an_image_in_the_same_answer`: `make_pdf(["Harbour figures 2026", None, "Quay list"])` gives one image block, `shown == [{"label": "page 2", "n": 2, …}]`, parts pages 1 and 3, the hint naming page 2.
  - `test_the_window_ends_before_a_page_whose_image_does_not_fit`: 6 pages `None` give 4 blocks, `truncated`, `next_part == "page 5"`.
  - `test_paging_always_moves_on`: with `strips=3` on 3 image-only pages, three calls following `next_part` show pages 1, 2 and 3 in turn.
  - `test_an_image_file_is_read_as_its_image`: kind `image`, `total_parts 1`, `pages_without_text [1]`, one block.
  - `test_a_text_pdf_reads_as_before`: no block, `shown == []`, the event's ids `[file id]`, criteria `images: 0`.
  - `test_an_encrypted_pdf_reads_as_before` (Review Focus 3): the same `refused` answer and `error` event as before this change.
  - `test_a_pptx_attaches_nothing_yet`.
  - `test_a_busy_render_still_answers_the_text`: the render job `Busy` gives the parts, `shown []` and the hint.
  - `test_the_images_are_recorded_with_the_read`: one `query` / `tool.read_document` with ids `[file, *renders shown]`, `images: 1`.
  - Existing: `tests/api/test_tools_golden.py` for docint unchanged; `tests/api/test_tools_mcp.py` instructions test against the new text.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_tools_documents.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**; edit both goldens by hand: `read_document` (description, `strips` in `inputSchema`, `shown` and `$defs.ShownImage` in `outputSchema`, the `pages_without_text` and model descriptions) and `run_python` (description).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): read_document shows pages without text, and pictures, as images`

### Task 11: Docs, isolation, release

**Files:**
- Modify:
  - `CLAUDE.md`: ten tools; `workbench/renders.py` among the shared modules; `tools_views.py`, `renders.py` (api) and `executor/render/`, `runners/render.py` in the module lists; derived files under State; the prepare gate; the fifth file route.
  - `README.md` (tool count).
  - `docs/README.md` § Third-party licences: pi-heif's wheel bundles libheif and libde265 (LGPL-3.0), used unmodified as shipped libraries and replaceable in the image; pypdfium2 (Apache-2.0 / BSD-3-Clause, with pdfium's bundled licences); pillow-heif (with the GPL x265 encoder) is in the dev group and the `test` stage only, never in a shipped image.
  - `docs/data-flow.md`:
    - renders are derived files of their source in `.derived/` with its creation time, counted in the quota, deleted with it;
    - what leaves: image bytes to the caller through `view_file`, `read_document` and rendition views, and the manifest's metadata;
    - every render is stripped of image metadata.
  - `docs/sandbox.md`:
    - § Residual risks: new parsers of hostile input in the executor only, namely Pillow's decoders (PNG, JPEG, WebP, GIF, BMP, TIFF), libheif and libde265 through pi-heif, and pdfium through pypdfium2;
    - the open is restricted to those formats, one HEIF decoder thread, a 120 MP cap;
    - the api trusts a render's stated width and height only within bounds, so a wrong value costs tokens, never access.
    - § What a job can import: the extra's list, and the description now names fifteen packages.
  - `docs/configuration.md`: `GHOSTWRITER_WORKBENCH_SLOTS` 1 makes prepares share the caller's one slot; keep 2 or more. No new variable.
- Modify: `tests/isolation/test_isolation.py`: `test_a_picture_and_a_scan_are_prepared_and_viewed_in_the_sandbox`: upload a `make_photo` JPEG and a 3-page PDF with page 2 `None`; `POST /files/prepare` for each; `GET /files/view?rendition=1` for the photo (no `Exif` in the bytes); `view_file` over MCP on the PDF; `read_document` shows page 2; delete the chat and see `GET /files/view?rendition=1` answer 404.
- Modify: `pyproject.toml` (`version = "0.7.0"`), `uv.lock` (`uv lock`).

- [ ] **Step 1: Write the isolation case** following the file's template case.
- [ ] **Step 2: Docs.** Run `uv run pytest tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 3: Version.** `uv lock`, then `uv run pytest tests/test_version.py -q`. Expected: PASS.
- [ ] **Step 4: Gates.** `make verify && make test`, then `make test-executor-root` and `make test-isolation`. Expected: each green. Paste the summary lines into the PR.
- [ ] **Step 5: Commit** `docs: renders, view_file and the new parsers in the design record and the references` and `chore(release): 0.7.0`.
- [ ] **Step 6: Push and PR.** `git push -u origin feat/vision-stage1`; PR to `main` with an **Operators** line: "0.7.0 adds the tool `view_file` and the route `POST /files/prepare`. If `GHOSTWRITER_WORKBENCH_TOOLS` is set in `.env`, add `view_file`, or Ghostwriter offers no images. The executor image gains Pillow (explicit), pi-heif and pypdfium2." Confirm CI ran on the last commit.

---

## Self-review

- **Spec coverage (§7.2, stage 1):**
  - derived table, reserved folder, hiding, chat delete, retention and cap: Tasks 2–3;
  - prepare runner for images and PDF, strips: Tasks 4–5;
  - prepare gate: Task 6;
  - `POST /files/prepare` and `GET /files/view?rendition=`: Task 8;
  - `view_file`: Task 9;
  - `read_document` images and instructions: Task 10.
  - Left for later stages with their extension points: Office (`kind_of` gains `office`; roles `pdf` reserved), `POST /files/derived` and media (roles `frame`, `transcript`, `nextext_*` reserved; `DerivedSpec` takes no width or height), `frame N` / `at mm:ss` in `view_file`'s grammar.
- **Decisions the spec left open, made here:**
  - Strips are cut on first request by a `prepare_file` job, from a fresh render of the source, and kept as derived rows. Not at prepare: 20 pages and their strips would pass the 20 outputs of one job, and only fixed-cost models need them.
  - Manifest facts ride on the head render's `meta`.
  - Prepare's event is `modify`, as for `fill_template` and `write_presentation` (a new file made from the user's file).
  - Prepare waits 120 s.
  - The prepare gate is kept by caller in its own dict.
  - Renders on the routes run on the user's own turn; only `POST /files/prepare` uses the prepare gate.
- **Proportion:** code appears only for `strip_spans` and the window cut; everything else is signatures, rules and tests.
