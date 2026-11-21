# Design: Ghostwriter sees images, pages, slides, video and audio

Date: 2026-11-20
Status: approved by the owner

## 1. Why

Ghostwriter cannot see. A file reaches the model only as the line `[Attached: name (path, size)]`, and the model reads it
through workbench's tools, which answer with text only:

- Images and videos are refused at upload (415).
- `read_document` reads a PDF's text layer only. A scanned page or a slide that is only a picture is listed in
  `pages_without_text` and never shown.
- `WorkbenchClient._decode` keeps only the first text part of a tool answer; an image block would be dropped.
- Every message Ghostwriter sends to the router is a string.

The models can see. `Qwen/Qwen3.8-27B-FP8` (vllm-service's default) and `gemma4:31b-cloud` (Ollama) both take images.
docint and Nextext already send images to them as base64 data URLs.

**Goal.** Ghostwriter offers visual intelligence as today's chat apps do. A user should notice no difference in speed or
quality between attaching a text file and attaching a picture, a scan, a deck or a clip. The limit is the model's, not
Ghostwriter's.

## 2. Goals, non-goals, anti-goals

### Goals

- **Images** (png, jpg, webp, gif, bmp, tiff, heic) are attached by picking, dropping or pasting a screenshot, and go
  straight into the user's message. No tool round.
- **Documents: text first, pictures where they carry the meaning.**
  - `read_document` reads the text as today.
  - Pages without text (scans, picture-only slides) come back as images in the same tool step, so a scanned PDF costs
    no more rounds than a text PDF.
  - For questions about how something looks (layout, design, charts, handwriting) the model looks at pages with a new
    tool, `view_file`.
- **Office designs.** Slides and pages of pptx, potx, docx, dotx, odt and odp are rendered as the user would see them.
- **Video and audio.** A clip is transcribed and sampled into keyframes by Nextext. The model reads the transcript and
  sees the frames.
- **Thumbnails** of attached images in the composer and in the user's bubble, with a lightbox.

### Non-goals

- Image generation or editing.
- Native video input (`video_url`): frames work on every vision model, video input on few.
- Raising the 50 MiB per-file cap. A phone clip of about a minute fits; longer clips need a shared cap change later.
- A model picker.
- OCR as a separate service. The chat model reads the page image.

### Anti-goals

The change has failed, even if it works, if any of these happen:
- Ghostwriter stores file bytes, or anything derived from a file outlives the file.
- A file is decoded, rendered or parsed outside workbench's executor (Nextext's media pipeline excepted, §5).
- A picture attached to a chat is visible in another chat or to another user.
- An image's metadata (GPS, camera, author) reaches the model.
- A Nextext job outlives its processing.
- An image attachment is noticeably slower to an answer than a text attachment.
- Anything is fetched at runtime.

## 3. Constraints

- **Inference** goes only through `OPENAI_API_BASE` with `X-Cache-Scope` and `X-Request-ID`. Images travel as
  `image_url` parts with base64 data URLs; that is what both vLLM and Ollama accept.
- **File processing** runs only in workbench's executor: network-less, under its seccomp filter, `RLIMIT_AS` 1536 MB,
  120 s wall. Media is the one exception and goes to Nextext, the federation's media tier (§5, ADR 0009).
- **Ghostwriter stores no file bytes**, and its audit records references only.
- **One process** in Ghostwriter.
- **Airgap-first.** Libraries and fonts are baked into images.

## 4. Approaches

- **A. Images inline, documents text first, pictures where needed (chosen).** Attached images go into the user's message
  as image parts. Documents keep the text tool; pages without text come back as images in the same step; `view_file`
  shows any page. Renders are made in workbench at attach time and kept as derived files.
- **B. Every document page as an image too, up front** (as Claude does for PDFs). The model never has to decide to look.
  But at the default 40k context a page costs 270–1,800 tokens, so about 20 pages crowd out the conversation, and the
  first answer on a long document comes later. Rejected by the owner in favour of A.
- **C. Everything through a tool.** The model calls a tool for each picture. Every image then costs an extra model round
  (seconds), and a model sometimes answers without looking. Rejected: it fails the "no difference" goal.
- **D. Describe images to text first** (captions or OCR, as docint does at ingest). Lossy, and a second model call per
  image. Rejected.

For media:
- **Nextext (chosen).** It already transcribes (Whisper, long recordings) and samples keyframes with dedup; docint uses
  it the same way.
- **Frames in workbench with PyAV, no audio.** No new seam, but what is said in a clip would be lost, audio files would
  not work, and the federation would have a second media pipeline.

## 5. The owner's decisions

1. **Images inline** in the user's message, as in ChatGPT (2026-11-20).
2. **Documents: text first, image on demand** (approach A), with pages without text shown in the same tool step
   (2026-11-20).
3. **Video and audio through Nextext**, as docint does (2026-11-20), on these conditions:
   - Ghostwriter streams the file from workbench to Nextext; nothing is spooled in Ghostwriter.
   - It fetches the transcript and keyframes and deletes the Nextext job at once.
   - The artifacts are stored in workbench as derived files of the clip and expire with it.
   - Without Nextext, video and audio are not offered; everything else works.
   - The 50 MiB cap stays for now.

## 6. What the spikes settled (2026-11-20)

Throwaway scripts, synthetic files only.

**S2, image placement and cost** (Ollama on the dev host):
- gemma4 read 1, 4 and 8 images in the user's message correctly, in 0.5–1.4 s.
- An image inside a `role: tool` message made gemma4 produce garbage. The same image in a user message after the tool
  messages was read correctly. Qwen3.5 on Ollama handled both.
  → **Images from tools go into one synthetic user message after the round's tool messages**, never into the tool
  message.
- Cost per image: gemma4 a fixed ~273 tokens at any size; Qwen3.5 about one token per 32×32 pixels (a 1600 px A4 page
  ≈ 1,760 tokens).
- gemma4 misread a 10 pt line on a whole A4 page at every size, and read it exactly when the page came as two or three
  horizontal strips (6 % overlap, ~550 tokens).
  → For models with a fixed cost per image, a document page goes as two strips.
- A text-only model (gpt-oss) behind Ollama returns no error for an image; it answers that it cannot see it. Ollama's
  `/api/show` lists `vision` among a model's `capabilities`.
  → On Ollama, Ghostwriter reads the capability at start; on vLLM, a 400 for images triggers one retry without them.
- vLLM with Qwen3.8 could not be tested on the dev host; the staging host checks it.

**S1, LibreOffice in the executor sandbox** (Docker, arm64):
- `soffice --headless --convert-to pdf` fails under workbench's seccomp filter: its single-instance pipe needs
  `socket(AF_UNIX, SOCK_STREAM)`, which the filter refuses.
- **LibreOfficeKit in-process** (`libreofficekit_hook_2` in `libmergedlo.so`, `documentLoadWithOptions`,
  `saveAs(pdf)`) needs no pipe and runs under the **unchanged** filter, as an unprivileged uid with `RLIMIT_AS`
  1536 MB and no network:
  - a 30-slide pptx in 2.8 s, the same as potx in 2.6 s, a 24-page docx in 1.8 s, from a cold profile;
  - peak RSS 237 MB;
  - charts render, Calibri is replaced by the metric-compatible Carlito.
- The apt layer (LibreOffice nogui, Carlito, Caladea, Liberation, DejaVu, Noto) adds 515 MB to the executor image.
- LibreOfficeKit initialises once per process, so each conversion is its own job process, which is how the executor
  runs jobs anyway.

**S3, the Nextext round trip,** needs a Whisper endpoint and runs on the staging host before stage 3 ships.

## 7. Design

### 7.1 Parts

| Part | Change |
|---|---|
| workbench | derived files; a prepare runner (images, PDF, Office via LibreOfficeKit, Nextext artifacts); `POST /files/prepare`, `POST /files/derived`, `GET /files/view?rendition=`; the MCP tool `view_file`; `read_document` returns images for pages without text |
| Ghostwriter backend | image, document and media kinds; attachment processing and status; image parts in the turn; Nextext client |
| Ghostwriter UI | thumbnails, processing state, paste a screenshot, lightbox, `view_file` in the trail |
| infra-ui | `AttachmentTile`, `ImageLightbox`, drawn icons for image, video, audio, document |
| Nextext | none in code; a service token for Ghostwriter, docs |
| vllm-service | docs: vision, `CHAT_LIMIT_MM_PER_PROMPT`, context size |
| deploy | ADR 0009, env-sync for the Nextext token, runbooks |

### 7.2 workbench

**Derived files.** A new table `derived` (id, source_id, caller, user_id, chat_id, role, ordinal, name, size, mime,
width, height, meta, created_at), not a new `files.kind`: `kind` has a `CHECK` and the store has no migrations, while a
new table needs only `CREATE TABLE IF NOT EXISTS`.
- `created_at` is the source's, so retention takes both at the same moment and nothing outlives its source.
- The bytes live under `<chat>/.derived/<source-id>/`. The name is reserved: no upload or output may take it, and
  `list_dir`, `files_in_chat`, `list_files`, `search_files` and `run_python`'s inputs never see it.
- Deleting a chat deletes its derived rows in the same transaction; retention names derived ids in its
  `retention.sweep` event and sweeps rows whose source is gone.
- At most 64 MiB of derived files per source; they count against the user's workspace quota.

**The prepare runner** (a new executor runner) makes the renders:
- **Images:** decode (Pillow; HEIC with a decode-only plugin), pixel cap against bombs, `exif_transpose`, sRGB, alpha
  on white, first frame only. Long side at most 2048 px and about 2.4 MP, above what Claude or ChatGPT send, so never
  the bottleneck. JPEG q90, or PNG for lossless sources, each under 768 KiB. **All metadata is dropped.**
- **PDF:** page count and text presence per page (pdfium); the first 20 pages rendered at 1600 px long side; later pages
  on demand. Strips are cut from the page render when asked for.
- **Office:** LibreOfficeKit converts to `converted.pdf` (a derived file), then pages as for PDF. A baked profile
  (macros and link updates off) is copied into the job, as matplotlib's cache is.
- **Media:** from Nextext's raw artifacts (stored as derived files): the keyframes zip is checked like any zip and its
  frames scaled to 1280 px; the transcript becomes `transcript.txt` with `[mm:ss] SPEAKER: text` lines.
- Prepare jobs have their own gate (one at a time across Ghostwriter's users, within its two slots, with a long queue
  wait), so a slow conversion never turns a user's tool call into `wait_timeout`.

**REST** (Ghostwriter's caller only):
- `POST /files/prepare?path=` is idempotent and answers with a manifest (kind, total, pages without text, sizes,
  duration).
- `POST /files/derived?source=&role=nextext_jsonl|nextext_zip` streams an artifact in; the source must be an audio or
  video upload of the chat.
- `GET /files/view?path=&rendition=n[&strips=k]` serves a render with the view hardening.

**MCP:**
- **`view_file`** (`path`, `part` in `read_document`'s grammar plus `frame N` / `at mm:ss`) answers with structured
  text and MCP image blocks, at most 8 images and 2.5 MiB. The api reads the renders from disk; images never cross the
  1 MiB executor line. It also shows outputs, so the model can look at a chart it made.
- **`read_document`** attaches the renders of pages without text inside its window (at most 4 images); if one does not
  fit, the window ends before that page. On an image it returns the image; on a clip, the transcript.
- The workspace instructions change from "never as bytes" to: files never as bytes; pages and pictures as images for
  you to see.

### 7.3 Ghostwriter backend

**Attachments** get a `kind` (document, image, media), a `status` (processing, ready, failed), a `render` manifest
(metadata only) and a `failure` token: schema version 2, one migration step.
- Text formats are ready at once. Images, PDFs, Office files and media are processed after the upload answers 201.
- An `AttachmentProcessor` (asyncio tasks in the one process) calls prepare, or for media runs the Nextext round trip
  and then prepare. A document whose prepare fails stays usable as text; an image whose prepare fails is `failed`.
- The SPA follows the status by long-poll. A message with an attachment that is not ready is refused (409).
- Each hand-over is recorded first (`disclose`, recipient `workbench:prepare` or `nextext:transcribe`), and the Nextext
  job's deletion is recorded as `delete`.

**The turn:**
- A ready image attachment is fetched as its rendition and goes into the user's message as an `image_url` part, after
  the attachment line, which now carries facts (`image 2048×1536`, `24 slides; slides 3, 7 have no text`,
  `video 01:12, transcript and 12 keyframes`).
- Tool images (`view_file`, `read_document`) go into one synthetic user message after the round's tool messages, with a
  legend naming the call and saying it is not the user speaking. `tool_steps` store only the text and
  `[N images were shown]`.
- **Budget:** an image counts by `GHOSTWRITER_IMAGE_TOKENS` (unset: one token per 32×32 pixels; a number: a fixed cost,
  280 for gemma), never by characters. Over budget, the current turn's oldest tool images go first (a note takes their
  place), then the existing clearing of tool results and arguments. Earlier turns are replayed newest first with their
  attached images while the budget and `GHOSTWRITER_MAX_IMAGES` (default 8) allow; otherwise a note says `view_file`
  shows them again.
- Data URLs are set into the messages only just before the call; they never reach a log, the store or the audit.
- **Capability:** `GHOSTWRITER_VISION` (on by default). On Ollama, `/api/show` without `vision` turns it off at start.
  Against a workbench without `view_file`, images are not offered. With vision off, nothing changes from today.
- **Refusal:** a round that carried images and fails with a client error is retried once without them; the SPA shows a
  notice.
- `ai_call` records the number of images, never their content.

**Media** (`NextextClient`): Bearer service token, purpose and request id forwarded. `GET /files/view` is streamed into
`POST /jobs` under a neutral name (`media.<ext>`, since Nextext writes the name into its transcript), the job is polled,
both artifacts are streamed into `POST /files/derived`, and `DELETE /jobs/{id}` runs in a shielded `finally`. Nextext
keeps jobs until deleted, so Ghostwriter also deletes its own jobs that no task owns, at start and in the daily sweep.
Media is offered only while `NEXTEXT_API_BASE` and `NEXTEXT_API_KEY` are set and a background health probe passes.

### 7.4 Ghostwriter's UI and infra-ui

- infra-ui gains `AttachmentTile` (thumbnail or drawn icon, status overlay, remove), `ImageLightbox` (dialog, Escape,
  focus trap) and drawn icons for image, video, audio and document.
- The composer shows a local thumbnail at once (an object URL), then the processing state; send waits until every file
  is ready. A lone pasted screenshot attaches as `screenshot-<date>-<time>.png`; text copied with a picture stays text.
- The user's bubble shows attached images as a grid, fetched like result images, opening the lightbox.
- The trail names `view_file` steps. English and German strings stay in parity.

### 7.5 The federation

- **ADR 0009** records the media seam and the images to the router.
- **Nextext:** a `ghostwriter=<token>` entry in `NEXTEXT_SERVICE_TOKENS`; both backends are already on `inference-net`,
  so no network changes. `NEXTEXT_JOB_CONCURRENCY=1` queues Ghostwriter's clips behind docint's ingest.
- **deploy:** env-sync gets a group for the token; `load_table` must allow two entries of one key.
- **vllm-service:** docs for vision: `CHAT_LIMIT_MM_PER_PROMPT` at least `GHOSTWRITER_MAX_IMAGES` (and docint's
  `VISUAL_ANSWER_MAX_IMAGES`), `CHAT_MAX_MODEL_LEN` at least `GHOSTWRITER_CONTEXT_TOKENS`.
- **Bring-up order** is unchanged. Ghostwriter starts without Nextext; media appears once the probe passes.

## 8. Delivery order

1. deploy: this spec and ADR 0009. infra-ui: the tile, lightbox and icons, released.
2. **Stage 1, images and PDFs.** workbench v0.7.0 (derived files, prepare for images and PDF, the routes, `view_file`,
   `read_document` images), then Ghostwriter (kinds, processing, the turn, the UI). Both orders are safe: Ghostwriter
   detects the capability (workbench lists `view_file`), and workbench shows images only to a call that carries
   `X-Accept-Images: 1`, which Ghostwriter v0.3.0 sends while it offers images. An older Ghostwriter next to
   workbench v0.7.0 is told and shown exactly what v0.6.0 told and showed it.
3. **Stage 2, Office designs.** workbench v0.8.0 (LibreOfficeKit, fonts). Ghostwriter needs no change.
4. **Stage 3, video and audio.** S3 on the staging host; deploy's env-sync; Nextext and vllm-service docs; workbench
   `/files/derived` and media prepare; Ghostwriter's Nextext client.

Each stage is useful on its own.

## 9. Residual risks

- **Prompt injection through pictures.** Text in an image can steer the model as text in a file can. Tools still run
  only in workbench.
- **Hostile files reach more parsers:** Pillow, the HEIC decoder, pdfium, LibreOffice. They run only in the executor's
  sandbox (no network, seccomp, limits), as pypdf and python-pptx do today.
- **A copy at Nextext** exists for the length of one job; a crash between upload and delete leaves it until the next
  reconcile.
- **Cloud models.** With an Ollama `*-cloud` model, images leave the host as text does today.
- **Model limits.** A model with a small fixed image size reads small print poorly; strips help, but the model sets the
  ceiling.
- **Image size.** The executor image grows by about 515 MB in stage 2.

## 10. End-to-end checks

All with synthetic files: a photo with EXIF rotation and a GPS tag, a screenshot with small text, a scanned page, a
pptx with a chart and a picture-only slide, a 30 s clip with speech and burned-in text.

1. Each kind attaches, shows a thumbnail or icon, and becomes ready.
2. **Parity.** Median over five runs of the time from the `start` frame to the first `token`: a screenshot against a
   `.txt` with the same words, and a scanned PDF against a text PDF (rounds and time). The picture is not slower.
3. The answers state the facts drawn into the pictures; the photo is upright and its GPS tag never reaches the model.
4. A deck's design question makes the model call `view_file`; the slide comes back and is described correctly.
5. A follow-up turn still sees the attached image; past the budget, the note appears and `view_file` works.
6. A text-only `TEXT_MODEL` gets no images, and the composer offers none.
7. Video: the transcript and frames arrive, the Nextext job is gone afterwards, and the audit shows the hand-over and
   the delete.
8. Deleting the chat removes uploads and derived files at once; retention takes both together.
9. `make bundle` and the airgap transfer work; nothing is fetched at runtime.

## 11. Calls made in the design

These were made by the designer, not the owner:

- Renders are made at attach time and kept as derived files in workbench.
- Office rendering through LibreOfficeKit, not the `soffice` command (S1).
- Tool images in a synthetic user message (S2).
- Pages as strips for models with a fixed cost per image (S2).
- `GHOSTWRITER_MAX_IMAGES` 8; images up to 2048 px / 2.4 MP; pages at 1600 px.
- HEIC through a decode-only plugin; no SVG.
- Delivery in three stages.

## 12. Left to the plans

- The exact `derived` columns and indexes, and the retention event's fields.
- The manifest and `view_file` schemas, and the golden files.
- The SSE `notice` frame and the attachment status route.
- The migration step and the processor's restart behaviour.
- infra-ui's component props.
