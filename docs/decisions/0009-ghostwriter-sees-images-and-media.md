# 0009 — Ghostwriter sees images and media

Status: accepted (2026-11-20)
Date: 2026-11-20

## Context

Ghostwriter's model reads files only as text, through workbench's tools
([0007](0007-ghostwriter-replaces-open-webui.md)). Images and videos are refused,
and a scanned page or a picture-only slide is never shown. The chat models the
federation serves take images, and docint already sends them images and sends
media to Nextext.

The owner wants visual intelligence in Ghostwriter with no noticeable
difference between a text file and a picture
([design](../superpowers/specs/2026-11-20-ghostwriter-vision-design.md)).

Two rules of the Ghostwriter design stand in the way of media: "Tools and file
processing run only in workbench", and the anti-goal "A copy of a user's file
exists outside workbench". workbench has no media pipeline; Nextext is the
federation's.

## Decision

**Ghostwriter sends images to the model, and sends video and audio to Nextext.**

- **Images go to the router.** Attached images, and pages or frames a tool
  shows, travel to `OPENAI_API_BASE` as `image_url` parts with base64 data URLs,
  as text from files does today. Ghostwriter holds them in memory for one
  message and stores none.
- **Renders are derived files in workbench.** workbench's executor decodes,
  normalises and renders (images, PDF pages, Office files through
  LibreOfficeKit, Nextext's keyframes). The renders are files of the chat,
  bound to their source: they carry its creation time, are hidden from listings
  and tools that list files, and are deleted with it. Image metadata is
  dropped.
- **Media goes to Nextext for one job.** Ghostwriter streams a clip from
  workbench to Nextext's `POST /jobs` under a neutral name, with a service token
  (`service:ghostwriter`), fetches the transcript and keyframes, stores them in
  workbench as derived files of the clip, and deletes the Nextext job at once.
  At start and in its daily sweep it deletes any of its own jobs that no task
  owns. Each hand-over and the deletion are recorded in Ghostwriter's audit
  chain; Nextext records its own collect and delete.
- **This is an exception to the anti-goal, and only for media.** The copy at
  Nextext lives for the length of one job. All other file processing stays in
  workbench; Ghostwriter still parses no file.
- **Fail-soft.** Without Nextext, Ghostwriter offers no video or audio. Without
  a vision model (Ollama's capabilities, or `GHOSTWRITER_VISION=off`), or
  against a workbench without `view_file`, it offers no images and behaves as
  before.

## Alternatives considered

- **Frames in workbench (PyAV), no audio.** No new seam and no copy outside
  workbench. But what is said in a clip would be lost, audio files would not
  work, and the federation would have a second media pipeline beside Nextext.
- **Describe images to text first** (captions or OCR). Lossy, a second model
  call per image, and below what the chat model can see itself.
- **`soffice --convert-to` with a relaxed seccomp filter for rendering.** The
  command needs a Unix socket for its single-instance pipe. LibreOfficeKit
  in-process needs none and runs under the unchanged filter, so the sandbox
  stays as it is.

## Consequences

- A new seam: Ghostwriter's backend calls `nextext-backend` on `inference-net`,
  where both already are. Nextext's `NEXTEXT_SERVICE_TOKENS` gains a
  `ghostwriter` entry; deploy's env-sync must allow two entries of one key.
- Nextext queues Ghostwriter's clips with docint's (`NEXTEXT_JOB_CONCURRENCY`).
- Bring-up order is unchanged: Ghostwriter starts without Nextext.
- workbench's executor image grows by about 515 MB (LibreOffice and fonts), and
  its parsers of hostile input grow by Pillow, a HEIC decoder, pdfium and
  LibreOffice, all inside the existing sandbox.
- workbench serves a tenth tool, `view_file`, to Ghostwriter. Operators who set
  `GHOSTWRITER_WORKBENCH_TOOLS` explicitly must add it.
- vllm-service's `CHAT_LIMIT_MM_PER_PROMPT` must allow at least
  `GHOSTWRITER_MAX_IMAGES` images.
- With an Ollama `*-cloud` model, images leave the host, as text already does.
