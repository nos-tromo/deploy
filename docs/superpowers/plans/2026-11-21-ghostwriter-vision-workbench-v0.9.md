# Ghostwriter vision, stage 3: workbench v0.9.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** workbench keeps Nextext's two raw artifacts of a clip as derived files (`POST /files/derived`), makes `transcript.txt` and 1280 px frames from them in its executor, and shows them. Prepare answers a media manifest, `read_document` reads a clip's transcript, and `view_file` shows its frames.

**Architecture:**
- **Artifacts in.** Ghostwriter streams `docint.jsonl` and `keyframes.zip` into the new route. Each is read whole into `<id>.part` in the clip's `.derived/<source-id>/` folder and its first bytes are checked; then, under the source's lock, it is recorded, renamed into place and inserted.
- **Prepare.** `prepare_file` gains a media branch. Its inputs are the two artifacts only, at their derived paths, as v0.8's converted PDF reaches its render job; the clip itself never enters a job. Frames are `frame` rows with their time in `meta`, the transcript is a `transcript` row, and the facts ride on the transcript, else on frame 1. The api reads the rows back as it reads renders.

**Builds on:** workbench v0.8.0 (stage 2), merged, as `2026-11-21-ghostwriter-vision-workbench-v0.8.md` plans it:
- the `office` kind, and `Unit = Literal["page", "slide"]` on `RenderFacts`, `Manifest` and `ViewedFile`;
- `_DERIVED_EXTENSIONS` with its role-to-type rule, `Noun`/`label(noun, …)`;
- `submit_renders(..., converted=)`, which makes a derived row a job's input at its own derived path (stage-2 decision D3).

Line numbers below are v0.7.0's; v0.8 shifts some, so find each by its name.

**Tech Stack:** Python 3.12, FastAPI, pydantic, SQLite; executor: Pillow and the stdlib (`zipfile`, `json`); tests: pytest. Synthetic artifacts are built at test time.

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§2 anti-goals, §5.3, §7.2 Media, REST and MCP, §8 stage 3, §10 check 7) and `deploy/docs/decisions/0009-ghostwriter-sees-images-and-media.md`. The consumer is `2026-11-21-ghostwriter-vision-app-v0.5.md`.

Every path below is relative to the `workbench` repo root unless it starts with `deploy/`.

## Decisions

The lead's calls (E4; the owner may correct them):
- **`POST /files/derived?source=<virtual path>&role=nextext_jsonl|nextext_zip`** is for Ghostwriter's caller only (`FILE_PATHS`).
  - It streams the body to `<id>.part`, then checks it and renames it into place.
  - The source must be an audio or video upload of the chat, by suffix: mp4 mov m4v webm mkv mp3 m4a wav ogg opus flac.
  - It has its own `BodyLimit` bound: jsonl 8 MiB, zip 32 MiB. A zip must start with `PK\x03\x04`; a jsonl's first line must be JSON.
  - At most one row per role per source: a second POST replaces it, recorded.
  - It counts in the 64 MiB per-source cap and the quota. The audit event is `modify` / `file.derived`, naming the source and the new id.
- **`kind_of`:** the media suffixes give the kind `media`. `_EXTRA_TYPES` (`api/store.py:107`) gains every media type explicitly.
- **`RenderFacts` / `Manifest`:** kind `media`, new fields with defaults (`duration_s: float | None = None`), `unit` `"frame"`.
- **The media branch of the prepare runner:**
  - Inputs: the two derived artifacts, through stage 2's derived-input mechanism (here: at their derived paths, as v0.8's `converted` input).
  - The jsonl becomes `transcript.txt` (role `transcript`): `[mm:ss] SPEAKER: text` lines, `Speaker N` kept, none of `source_file`, the hash or `language`; hours as `h:mm:ss` past 59:59.
  - The zip goes through `check_zip` and a member check: names `frame_NNN.jpg` and `manifest.json` only, the JPEG signature, the pixel cap. Its frames are normalised to a 1280 px long side (role `frame`, meta `time_s`) through v0.7's `normalise`/`encode`.
  - `duration_s` is the last `end_seconds`, else the last frame's `time_sec`.
  - A clip with no speech has no jsonl, so no transcript, and the hint says so. Audio has no zip, so no frames.
- **`read_document` on a clip** reads `transcript.txt` with the text window of a text file, kind `transcript`, and attaches nothing. Without a transcript it answers, not refuses, that there is no speech.
- **`view_file` on a clip:** `frame N`, `frames N-M`, `at mm:ss` (the nearest frame); by default 8 frames spread evenly; labels `frame 3 (01:24)`.
- Descriptions, goldens and docs (permissions, audit, data-flow, retention, sandbox: the zip member checks) follow; the version is 0.9.0.

Calls made in this plan:
1. **A clip's frame count is `total`, with `unit: "frame"`** (0 for audio or a clip without pictures). `frames` keeps its stage-1 meaning, the frames of a multi-frame picture, and is `null` for a clip: `RenderFacts.frames` is `Field(ge=2)` (`renders.py:235`) and could not hold a clip with one keyframe.
2. **At most `MEDIA_FRAMES_MAX = 19` frames are kept.** One prepare job has at most 20 outputs (`executor/supervisor.py:100` `MAX_OUTPUTS`, `renders.RENDERS_PER_JOB`), and one of them is `transcript.txt`. Ghostwriter asks Nextext for 19 (`keyframes_max`); a zip with more keeps 19, spread evenly.
3. **Frame `n` is the 1-based position in time order.** Nextext counts `frame_000.jpg` from 0; `RenderEntry.n` and the `frame N` grammar count from 1.
4. **The artifact travels as the raw request body**, not multipart.
5. **A second POST of a role replaces that row and also removes the source's `transcript` and `frame` rows**, which were made from the old input. All of them are named in one event, and the next prepare makes them again.
6. **A prepare of a clip with no raw artifact answers at once:** no job, `total 0`, `transcript false`, hint `NOTHING_FOUND`.
7. **The api parses no artifact.** It checks a zip's first four bytes and a jsonl's first line (at most 64 KiB, a JSON object); the executor parses everything else.
8. **`read_document` reads a clip's transcript with or without `X-Accept-Images`**, since the transcript is text. Its description therefore names clips in both listings: the without-header description, 0.6's otherwise (R15), gains that one sentence. `view_file` stays listed only with the header.
9. **One part may fail without the other.** A transcript that cannot be read leaves the frames, and frames that cannot be read leave the transcript, each with a hint. Only when nothing usable comes from the inputs present does prepare answer `422 unreadable_file`.
10. **A clip's frames are never cut into strips:** `strips` is ignored on a clip.
11. **Ghostwriter tells v0.9 by the version its discovery names** (`serverInfo`, ≥ 0.9.0).

## Global Constraints

- Branch `feat/vision-stage3` from a `main` that holds v0.8.0; one PR. `pyproject.toml` `version = "0.9.0"` and `uv lock` in Task 8; the tag is minted on merge. Workflow pins are unchanged.
- Python `>=3.12,<3.13`; ruff `D` and `ANN`; pyrefly strict; 120-character lines. `make verify` and `make test` green before every push (`git add` new files first). `make test-executor-root` and `make test-isolation` green before the PR.
- The executor imports nothing of `workbench.api`. The rules both sides use live in `workbench/renders.py`, which imports no imaging library.
- The api decodes nothing: no `PIL`, `pypdfium2` or `pi_heif`, no `zipfile` on an artifact, no JSON parse beyond a jsonl's first line.
- No media decoder in workbench: the clip never enters an executor job. Nextext's pipeline is the one media exception (ADR 0009).
- Fail closed:
  - an artifact becomes visible only after its `modify` / `file.derived` event, and a transcript or frame only after its `modify` / `file.prepare`;
  - frames leave only after a `query` / `tool.view_file` or a rendition's `disclose` / `file.view` event that names the source and each frame;
  - a transcript's text leaves only after its `query` / `tool.read_document`.
- R15: images only for a call that carries `X-Accept-Images: 1`. Without it, Ghostwriter's listing has no `view_file`; `read_document` attaches no image to a clip either way.
- A new field of `RenderFacts`, `RenderEntry`, `RenderAnswer` or `Manifest` has a default, and stored facts are read leniently (stage-1 R17).
- Logs and events hold ids, counts, codes and exception types only. Never a file name, a path, a transcript line, a speaker label or an image byte.
- Airgap: nothing is fetched at runtime. Test artifacts are built at test time with invented content (`tests/fixtures/make_media.py`): no committed binary, no absolute local path.
- The goldens in `tests/api/golden/` are edited by hand.
- Spec values, each the only one used:
  - frames: 1280 px on the long side, JPEG q85 at 4:2:0, each under 768 KiB; at most 19 kept;
  - caps: jsonl at most 8 MiB, zip at most 32 MiB, transcript at most 4 MiB; derived files at most 64 MiB per source, counted in the quota;
  - `view_file`: at most 8 images and 2.5 MiB; by default 8 frames spread evenly;
  - zip members: `frame_NNN.jpg` and `manifest.json` only; sources over 120 MP refused.
- Commit messages conventional, no real data.

## Review Focus

1. **A hostile or odd keyframes zip**: a member `../x.jpg`, `frame_001.jpg.exe` or `sub/frame_002.jpg`, a PNG named `frame_000.jpg`, a JPEG that claims 12000 × 12000. Expect the frames refused as a set with the hint `The frames could not be read.`, the transcript kept, and prepare `200`. Tests in Task 4.
2. **A transcript line whose text holds a newline and a forged stamp** (`"Hello\n[00:00] Speaker 9: …"`), an odd speaker label, a clip over an hour. Expect one line per sentence, the label dropped, `[1:02:03]`. Tests in Task 4.
3. **The same artifact posted again while a prepare of that clip runs** (Ghostwriter requeued after a restart). Expect the POST to wait for the source lock, then replace the row and the frames and transcript made from it, recorded; the next prepare makes them again. Tests in Tasks 3 and 5.
4. **A blind caller** (no `X-Accept-Images`) reading a clip. Expect the transcript text, no image block, and no `view_file` in its listing. Test in Task 7.
5. **A 4K clip's keyframes.** Expect each frame 1280 × 720 under 768 KiB, and the whole set within the 64 MiB cap beside both artifacts. Tests in Tasks 4 and 5.

## Contract for Ghostwriter (what the v0.5 plan consumes)

Headers on every route as before: `Authorization: Bearer <GHOSTWRITER_WORKBENCH_KEY>`, `X-User-Id`, `X-Session-Id`, `X-Audit-Purpose`, `X-Request-ID`. Media is served by a workbench whose discovery names version 0.9.0 or later.

**`POST /files/derived?source=<virtual path>&role=nextext_jsonl|nextext_zip`.** The body is the artifact's bytes, streamed. Send `Content-Type: application/x-ndjson` or `application/zip`; the route does not trust it. `200`:

```json
{"id": "<32 hex>", "role": "nextext_jsonl", "bytes": 48213, "replaced": null}
```

- `replaced` is the id of the row of that role it replaced, or `null`.
- Statuses:
  - `400`: bad path or session.
  - `404 not found`: no such file, another chat's, or a `.derived` path.
  - `413 file too large`: a jsonl over 8 MiB or a zip over 32 MiB; a declared `Content-Length` over 32 MiB is refused before any byte.
  - `422`: `unsupported_format: the source is not an audio or video upload`, or `unreadable_file: …` (empty, wrong signature, a first line that is no JSON object).
  - `428`: no purpose.
  - `503 busy, retry later`: the clip's lock was not free within 120 s.
  - `507`: quota, storage, or the renders at their limit.
- Recorded as `modify` / `file.derived`, with target the source, then the new id, then each replaced id, and criteria `role`, `bytes`, `replaced` (a count).
- Client timeout: the transfer plus 150 s.

**`POST /files/prepare?path=<clip>`**, the media manifest (10 keys, always all present):

```json
{"path": "/workspace/chat-1/harbour-tour.mp4", "kind": "media", "unit": "frame", "total": 12,
 "pages_without_text": [], "renditions": [{"n": 1, "width": 1280, "height": 720, "mime": "image/jpeg", "bytes": 91234}],
 "frames": null, "hint": null, "duration_s": 134.2, "transcript": true}
```

- Every manifest now has `duration_s` (`null` but for a clip) and `transcript` (`false` but for a clip with a transcript).
- `total` is the number of frames kept: 0 for audio and for a clip without pictures.
- `renditions` lists every frame, `n` from 1.
- Without a raw artifact, the answer is given at once: `total 0`, `transcript false`, `hint` "Nothing was found in this clip: no speech and no pictures, or it has not been transcribed."
- With artifacts, the first prepare runs one job on the prepare gate; v0.8's client timeout (390 s) covers it. `422 unreadable_file: …` means neither artifact gave anything usable.
- Call it after both POSTs.

**`GET /files/view?path=<clip>&rendition=<n>`** serves frame `n` whole. `strips` above 1 is `422 a clip's frames are not cut into strips`, and `n` outside `1..total` is `404`.

**MCP `view_file` on a clip** (listed only with `X-Accept-Images: 1`).
- `part` grammar: `frame N` or `N`; `frames N-M` or `N-M`; `at mm:ss` or `at h:mm:ss`, the nearest frame. Left out, at most 8 frames spread evenly.
- The answer: `kind: "media"`, `total` the frames. Labels are `frame 3 (01:24)`, or `frame 3` when the zip had no times. `strips` is ignored.
- Audio, or a clip without frames: `shown: []` and a hint. `no_such_part: …` for a frame or time outside the clip.

**MCP `read_document` on a clip**, with or without the header:
- The answer: `kind: "transcript"`, `parts` as a text file's (`lines 1-200`), `total_parts` the lines, `pages_without_text: []`, `shown: []`.
- Without a transcript: `parts: []`, `total_parts: 0` and the hint `No speech was found in this clip.`
- Lines read `[01:24] Speaker 1: text`, `[1:02:03] text` from one hour on; a sentence without a speaker label has none.

---

### Task 1: The rules of a clip

**Files:**
- Modify: `workbench/renders.py`, `workbench/api/store.py:107` (`_EXTRA_TYPES` takes `MEDIA_TYPES`)
- Test: `tests/test_renders.py`, `tests/api/test_store.py`

**Interfaces:**
- Produces in `renders.py`, all `Final`:
  - The suffixes:
    - `VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})`;
    - `AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac"})`;
    - `MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES`.
  - `MEDIA_TYPES: Mapping[str, str]`: `.mp4` `video/mp4`, `.mov` `video/quicktime`, `.m4v` `video/x-m4v`, `.webm` `video/webm`, `.mkv` `video/x-matroska`, `.mp3` `audio/mpeg`, `.m4a` `audio/mp4`, `.wav` `audio/wav`, `.ogg` `audio/ogg`, `.opus` `audio/opus`, `.flac` `audio/flac`.
  - `Kind` gains `"media"`, and `kind_of` gives it for `MEDIA_SUFFIXES`.
  - The bounds:
    - frames: `FRAME_LONG_SIDE = 1280`, `FRAME_QUALITY = 85`, `MEDIA_FRAMES_MAX = RENDERS_PER_JOB - 1`;
    - artifacts: `JSONL_MAX_BYTES = 8 * 1024 * 1024`, `ZIP_MAX_BYTES = 32 * 1024 * 1024`, `TRANSCRIPT_MAX_BYTES = 4 * 1024 * 1024`.
  - `TRANSCRIPT_NAME = "transcript.txt"`, the runner's output name.
  - `class MediaRequest(_Strict)`: `nextext_jsonl: str | None = None`, `nextext_zip: str | None = None` (each a derived path relative to the chat folder), `video: bool`. It is the `media` argument of a `prepare_file` job, as v0.8's `OfficeRequest` is its `office` argument.
  - The texts: `NOTHING_FOUND = "Nothing was found in this clip: no speech and no pictures, or it has not been transcribed."`, `NO_SPEECH = "No speech was found in this clip."`
  - `stamp(seconds: float) -> str`: a negative value counts as 0, then whole seconds `s`: `f"{h}:{m:02d}:{sec:02d}"` when `h = s // 3600` is positive, else `f"{m:02d}:{sec:02d}"`.
  - `parse_stamp(text: str) -> float | None`: `mm:ss` (minutes 0–5999, seconds 0–59) or `h:mm:ss` (minutes and seconds 0–59); anything else `None`.
  - `frame_label(n: int, time_s: float | None) -> str`.
  - `RenderEntry`:
    - `role: Literal["image", "page", "frame"]` (keeping v0.8's additions);
    - `time_s: float | None = Field(default=None, ge=0)`;
    - a validator: only a `frame` carries `time_s`, and a frame has `strips == 1`.
  - `TranscriptEntry(_Strict)`: `name: Literal["transcript.txt"]`, `lines: int = Field(ge=1)`.
  - `RenderFacts`:
    - `kind` gains `"media"`, and v0.8's `Unit` gains `"frame"`;
    - `total: int = Field(ge=0)`, with a validator: `total >= 1` unless kind `media`;
    - `duration_s: float | None = Field(default=None, ge=0)`;
    - a validator: kind `media` if and only if unit `frame`, and then `pages_without_text == []` and `frames is None`.
  - `RenderAnswer.transcript: TranscriptEntry | None = None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_renders.py`):
  - `test_clips_are_media`: `kind_of("Harbour-Tour.MP4") == "media"`, and the same for `voice-memo.opus`; `kind_of("clip.avi") == "none"`. `MEDIA_SUFFIXES` has 11 items, and `set(MEDIA_TYPES) == MEDIA_SUFFIXES`.
  - `test_stamps`:
    - `stamp(84.9) == "01:24"`, `stamp(3599) == "59:59"`, `stamp(3600) == "1:00:00"`, `stamp(3723.4) == "1:02:03"`, `stamp(-2) == "00:00"`;
    - `parse_stamp("01:24") == 84`, `parse_stamp("1:02:03") == 3723`, `parse_stamp("75:00") == 4500`;
    - `parse_stamp` of `"1:60"`, `"1:02:60"`, `"x"` and `""` is `None`.
  - `test_frame_label`: `frame_label(3, 84.0) == "frame 3 (01:24)"`, `frame_label(3, None) == "frame 3"`.
  - `test_media_facts`:
    - `RenderFacts(kind="media", unit="frame", total=0, pages_without_text=[], frames=None, hint=None)` validates, with `duration_s is None`;
    - refused: `total=0` with kind `pdf`; kind `media` with unit `page`; unit `frame` with kind `pdf`; kind `media` with `frames=3`;
    - a stage-1 facts dict without `unit` or `duration_s` still validates.
  - `test_frame_entries_carry_their_time`: a frame with `time_s=4.0` and the name `render_name(1, 1, 1, "image/jpeg")` validates. A page with `time_s` is refused, and so is a frame with `strips=2`. `TranscriptEntry(name="notes.txt", lines=1)` is refused. `MediaRequest(video=True)` validates with both paths `None`; an extra key is refused.
  - `tests/api/test_store.py::test_media_types_are_known`: `_mime` of each suffix is `MEDIA_TYPES`'s value.
- [ ] **Step 2: Run** `uv run pytest tests/test_renders.py tests/api/test_store.py -k "media or stamp or frame" -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** `uv run pytest tests/test_renders.py tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(renders): the kinds, bounds and stamps of a clip`

### Task 2: Derived files that are not pictures, streamed in

**Files:**
- Modify: `workbench/api/store.py`:
  - `_RENDER_NAME` `:77` and v0.8's `_DERIVED_EXTENSIONS` (with its role-to-type rule);
  - `_orphan_ids` `:276`, `register_derived` `:1581`;
  - new: `stage_derived`, `place_derived`, `drop_staged`, `StagedArtifact`, `UnreadableArtifact`.
- Test: `tests/api/test_store.py`

**Interfaces:**
- Produces:
  - `_DERIVED_EXTENSIONS` gains `"text/plain": "txt"`, `"application/x-ndjson": "jsonl"` and `"application/zip": "zip"`; `_RENDER_NAME` matches `([0-9a-f]{32})\.(?:jpg|png|pdf|txt|jsonl|zip)`.
  - v0.8's role-to-type rule gains three pairs: `transcript` only with `text/plain`, `nextext_jsonl` with `application/x-ndjson`, `nextext_zip` with `application/zip`; `frame` takes `image/jpeg` alone. Anything else is `PathError`. The transcript goes in through `register_derived`.
  - `class UnreadableArtifact(StoreError)`: its message starts `unreadable_file: `. `errors.py` maps it to 422 with the message as detail.
  - `ArtifactRecord = Callable[[str, int, Sequence[str], str], object]`, called with the new id, its size, the replaced ids and `ok` or `error`.
  - `@dataclass(frozen=True) class StagedArtifact`: `source: FileRecord`, `spec: DerivedSpec`, `id: str`, `part: Path`, `size: int`.
  - Two calls, so that the body is read before the route waits for the source's lock (a client whose body is not read meanwhile would hit its write timeout):
    - `Store.stage_derived(self, source: FileRecord, src: BinaryIO, spec: DerivedSpec, *, max_bytes: int, quota_bytes: int, check: Callable[[Path], None]) -> StagedArtifact`:
      - under the lock: the source row is there (`Gone`) and its chat is not held (`_refuse_deleted`); it opens `<chat>/.derived/<source-id>/<id>.part` with `O_EXCL | O_NOFOLLOW`, mode 0600, its folders 0700;
      - outside the lock: `_write_part` streams `src` into it (`TooLarge` past `max_bytes`, `QuotaExceeded` past the room), then `check(part)` runs (`UnreadableArtifact`);
      - on any failure the `.part` is removed.
    - `Store.place_derived(self, staged: StagedArtifact, *, quota_bytes: int, record: ArtifactRecord, cap_bytes: int = DERIVED_MAX_BYTES) -> tuple[DerivedRecord, list[DerivedRecord]]`, under the lock:
      - the source and the chat are checked again;
      - `replaced` is the source's rows of `spec.role` plus every `transcript` and `frame` row;
      - usage minus their sizes plus the new size must fit `quota_bytes`, and the source's derived bytes likewise `cap_bytes` (`RendersFull`);
      - then `record(id, size, [ids of replaced], "ok")`; the `.part` is renamed to `<id>.<ext>`; one transaction inserts the new row and deletes the replaced ones; their files are unlinked.
      - A failure after the record records again as `error`. On any failure the `.part`, or the file, is removed.
      - It returns the new record and the replaced ones, by role and ordinal.
    - `Store.drop_staged(self, staged: StagedArtifact) -> None`: removes a `.part` that is not placed; gone is fine.
  - A `<id>.part` or a `<id>.jsonl|txt|zip` in `.derived/<source-id>/` without a row is an orphan, named by its id.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_store.py`; `media_source()` uploads `harbour-tour.mp4` with invented bytes):
  - `test_an_artifact_is_streamed_in_beside_its_clip`: after `stage_derived`, the `.part` is in `.derived/<sid>/`. After `place_derived`, the file is at `.derived/<sid>/<id>.jsonl`, mode 0600, with `created_at == source.created_at`. `record` is called once, with `(id, size, [], "ok")`, while `derived_of(source)` is still empty.
  - `test_an_artifact_past_its_bound_leaves_nothing`: `max_bytes=10` and 11 bytes raise `TooLarge` from `stage_derived`; no `.part`, no row.
  - `test_a_failed_check_leaves_nothing`: the check raises `UnreadableArtifact`, which is raised; nothing is left.
  - `test_a_dropped_stage_leaves_nothing`: `drop_staged` removes the `.part`; a second call is fine.
  - `test_a_second_artifact_replaces_the_first_and_what_was_made_from_it`. Setup: a jsonl, a `nextext_zip` row, and a transcript and a frame through `register_derived`; then a second jsonl.
    - The replaced list is the first jsonl, the transcript and the frame; `record` got their ids.
    - The zip row stays, and the replaced files are gone.
  - `test_replacing_counts_the_replaced_bytes`: `cap_bytes=20`; a 12-byte jsonl, then another 12-byte one, succeeds.
  - `test_an_artifact_needs_its_source_and_its_chat`: the source removed between `stage_derived` and `place_derived` gives `Gone`, and nothing is left; so does a chat deleted in between.
  - `test_an_unrecorded_artifact_is_not_kept`: `record` raises, the error is raised, and there is no row, file or `.part`.
  - `test_the_orphan_sweep_names_parts_and_artifacts_by_id`: `.derived/<sid>/<hex>.part` and `<hex2>.zip` without rows are removed, and `record` receives both ids.
  - `test_a_transcript_is_a_derived_text_file`: `register_derived` with `text/plain` keeps `<id>.txt`.
  - `test_media_roles_need_their_types`: `("transcript", "image/jpeg")`, `("nextext_zip", "application/x-ndjson")`, `("frame", "image/png")` and `("page", "text/plain")` raise `PathError`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_store.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** as in Interfaces; the two calls reuse `_write_part`, `_place` and `_take_staged`'s checks.
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(store): Nextext's artifacts stream in as derived files of their clip`

### Task 3: `POST /files/derived`

**Files:**
- Modify:
  - `workbench/api/files.py` (the route), `workbench/api/auth.py:57-60` (`DERIVED_PATH` in `FILE_PATHS`), `workbench/api/body_limit.py` (its bound);
  - `workbench/api/audit.py:57` (`OPERATIONS["derived"] = ("modify", "file.derived")`), `workbench/api/errors.py:81-91` (`(UnreadableArtifact, 422, None)`);
  - `docs/permissions.md` § File routes, `docs/audit.md` (the `derived` row).
- Test: `tests/api/test_files_derived.py` (new); `tests/api/test_files_ghostwriter.py:278` (`…_five_paths` becomes `…_six_paths`).

**Interfaces:**
- Consumes: Task 2; `JobQueue.source_lock`; `PREPARE_QUEUE_WAIT_S`.
- Produces:
  - `auth.DERIVED_PATH: Final = "/files/derived"`. `body_limit.DERIVED_BODY_MAX: Final = ZIP_MAX_BYTES`, the bound of `POST DERIVED_PATH`, whose 413 detail is `file too large`.
  - `ROLE_TYPES: Final = {"nextext_jsonl": ("application/x-ndjson", JSONL_MAX_BYTES), "nextext_zip": ("application/zip", ZIP_MAX_BYTES)}`.
  - `class _Body`: a `read(n: int) -> bytes` over `request.stream()` for the worker thread. It pulls the next chunk through `anyio.from_thread.run`, so `stage_derived` streams the body as `add_upload` streams an upload; it never buffers more than one chunk.
  - `_artifact_check(role) -> Callable[[Path], None]`: a zip's first four bytes are `PK\x03\x04`; a jsonl is not empty and its first line (to the first `\n`, at most 64 KiB) parses to a JSON object. Otherwise `UnreadableArtifact`, with `unreadable_file: the artifact is empty` or `unreadable_file: the artifact is not what its role says`.
  - `@router.post(DERIVED_PATH) async def store_derived(source: str, role: Literal["nextext_jsonl", "nextext_zip"], request: Request, p, settings, store, audit, jobs: JobsDep) -> dict[str, object]`, in order:
    1. `audit.check("derived", p)`.
    2. `store.get`; a source that is not `kind == "upload"` with `kind_of == "media"` is `422 unsupported_format: the source is not an audio or video upload`, with no event.
    3. `stage_derived` with `DerivedSpec(role, 0, mime, None, None, {})`, the role's bound and its check: the body is read whole into the `.part` first.
    4. Under `jobs.source_lock(rec.id, PREPARE_QUEUE_WAIT_S)`, `place_derived`. Its `record` writes `derived` with file ids `[source, new, *replaced]` and criteria `{"role", "bytes", "replaced": n}`. A `Busy` from the lock, or any other failure before placing, calls `drop_staged` first.
    5. It answers the contract's four keys.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_files_derived.py`; Ghostwriter's headers with a purpose; `make_media` from Task 4 is not needed, since a jsonl body is `b'{"text": "invented"}\n'` and a zip body `b"PK\x03\x04" + bytes(60)`):
  - `test_an_artifact_is_kept_as_a_derived_file_of_its_clip`:
    - the answer's keys are exactly `{"id", "role", "bytes", "replaced"}`, with `replaced` `None`;
    - `derived_of` has one `nextext_jsonl` row, and `GET /files/list` shows no `.derived`;
    - one event `modify` / `file.derived` with ids `[source, new]`, criteria `role`, `bytes`, `replaced: 0`, carrying the purpose and the request id.
  - `test_the_source_must_be_a_clip_upload`: a `.pdf` upload, and a `run_python` output named `.mp4`, each give 422 with a detail starting `unsupported_format:`; nothing is stored and nothing recorded.
  - `test_an_artifact_must_look_like_its_role`: a zip body without `PK\x03\x04`, a jsonl `not json\n`, and an empty body each give 422 starting `unreadable_file:`, and leave nothing.
  - `test_an_artifact_past_its_bound_is_413`:
    - a chunked jsonl of 8 MiB + 1 gives 413 `file too large`, and nothing is left;
    - a declared `Content-Length` of 32 MiB + 1 gives 413 before the route runs (a patched `store.stage_derived` is never called).
  - `test_a_second_artifact_replaces_the_first_and_says_so`: `replaced` equals the first id, and the event's ids are `[source, new, first]`.
  - `test_a_replace_waits_for_the_clips_lock` (Review Focus 3): with the lock held in the test, the whole body is read (the `.part` is complete) and the POST answers only after the release. With `PREPARE_QUEUE_WAIT_S` patched to 0.1 it is 503, and no `.part` is left.
  - `test_derived_needs_a_purpose` (428, `denied`); `test_another_chats_clip_or_a_derived_path_is_404`; `test_the_quota_holds` (quota patched low: 507).
  - `test_docint_cannot_post_artifacts`: docint's key answers as on `POST /files/prepare`.
  - `test_the_file_caller_check_keeps_ghostwriter_to_its_six_paths`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_files_derived.py tests/api/test_files_ghostwriter.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, then the docs:
  - `docs/permissions.md`: the derived row. Ghostwriter's caller only; a clip upload of the request's chat; writes under `.derived/`; `modify` / `file.derived`; kept until the clip goes.
  - `docs/audit.md`: the `derived` row; the target names the replaced ids too.
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(files): POST /files/derived keeps Nextext's artifacts of a clip`

### Task 4: The executor makes a transcript and frames

**Files:**
- Create: `workbench/executor/render/media.py`, `tests/fixtures/make_media.py`, `tests/executor/test_render_media.py`
- Modify:
  - `workbench/executor/runners/render.py:298`: `prepare_file` sends `kind_of(path) == "media"` to `media.prepare_media` before its `is_file` check;
  - `workbench/executor/render/images.py:255`: `normalise(source: Path | BinaryIO, …)`, since `Image.open` takes either;
  - `tests/executor/test_supervisor.py` (one root case).

**Interfaces:**
- Consumes: Task 1; `readers.check_zip`, `readers.UnreadableFile`, `readers.check`, `readers.set_deadline`; `images.normalise`, `encode.encode`.
- Produces in `media.py`:
  - `@dataclass(frozen=True) class Transcript`: `text: str`, `lines: int`, `duration_s: float | None`, `cut_at: float | None`, `skipped: int`.
  - `@dataclass(frozen=True) class Frame`: `n: int`, `time_s: float | None`, `data: bytes`, `width: int`, `height: int`, `mime: str`.
  - `read_transcript(path: Path) -> Transcript`. Raises `UnreadableFile` when a line is not JSON, the file is not UTF-8, or no line survives.
  - `read_frames(path: Path, *, file_mb: int) -> tuple[list[Frame], int]`: the frames kept and the number the zip held. Raises `UnreadableFile(NOT_KEYFRAMES)`, `NOT_KEYFRAMES = "the file is not a set of keyframes"`.
  - `prepare_media(args: dict[str, Any], ctx: JobContext) -> dict[str, Any]`:
    - `args["media"]` is a `MediaRequest`. It reads the inputs it names (`protocol.check_input_path`, below `ctx.work_dir`), and never `args["path"]`, the clip, which is no input.
    - It writes `transcript.txt` and `render_name(n, 1, 1, "image/jpeg")` for each frame.
    - It answers a `RenderAnswer`: kind `media`, unit `frame`, `total` the frames, `frames None`, `pages_without_text []`, plus `duration_s`, `hint`, `renders` (role `frame`, `time_s`) and `transcript`.
    - Its hint joins, in order:
      - `The transcript could not be read.`
      - `The frames could not be read.`
      - `The transcript is cut at {stamp}: it is longer than workbench keeps.`
      - `{k} of the clip's {n} frames are kept, spread evenly.`
      - `NO_SPEECH`, when no transcript input was given;
      - `No frames were taken from this clip.`, for `video` with no zip input.
    - Errors: `unreadable_file` when nothing usable came from the inputs present; `read_timeout`.
- `make_media.py`:
  - `jsonl_record(start: float, end: float, text: str, *, speaker: str | None = None, index: int = 0) -> dict`, in Nextext's line shape: `source_file "media.mp4"`, `source_file_hash "sha256:" + "0" * 64`, `language "en"`, `sentence_index`, `start_seconds`, `end_seconds`, `start_ts`, `end_ts`, `speaker` when given, and `text`.
  - `make_jsonl(path, records) -> Path`.
  - `make_keyframes_zip(path, frames: int = 3, *, size=(64, 36), times: Sequence[float] | None = (0.0, 4.0, 8.0), extra: Mapping[str, bytes] = {}) -> Path`: JPEGs in distinct colours, plus `manifest.json` (`{"frames": [{"file", "index", "time_sec"}]}`) unless `times` is `None`.
  - `jpeg_claiming(width: int, height: int) -> bytes`: a small JPEG whose SOF0 says another size.

The transcript, line by line (the format is fixed here, not by the tests alone):

```python
SPEAKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
for raw in text.splitlines():                                    # UTF-8; a decode error: UnreadableFile(DAMAGED)
    if not raw.strip():
        continue
    record = json.loads(raw)                                     # ValueError/RecursionError: UnreadableFile(DAMAGED)
    start, end, said = record.get("start_seconds"), record.get("end_seconds"), record.get("text")
    if not (_number(start) and _number(end) and isinstance(said, str)) or not (words := " ".join(said.split())):
        skipped += 1
        continue
    speaker = record.get("speaker")
    who = f"{speaker.strip()}: " if isinstance(speaker, str) and SPEAKER.fullmatch(speaker.strip()) else ""
    duration = max(duration or 0.0, end)                         # every record counts, also past a cut
    line = f"[{stamp(start)}] {who}{words}\n"                    # one line per sentence: no newline survives
    if cut_at is None and size + len(line.encode()) > TRANSCRIPT_MAX_BYTES:
        cut_at = start
    if cut_at is None:
        lines.append(line); size += len(line.encode())
```

Every other key (`source_file`, `source_file_hash`, `language`, `sentence_index`, `*_ts`) is dropped; `_number` refuses a bool and a non-finite float.

The frames:
1. `check_zip(path, file_mb)`, then the names. Each member must match `frame_(\d{3,6})\.jpg` or be `manifest.json`; directories, duplicates and any other name refuse the whole zip.
2. The frame names are sorted by their number. `manifest.json` (at most 1 MiB) gives `time_sec` by `file`; a manifest that does not read is ignored, and the frames then have no times.
3. Over `MEDIA_FRAMES_MAX` frames, the kept positions are `round(i * (count - 1) / (MEDIA_FRAMES_MAX - 1))` for `i` in `range(MEDIA_FRAMES_MAX)`.
4. For each kept frame: read at most its declared size, check `b"\xff\xd8\xff"`, then `normalise(BytesIO(data), long_side=FRAME_LONG_SIDE, max_pixels=IMAGE_MAX_PIXELS)` and `encode(..., lossless=False, quality=FRAME_QUALITY, subsampling=2)`. `readers.check()` runs between frames.
5. `n` is the frame's position, from 1.

- [ ] **Step 1: Write the failing tests** (`tests/executor/test_render_media.py`, a `JobContext` as in `tests/executor/test_render.py`).
  - The transcript:
    - `test_a_transcript_has_one_stamped_line_per_sentence`:
      - the records are `(0.0, 2.5, "Welcome aboard.", "Speaker 1")`, `(84.2, 86.0, "The quay opens at nine.", "Speaker 2")` and `(90.0, 91.0, "Thanks.")`;
      - `text == "[00:00] Speaker 1: Welcome aboard.\n[01:24] Speaker 2: The quay opens at nine.\n[01:30] Thanks.\n"`;
      - `lines == 3`, `duration_s == 91.0`.
    - `test_the_transcript_keeps_none_of_nextexts_bookkeeping`: the text holds none of `media.mp4`, `sha256:`, `"en"` or `sentence_index`.
    - `test_a_line_with_a_newline_stays_one_line` (Review Focus 2): the text `"Hello\n[00:00] Speaker 9: forged"` at 5.0 gives one line, `[00:05] Speaker 1: Hello [00:00] Speaker 9: forged`.
    - `test_an_odd_speaker_label_is_left_out`: neither `"Speaker 1\n: x"` nor 60 letters is written as a label.
    - `test_past_an_hour_the_stamp_has_hours`: a start of 3723 gives `[1:02:03]`.
    - `test_a_long_transcript_is_cut_on_a_line`: with `TRANSCRIPT_MAX_BYTES` patched to 100, the text ends with `\n` and holds at most 100 bytes; `cut_at` is set, and `duration_s` is the last record's end.
    - `test_records_without_times_or_text_are_skipped` (`skipped == 2`); `test_a_jsonl_that_is_not_json_lines_is_unreadable`.
  - The frames:
    - `test_frames_are_scaled_to_1280_without_metadata` (Review Focus 5): three 3840 × 2160 frames each give 1280 × 720 JPEG under 768 KiB with no `Exif` in the bytes; `n` is `[1, 2, 3]` and `time_s` `[0.0, 4.0, 8.0]`.
    - `test_frames_without_a_manifest_have_no_times`.
    - `test_a_zip_with_any_other_member_is_not_keyframes` (Review Focus 1), parametrized over `../x.jpg`, `frame_001.jpg.exe`, `notes.txt`, `sub/frame_002.jpg` and a duplicate `frame_000.jpg`: each raises `UnreadableFile(NOT_KEYFRAMES)`.
    - `test_a_png_named_as_a_frame_is_refused`; `test_a_frame_claiming_12000_by_12000_is_refused_within_2_s`.
    - `test_more_frames_than_kept_are_spread_evenly`: 30 frames give 19, from the first to the last, and the held count is 30.
  - The runner:
    - `test_prepare_media_answers_frames_and_a_transcript`:
      - `RenderAnswer.model_validate(answer)` passes;
      - `transcript.lines == 3`, and `out/` holds `transcript.txt` and `p0001.jpg`…`p0003.jpg`;
      - `json.dumps(answer)` holds neither `/workspace` nor `media.mp4`.
    - `test_a_bad_transcript_leaves_the_frames` and `test_bad_frames_leave_the_transcript`, each with its hint sentence.
    - `test_nothing_usable_is_unreadable_file`.
    - `test_audio_says_nothing_of_frames`: `voice-memo.m4a` with a jsonl only gives `total 0` and a hint that names no frames.
    - `test_a_video_without_frames_says_so`.
    - `test_the_clip_itself_is_never_read`: the work dir holds no clip, and the runner still answers.
  - Root (`tests/executor/test_supervisor.py`), `test_as_root_a_clips_artifacts_are_prepared_in_a_slot`:
    - the real entry point, `tool="prepare_file"` with `path` `harbour-tour.mp4`, production limits;
    - the two artifacts written at `.derived/<sid>/<id>.jsonl` and `.zip` paths of the chat folder, as v0.8's root case stages its converted PDF, and named in `args["media"]`;
    - `ok`, and the staged outputs match the answer.
- [ ] **Step 2: Run** `uv run pytest tests/executor/test_render_media.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** `uv run pytest tests/executor -q`, then `make test-executor-root`. Expected: PASS in both shapes.
- [ ] **Step 5: Commit** `feat(executor): a clip's transcript and frames from Nextext's artifacts`

### Task 5: The queue and the api prepare a clip

**Files:**
- Modify:
  - `workbench/api/jobs.py`: `submit_renders` `:647`, `submit` `:589`, `_render_place` `:1448`;
  - `workbench/api/tools_mcp.py` (`CallJobs` passes the new arguments);
  - `workbench/api/renders.py`: `Manifest` `:102`, `prepared` `:353`; new `media_manifest_of`.
- Test: `tests/api/test_jobs.py`, `tests/api/test_renders_media.py` (new), `tests/api/test_files_renders.py`

**Interfaces:**
- Consumes: v0.8's `submit_renders(..., converted=)`: a derived row as a job's input at its own `relative_path`, set from the record, never from a caller's string (stage-2 D3). Tasks 1–4; the tests add artifacts with Task 2's `stage_derived` and `place_derived`.
- Produces:
  - `JobQueue.submit_renders(..., artifacts: Sequence[DerivedRecord] = ())`, likewise on `CallJobs` and `RouteJobs`, beside v0.8's `converted`:
    - each must be a row of the source of role `nextext_jsonl` or `nextext_zip` (else `ValueError`);
    - with any, the job's inputs are their `relative_path`s alone, never the source; `path` stays the source's name;
    - `args["media"]` carries their paths by role (`MediaRequest`).
  - `_render_place`:
    - a `frame` entry gets `DerivedSpec("frame", n, mime, w, h, {"time_s": t} if t is not None else {})`;
    - the answer's `transcript` is kept from the output `transcript.txt` (regular, under `TRANSCRIPT_MAX_BYTES + 1`, else `invalid`) as `DerivedSpec("transcript", 0, "text/plain", None, None, {"lines": n})`;
    - the facts of a job that looked for them go on the transcript, else on frame 1;
    - facts are read with `answer.model_dump(exclude={"renders", "transcript"})`.
  - `Manifest` gains `duration_s: float | None = None` and `transcript: bool = False`.
  - `media_manifest_of(source: FileRecord, rows: Sequence[DerivedRecord]) -> Manifest | None`:
    - the head is the transcript row, else the frame with `n == 1`, with readable facts (`_facts`, lenient);
    - `renditions` are the frame rows by `n`, and `transcript` says whether a transcript row exists;
    - `None` without a head.
  - `prepared()` for kind `media`:
    - a manifest from the rows, when there is one;
    - else, without a `nextext_*` row, `Manifest(kind="media", unit="frame", total=0, pages_without_text=[], renditions=[], frames=None, hint=NOTHING_FOUND)`, with no job and no event;
    - else, under the source lock, one job: `args {"media": MediaRequest(...), "budget_s": _budget(settings), "facts": True}`, `artifacts` the raw rows, `video` from `VIDEO_SUFFIXES`, and the same keeper (`modify` / `file.prepare`).

- [ ] **Step 1: Write the failing tests** (`tests/api/test_renders_media.py`, a `Local` executor; the artifacts go in with `stage_derived`/`place_derived` and `make_media`):
  - `test_a_clip_with_its_artifacts_is_prepared_once`:
    - the manifest: kind `media`, unit `frame`, `total 3`, `renditions` n 1–3, `duration_s 91.0`, `transcript True`, `frames None`, `pages_without_text []`;
    - one `modify` / `file.prepare` event naming the source, the transcript and the three frames;
    - a second `prepared` sends no request and writes no event.
  - `test_the_clip_never_enters_the_job`: the request's `input_files` are exactly the two artifacts' `relative_path`s, and the clip's name is not among them.
  - `test_audio_with_a_transcript_has_no_frames`: an `.m4a` with a jsonl gives `total 0`, `transcript True`, `renditions []`.
  - `test_a_silent_video_has_frames_and_no_transcript`: `transcript False`, and the facts are on frame 1.
  - `test_a_clip_without_artifacts_answers_at_once`: no request, no event, `hint == NOTHING_FOUND`.
  - `test_unusable_artifacts_are_422`: a zip with `notes.txt` and a jsonl `not json` give `HTTPException` 422, a detail starting `unreadable_file:`, and no `transcript` or `frame` row.
  - `test_a_replaced_artifact_is_prepared_again` (Review Focus 3): prepare; a new jsonl placed; prepare again, which makes one new job.
  - `test_stored_media_facts_are_read_leniently`: a head whose facts hold an unknown key still gives the manifest.
  - `test_a_4k_clip_fits_its_cap` (Review Focus 5): a zip of 19 noise frames of 3840 × 2160 (JPEG quality lowered at build time until the zip is under `ZIP_MAX_BYTES`) and a jsonl of about 7 MiB prepare without `RendersFull`, and the sum of `derived_of`'s sizes stays under `DERIVED_MAX_BYTES`.
  - `tests/api/test_jobs.py`:
    - `test_a_clips_artifacts_are_its_jobs_only_inputs`; `test_an_artifact_of_another_source_is_refused` (`ValueError`);
    - `test_a_frame_keeps_its_time` (`meta["time_s"] == 4.0`);
    - `test_a_transcript_over_its_cap_is_not_kept` (lost as `invalid`, and `again` names its id).
  - `tests/api/test_files_renders.py`:
    - `test_prepare_of_a_clip_answers_the_media_manifest`: the keys are exactly the contract's ten;
    - `test_every_manifest_has_duration_and_transcript`: a PDF gives `null` and `false`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_renders_media.py tests/api/test_jobs.py tests/api/test_files_renders.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS; the PDF, picture and Office tests are unchanged but for the two new keys.
- [ ] **Step 5: Commit** `feat(api): a clip is prepared from its artifacts, and never enters a job itself`

### Task 6: `view_file` shows a clip's frames

**Files:**
- Modify:
  - `workbench/api/renders.py` (`frame_window`, `FRAME_GRAMMAR`), `workbench/api/tools_views.py` (`ViewedFile.kind`; the `view_file` media branch), `workbench/api/files.py` (`_view_rendition` `:338` for a clip);
  - `workbench/api/tool_table.py:175-189` (the `view_file` description), `tests/api/golden/ghostwriter-modern.json`, `docs/permissions.md` (the `view_file` row).
- Test: `tests/api/test_tools_views.py`, `tests/api/test_files_renders.py`

**Interfaces:**
- Consumes: Tasks 1 and 5; `read_render`, `image_block`, `MAX_VIEW_IMAGES`, `MAX_SHOWN_BYTES`.
- Produces:
  - `FRAME_GRAMMAR: Final = "no_such_part: give a frame as 'frame 3', frames as 'frames 2-5', or a time as 'at 01:24'"`.
  - `frame_window(part: str | None, total: int, times: Mapping[int, float]) -> list[int]`:
    - `None`: all frames when `total <= 8`, else `[1 + round(i * (total - 1) / 7) for i in range(8)]`; `[]` for `total == 0`.
    - `(?:frames?\s*)?(\d{1,9})(?:\s*-\s*(\d{1,9}))?`, in any case: N to `min(M, total)`. An N outside `1..total` is `no_such_part: this clip has {total} frames`, or `… has no frames`.
    - `at\s+(\S+)`, read by `parse_stamp`: the frame whose time is nearest, the earlier one on a tie; a time past the last frame gives the last. Without times it is `no_such_part: the frames of this clip carry no times; ask for 'frame N'`.
    - Anything else: `FRAME_GRAMMAR`. Every refusal is `HTTPException(422)`.
  - `ViewedFile.kind` gains `"media"`, and its `unit` (v0.8) gains `"frame"`. `shown_frame(rec: DerivedRecord) -> ShownImage` labels a frame with `frame_label(rec.ordinal, rec.meta.get("time_s"))`, beside v0.8's `shown_image(noun, rec)`.
  - The `view_file` media branch:
    - `prepared`, then the frames from the rows, with no job (they all exist).
    - It takes frames in window order while there are at most 8 images and 2.5 MiB. Cut short, it is `truncated` with `next_part = "frames {n}-{last}"`. For the default spread over more than 8 frames, the hint is `8 of the clip's {total} frames are shown, spread over it; ask for 'frames 2-5' or 'at 01:24' to see others.`
    - Labels are `frame_label(n, time_s)`. A frame `read_render` refuses is skipped and named (`frame 7 could not be shown`).
    - `total 0`: `shown []`, with the hint `This clip has no pictures; read its transcript with read_document.` (with a transcript) or `NOTHING_FOUND`.
    - Recorded as `view_file`, naming the source and each frame shown, criteria `{"images": n, "strips": 1, "paged": part is not None}`.
  - The rendition view of a clip serves frame `n` whole, recorded as any rendition view. `strips > 1` is `422 a clip's frames are not cut into strips`; `n` outside `1..total` is 404.
  - The description gains: "Video: a clip's frames, 'frame 3', 'frames 2-5' or 'at 01:24' (the frame nearest that time); left out, up to 8 frames spread over the clip. Audio has no pictures: read its transcript with read_document."

- [ ] **Step 1: Write the failing tests.**
  - `test_frame_window_grammar`, each assertion exact:
    - `(None, 5, {})` gives `[1, 2, 3, 4, 5]`; `(None, 19, {})` gives `[1, 4, 6, 9, 11, 14, 16, 19]`; `("frame 3", 19, {})` gives `[3]`; `("FRAMES 18-40", 19, {})` gives `[18, 19]`;
    - `("at 01:24", 3, {1: 0.0, 2: 80.0, 3: 90.0})` gives `[2]`; `("at 99:00", 3, …)` gives `[3]` (past the end: the last);
    - `("at 00:10", 3, {})` is 422 "carry no times"; `("page 2", 3, {})` and `("at 1:75", 3, …)` are 422 `FRAME_GRAMMAR`; `("frame 20", 19, {})` is 422 "this clip has 19 frames".
  - `test_view_file_shows_eight_frames_spread_over_a_clip`: 19 frames give 8 blocks with `[s["n"] for s in shown] == [1, 4, 6, 9, 11, 14, 16, 19]`, and the spread hint.
  - `test_frame_labels_carry_their_time`: `shown[0]["label"] == "frame 1 (00:00)"`, or `"frame 1"` without times.
  - `test_frames_cap_at_2_5_mib`: frames patched to about 700 KiB with `part="frames 1-8"` give 3 blocks, `truncated`, and `next_part == "frames 4-8"`.
  - `test_view_file_of_audio_says_read_the_transcript`: no image block, and that hint.
  - `test_view_file_of_a_clip_is_recorded_before_the_frames_go`: the ids are `[source, *frames shown]`; with `broken_audit` no block is sent.
  - `test_a_tampered_frame_is_skipped_with_a_hint`; `test_strips_are_ignored_on_a_clip`.
  - `tests/api/test_files_renders.py`: `test_a_frame_rendition_is_served_whole` (`image/jpeg`, the hardening headers, one `disclose` / `file.view` naming the source and the frame); `test_a_frame_rendition_with_strips_is_422`; `test_a_frame_past_the_clip_is_404`.
  - The existing `view_file`-not-listed-without-the-header test stays green.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_tools_views.py tests/api/test_files_renders.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, then:
  - the description, and the golden by hand: `view_file`'s description and `kind` enum;
  - the `docs/permissions.md` row: a clip's frames, read from its renders, never decoded in the api.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): view_file shows a clip's frames, by number or by time`

### Task 7: `read_document` reads a clip's transcript

**Files:**
- Modify:
  - `workbench/api/tools_views.py:791` (`read_document`: the media branch comes before the `images` switch);
  - `workbench/api/tool_table.py:102-130` (both descriptions of `read_document`), both `tests/api/golden/ghostwriter-*.json`;
  - `docs/permissions.md` § What the model is told, `docs/audit.md` (a clip's read names the transcript).
- Test: `tests/api/test_tools_documents.py`

**Interfaces:**
- Consumes: Tasks 1 and 5. A derived row's `relative_path` as a job input, as v0.8 passes its converted PDF.
- Produces:
  - `SEE_FRAMES: Final = "Its {total} frames can be looked at with view_file ('frame 3', 'at 01:24')."`
  - `read_document` on kind `media`, whatever `images` is:
    - `prepared` first.
    - With a transcript row, one read job: `jobs.submit(p, "read_document", {"path": transcript.relative_path, "part": body.part, "budget_s": …, "images": False}, [transcript.relative_path])`. The path comes from the row, never from the model.
      - The answer is the runner's, with `path` the clip's virtual path and `kind = "transcript"`. Its hint is followed by `SEE_FRAMES`, only when `images` is set and `total > 0`.
      - Recorded as `read_document` naming `[clip, transcript]`, criteria `{"paged", "images": 0}`.
    - Without a transcript, no job: `DocumentRead(path, kind="transcript", parts=[], total_parts=0, pages_without_text=[], truncated=False, hint=…)`. The hint is `NO_SPEECH` (plus `SEE_FRAMES` as above), or the manifest's `NOTHING_FOUND` when it has no frames either. Recorded naming `[clip]`.
    - A refusal of the read job is recorded and raised as today.
  - Both descriptions of `read_document` gain, after "Readable": "video and audio (mp4, mov, m4v, webm, mkv, mp3, m4a, wav, ogg, opus, flac): their transcript, one line per sentence, as '[01:24] Speaker 1: text'."

- [ ] **Step 1: Write the failing tests:**
  - `test_a_clip_reads_as_its_transcript`:
    - `parts[0]["label"] == "lines 1-3"`, its text the three stamped lines of Task 4; `kind == "transcript"`, `path` the clip's; `shown == []` and no image block;
    - the event `query` / `tool.read_document`, ids `[clip, transcript]`, `images: 0`;
    - the read job's only input is the transcript's `relative_path`, and nothing in the answer names a `.derived` path.
  - `test_a_clip_reads_the_same_without_the_header` (Review Focus 4): without `X-Accept-Images`, the same text and no image block; `tools/list` has no `view_file`; the hint does not name `view_file`.
  - `test_a_clip_without_speech_says_so`: `parts == []`, and the hint starts `No speech was found in this clip.` With frames and the header it names `view_file`.
  - `test_a_long_transcript_pages_by_lines`: following `next_part` reads on.
  - The goldens: both match the served listings (`tests/api/test_tools_golden.py`).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_tools_documents.py tests/api/test_tools_golden.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; edit both goldens by hand.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): read_document reads a clip's transcript, with or without images`

### Task 8: Docs, isolation, release

**Files:**
- Modify:
  - `CLAUDE.md`: the sixth file route, the media branch, the `MEDIA_*` rules in `renders.py`, `executor/render/media.py` in the module list, and "the clip never enters a job".
  - `README.md` (one line: clips).
  - `docs/data-flow.md`:
    - artifacts in from Ghostwriter;
    - the transcript and the frames as derived files of the clip;
    - what leaves: the transcript's text through `read_document`, frames through `view_file` and rendition views;
    - Nextext's bookkeeping (file name, hash, language) is dropped.
  - `docs/retention.md`: a clip's artifacts and what was made from them go with it, in its record; a replaced artifact is recorded.
  - `docs/sandbox.md`:
    - § Residual risks: a keyframes zip is checked before any member is read (`check_zip`, the names, the JPEG signature, 120 MP); frames are decoded by Pillow's JPEG decoder alone; the transcript is parsed as JSON lines; the clip never enters the executor.
    - § What a job can import: no change.
  - `docs/configuration.md`: no new variable. A caps table: jsonl 8 MiB, zip 32 MiB, transcript 4 MiB, frames 19 × 1280 px. The quota: a 50 MiB clip with its artifacts and renders holds up to about 110 MiB of the 500 MiB.
- Modify: `tests/isolation/test_isolation.py`, a new case `test_a_clips_artifacts_are_prepared_read_and_viewed_in_the_sandbox`, in order:
  1. upload `harbour-tour.mp4` (invented bytes);
  2. `POST /files/derived` with a `make_jsonl` and a `make_keyframes_zip`;
  3. `POST /files/prepare` (kind `media`, `total 3`);
  4. `read_document` over MCP (the stamped lines);
  5. `view_file` `at 00:04` (one block, label `frame 2 (00:04)`);
  6. delete the chat, after which `GET /files/view?rendition=1` is 404.
- Modify: `pyproject.toml` (`version = "0.9.0"`), `uv.lock`.

- [ ] **Step 1: Write the isolation case**, following the file's prepare case.
- [ ] **Step 2: Docs.** Run `uv run pytest tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 3: Version.** `uv lock`, then `uv run pytest tests/test_version.py -q`. Expected: PASS.
- [ ] **Step 4: Gates.** `make verify && make test`, then `make test-executor-root` and `make test-isolation`. Expected: each green; paste the summary lines into the PR.
- [ ] **Step 5: Commit** `docs: clips, their artifacts and their frames in the design record and the references`, then `chore(release): 0.9.0`.
- [ ] **Step 6: Push and PR.** `git push -u origin feat/vision-stage3`; a PR to `main`.
  - **Operators** line: "0.9.0 adds `POST /files/derived` (Ghostwriter's caller only) and prepares video and audio from Nextext's artifacts. No new variable. Ghostwriter 0.5.0 uses it; older callers see no change but a sentence on clips in `read_document`'s description."
  - Confirm CI ran on the last commit.

---

## Self-review

- **Spec coverage.** Spec §7.2 asks for four things, covered by these tasks:
  - Media: Nextext's raw artifacts as derived files (Tasks 2–3); the zip checked like any zip, frames at 1280 px, `transcript.txt` (Task 4);
  - REST: `POST /files/derived` (Task 3); the manifest's duration (Task 5);
  - MCP: `view_file` with `frame N` and `at mm:ss` (Task 6); `read_document` on a clip (Task 7);
  - Derived files: hidden, deleted with the source, the 64 MiB cap and the quota — v0.7's, reused (Tasks 2, 5).
  - Retention needs no new code: replaced rows are recorded in Task 3, the docs come in Task 8.
- **Type consistency:**
  - `MediaRequest`, `TRANSCRIPT_NAME`, `NOTHING_FOUND` and `NO_SPEECH` are defined in Task 1 and used in Tasks 4–7.
  - `stage_derived`, `place_derived`, `drop_staged`, `StagedArtifact` and `ArtifactRecord` are Task 2's, used in Tasks 3 and 5.
  - `artifacts` on `submit_renders` is Task 5's; Task 7 passes the transcript's `relative_path` to `submit` as v0.8 passes its PDF.
  - `frame_window` and `FRAME_GRAMMAR` are Task 6's.
- **Proportion:** code appears only for the transcript loop (the line format) and the frame selection order. Everything else is signatures, values and assertions.
