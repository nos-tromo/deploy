# Design: the assistant, a lean chat app in place of Open WebUI

Date: 2026-11-16
Status: approved by the owner

## 1. Why

Of Open WebUI, only the chat is still used. Its core features are switched off and built in the federation instead:
- document upload and knowledge bases are covered by docint;
- tools, code and MCP are covered by workbench.

What remains costs a lot:

- **Using workbench is awkward.** A user has to pick workbench in each chat. Uploads work only then, and results are fetched through a file menu. Today's chat apps (Claude, ChatGPT, Gemini) work differently: drop a file in, let it be processed, download the result in the chat.
- **Sign-in is a special path.** It runs on a trusted header, with the `edge-guard` sidecar and a port of its own (`:8443`), because Open WebUI has no sub-path support.
- **The upstream image is heavy, and so is keeping it in check.**
  - The image is 5.8 GB.
  - open-webui-service holds 20 `edge-guard` rules that refuse routes the image serves.
  - Its retention sidecar deletes what the image keeps forever. The sidecar's schema guard is pinned to one migration revision, so every image bump stops chat retention until it is re-verified.
  - Its `CLAUDE.md` asks for a re-check at seven places on every image bump.

open-webui-service#67 eases the workbench flow as a bridge until the replacement lands.

**Goal: the assistant.** It is an ad-hoc chat app of the federation's own, for the same signed-in users. Drop a file into a chat, workbench processes it, and the result can be downloaded in the chat. docint stays the platform for larger analyses.

## 2. Goals, non-goals, anti-goals

### Goals (day one)

- **Answers.** Streamed answers with Markdown, code highlighting and tables. Stop and regenerate.
- **Chat list.** New, rename, delete.
- **Attachments.** By drag and drop or paste, for the formats workbench reads: PDF, DOCX, XLSX, PPTX, CSV, TXT, MD, ODT, ODS, EML, HTML. At most 50 MB per file.
- **workbench's tools.** Read documents, search files, run Python, inspect and fill templates, write tables (xlsx, csv) and documents (docx, md).
- **Result cards** below the answer:
  - images inline;
  - a preview for tables;
  - a download button per file;
  - "expired" once a file is gone.
- **Speech input** through the router's ASR service. An operator can switch it off.
- **Data protection as in docint.**
  - Sign-in through edge-plane.
  - A hash-chained audit trail that records references only.
  - Retention with a notice period.
  - A UI in English and German.

### Non-goals

- Knowledge bases, notes, memories and web search: docint covers these, or they are off today.
- Sharing, folders, temporary chats, plugins and image generation.
- A model picker: the router serves one chat model.
- Importing old Open WebUI chats.
- User administration.

### Anti-goals

The assistant has failed, even if it works, if any of these happen:
- It has accounts or passwords of its own.
- A copy of a user's file exists outside workbench.
- A file is visible in another chat.
- Code runs anywhere but in workbench.
- It keeps data the record of processing does not name.
- An admin can read other users' chats.
- Anything is fetched at runtime.
- It ships a heavy upstream image.

## 3. Constraints

These are the federation's invariants:

- **Inference** goes only through the router: `vllm-router`, `TEXT_MODEL`, with `X-Cache-Scope` and `X-Request-ID`.
- **Tools and file processing** run only in workbench.
- **State** lives on an external volume, `assistant-data`.
- **Networks:**
  - The frontend joins only `edge-net`.
  - The backend joins only `inference-net` and `workbench-net`.
  - The backend checks its own `ASSISTANT_EDGE_SECRET` (edge-plane `docs/identity-contract.md`).
- **Airgap, images and hardening:**
  - Airgap-first, with `make bundle`.
  - Images pinned by digest.
  - Hardened per ADR 0001: uid 10001, read-only, `cap_drop: ALL`.
- **Repo shape:**
  - A vendored `make/common.mk`, `scripts/bundle-lib.sh` and audit library.
  - `.env.example` in the federation format.
  - A release tag minted on merge.
- **Docs stay proportionate.**

## 4. Approaches

- **A. The assistant as a third caller on workbench's REST tool door.**
  - The least work in workbench: the nine tools and the file routes exist and are tested.
  - But that door is Open WebUI's terminal protocol. It would stay as a special path for one caller once Open WebUI is gone.
- **B. Tools over workbench's MCP door, files over REST (chosen).**
  - The workspace tools move onto the MCP door that docint uses.
  - Upload, download and deleting a chat stay REST for the same caller, because the MCP door takes at most 2 MiB per request.
  - The assistant reuses docint's MCP transport, and purpose and request id reach the tool events.
  - When Open WebUI goes, the terminal door goes with it, and workbench is left with one tool protocol for every AI service.
  - This is more work in workbench and reverses a v2 non-goal ("no workspace tools over MCP"). That reversal is deliberate.
- **C. A mode in docint.** Rejected:
  - docint's sessions are bound to collections (`SessionManager`, citations, retention derived from documents).
  - It would blur the split between the ad-hoc assistant and the analysis platform.
- **Another open-source chat UI** (for example LibreChat): rejected, because it only swaps one heavy upstream for another.

## 5. The owner's decisions

1. **A file belongs to its chat.** It is visible only there, and it is deleted with the chat, or after workbench's 7 days at the latest.
2. **Day-one scope** is as in §2. Speech input is part of it and can be switched off.
3. **Approach B.**
4. **A chat is kept 90 days after it starts.** A new or shorter period takes effect only after a 30-day notice.
5. **Stating a purpose is optional.**
   - An operator can require it with `AUDIT_REQUIRE_PURPOSE`.
   - Without a stated purpose, the assistant sends the fixed reason `assistant chat` to workbench.
6. **Tool results expire with the files.**
   - Code, output, document excerpts and table previews are deleted when workbench deletes the chat's files, after 7 days. The conversation text stays 90 days.
   - Follow-ups work for a week; after that the cards say "expired".
   - Nothing derived from a file outlives the file.

## 6. Design

### 6.1 Parts

| Part | What |
|---|---|
| `assistant` (new repo, v0.1.0) | Backend (FastAPI), SPA (React 19, Vite, Tailwind v4, `@infra/ui`), compose, bundles, CI. The template is translator. |
| workbench v0.3.0 | The caller `assistant`: tools over MCP, files over REST, delete a chat, limits per caller, a table preview |
| edge-plane | The `/assistant/` route on `:443`, `ASSISTANT_EDGE_SECRET`, a portal tile |
| deploy | `APP_DIRS`, env-sync groups, `copy-bundles.sh`, ADR 0007, tech-stack pages |
| obs-plane | The emitter `assistant` / `backend` in the verifier, the scrape target `assistant-backend:8000` |
| `infra/CLAUDE.md` | The workspace map (a local file, no PR) |

Retiring Open WebUI is **phase 2**, with its own spec, after the owner accepts the assistant. Phase 2 covers:
- the repo, and the `open-webui-data` volume;
- workbench's terminal door (v0.4);
- edge-plane's `:8443`, `/webui` and ADR 0002;
- deploy's rows;
- obs-plane's rules;
- vllm-service's JWT attribution.

### 6.2 The assistant's backend

- **Identity.**
  - A copy of docint's `core/auth/principal.py`, with `ASSISTANT_EDGE_SECRET` (at least 32 characters, or the backend does not start) and `X-Auth-User`.
  - There is no admin access to other users' chats, and no `owner` parameter. This matches Open WebUI's `ENABLE_ADMIN_CHAT_ACCESS=false` today.
- **Storage.** SQLite on `assistant-data`, through SQLAlchemy 2.0, as in docint.

  | Table | Columns | Kept |
  |---|---|---|
  | `chats` | id (uuid), owner, title, created_at, expires_at | 90 days |
  | `messages` | id, chat_id, role (`user` or `assistant`), content, created_at | 90 days (conversation text) |
  | `tool_steps` | id, message_id, tool, arguments, result, status, created_at, content_expires_at | `arguments` (the code) and `result` (output, excerpts, preview) are emptied at `content_expires_at`; the tool name and status stay for the trail |
  | `attachments` | id, chat_id, workbench_file_id, name, size, mime, created_at | with the chat |
  | `results` | id, message_id, kind (`image`, `table` or `file`), workbench_file_id, name, mime, size, preview (JSON), preview_expires_at | the preview (a header and at most 5 rows, cells cut short) until `preview_expires_at` |

  The assistant stores no file bytes. Images and downloads are fetched from workbench on request.
- **Context for follow-ups.**
  - While a chat's tool results exist, later turns give the model the earlier tool calls and results, cut to the context budget.
  - After that, the model gets the conversation text and a note that the files have expired.
- **The chat flow.** `POST /api/chats/{id}/messages` answers as an SSE stream:
  - frames: `token`, `step` (a tool step for the trail), `result` (a result card), `error`, `final`;
  - the stream bridge is docint's `_aiter_sync_gen`.
- **The agent loop** is new, and lean, over the OpenAI SDK. It reuses docint's `ToolCallAccumulator` and its prepare/fetch/finish pattern.
  - The tool schemas come from workbench's `tools/list`.
  - Each message has a cap on tool rounds, a wall time and a context budget, sized for a 40k context. The plan sets the values.
- **Identity towards workbench.**
  - The assistant sends HMAC pseudonyms, as docint does, keyed with its own workbench key.
  - `X-User-Id` is the user's pseudonym.
  - `X-Session-Id` is the chat's pseudonym, which becomes the chat's folder in workbench.
- **Attachments.** `POST /api/chats/{id}/attachments` (multipart):
  - it checks the type and the size (at most 50 MB);
  - it streams the file to workbench's upload route, into the chat's folder.
- **Downloads.** `GET /api/chats/{id}/files/{file_id}` proxies workbench's view route, for the chat's owner only. workbench's hardening (active content sent as attachments) is kept.
- **Deleting a chat** (by the user or by retention), in this order:
  1. record the `delete` event;
  2. call workbench's chat delete;
  3. delete the chat's own rows.
- **Retention.**
  - **Chats** go 90 days after they start, with a 30-day notice for a new or shorter period. The rule follows open-webui-service's `StoreRule`, and the helpers come from docint's `core/retention.py`.
  - **A daily sweep:**
    - records `delete` / `retention.chat`;
    - empties the tool contents and previews whose 7 days are over, and records them as `delete` / `retention.tool_content`. That period is `WORKBENCH_RETENTION_DAYS`, the same value as workbench's through env-sync.
  - **A banner** names both periods.
- **Speech input.** `POST /api/transcribe` passes the recording to the router's `/v1/audio/transcriptions`.
  - The recording is held in memory only and never stored.
  - `ASSISTANT_SPEECH_INPUT=on|off`, `on` by default. With `off`, the route answers 404 and `/api/config` reports the feature as off.
  - The plan sets the limits on length and size.
- **Audit.**
  - The emitter is `assistant` / `backend`, recording references only. An action whose event cannot be written does not happen.
  - Events:
    - a chat message, with `ai_call` children;
    - an attachment (`collect`, and the hand-over to workbench as `disclose`);
    - a download (`disclose`);
    - deletion and retention (`delete`);
    - a transcription (`ai_call`).
  - The plan fixes the exact catalogue.
- **Purpose.**
  - It is stated through `@infra/ui`'s `PurposeBar`, as in docint.
  - A set `AUDIT_REQUIRE_PURPOSE` requires it (HTTP 428).
  - Without a purpose, `assistant chat` goes to workbench.

### 6.3 The assistant's UI

- **Layout.** `AppShell` from `@infra/ui`.
  - On the left, the chat list: new, rename, delete.
  - On the right, the conversation.
  - Below it, the composer: attach (paperclip, drag and drop, paste), the microphone when enabled, send and stop.
- **Taken from docint.** `sse.ts`, `MarkdownProse` (react-markdown with remark-gfm) and the trail disclosure.
- **Code highlighting** is bundled, with no CDN; the plan picks the library.
- **Result cards** are modelled on docint's `AnalysisCard`: images inline, a table preview, a file with its size and a download button, and an "expired" state.
- **The recorder** is built in the assistant. It moves to `@infra/ui` only once a second app needs it.
- **nginx:**
  - the SPA lives under `/assistant/`, and the prefix is stripped;
  - a streaming location like docint's `/stream_query`;
  - a `client_max_body_size` that fits 50 MB;
  - `Permissions-Policy microphone=(self)`.

### 6.4 workbench v0.3.0

- **The caller `assistant`.**
  - `ASSISTANT_WORKBENCH_KEY`: at least 32 characters, a key of its own.
  - `door=mcp`, `files=workspace`.
  - The tool list comes from `ASSISTANT_WORKBENCH_TOOLS`. By default it holds the eight workspace tools without `display_file`, which is Open WebUI's card.
  - Its own `wall_s` and `slots`.
- **Workspace tools over MCP.**
  - Each workspace tool gets a `run` adapter and an `input_schema`.
  - A result refers to its output files by path and file id, never by bytes.
  - `assistant` is the only caller that combines MCP with workspace files.
- **Table preview.**
  - For every csv or xlsx output, workbench's executor reads the header and at most 5 rows, with cells cut short. The result carries them beside the file reference.
  - Reading office files thus stays in workbench, as the invariant requires. The assistant parses no file, and the preview costs no extra job or event.
- **Files over REST, for this caller only.** Upload, view, list, and a new `DELETE` that deletes a chat.
  - `KeyGate` lets the assistant's key reach only `/mcp` and these routes.
  - Deleting a chat:
    1. records the `delete` event by id first;
    2. then removes the chat's files, jobs and folder;
    3. refuses late outputs of jobs still running.
- **Limits per caller** for the workspace tools, as for docint's inline tool.
- **Audit.** Purpose and request id reach the assistant's workspace events.
- **Unchanged:** Open WebUI's terminal door (both run side by side), docint's inline mode, and the 7-day retention.
- **Docs.** Short additions to `docs/data-flow.md` (the record of processing), `permissions.md`, `audit.md` and `configuration.md`.
- **Tests.** A conformance test against the official MCP client.

### 6.5 The federation

- **edge-plane.**
  - A `handle` block like translator's.
  - `ASSISTANT_EDGE_SECRET` in compose and `.env.example`.
  - `NO_PROXY`.
  - A portal tile "Assistant". The "Chat" tile stays until phase 2.
  - The info pages and docs.
- **deploy.**
  - `APP_DIRS` and `federation.env.example`.
  - env-sync groups:
    - `assistant-edge`: edge-plane and assistant, generated;
    - `router-key`: the assistant's `OPENAI_API_KEY`;
    - `workbench-assistant-key`: workbench `ASSISTANT_WORKBENCH_KEY` and assistant `WORKBENCH_API_KEY`, generated;
    - `workbench-period`: the assistant's `WORKBENCH_RETENTION_DAYS`.
  - A line in `copy-bundles.sh`.
  - ADR 0007, "The assistant replaces Open WebUI".
  - The tech-stack pages (en and de) and `bring-up.md`.
- **obs-plane.** The pair in `DEFAULT_EMITTERS`, the scrape target and `coverage.md`. It merges before the assistant, or with it.
- **vllm-service.** Nothing.

## 7. Delivery order

0. **The v2 PRs merge first:** deploy#80, workbench#10, docint#764, then open-webui-service#66 and #67. Every assistant plan builds on the merged `main`, and the assistant takes its transport from the merged docint.
1. deploy: the env-sync rows and ADR 0007. Rule T5 needs them before the members' tags.
2. obs-plane: the emitter and the scrape target.
3. workbench v0.3.0.
4. assistant v0.1.0.
5. edge-plane: the route and the tile.
6. The map in `infra/CLAUDE.md`.
7. The owner's end-to-end acceptance, with Open WebUI still running alongside.
8. Phase 2, with its own spec.

## 8. Residual risks

- **Prompt injection.** File content can steer the code. The code runs only in workbench's network-less sandbox, and outputs are only read back and offered for download.
- **Two periods.** Files are kept 7 days and chats 90, so older chats show "expired".
- **The router does not see the user.** The assistant records the user and forwards `X-Request-ID`.
- **Database growth.** SQLite grows only with text and small previews, and holds no file bytes.
- **The microphone** needs the browser's permission and HTTPS, which edge-plane provides.
- **One chat model.** Answer quality rests on `TEXT_MODEL` and the host's context size.

## 9. End-to-end checks (after the merges)

1. `make env ENV_GENERATE=1` gives each new key pair one value on both sides.
2. **Sign-in.** Sign in through `/assistant/`. Without the matching edge secret the backend answers 401. One user sees no chat of another.
3. **The main flow.** Drop an XLSX into a chat and ask "sum column B by month, as Excel and as a chart". Then check:
   - the trail shows the tool steps;
   - the card shows the chart inline and the xlsx with a download button;
   - the file sits in the chat's workbench folder only.
4. **Chats are separate.** A second chat does not see the file.
5. **Deleting a chat.** Delete the chat:
   - its workbench files are gone at once;
   - the assistant's trail holds `delete`, and workbench's trail holds `chat.delete`.
6. **Speech input.** Dictation works. With `ASSISTANT_SPEECH_INPUT=off`, the microphone is gone and the route answers 404.
7. **Audit.**
   - obs-plane's verifier accepts the `assistant` / `backend` chain.
   - Events hold references only.
   - workbench's tool events carry the purpose `assistant chat`.
8. **Retention.** The dry run shows the chats that are due, and the notice period holds.
9. **Airgap.** `make bundle` and deploy's transfer work, and nothing is fetched at runtime.
10. **Side by side.** Open WebUI keeps running unchanged.

## 10. Calls made in the design

These were made by the designer, not the owner:

- No admin access to other users' chats.
- No file bytes in the assistant.
- The table preview comes from workbench's executor.
- The recorder is built in the assistant first.
- No model picker.
- Eight tools, without `display_file`.
- This spec lives in deploy, because it spans the federation.
- Retiring Open WebUI is phase 2.

## 11. Left to the plans

The plans settle:
- the caps per message (tool rounds, wall time, context budget);
- the speech-input limits;
- the exact audit catalogue;
- the code-highlighting library;
- the column types and migrations;
- the SSE frame fields.

There is one plan per PR, in the order of §7.
