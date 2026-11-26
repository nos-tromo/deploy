# Design: Ghostwriter says when the model did not see everything

Date: 2026-11-24
Status: approved by the owner

## 1. Why

Ghostwriter never tells the user when the model did not see everything:

- **The context.**
  - `context.fit` (`ghostwriter/agent/context.py`) drops earlier turns, clears tool results and cuts arguments without
    saying so. It returns only the message list.
  - When the current turn alone is over the budget, this only reaches the log.
- **Router refusals.**
  - The text of a refusal is never read.
  - A round that carried images is read as an image refusal and answered with `images_dropped`, because
    `_refuses_images` checks only the status (400, 413 or 422).
  - Any other round becomes `error.model`, "The model did not answer."
- **Cut answers.**
  - An answer that ends at `max_tokens` (4,000, or 12,000 thinking included) is noticed only in a tool round.
  - The check sits after the loop's `return` for a round without calls.
  - It cannot be continued.
- **Long files.**
  - The only limit is 50 MB.
  - The model reads through `read_document`, which pages with `part` and `next_part` at about 16 KB a call.
  - Ghostwriter cuts every tool result at 12,000 characters (`TOOL_RESULT_CHARS`). The cut lands inside `parts`, and
    `total_parts`, `truncated`, `next_part` and `hint` come after it. So the model most likely cannot page on at all. This
    was traced through the code, not run.
- **Unread attachments.** Nothing checks whether an attached document was opened. A small model sometimes answers from
  the file name.
- **Notices.**
  - `images_dropped` is the only notice.
  - Notices live in the browser's memory (`chatStreams.tsx`).
  - They show under the latest answer only and are gone after a reload.

**Goal.** No user takes an answer for complete when it rests on any of these:

- a trimmed chat
- a document read in part or not at all
- an output that was cut off

## 2. Decisions of the owner

1. All cases are covered: a context that is nearly full or exhausted, a cut answer, a file too long to read.
2. A long file is announced **both** on its tile at upload and with the answer.
3. Notices are **durable**:
   - stored with their answer
   - shown with every answer they concern
   - kept across reloads

   `images_dropped` moves to the same model.
4. The length measure comes from **workbench, which measures every document** (workbench 0.10.0).
5. Also in scope:
   - a notice that an attachment was **not read**
   - a **context meter**
   - a **Continue button in its full form**: the continuation is appended to the same answer

## 3. Goals, non-goals, anti-goals

### Goals

- Honest, short notices, with numbers where they are reliable.
- The tile warns before anything is sent.
- A router refusal for context length gets its own, true message.
- Long documents can be paged through again.
- A cut answer can be continued without a seam.
- How full the chat is can be seen at any time.

### Non-goals

- No automatic summary of earlier turns. That is a project of its own: an extra model call, storage, deadlines and audit.
- No preview of what an attachment would add to the meter. An attachment takes context only once it is read.
- No notices in docint.

### Anti-goals

- No notice claims what is not true. Better no notice or no number than a wrong one.
- A notice never blocks or delays a send.
- No content and no file name in a log or an audit event.
- The same warning is not repeated under every answer of a long chat.
- Nothing derived from a file outlives the file (`ghostwriter/store.py` module docstring).

## 4. Approaches considered

### The length measure

- **A, chosen: workbench measures every document.**
  - It covers every type, vision on or off.
  - It needs a workbench release first.
- **B: Ghostwriter estimates.**
  - It uses bytes for text files and the page count of a PDF or Office manifest.
  - That leaves gaps: xlsx, ods, and a PDF with vision off.
  - It is coarse.
- **C: B first, then A.** The gaps stay until A lands.

### The other parts

- **The notice with the answer.** There is one sensible way: count what `read_document` actually gave the model.
- **Storage.** Durable (schema 3) was chosen over live only.
- **Continue.**
  - The simple form sends a new message, "please go on".
  - The full form, chosen, appends to the same answer.

## 5. Design

### 5.1 Notices and where they show

| Code | Level | When | Shown |
|---|---|---|---|
| `answer_cut` | answer | The last round, one without calls, ends with `finish_reason == "length"` or `output_tokens >= cap` | A line under the answer. On the latest answer the line carries **Continue** (§5.6). |
| `document_partial` | answer, per file | The parts given to the model do not cover the document (§5.4) | "Of '{name}', {read} of {total} {units} were read." |
| `document_unread` | answer, per file | An attachment of this question was never opened in this message (§5.5) | "'{name}' was not read for this answer." |
| `images_dropped` | answer | An image refusal, as today, also after a context refusal of a round with images (§5.3) | A line under the answer |
| `context_trimmed` | chat | In the message's **first round**, `fit` left earlier turns out (`replayed_turns < past_turns`) | The composer note, for the latest answer only |
| meter | chat | The stored estimate of the next prompt (§5.7) | The **context meter** at the composer. From 80 % the composer note "nearly full" shows too. |
| error `context` | answer | The router refused for context length: a round without images, or the second try of one with images | The error line, in place of `error.model` |

- **The composer note** ("nearly full", "trimmed", or both) carries a **New chat** button.
- **Chat-level notices** show only there, never under each answer.
- **"Trimmed" is measured in the first round.** That makes it about the chat. Results cleared while the model pages
  within one answer get no notice of their own, since `document_partial` covers them.
- **"Nearly full" and the meter come from the same number** and are both derived in the frontend (§5.7), so they never
  disagree.

### 5.2 Storage (schema 3)

**Messages**

- `messages.notices`: JSON, nullable. A list of `{code, …params}`, never content.
  - `document_partial` holds `file_id`, `read`, `total` and `unit`.
  - `document_unread` holds `file_id`.
- `messages.context_estimate`: an integer, nullable. The estimated tokens of the next prompt (§5.7).

**Attachments**

- `attachments.measure`: JSON, nullable. Holds `{total, unit, chars, capped}`.
- Its state is `pending`, `measured` or `none`.

**Migration**

- `SCHEMA_VERSION = 3` and `MIGRATIONS[2]` in `ghostwriter/db.py`.
- `retention.py`'s dry run is touched only if it reads a new column.

**Deadlines** (the store's rule applied)

- `measure` lives as the manifest in `render` does. Once the file's period has passed it is not served, and the sweep
  empties it.
- The numbers and `file_id` of `document_partial` and `document_unread` are content made when the answer is made, and
  follow the content deadline of steps and results. After it:
  - only the code is served, with a text that names neither the file nor numbers;
  - the sweep removes the params.

**API**

- `MessageOut.notices` and `MessageOut.context_estimate`. A file's name is read from its attachment row when served.
- `AttachmentOut.length`: `{state, total, unit, too_long}`.
- `/api/config` (`ConfigOut`) gains `prompt_tokens` and `reasoning_prompt_tokens`, the budgets from
  `prompt_tokens(settings.context_tokens, …)`.

**Writing and the stream**

- `finish_assistant_message` stores the notices and the estimate with the answer.
- The `notice` frame carries `{code, …params}`; a live frame carries the name.
- The `final` frame carries `context_estimate`.

**Audit**

- No new action for a notice. `chat.answer` records `finish_reason` already.
- Continue has its own primary event (§5.6).

### 5.3 The context (Ghostwriter)

**`fit` and the notices**

- `fit` takes an optional out argument, `report: FitReport | None = None`, holding `past_turns` and `replayed_turns`.
  - Its 36 test calls stay as they are.
  - Only `_Request.fitted` passes a report.
  - `AgentLoop` reads the first round's.
- **`answer_cut`:** the check runs right after `_round`, for the last round without calls too.

**Context refusal**

`_refuses_context(exc)` takes an `openai.APIStatusError` with status 400 or 413 whose `exc.message` or `exc.body`
holds, ignoring case, one of:

- `context length`
- `context window`
- `maximum context`
- `contextwindowexceeded`
- `too many tokens`
- `prompt is too long`
- `input is too long`

It runs before `_refuses_images`. So that nothing does worse than today:

- **A round that carried images** goes once more without them, with `images_dropped`.
- **A round without images, or a second refusal**, is `ErrorEvent("context")`. With it comes a `logger.error` that holds
  no content and names `GHOSTWRITER_CONTEXT_TOKENS`, since the likely cause is a value above the model's real context.

The router's texts are not verified: the tests use invented ones, and the staging run checks the real one.

### 5.4 Long documents

#### workbench 0.10.0

**The route `POST /files/measure {path}`**

- It is open to the `ghostwriter` caller only.
- It runs in the executor, so without network, and counts with the existing readers.
- It answers `{path, kind, unit, total, chars, capped}`.
- `chars` is counted up to `MEASURE_CHARS_CAP`.
- A result is remembered per file, and audited as `prepare` is.
- It runs on **a gate of its own, with one caller slot of its own** (the owner's choice): a slow measure must never
  stand in front of a prepare that someone's send waits for, or of an interactive call.
  - workbench's defaults change:

    | Setting | Today | New |
    |---|---|---|
    | `GHOSTWRITER_WORKBENCH_SLOTS` | 3 | 4 |
    | `WORKBENCH_EXECUTOR_SLOTS` | 4 | 5 |
    | `WORKBENCH_QUEUE_MAX` | 16 | 20 |
    | `WORKBENCH_EXECUTOR_MEM_LIMIT` | 6656m | 8192m (1.5 GiB more) |
    | `WORKBENCH_EXECUTOR_PIDS_LIMIT` | 320 | 384 |

  - A host that pins the old memory limit in its `.env` must raise it, or the executor refuses to start. The release
    notes say so.
  - Ghostwriter measures one file at a time on a slot of its own, never one of its prepare slots.
- The docs it touches: `docs/permissions.md` (Ghostwriter's file routes become eight), `docs/data-flow.md` and
  `docs/configuration.md`.

**Units**

The route and `read_document` use one fine unit per reader:

| Reader | Unit |
|---|---|
| PDF | page |
| PPTX | slide |
| DOCX, ODT | paragraph; a table counts as one |
| XLSX, ODS | row, numbered through all sheets in sheet order |
| text, CSV, JSON, … | line |

The route's `total` counts in that unit.

**`read_document`**

- Each item of `parts` gains `units: [first, last] | null`, 1-based and inclusive.
- A part cut with ` [cut]` carries `cut: true` and does not count as read.

#### Ghostwriter

**Measuring**

- With workbench 0.10.0 or later (discovery's `server_version`), a `document` attachment is measured in the background.
- It runs in the `AttachmentProcessor` on a slot of its own, `MEASURE_SLOTS` (1).
  - The processor holds one job per attachment, so for an Office file or a PDF the measuring follows the prepare in the
    same job.
  - It starts only after the prepare has left its slots, so a measure holds neither a prepare slot nor the background
    slot.
- `length.state` is `pending`, then `measured`, or `none` (workbench before 0.10, or a failure).
- The attachment is `ready` at once; a send never waits for it.
- The long poll goes on waiting on `ready` with `pending`, and the SPA follows a ready attachment while `pending`.

**`too_long`** holds when `chars / CHARS_PER_TOKEN > prompt_tokens(settings.context_tokens, reasoning=False)`, or when
`capped`.

**The structured cut**

This is a regular path: workbench sizes its answer in bytes and without `None` fields, and `WORKBENCH_RESULT_MAX_KB`
goes up to 256. A JSON object result with `parts` is cut thus:

- whole parts go from the end;
- the last part kept may be cut, and then carries `cut: true`;
- the control fields stay;
- `truncated` becomes `true`;
- `next_part` becomes the `label` of the first part left out;
- a sentence in `hint` says Ghostwriter cut.

Anything else is cut as today. `TOOL_RESULT_CHARS` rises to 16,384.

**`document_partial`**

- For each message and file, the `units` of the parts given to the model, and not cut, are joined.
- When the join covers less than `total`, the notice stores `read`, `total` and `unit`. `total` comes from the measure,
  or from `total_parts` where the unit matches.
- There is no notice when the message called `run_python` or `search_files`, since what they read is unknown.
- There is none either without `units` (workbench before 0.10).

**What the model is told**

- `attachment_facts` adds, for a `too_long` file: "longer than one prompt: read it in parts and say which parts you read".
- `agent/prompt.py` gains a short instruction to page with `next_part` and to say plainly what was not read.

### 5.5 Not read (`document_unread`)

- **Which attachments count:** those of this message's question with kind `document` or `media`, and pictures when the
  turn does not see.
- **Opened** means a `read_document` or `view_file` call of this message named the attachment's path and did not fail.
  In a turn that sees, a picture sent as an image part counts as seen.
- **No notice** when the message called `run_python` or `search_files`. They see every file, so what they read is unknown.
- **A turn offered no tools** (for example, workbench was unreachable) gets the notice all the same, and it is true.
- **Later actions:** Regenerate computes it again. Continue keeps the answer's notices and recomputes only `answer_cut`.
- workbench does not change for this; it all runs in `AgentLoop` over the recorded calls.

### 5.6 Continue (the full form)

**The route**

- `POST /api/chats/{chat_id}/messages/{message_id}/continue`, as SSE with the same frames.
- It is allowed only for the latest answer, and only one that carries `answer_cut`; else 409.
- It claims the chat (`TurnRegistry`).
- Its primary event, `modify` / `chat.continue`, is written before anything is stored. It goes into `docs/audit.md`;
  `modify` is already in `ENDPOINT_ACTIONS`.

**One continuation round**

- It runs without tools and without thinking. The answer's thinking was never stored, and writing on needs none.
- It has the budget of a round without thinking.
- The messages, in order:
  1. the system prompt;
  2. the earlier turns, as usual;
  3. this message's question with its calls and results, in the history form;
  4. the answer so far, as the **last assistant message**.

**By provider** (no new setting)

- **`vllm` continues for real**, with `extra_body` set to
  `{"continue_final_message": true, "add_generation_prompt": false, "chat_template_kwargs": {"enable_thinking": false}}`.
  - vLLM's chat API documents both flags; they exclude each other.
  - Whether the LiteLLM router passes them through as it passes `chat_template_kwargs` today is for the staging run to show.
- **Any other provider** (Ollama and others) gets the answer so far as an assistant message, then a short user
  instruction: "Go on exactly where your answer stopped; repeat nothing."
  - Whether Ollama continues a last assistant message is not documented.
  - This path works everywhere, with a less seamless join.

**The result**

- The new text is appended to the same message's `content`.
- `answer_cut` is computed again, so a second cut shows the button again.
- A stop keeps what was appended until then.
- The estimate (§5.7) is computed again.
- Regenerate replaces the whole answer afterwards, as it does now.

**Frontend:** the draft starts with the existing text as its base, and new tokens append to it. The button belongs to
the `answer_cut` line.

### 5.7 The context meter

**The number**

- At the end of every answer, Regenerate and Continue included, the server estimates the next prompt. It counts the
  system prompt, the tool schemas offered and the history, the answer just finished included.
- It uses `fit`'s own replay and sizing (`_replayed`, `_Size`), so it counts what could no longer be sent.
- It is stored as `messages.context_estimate`.

**The display**

- The frontend divides the estimate by the budget of the reasoning switch's current state (`/api/config`), up to 100 %.
- A small ring sits at the composer next to the reasoning switch, with the tooltip "about 62 % of the context used".
- From 80 % (`NEAR_FULL_RATIO`, a frontend constant) the ring takes the warning colour, and the composer note "nearly
  full" shows with New chat.
- An empty chat shows no ring.

**infra-ui:** a new primitive, `ContextMeter`.

- It has `role="meter"`, `aria-valuenow` and a label.
- It works in light and dark, and has a warning level.

### 5.8 UI and strings

**infra-ui** (from `origin/main`, v0.27.0)

- `AttachmentTile.warning?: string`: a `WarningIcon` with a short text, tied to the tile by `aria-describedby`.
- `ContextMeter`.
- One minor release; Ghostwriter pins its commit SHA.

**Ghostwriter frontend**

- The tile shows `warning` when `length.too_long`.
- `MessageView` renders an answer's stored notices.
  - `NoticeLine` is generalised: muted, with `WarningIcon`, and `role="status"` only while streaming.
  - The `answer_cut` line carries Continue.
- The composer carries the ring and the chat note.
- `ERROR_KEYS` gains `context`.
- New types; `NOTICE_CODES` grows.

**Strings**

- In en and de, German with "Sie", and the keys in parity.
- Units with plurals: page, slide, paragraph, line.
- Drafts (en):

| Key | Text |
|---|---|
| `answer_cut` | "The answer was cut at the length limit." (button "Continue") |
| `document_partial` | "Of '{name}', {read} of {total} pages were read." After the deadline: "A document was read only in part." |
| `document_unread` | "'{name}' was not read for this answer." After the deadline: "An attachment was not read for this answer." |
| `context_trimmed` | "Earlier parts of this chat are no longer sent to the model." |
| nearly full | "This chat is nearly as long as the model can read at once. For a new topic, start a new chat." |
| meter tooltip | "about {n} % of the context used" |
| `error.context` | "This chat is too long for the model. Start a new chat or shorten your message." |
| tile | "Very long: read only in parts" |

### 5.9 Left to the builder

- `MEASURE_CHARS_CAP`.
- How the counting reuses the readers.
- The name of the measuring in workbench's audit.
- The detailed form of the composer note and the ring.
- The wording of the cut's hint and of the continuation instruction.

## 6. Order of work

0. This spec, and one plan per repo (`docs/superpowers/plans/`), in one deploy PR.
1. **workbench 0.10.0:**
   - `/files/measure` and the units;
   - `parts[].units` and `cut`;
   - tests, docs and the version.
2. **infra-ui:** `AttachmentTile.warning` and `ContextMeter`, with tests, `docs/components.md` and the release.
3. **Ghostwriter 0.7.0**, each step test first:
   1. A test shows the 12,000-character cut on a realistic workbench answer. Then the structured cut, and
      `TOOL_RESULT_CHARS` at 16,384.
   2. `_refuses_context`, the `context` error, and the try without images.
   3. `FitReport` (the first round); `context_trimmed` and `answer_cut`, live.
   4. Schema 3, the migration, `MessageOut`, storing the notices (`images_dropped` too), the deadlines and the sweep.
   5. The estimate of the next prompt; the budgets in `/api/config`.
   6. `WorkbenchClient.measure`, the measuring step, `AttachmentOut.length` and the long poll.
   7. `document_partial` and `document_unread`, with the `run_python` / `search_files` exception.
   8. The Continue route, the continuation round by provider, appending, and the `chat.continue` audit.
   9. The prompt and `attachment_facts`.
   10. The frontend:
       - types and streams, including the draft with a base;
       - notices and Continue;
       - the ring and the note;
       - the tile warning, and the SPA following `pending`;
       - i18n and the infra-ui pin.
   11. The docs and the 0.7.0 release.
4. **Federation docs:** Ghostwriter's file routes in `infra/CLAUDE.md` and ADR 0007 are already out of date and are
   brought up to date.

## 7. Verification

### Gates

- **workbench:**
  - `make verify` and `make test`
  - tests for measuring (PDF, docx, xlsx with several sheets, txt, capped)
  - tests for `parts[].units` and `cut`
- **infra-ui:**
  - `pnpm lint`, `pnpm test` and `pnpm build`, with `dist/` committed
  - tests for `ContextMeter` (aria, warning level) and `AttachmentTile.warning`
- **Ghostwriter:** `make verify` and `make test`, with tests for:
  - FitReport and the next-prompt estimate;
  - the context error with and without images;
  - `answer_cut` in the last round, and `context_trimmed` from the first round only;
  - the structured cut;
  - `document_partial` and `document_unread`, with the exception;
  - the continuation round by provider;
  - the migration from 2 to 3, the deadlines, and appending on Continue;
  - measuring and the long poll on `pending`;
  - stored notices and their frames;
  - the Continue route: 409 when not the latest answer or without `answer_cut`, the audit, and a stop;
  - the frontend's tests, and i18n parity.

### End to end, locally

This runs on `make dev` with workbench 0.10.0 and Ollama, at the default context of 40k, with a synthetic long document
of invented text, about 500 pages. Check:

- the tile warning;
- "x of y pages";
- "not read", for a question that does not need the file;
- the ring growing over several turns, then "nearly full" and "trimmed" at the composer only;
- a long writing task up to `answer_cut`, then Continue appending (the fallback path);
- that everything stays after a reload;
- a browser check in light and dark.

### End to end, on staging

This runs on the LiteLLM router with vLLM. Check:

- Continue with `continue_final_message` joins without a seam, and the router passes the flags through;
- `error.context`, with `GHOSTWRITER_CONTEXT_TOKENS` above the model's real context.

Ollama is expected to shorten an over-long prompt silently rather than refuse it.

### A side finding, out of scope

At the smallest `GHOSTWRITER_CONTEXT_TOKENS` the backend allows, 22,000, a reasoning round has a prompt budget of 8,000
tokens. That is below the ten tool schemas alone, about 8,900 estimated. It goes into an issue of its own.
