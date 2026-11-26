# Ghostwriter says when the model did not see everything — Ghostwriter v0.7.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ghostwriter v0.7.0 tells the user when an answer rests on a trimmed chat, a document read in part or not at all, or an output cut off.
- Notices are stored with their answer and shown after a reload.
- A context refusal gets its own error.
- A cut answer can be continued in place.
- A meter at the composer shows how full the chat is.
- A long document warns on its tile and can be paged through again.

**Architecture:**
- **The structured cut.** `agent/tools.py` cuts a `read_document` answer by whole parts, keeping its paging fields.
- **The notices.** A new `ghostwriter/agent/notices.py` holds the notice event, its stored form, and what one message's calls opened and read. `AgentLoop` collects the notices and yields each as a `notice` frame. `ChatStore` stores them with the answer (schema 3); their file ids and numbers follow the content deadline.
- **The measure.** workbench 0.10.0 measures every document (`POST /files/measure`), in the processor's background job, chained after the prepare.
- **The estimate.** At the end of every answer, a `fit`-sized estimate of the next prompt is stored. The SPA divides it by `/api/config`'s budget.
- **Continue.** A route runs one continuation round through the same `_round` and appends to the same message.

**Builds on:** Ghostwriter 0.6.1 on `main` (`35d06fc`).

**Tech Stack:**
- Backend: Python 3.11, FastAPI, SQLAlchemy 2.0, httpx and anyio (`MockTransport` in tests), loguru, openai SDK.
- Frontend: React 19, react-query, `@infra/ui` 0.28.0 (`AttachmentTile.warning`, `ContextMeter`), vitest.

**Spec:** `deploy/docs/superpowers/specs/2026-11-24-ghostwriter-limit-notices-design.md`. This plan covers §5.1–§5.8, §6 step 3 and §7 (Ghostwriter).

**Contracts, consumed exactly.** The workbench 0.10.0 and infra-ui 0.28.0 plans of the same deploy PR implement them.

- **A. workbench 0.10.0.** Gate on discovery's `server_version` ≥ 0.10.0.
  - `POST /files/measure` takes the JSON body `{"path": str}`, with the headers and chat scoping of `WorkbenchClient.prepare`.
    - `200` answers `{"path": str, "kind": str, "unit": "page"|"slide"|"paragraph"|"row"|"line", "total": int, "chars": int, "capped": bool}`.
    - Errors are as prepare's: `404`, `422` (`unreadable_file`, `unsupported_format`), busy (`503`), `507`.
  - `read_document`: from 0.10.0, every `parts` item carries `"units": [first, last] | null` (1-based, inclusive) and `"cut": bool`.
- **B. infra-ui 0.28.0.**
  - `AttachmentTile` gains `warning?: string`. It shows only while `status === 'ready'`, as a third line under a document row's detail.
    - A tile without `onOpen` (Ghostwriter's document tile) has a `div` face. Its warning reaches a screen reader as text in reading order, not through `aria-describedby`.
  - New `ContextMeter` from the package root: `ContextMeterProps { value: number /* 0..1 */; label: string; warnAt?: number /* 0.8 */; className?: string }`.
    - Its level is decided on the clamped, unrounded value. So 0.799 shows `aria-valuenow` 80 and is still the normal level.
  - A new token, `--color-warning` (the utility `text-warning`, light and dark, AA contrast), reached through `@infra/ui/theme.css`. `frontend/src/styles/globals.css:2` imports it already.
  - The pin is `refs/tags/v0.28.0^{}`, resolved after infra-ui's merge.

Every path below is relative to the `ghostwriter` repo root unless it starts with `deploy/`. Read `CLAUDE.md` before Task 1: "What changes together", Invariants, Testing.

## Calls made in this plan

The spec leaves these open, or the code made them necessary. The owner may correct any of them.

1. **A third message column, `messages.notices_expire_at`.**
   - The content deadline of a notice's details needs a stored time. Steps keep theirs in `content_expires_at`, and `apply_periods` moves stored deadlines (`ghostwriter/store.py:497-520`).
   - It is set only when a stored notice has details (`document_partial`, `document_unread`). `made` is the answer's `created_at`, as `apply_periods` uses for it.
   - `NULL` serves the code only. The sweep strips the details and sets it back to `NULL`.
2. **The measure's state is its own column, `attachments.measure_state`**, nullable, with `pending`, `measured` and `none`. `attachments.measure` keeps exactly `{total, unit, chars, capped}`.
   - Requeue at start and the dry run can query the state in SQL.
   - The sweep empties `measure` as it empties `render`.
   - `NULL` means never measured: a picture, a clip, or a row from before schema 3. It serves `length: null`.
   - A document gets `none` when workbench is older than 0.10.
3. **Measuring is recorded as prepare is**: `disclose` / `chat.attachment`, recipient `workbench:measure`, criteria `kind`, written before the request. If it is not recorded, nothing is sent and the state is `none`. No new action is needed.
4. **The measure waits up to `PREPARE_TIMEOUT_S` (390 s) per read.** It is a background job.
   - workbench 0.10.0's measure gate waits 120 s, plus the caller's wall, plus 30 s: 270 s at its defaults. 390 s covers that.
   - The wait must stay above that sum if workbench's limits are raised.
5. **`document_partial` counts only the chat's attachments** (by path), since the name comes from the attachment row (spec §5.2).
   - Pages or slides in an answer's `pages_without_text` count as read: they were looked at. Without this, every PDF with scans would be "partial".
   - Without a measure, `total_parts` stands in only where a part is one unit: kind `pdf` → page, `pptx` → slide, `docx`/`odt` → paragraph, `text` → line. For `xlsx` it counts sheets, not rows, so no number is given there.
6. **`document_unread` counts only `ready` attachments.** A `failed` one already says "could not be read" on its tile and to the model. Pictures count only when the turn did not see as it began (`_Sight.sees`).
7. **Continue keeps `answer_cut` unless the continuation round ended cleanly below the cap.** A failed round or a stop leaves the answer incomplete, so it stays continuable. A continuation cut again shows the button again.
8. **The 409 of a Continue that is not allowed** has the code `not_continuable`. Its string `chat.not_continuable` is not in the spec's table.
9. **The final frame of a turn stopped before it got a thread** carries `context_estimate: null`.
10. **The retention dry run reads the new columns.** It counts measures with the manifests, and notice details as a fourth count. Each select is guarded by `PRAGMA table_info`, as `render`'s is.
11. **Durable notices replace the stream's kept notices.** `StreamState.notices` goes, and the stored answer renders its own. `useChatStream.test.tsx`'s "the next message clears it" test is rewritten (Task 10).
12. **A measure has a slot of its own: `MEASURE_SLOTS = 1`.** This is the lead's decision, and spec §5.4 says the same ("one file at a time on a slot of its own, never one of its prepare slots").
    - The measure is entered only after the job has left the prepare's `async with gate, self._slots:` (`ghostwriter/processing.py:730-731`), never nested in it.
    - It holds neither `_slots` nor `_background`. An Office job already holds `_background` (`BACKGROUND_SLOTS = 1`) through its prepare, so taking it again inside would wait on itself forever.
    - A slow measure never delays a picture's or a PDF's prepare on Ghostwriter's side.
    - Spec §5.4's "Measuring" bullet still says "on `BACKGROUND_SLOTS`". It contradicts the same section's workbench part; the plan follows the latter.
    - workbench 0.10.0 gives the measure a gate of its own, and raises `GHOSTWRITER_WORKBENCH_SLOTS` to 4 (and its executor's slots, queue, memory and PIDs). Nothing changes in Ghostwriter's `config.py` or `.env.example`.
    - The texts that say "`GHOSTWRITER_WORKBENCH_SLOTS` at 3 or more" change to the sentence of Task 6.

## Global Constraints

**Branch and release**
- Branch `feat/limit-notices` from `main`. One PR, opened once workbench 0.10.0 and infra-ui 0.28.0 are merged.
- `pyproject.toml` `version = "0.7.0"` and `uv lock` in Task 12 only. The tag is minted on merge. `frontend/package.json` keeps its `0.1.0`, as every release has.

**Code and gates**
- Python 3.11 only. Ruff with Google docstrings (`D`) and annotations (`ANN`), pyrefly strict, 120-character lines.
- `make verify` and `make test` are green before every push. `git add` new files first.
- One process, one uvicorn worker. The turn registry, the processor's jobs and the audit chain live in it. Never add workers.
- Vendored files are never edited: `ghostwriter/utils/audit_*.py`, `make/common.mk`, `scripts/bundle-lib.sh`, `frontend/eslint.config.js`, and the `[tool.ruff]` and `[tool.pyrefly]` sections.

**Data, logs and audit**
- No file bytes here. A measure sends a path. A notice holds a file id and counts, never content.
- Logs hold ids, counts, codes, statuses and tokens, with loguru `{}` placeholders. Never a file name, a notice's name, a router's refusal text, a prompt or an answer.
- Audit fails closed. A route's primary event is written before anything is stored, and its action must be in `ENDPOINT_ACTIONS` (`modify` is).
- The `chat.answer` criteria do not change (`tests/test_api_messages.py:713-720`). `docs/audit.md` lists every new record.
- Every workbench call carries the chat's `X-Session-Id` (`CallFor`). A path outside the chat's folder is never asked for.
- Inference goes only through `OPENAI_API_BASE`, with `inference_headers`. No new setting. Nothing is fetched at runtime.

**Spec values, verbatim**
- The notice codes: `answer_cut`, `document_partial` {`file_id`, `read`, `total`, `unit`}, `document_unread` {`file_id`}, `images_dropped`, `context_trimmed`. The error code is `context`.
- `_refuses_context`:
  - statuses `400` and `413`;
  - patterns, case-insensitive, in `exc.message` or `str(exc.body)`: `context length`, `context window`, `maximum context`, `contextwindowexceeded`, `too many tokens`, `prompt is too long`, `input is too long`.
- `TOOL_RESULT_CHARS = 16_384` and `NEAR_FULL_RATIO = 0.8` (a frontend constant).
- The units: `page`, `slide`, `paragraph`, `row`, `line`.
- `too_long`: `chars / CHARS_PER_TOKEN > prompt_tokens(settings.context_tokens, reasoning=False)`, or `capped`.
- The vLLM continuation `extra_body` is exactly `{"continue_final_message": True, "add_generation_prompt": False, "chat_template_kwargs": {"enable_thinking": False}}`.
- Any other provider gets the instruction `"Go on exactly where your answer stopped; repeat nothing."`.

**Tests and copy**
- Test data is invented: names such as `long-report.pdf`, `harbour-survey.docx`, `quay-ledger.xlsx` and `tide-notes.txt`. Router refusal texts are invented too, such as `"synthetic router: maximum context exceeded"`. No absolute local path in any committed file or message.
- UI strings go into `en.ts` and `de.ts`, the German with "Sie". `i18n.test.ts` keeps parity. Commit messages are conventional, with no real data.

## Review Focus

1. **A chat written by 0.6.x is opened after the upgrade.**
   - Its answers have `notices` and `context_estimate` `NULL`, and its attachments `measure_state` `NULL`.
   - Expect every message to render as before: no notice line, no ring until the next answer, no tile warning, and no follower for its attachments.
   - Tests in Tasks 4 (store and API) and 10 (the SPA).
2. **Continue is pressed twice, or while another answer runs.**
   - While a turn holds the chat, the answer is `409 busy`.
   - After a clean continuation, `answer_cut` is gone, so a second press is `409 not_continuable`, recorded as nothing and changing nothing.
   - Test in Task 8.
3. **A measure answers after its attachment was taken back, or its chat deleted.**
   - The job is cancelled. Nothing is settled, no `error`-level log is written, and no `error` child is recorded.
   - If the row went without a cancel, `settle_measure` changes nothing and logs at debug.
   - Test in Task 6.
4. **A tool result over the cap that is not a paged JSON object.** Examples: `run_python`'s output, a JSON array, an object whose `parts` is not a list of `{label, text}`, or non-JSON text.
   - Expect it cut exactly as in 0.6, never raising.
   - Test in Task 1.
5. **A stored `document_partial` whose content deadline has passed** (the file's period is over), before and after the sweep.
   - The API serves `{"code": "document_partial"}` alone, and the SPA shows "A document was read only in part.", never a stale name or number.
   - Tests in Tasks 4 and 10.

---

### Task 1: The structured cut of a paged tool result

**Files:**
- Modify: `ghostwriter/agent/tools.py` (`cut` `:83-93`, `tool_message` `:240-264`), `ghostwriter/agent/limits.py:57`
- Test: `tests/test_agent_context.py` (where `tool_message` is tested, `:242-250`)

**Interfaces:**
- Produces:
  - `limits.TOOL_RESULT_CHARS: Final = 16_384`.
  - `tools.CUT_HINT: Final`: `"Ghostwriter cut this answer to fit; call read_document again with part set to next_part to read on."`
  - `tools.cut_result(text: str, limit: int) -> str`. `tool_message` uses it in place of `cut` for a `ToolResult`.

- [ ] **Step 0: Branch.** `git fetch origin && git switch main && git pull --ff-only && git switch -c feat/limit-notices`.
- [ ] **Step 1: Write the failing test that shows today's cut.**
  - The fixture `_read_answer(parts: int, chars: int) -> str` builds a synthetic `read_document` answer as workbench writes it:
    - compact JSON, `ensure_ascii=False`, keys in workbench's order: `path`, `kind`, `parts`, `total_parts`, `pages_without_text`, `truncated`, `next_part`, `hint`;
    - parts labelled `page 1` … `page N`, each with invented text of `chars` characters (umlauts and a `"` included), `units` `[n, n]` and `cut: false`;
    - `next_part` is `"page {N+1}"`.
  - `test_a_long_read_document_answer_keeps_its_paging_fields`: `_read_answer(5, 3_900)` (about 20 k characters), through `tool_message(ToolResult(text, ()))`. Assert:
    - `json.loads(out)` succeeds and `len(out) <= TOOL_RESULT_CHARS`;
    - `body["truncated"] is True`, and `body["next_part"]` is the label of the first part left out;
    - `body["total_parts"]` and `body["pages_without_text"]` are as sent, and `body["hint"]` ends with `CUT_HINT`;
    - every part but the last kept is unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_context.py -k paging -q`. Expected: FAIL with `JSONDecodeError`. The 12,000-character cut lands inside `parts`.
- [ ] **Step 3: Write the remaining tests.**
  - `test_the_last_part_kept_may_be_cut`: the part that does not fit whole is kept with as much text as fits. Its text ends with `CUT_MARK`, it carries `cut: true`, and `next_part` is the next part's label.
  - `test_a_part_cut_last_names_workbenchs_own_next_part`: the last part is cut, and `next_part` keeps workbench's value.
  - `test_escaped_text_still_fits`: parts full of `"` and `\n` (6 characters each when escaped). `len(out) <= limit`, and it is valid JSON.
  - `test_other_results_are_cut_as_before` (Review Focus 4), parametrized: non-JSON text, a JSON array, `{"parts": "x"}`, `{"parts": [1, 2]}`, `{"stdout": "…"}` (run_python's shape), each over the cap. `out == cut(text, TOOL_RESULT_CHARS)`, and nothing raises.
  - `test_a_result_within_the_cap_is_unchanged`.
  - `test_a_shell_too_long_alone_falls_back`: a `hint` of 20 k characters gives `cut(text, limit)`.
- [ ] **Step 4: Implement `cut_result`.** The algorithm is decided here:

```python
def cut_result(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    paged = _paged(text)  # the JSON object whose "parts" is a list of objects with string "label" and "text"; else None
    if paged is None:
        return cut(text, limit)
    parts, hint = paged["parts"], paged.get("hint")
    told = f"{hint} {CUT_HINT}" if isinstance(hint, str) and hint else CUT_HINT

    def shaped(kept: list[dict[str, Any]], first_left_out: int) -> str:
        after = parts[first_left_out]["label"] if first_left_out < len(parts) else paged.get("next_part")
        return compact({**paged, "parts": kept, "truncated": True, "next_part": after, "hint": told})

    kept: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        if len(shaped([*kept, part], index + 1)) <= limit:
            kept.append(part)
            continue
        room = limit - len(shaped([*kept, {**part, "text": "", "cut": True}], index + 1))
        while room > len(CUT_MARK):
            trial = [*kept, {**part, "text": cut(part["text"], room), "cut": True}]
            over = len(shaped(trial, index + 1)) - limit
            if over <= 0:
                return shaped(trial, index + 1)
            room -= over  # escaping made the text longer than its characters
        fitted = shaped(kept, index)
        return fitted if len(fitted) <= limit else cut(text, limit)
    whole = compact(paged)  # every part fits once compacted: nothing was left out
    return whole if len(whole) <= limit else cut(text, limit)
```

  Update `tools.py`'s module docstring and `tool_message`'s: a paged answer is cut by parts, anything else as before.
- [ ] **Step 5: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(agent): cut a paged tool result by whole parts, keeping next_part; 16,384 characters`

### Task 2: A context refusal is its own error

**Files:**
- Modify:
  - `ghostwriter/agent/loop.py`: constants `:131-137`, `ErrorEvent` `:209-219`, `_refuses_images` `:428-437`, `_round`'s except branch `:802-816`, and the module docstring;
  - `ghostwriter/api/messages.py` (module docstring: the `context` error).
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Produces in `loop.py`:
  - `CONTEXT_ERROR: Final = "This chat is too long for the model."`
  - `REFUSED_CONTEXT_STATUSES: Final = frozenset({400, 413})`
  - `CONTEXT_REFUSALS: Final = ("context length", "context window", "maximum context", "contextwindowexceeded", "too many tokens", "prompt is too long", "input is too long")`
  - `_refuses_context(exc: Exception) -> bool`: an `openai.APIStatusError` with a status of `REFUSED_CONTEXT_STATUSES` whose `f"{exc.message} {exc.body}".lower()` holds a pattern.
  - `ErrorEvent.code: Literal["model", "audit", "internal", "context"]`.
- The order in `_round`'s except branch, for a failure before any text and not after a stop:
  1. `_refuses_context(exc)` and the round carried images (`count`): as today's image path (`_drop_images`, `images_dropped`, fit again, `continue`).
  2. `_refuses_context(exc)` without images: `logger.error("The router refused round {} for its length; GHOSTWRITER_CONTEXT_TOKENS may be above the model's context.", round_no)`, then `ErrorEvent("context", CONTEXT_ERROR)`.
  3. Otherwise as today: `_refuses_images`, else `ErrorEvent("model", MODEL_ERROR)`.

  A round sent again without images that is refused again has no images left, so it takes branch 2. `audit.model_called(..., outcome="error")` is written for every failed round, as today.

- [ ] **Step 1: Write the failing tests.** A helper `_context_refused(text: str, *, status: int = 400, body: object = None)` builds an `openai.APIStatusError` subclass for that status, as `_refused()` (`:951`) does.
  - `test_refuses_context_reads_the_message_and_the_body`, parametrized:
    - each of the seven patterns, in mixed case, in the message (`True`);
    - one only in `body={"error": {"message": "Synthetic: ContextWindowExceeded"}}` (`True`);
    - `422` with a pattern (`False`), `400` without one (`False`), and a `ValueError` (`False`).
  - `test_a_context_refusal_without_images_is_the_context_error`:
    - `events == [ErrorEvent("context", "This chat is too long for the model.")]`;
    - one error-level log line names `GHOSTWRITER_CONTEXT_TOKENS` and holds neither the router's text nor the question (`loguru_caplog`);
    - the audit log holds one `error` round.
  - `test_a_context_refusal_with_images_goes_again_without_them`: vision on and a picture attached; round 1 is refused for its length, round 2 answers. The events start `[NoticeEvent("images_dropped"), TokenEvent(...)]`, and round 2 carried no image part.
  - `test_a_second_context_refusal_is_the_context_error`: the same, refused twice. `events == [NoticeEvent("images_dropped"), ErrorEvent("context", CONTEXT_ERROR)]`.
  - `test_other_refusals_stay_as_they_were`: `_refused()` with images still drops them; a `400` without a pattern and without images is still `ErrorEvent("model", ...)`.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_loop.py -k "context or refusals" -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): a context refusal is the context error, after one try without images`

### Task 3: `FitReport`, and `context_trimmed` and `answer_cut` live

**Files:**
- Create: `ghostwriter/agent/notices.py`
- Modify:
  - `ghostwriter/schemas.py` (`NoticeCode`, `MeasureUnit`);
  - `ghostwriter/agent/context.py` (`fit` `:505-613`);
  - `ghostwriter/agent/loop.py`: `NoticeEvent` `:198-206` moves to `notices.py` and is imported back; `_Progress` `:259-276`; `_Request.fitted` `:387-405`; the main loop `:655-665`; `_round` `:774`;
  - `ghostwriter/api/messages.py` (`_frame` `:233-234`, the frames docstring `:6-10`).
- Test: `tests/test_agent_context.py`, `tests/test_agent_loop.py`, `tests/test_api_messages.py`

**Interfaces:**
- `schemas.py`:
  - `NoticeCode = Literal["answer_cut", "document_partial", "document_unread", "images_dropped", "context_trimmed"]`
  - `MeasureUnit = Literal["page", "slide", "paragraph", "row", "line"]`
- `notices.py`, a frozen dataclass `NoticeEvent(code: NoticeCode, file_id: str | None = None, name: str | None = None, read: int | None = None, total: int | None = None, unit: MeasureUnit | None = None)`:
  - `frame(self) -> dict[str, Any]`: `code`, and every other field that is set, the name included;
  - `stored(self) -> dict[str, Any]`: the same without `name`, which is never stored;
  - `@classmethod from_stored(cls, value: Mapping[str, Any]) -> NoticeEvent | None`: `None` for an unknown code or a malformed value.
  - `NoticeEvent("images_dropped")` stays valid, and `ghostwriter.agent.loop.NoticeEvent` still names it.
- `context.py`:
  - `@dataclass class FitReport: past_turns: int = 0; replayed_turns: int = 0`, with the property `trimmed -> bool` (`replayed_turns < past_turns`);
  - `fit(..., report: FitReport | None = None)`, keyword-only, last. When given, it is filled once the replay ends. The name `fit` and its 36 test calls stay; `test_agent_loop.py:859`'s monkeypatch keeps working.
- `loop.py`:
  - `_Progress.notices: list[NoticeEvent]` (default empty). `_Progress.told(self, event: NoticeEvent) -> NoticeEvent` appends the event and returns it, for `yield`.
  - `_Request.fitted(..., report: FitReport | None = None)` passes it to `fit`. `_round` passes a fresh report for `round_no == 1` only, and yields `message.told(NoticeEvent("context_trimmed"))` before streaming when `report.trimmed`.
  - `_at_cap(done: RoundDone, cap: int) -> bool`: `done.finish_reason == "length" or (done.output_tokens is not None and done.output_tokens >= cap)`. The existing check at `:665` uses it.
  - Right after `_round`: when `done` is not `None`, not `stopped`, has no calls and `_at_cap(done, cap)`, yield `message.told(NoticeEvent("answer_cut"))` before the existing `return`.
  - The image refusal's notice also goes through `message.told`.
- `messages._frame`: `NoticeEvent` becomes `sse_frame("notice", event.frame())`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_context.py`:
    - `test_fit_reports_the_turns_it_replayed`: five past turns of about 3,000 tokens each with `budget=10_000` give `past_turns == 5` and `replayed_turns` equal to the turns in the result. With a large budget, `trimmed` is false.
    - `test_fit_without_a_report_is_unchanged`: same messages with and without a report.
  - `tests/test_agent_loop.py`:
    - `test_a_trimmed_chat_is_told_once_in_the_first_round`:
      - 30 big earlier turns (as `test_a_reasoning_message_reasons_in_every_round_within_its_budget` builds them);
      - one tool round, then an answer;
      - exactly one `NoticeEvent("context_trimmed")`, before the first `TokenEvent`.
    - `test_no_trim_notice_when_every_turn_fits`.
    - `test_an_answer_cut_at_the_cap_says_so`, parametrized over `[text_chunk("Kapitel 1 …"), finish_chunk("length")]` and `[text_chunk(...), usage_chunk(900, MAX_ANSWER_TOKENS, "served-model")]`. The events end with `NoticeEvent("answer_cut")`.
    - `test_a_reasoning_answer_is_cut_at_its_own_cap`: `usage_chunk(900, 4_000, …)` with `reasoning=True` gives no notice; `12_000` gives one.
    - `test_a_stopped_round_is_not_cut`.
  - `tests/test_api_messages.py::test_a_notice_frame_carries_its_details`:
    - `_frame(NoticeEvent("document_partial", file_id="f" * 32, name="long-report.pdf", read=9, total=500, unit="page"))` carries exactly those six keys;
    - `NoticeEvent("answer_cut")` gives `{"code": "answer_cut"}`;
    - `NoticeEvent(...).stored()` has no `name`;
    - `from_stored({"code": "later_code"})` is `None`.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_context.py tests/test_agent_loop.py tests/test_api_messages.py -k "report or trim or cut or notice_frame" -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, and update `fit`'s and the loop's docstrings: the first round's report, and `answer_cut` after the last round.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. The existing `images_dropped` asserts (`test_agent_loop.py:1145-1289`) hold unchanged.
- [ ] **Step 5: Commit** `feat(agent): tell a trimmed chat and an answer cut at its cap, live`

### Task 4: Schema 3, and notices stored with their answer

**Files:**
- Modify:
  - `ghostwriter/models.py` (`Message` `:50-64`, `Attachment` `:86-111`), `ghostwriter/db.py` (`SCHEMA_VERSION` `:20`, `MIGRATIONS` `:44`), `ghostwriter/schemas.py` (`NoticeOut`, `MessageOut` `:122-141`);
  - `ghostwriter/store.py`: module docstring `:1-17`, `apply_periods`, `chat_detail` `:582-629`, `finish_assistant_message` `:852-862`; new `due_notices` and `strip_notices`;
  - `ghostwriter/agent/loop.py` (`_store_text` `:573-583` becomes `_store_answer`), `ghostwriter/retention.py` (`SweepReport`, `_sweep_contents` `:320-348`, `dry_run` `:424-481`).
- Test: `tests/test_db.py`, `tests/test_store.py`, `tests/test_retention.py`, `tests/test_api_messages.py`

**Interfaces:**
- Consumes: Task 3's `NoticeEvent.stored()`.
- `models.py`:
  - `Message` gains `notices: Mapped[str | None] = mapped_column(Text)`, `context_estimate: Mapped[int | None]` and `notices_expire_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), index=True)`.
  - `Attachment` gains `measure: Mapped[str | None] = mapped_column(Text)`, `measure_state: Mapped[str | None] = mapped_column(String(8))`, and `CheckConstraint("measure_state IN ('pending', 'measured', 'none')", name="ck_attachments_measure_state")`.
- `db.py`: `SCHEMA_VERSION = 3`. `_to_3(conn)` runs:
  - `ALTER TABLE messages ADD COLUMN notices TEXT`
  - `… context_estimate INTEGER`
  - `… notices_expire_at DATETIME`
  - `CREATE INDEX ix_messages_notices_expire_at ON messages (notices_expire_at)`
  - `ALTER TABLE attachments ADD COLUMN measure TEXT`
  - `… measure_state VARCHAR(8) CHECK (measure_state IN ('pending', 'measured', 'none'))`

  `MIGRATIONS = {1: _to_2, 2: _to_3}`. The attachment columns are written by Task 6. They are here so that schema 3 is one step.
- `schemas.py`:
  - `NoticeOut(BaseModel)`: `code: NoticeCode`, `file_id: str | None = None`, `name: str | None = None`, `read: int | None = None`, `total: int | None = None`, `unit: MeasureUnit | None = None`.
  - `MessageOut` gains `notices: list[NoticeOut] | None = None` and `context_estimate: int | None = None`.
- `store.py`:
  - `PARAM_CODES: Final = frozenset({"document_partial", "document_unread"})`.
  - `finish_assistant_message(self, message_id: str, content: str, *, notices: Sequence[Mapping[str, Any]] = (), context_estimate: int | None = None) -> None`:
    - stores `notices` as a JSON list (`[]` too) and the estimate;
    - when a notice has a code of `PARAM_CODES` and `notices_expire_at` is `NULL`, sets it to `_content_deadline(s, chat_id, message.created_at)`. A later write (Continue) keeps it.
  - Served by `chat_detail`, per answer, `None` for a `NULL` column:
    - each stored notice whose code is known, in order;
    - with its details and its `name` (read from the chat's attachment rows by `workbench_file_id`) while `notices_expire_at > now`;
    - else the code alone. An unknown code is left out.
  - `due_notices(self, now: datetime, limit: int) -> list[str]`: answers with `notices_expire_at <= now`, the earliest first.
  - `strip_notices(self, message_ids: Sequence[str]) -> None`: rewrites each list to `{"code"}` items and sets `notices_expire_at` to `NULL`.
  - `apply_periods` gives `notices_expire_at > now` a new deadline as it does steps', with `made = Message.created_at`.
- `loop.py`: `_store_answer(turn, message)` calls `finish_assistant_message(id, text, notices=[n.stored() for n in message.notices])`. It never raises, as today.
- `retention.py`:
  - `SweepReport.notices: int`.
  - `_sweep_contents` takes `due_notices` in the same batch loop, recorded first as `delete` / `retention.tool_content`, target `message` (ids, count), criteria `file_days`, then `strip_notices`.
  - The dry run line reads `…, {n} manifests, {m} notices` (call 10): manifests count `render IS NOT NULL OR measure IS NOT NULL`, and notices count `notices_expire_at`, each guarded by `PRAGMA table_info`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_db.py`:
    - `test_a_version_2_file_gets_notices_and_measures` (Review Focus 1):
      - a schema-2 file with a chat, a question, an answer and a `ready` attachment;
      - `init_db` gives version 3 and every new column `NULL`;
      - `measure_state = 'lost'` raises `IntegrityError`, and the index `ix_messages_notices_expire_at` exists.
    - `test_a_version_1_file_gets_kinds_and_statuses` asserts `SCHEMA_VERSION` in place of `2`.
    - `test_a_fresh_file_has_the_same_columns_and_checks_as_a_migrated_one` gains the new columns and the `measure_state` check.
  - `tests/test_store.py`:
    - `test_an_answer_keeps_its_notices_and_estimate`: `finish_assistant_message(..., notices=[{"code": "answer_cut"}, {"code": "document_unread", "file_id": FILE}], context_estimate=1234)`, read back by `chat_detail`:
      - the codes in order, `name` the attachment's, `context_estimate == 1234`;
      - a question's `notices is None`.
    - `test_notice_details_follow_the_content_deadline` (Review Focus 5): past the deadline, before any sweep, `document_partial` is served as `NoticeOut(code="document_partial")` with every other field `None`.
    - `test_a_continued_write_keeps_the_deadline`: a second `finish_assistant_message` leaves `notices_expire_at`.
    - `test_apply_periods_moves_notice_deadlines`: a shorter file period moves a future deadline earlier, and a passed one stays.
    - `test_an_answer_from_before_schema_3_has_no_notices`: a row with `NULL` gives `notices is None` and `context_estimate is None`.
  - `tests/test_retention.py`:
    - `test_the_sweep_strips_notice_details`: the event comes first, then the stored JSON is `[{"code": "document_partial"}]` and `report.notices == 1`;
    - `test_the_dry_run_counts_notices_and_measures`, also on a schema-2 file.
  - `tests/test_api_messages.py::test_notices_are_stored_with_their_answer`: the `images_dropped` run (as `:1114`) is followed by `GET /api/chats/{id}`. The answer's `notices == [{"code": "images_dropped", "file_id": None, "name": None, "read": None, "total": None, "unit": None}]`.
- [ ] **Step 2: Run** `uv run pytest tests/test_db.py tests/test_store.py tests/test_retention.py tests/test_api_messages.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Update:
  - the store's module docstring: the notices' deadline, and the measure lives as the manifest does;
  - `retention.py`'s docstrings;
  - `CLAUDE.md`'s "What changes together" line on the schema: version 3.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(store): schema 3; an answer's notices stored, their details on the content deadline`

### Task 5: The estimate of the next prompt, and the budgets in `/api/config`

**Files:**
- Modify:
  - `ghostwriter/agent/context.py`: new `next_prompt_tokens` and `finished_turn`;
  - `ghostwriter/agent/loop.py`: `run` `:545-571`, `_Progress`, `_answer`;
  - `ghostwriter/api/messages.py`: `TurnStream` `:240-343`, the `final` frame;
  - `ghostwriter/api/meta.py` (`ConfigOut` `:48-88`, `config`).
- Test: `tests/test_agent_context.py`, `tests/test_agent_loop.py`, `tests/test_api_messages.py`, `tests/test_api_meta.py`

**Interfaces:**
- Consumes: Task 4's `finish_assistant_message(..., context_estimate=)`.
- `context.py`:
  - `next_prompt_tokens(system: str, tools: list[dict[str, Any]], past: Sequence[PastTurn], *, images: ImageBudget | None = None) -> int`:
    - `_Size([system message, *every turn's _replayed(...)], images).tokens()` plus `estimate_tokens(tools)` when there are tools;
    - each turn in mode `images` when `images` is set and it has images, else `none`;
    - never capped at a budget: it counts what could no longer be sent.
  - `finished_turn(question: str, images: tuple[AttachedImage, ...], current: Sequence[Mapping[str, Any]], answer: str) -> PastTurn`:
    - the current turn as the store will replay it;
    - `rounds` from each assistant message with `tool_calls` and the tool messages that answer them: `PastCall(id, name, arguments, content)`;
    - `expired=False`.
- `loop.py`:
  - `@dataclass class TurnOutcome: context_estimate: int | None = None`.
  - `AgentLoop.run(self, turn: Turn, audit: TurnAudit, *, outcome: TurnOutcome | None = None) -> Iterator[TurnEvent]`.
  - `_Progress.next_prompt: Callable[[str], int] | None = None`. `_answer` sets it once `current` exists: `lambda text: next_prompt_tokens(offer.system, offer.tools, [*past, finished_turn(question.text, question.images, current, text)], images=sight.budget)`, where `offer` is `request.offer(sight)` as the call runs (after a refusal, the blind one).
  - In `run`'s `finally`, before `_store_answer`: the estimate from `next_prompt` and the final text. If that raises, it is logged by type and stored as `None`. It is passed to `finish_assistant_message` and set on `outcome`.
- `messages.py`: the `final` frame gains `"context_estimate"`, from the stream's `TurnOutcome`. It is `None` from `attach`'s stop before start (call 9).
- `meta.ConfigOut` gains `prompt_tokens: int` and `reasoning_prompt_tokens: int`: `prompt_tokens(settings.context_tokens, reasoning=False)` and `…, reasoning=True`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_context.py`:
    - `test_the_next_prompt_counts_every_turn_and_the_tools`: 40 big turns give more than `PROMPT_TOKENS`, which proves it is not capped. It equals `_Size` of the system message and every replayed turn, plus `estimate_tokens(tools)`.
    - `test_the_estimate_counts_images_when_the_turn_sees`: a turn with an attached picture counts more with an `ImageBudget` than without.
    - `test_a_finished_turn_is_what_the_store_replays`: after a loop turn with one tool call, `finished_turn(...)` equals the last `PastTurn` of `store.past_turns(CHAT, before_position=10**6)`.
  - `tests/test_agent_loop.py`:
    - `test_every_answer_leaves_its_estimate`: the stored `context_estimate` equals `next_prompt_tokens(fake.calls[-1]["messages"][0]["content"], fake.calls[-1]["tools"], store.past_turns(...)[0])`.
    - `test_a_stopped_or_failed_answer_still_leaves_its_estimate`.
    - `test_an_estimate_that_fails_is_none`: `next_prompt_tokens` patched to raise gives `None`, one warning naming the type, and the text stored.
  - `tests/test_api_messages.py::test_final_carries_the_estimate`: the final frame's `context_estimate` is an int equal to the stored one.
  - `tests/test_api_meta.py`:
    - `test_config_reports_what_the_spa_needs` gains `"prompt_tokens": 34_000, "reasoning_prompt_tokens": 26_000`;
    - `test_the_budgets_follow_the_context`: `context_tokens=60_000` gives `54_000` and `46_000`.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_context.py tests/test_agent_loop.py tests/test_api_messages.py tests/test_api_meta.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): estimate the next prompt at the end of every answer; the budgets in /api/config`

### Task 6: Measuring a document, `AttachmentOut.length`, and the long poll

**Files:**
- Modify:
  - `ghostwriter/workbench.py`: `Measure`, `serves_measure`, `WorkbenchClient.measure`;
  - `ghostwriter/nextext.py` (`serves_media` `:592-603` delegates to the shared check);
  - `ghostwriter/schemas.py` (`MeasureState`, `LengthOut`, `AttachmentOut` `:33-56`);
  - `ghostwriter/store.py`: `__init__`, `AttachmentRef`, `_attachment_out`, `add_attachment` `:968`, `due_contents`, `empty_contents`; new `settle_measure`, `pending_measures` and `settle_pending_measures`;
  - `ghostwriter/processing.py`: module docstring (`:24-33`, and `:29`'s "3 or more"), `PrepareJob` `:182-211`, `_requeued` `:214`, `__init__` `:348-377`, `_run` `:711-738`, `requeue` `:641`, `requeue_chat` `:654`; new `_measure`;
  - `ghostwriter/api/files.py`: docstring, `attach_file` `:255-328`, `_read_after_wait` `:488-511`; new `_measures_offered`;
  - `ghostwriter/main.py` (`ChatStore(..., prompt_budget=...)`; without workbench, `settle_pending_measures()`), `tests/fakes.py` (`FakeDoor`: `_ROUTES`, `/files/measure`, `measures`, `gates`);
  - `docs/audit.md`, `docs/configuration.md` (the caps table: a `MEASURE_SLOTS` row, and `:74`'s "3 or more").
- Create: `tests/test_processing_measure.py`
- Test: `tests/test_workbench.py`, `tests/test_api_files.py`, `tests/test_store.py`

**Interfaces:**
- `workbench.py`:
  - `MEASURE_WORKBENCH: Final = (0, 10)`.
  - `version_at_least(version: str, floor: tuple[int, int]) -> bool`: the `_RELEASE` rule moved from `nextext.py`. `serves_measure(version: str) -> bool` uses it, and so does `serves_media`.
  - `@dataclass(frozen=True) class Measure: total: int; unit: MeasureUnit; chars: int; capped: bool`, with `as_stored(self) -> dict[str, Any]` giving `{"total", "unit", "chars", "capped"}`.
  - `async def WorkbenchClient.measure(self, *, call: CallFor, path: str, timeout_s: float = PREPARE_TIMEOUT_S) -> Measure`:
    - `POST /files/measure`, `json={"path": path}` (contract A), the chat's headers with `Accept-Encoding: identity`, under `_rest_errors("measure")`;
    - a path outside the chat's folder raises `WorkbenchNotFound`, and is never sent;
    - `404` is `WorkbenchNotFound`, `507` is `StorageFull`, `422` raises its token, any other non-200 is `http:<status>`;
    - an answer whose `unit` is not a `MeasureUnit`, whose `total` or `chars` is not an int ≥ 0 (a `bool` included), or whose `capped` is not a bool, is `bad_answer`.
- `schemas.py`:
  - `MeasureState = Literal["pending", "measured", "none"]`.
  - `LengthOut(BaseModel)`: `state: MeasureState`, `total: int | None`, `unit: MeasureUnit | None`, `too_long: bool`.
  - `AttachmentOut.length: LengthOut | None = None`.
- `store.py`:
  - `ChatStore(engine, *, file_days, notice_days, prompt_budget: int = PROMPT_TOKENS, now=utcnow)`. `main.py` passes `prompt_tokens(settings.context_tokens, reasoning=False)`.
  - `_too_long(self, attachment: Attachment) -> bool`: measured, and `capped` or `chars / CHARS_PER_TOKEN > prompt_budget`.
  - `_attachment_out` serves `length`:
    - `None` while the file has expired or the state is `NULL`;
    - else `LengthOut(state, total, unit, too_long)`, where `pending` and `none` carry `None`, `None`, `False`.
  - `AttachmentRef.measure_state: MeasureState | None`.
  - `add_attachment(..., measure_state: MeasureState | None = None)`.
  - `settle_measure(self, attachment_id: str, measure: Mapping[str, Any] | None) -> bool`: only a `pending` row changes. A mapping stores it as `measured`; `None` stores `none`.
  - `pending_measures(self) -> list[AttachmentRef]`: `ready` rows still `pending`.
  - `settle_pending_measures(self) -> int`: every `pending` row becomes `none`.
  - `due_contents` and `empty_contents` treat `measure` as they treat `render`: due once the file's period has passed, emptied together.
- `processing.py`:
  - `MEASURE_RECIPIENT: Final = "workbench:measure"`.
  - `MEASURE_SLOTS: Final = 1`, the measures at once. `self._measuring = asyncio.Semaphore(MEASURE_SLOTS)` (call 12).
  - `PrepareJob` gains `prepare: bool = True` and `measure: bool = False`, after `size`.
  - `_requeued(ref)` sets `prepare=ref.status == "processing"` and `measure=ref.measure_state == "pending"`.
  - `requeue()` and `requeue_chat()` submit `processing_attachments()` and `pending_measures()`, each id once.
  - `_run`, the order decided here (call 12):

    ```python
    if job.prepare:
        gate = self._background if job.background else nullcontext()
        async with gate, self._slots:      # as today
            await self._prepare(job)
    if job.measure:                         # after leaving that block: never nested in it
        async with self._measuring:         # neither _slots nor _background
            await self._measure(job)
    ```

    A clip never measures. An unexpected exception settles as today.
  - `_measure(job) -> None`:
    1. `_hand_over(job, target, recipient=MEASURE_RECIPIENT, criteria={"kind": job.kind})`. Unrecorded: `settle_measure(id, None)`, nothing sent.
    2. `workbench.measure`, asked once more after `PREPARE_RETRY_S` on `RETRIED`.
    3. `settle_measure(id, measure.as_stored())`. A failure gets an `error` child (`reason`, its token) through `_record_error`, then `settle_measure(id, None)`.
- `files.py`:
  - `_measures_offered(request) -> bool`: `serves_measure` of the cached discovery (`system_call()`, `images=False`), on a pool thread.
  - The upload:
    - `measured = kind == "document" and await run_in_threadpool(_measures_offered, request)`;
    - `add_attachment(..., measure_state="pending" if measured else ("none" if kind == "document" else None))`;
    - a job when `prepared or later or measured`, as `PrepareJob(..., background=later, size=..., prepare=prepared or later, measure=measured)`.
  - `_read_after_wait` answers at once unless the row is `processing`, or is `ready` with `length.state == "pending"`; then it waits on the job as today.
- `FakeDoor`:
  - `_ROUTES` gains `("POST", "/files/measure")` and `answers["/files/measure"] = None`.
  - The default answers a stored file of the chat as `{"path", "kind": "text", "unit": "line", "total": <its lines>, "chars": <its bytes>, "capped": False}`, or `self.measures[path]` when set, and 404 otherwise.
  - `gates: dict[str, asyncio.Event]`, by route as `delays` is: a request waits for its route's event before it is answered, so a test can hold a measure or a prepare open.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_workbench.py`:
    - `test_measure_asks_for_one_file_of_the_chat`: the method, the route and the JSON body `{"path": …}`; the chat's session, purpose and request id; the answer read into `Measure`.
    - `test_measure_maps_refusals`, parametrized: `404`, `422` `unreadable_file: …`, `507`, `503`; a `unit` of `"chapter"`; `chars: -1`; `capped: "yes"`; `total: true`.
    - `test_measure_never_asks_outside_the_chat`.
    - `test_serves_measure`: `"0.10.0"`, `"0.11"` and `"1.0.2"` give `True`; `"0.9.3"`, `""` and `"dev"` give `False`.
    - `tests/test_nextext.py::test_serves_media` stays green.
  - `tests/test_processing_measure.py` (a store, a `WorkbenchClient` over `FakeDoor(server_version="0.10.0")`, an emitter on `CapturedAuditStream`):
    - `test_a_text_file_is_measured_in_the_background`:
      - the row is `ready` with `pending` before the job runs, then `measured` with the fake's numbers;
      - the `disclose` `chat.attachment` (`workbench:measure`, `kind: document`) event comes before the request.
    - `test_a_pdf_is_measured_after_its_prepare_in_the_same_job`: one job; the requests are `/files/prepare`, then `/files/measure`; the row is `ready` before the measure answers.
    - `test_an_office_file_is_measured_after_its_background_prepare`, under `anyio.fail_after(5)`:
      - the job ends, with no deadlock on `_background`;
      - the requests are `/files/prepare`, then `/files/measure`;
      - the manifest is kept and the state is `measured`.
    - `test_a_measure_holds_no_prepare_slot`:
      - `gates["/files/measure"]` holds a text file's measure open;
      - then two pictures are submitted with `gates["/files/prepare"]` closed, and an Office file;
      - both pictures' `/files/prepare` requests are in flight at once, which uses both `PREPARE_SLOTS`, and the Office file's background prepare is sent once a slot frees;
      - with the prepare gate opened, both pictures are `ready` while the measure is still held.
    - `test_measures_run_one_at_a_time`: with the first of two documents' measures held, the second's request has not arrived. Once it is released, the second arrives.
    - `test_a_failed_measure_is_none`, parametrized over `422` and `503` twice: state `none`, an `error` child with `reason`, and no error-level log.
    - `test_an_unrecorded_measure_sends_nothing`: `unrecorded_child(monkeypatch, "chat.attachment")` gives no `/files/measure` request and state `none`.
    - `test_a_measure_after_take_back_writes_nothing` (Review Focus 3):
      - `FakeDoor.delays["/files/measure"]` set, then `processor.cancel(id)` and `remove_pending_attachment` mid-request: no settle, no `error` child, and no error-level log;
      - again with the row deleted and no cancel: `settle_measure` returns `False`, and one debug line.
    - `test_a_chat_deleted_mid_measure_writes_nothing` (`cancel_chat`).
    - `test_a_pending_measure_is_requeued_at_start`: a `ready` `pending` row and `requeue()` give one job with `prepare=False`. Its hand-over is the system's primary, `requeued: true`.
  - `tests/test_api_files.py`:
    - `test_a_document_is_ready_at_once_and_measured`:
      - 201 with `status "ready"` and `length {"state": "pending", "total": null, "unit": null, "too_long": false}`;
      - `GET …?wait=20` answers `measured` with `total` and `too_long false`.
    - `test_a_long_document_is_too_long`, parametrized: `chars = 3 * 34_000 + 3` gives `too_long true`; `capped: true` with `chars` 10 gives `too_long true`; `chars = 3 * 34_000` gives `false`.
    - `test_no_measure_before_workbench_0_10`: `"0.9.0"` gives `length.state "none"` and no `/files/measure` request.
    - `test_a_picture_has_no_length`: `length is null`.
    - `test_the_long_poll_waits_on_a_pending_measure`: with the measure delayed, a `?wait=` request answers only once it is measured.
  - `tests/test_store.py`:
    - `test_length_goes_with_the_file`: past the file's period, `length is None`, and the sweep empties `measure` with `render`.
    - `test_settle_pending_measures_at_start_without_workbench`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workbench.py tests/test_processing_measure.py tests/test_api_files.py tests/test_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, then the texts:
  - `docs/audit.md`:
    - the attachment row gains, for a document with workbench 0.10.0, `disclose` `chat.attachment` (`workbench:measure`, criteria `kind`) before the measure, and an `error` one (`reason`) if it fails;
    - "start-up …" covers a `pending` measure.
  - `processing.py`'s module docstring gains the measure step: after the prepare and out of its slots, on `MEASURE_SLOTS`, one at a time; `pending`, then `measured` or `none`; requeued at start.
  - `docs/configuration.md`'s caps table gains `MEASURE_SLOTS` | 1 | "Documents measured at once, on a slot of their own, never one of `PREPARE_SLOTS` or `BACKGROUND_SLOTS` (`processing.py`)".
  - The slots sentence. `processing.py:29` and `docs/configuration.md:74` say "`GHOSTWRITER_WORKBENCH_SLOTS` at 3 or more". Each says instead, in its own form: "From workbench 0.10.0 a measure has a gate of its own too, so Send never waits for an Office file or a measure while workbench's `GHOSTWRITER_WORKBENCH_SLOTS` is 4 or more, its default; at 3, a prepare, a conversion and a measure together take every slot." Task 12 does the same in `CLAUDE.md:37` and `docs/data-flow.md:106`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(processing): workbench 0.10 measures every document in the background; AttachmentOut.length`

### Task 7: `document_partial` and `document_unread`

**Files:**
- Modify:
  - `ghostwriter/agent/notices.py`: `SentFile`, `Reading`, `covered`;
  - `ghostwriter/store.py` (`UserTurn` `:132-144`, `user_turn` `:1404`; new `chat_files`);
  - `ghostwriter/agent/loop.py`: `_Progress`; `_answer`, where each call is recorded and the notices come at the end.
- Create: `tests/test_agent_notices.py`
- Test: `tests/test_agent_loop.py`, `tests/test_api_messages.py`

**Interfaces:**
- Consumes:
  - Task 1's `cut: true` on a part Ghostwriter cut;
  - Task 3's `NoticeEvent` and `_Progress.told`;
  - Task 6's measure.
- `notices.py`:
  - `@dataclass(frozen=True) class SentFile: file_id: str; path: str; name: str; kind: AttachmentKind; status: AttachmentStatus; total: int | None; unit: MeasureUnit | None`. `total` and `unit` are the measure's, while it is `measured` and the file has not expired.
  - `BLIND_TOOLS: Final = frozenset({"run_python", "search_files"})`, `OPENING_TOOLS: Final = frozenset({"read_document", "view_file"})`.
  - `TOTAL_PARTS_UNITS: Final[Mapping[str, MeasureUnit]] = {"pdf": "page", "pptx": "slide", "docx": "paragraph", "odt": "paragraph", "text": "line"}` (call 5).
  - `covered(spans: Iterable[tuple[int, int]]) -> int`.
- `class Reading`, what one message's calls opened and read:
  - `call(self, tool: str, arguments: Mapping[str, Any] | None, outcome: ToolResult | ToolFailure | Refusal, content: str) -> None`.
    - A `BLIND_TOOLS` call that was sent (not a `Refusal`) silences every notice of this class.
    - An `OPENING_TOOLS` call answered with a `ToolResult` opens its `path` argument (normalised with `posixpath.normpath`).
    - A `read_document` `ToolResult`: `content`'s first line read as JSON. Its `path` is opened too. Each part whose `units` is a valid `[first, last]` and whose `cut` is not true adds a span. When the unit is page or slide, every page of `pages_without_text` adds `(p, p)` (call 5). Its `kind` and `total_parts` are kept, and whether any part carried a `units` key.
  - `partial(self, files: Mapping[str, SentFile]) -> list[NoticeEvent]`: per path read in order, a `SentFile` of the chat, and an answer that carried `units`.
    - `total`/`unit` come from the measure, else `total_parts` with `TOTAL_PARTS_UNITS[kind]`, else no notice.
    - When `covered(spans) < total`, the notice is `NoticeEvent("document_partial", file_id, name, read, total, unit)`.
  - `unread(self, attached: Sequence[SentFile], *, sees: bool) -> list[NoticeEvent]`: each `ready` attachment of kind `document` or `media`, or `image` when not `sees`, whose path was not opened, gives `NoticeEvent("document_unread", file_id=…, name=…)`.
  - Both are empty once a blind tool ran.
- `store.py`:
  - `UserTurn.files: tuple[SentFile, ...] = ()`, the question's attachments in order.
  - `chat_files(self, chat_id: str) -> dict[str, SentFile]`: every attachment of the chat, by path.
- `loop.py`:
  - `_Progress.reading: Reading`.
  - In `_answer`, after each call's `content`: `message.reading.call(call.name, parse_arguments(call.arguments), outcome, content)`.
  - When the rounds end without an exception (also after a stop or a model error), each notice of `reading.partial(self._store.chat_files(turn.chat_id))`, then of `reading.unread(question.files, sees=sight.sees)`, is yielded through `message.told`, after `answer_cut`.

`covered`, decided here:

```python
def covered(spans: Iterable[tuple[int, int]]) -> int:
    count, end = 0, 0
    for first, last in sorted(spans):
        if last > end:
            count += last - max(first, end + 1) + 1
            end = last
    return count
```

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_notices.py`:
    - `test_covered_counts_the_union`, parametrized: `[(1, 9)]` gives 9; `[(1, 9), (5, 12)]` 12; `[(1, 3), (7, 7)]` 4; `[(3, 3), (3, 3)]` 1; `[]` 0.
    - `test_a_document_read_in_part_says_how_much`:
      - `long-report.pdf` measured at 500 pages, one answer with parts `[1, 4]` and `[5, 9]`;
      - `[NoticeEvent("document_partial", FILE, "long-report.pdf", 9, 500, "page")]`.
    - `test_a_cut_part_does_not_count`: workbench's `cut: true`, and a part `cut_result` cut.
    - `test_pages_without_text_count_as_read`: parts 1–2 and 4, `pages_without_text: [3]` of a measured 4 give no notice.
    - `test_without_units_there_is_no_partial_notice`: an answer shaped as workbench 0.9's.
    - `test_total_parts_stands_in_where_a_part_is_one_unit`: no measure, kind `pdf`, `total_parts` 12, read 1–3 gives `3 of 12 page`. Kind `xlsx` gives none.
    - `test_a_whole_read_has_no_notice`.
    - `test_a_blind_tool_silences_both`, parametrized over `run_python` and `search_files`. A refused `run_python` does not.
    - `test_an_unopened_attachment_is_unread`, `test_a_failed_read_does_not_open`, `test_view_file_opens`.
    - `test_a_picture_counts_only_without_sight`.
    - `test_a_failed_attachment_is_not_reported`.
  - `tests/test_agent_loop.py`:
    - `test_the_notices_come_last_and_are_stored`:
      - a question attaching `long-report.pdf` (measured), one `read_document` round answering parts 1–9, then the text;
      - the events end `[..., TokenEvent(...), NoticeEvent("document_partial", ...)]`;
      - the stored notices hold `file_id`, `read`, `total` and `unit`, but no name.
    - `test_an_attachment_not_read_without_tools_is_unread`: `workbench=None` and an attached `tide-notes.txt` give `document_unread`.
    - `test_an_earlier_attachment_read_in_part_is_named`.
  - `tests/test_api_messages.py::test_a_regenerated_answer_reports_again`: the first answer read nothing (`document_unread`), the regenerate read it whole, and the new answer's `notices == []`.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_notices.py tests/test_agent_loop.py tests/test_api_messages.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): say which document was read in part, and which attachment was not read`

### Task 8: Continue a cut answer in place

**Files:**
- Modify:
  - `ghostwriter/inference.py` (`ModelClient.stream` `:137-178`), `ghostwriter/agent/stream.py` (`stream_round` `:301-383`);
  - `ghostwriter/agent/context.py` (new `continuation`), `ghostwriter/agent/prompt.py` (`CONTINUE`);
  - `ghostwriter/agent/loop.py`: `Turn` `:140-162`, `run`, `_Progress`, new `_continue`;
  - `ghostwriter/store.py` (new `answer_notices` and `answered_turns`);
  - `ghostwriter/api/messages.py`: the new route, constants, `_answer` `:425-475`, the module docstring;
  - `docs/audit.md`.
- Test: `tests/test_agent_stream.py`, `tests/test_agent_loop.py`, `tests/test_api_messages.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 4 and 5 (`_round`, `_at_cap`, `NoticeEvent.from_stored`, `next_prompt_tokens`).
- `inference.py`:
  - `CONTINUE_BODY: Final` is the Global Constraints' value.
  - `ModelClient.stream(..., continuation: bool = False)`. On `vllm`, `extra_body` is `CONTINUE_BODY` when continuing, else as today. Elsewhere nothing is sent for it.
  - `stream_round(..., continuation: bool = False)` passes it on.
- `prompt.CONTINUE: Final = "Go on exactly where your answer stopped; repeat nothing."`
- `context.continuation(this: PastTurn, *, instruct: bool) -> list[dict[str, Any]]`:
  - `_replayed(this)`: the question, each round's calls and results in history form, then the answer so far as the last assistant message;
  - with `instruct`, a user message `CONTINUE` after it.
- `store.py`:
  - `answer_notices(self, message_id: str) -> list[dict[str, Any]]`: the stored list as stored, `[]` for `NULL`.
  - `answered_turns(self, chat_id: str, answer_id: str) -> list[PastTurn]`: every turn through that answer, with `seen=False`, the last being the answer's own.
- `loop.py`:
  - `Turn.continued: bool = False`.
  - `_Progress.base: str = ""`. The stored text is `base + "".join(shown)`.
  - `run` dispatches to `_continue` when `turn.continued`.
  - `_continue(turn, audit, message)`:
    1. `earlier, this = turns[:-1], turns[-1]`, from `answered_turns`. `message.base = this.answer`.
    2. `message.notices` = the stored notices but `answer_cut`, through `from_stored`.
    3. The offer is `self._offer(blind discovery, seeing=False, …)`. The request is `_Request(seeing=None, blind=lambda: offer, past=earlier, budget=prompt_tokens(settings.context_tokens, reasoning=False))`, with `_Sight(sees=False, budget=None, known={})`.
    4. `current = continuation(this, instruct=settings.inference_provider != "vllm")`.
    5. One `yield from self._round(..., tooled=False, round_no=1)`. `_round` passes `continuation=turn.continued` to `stream_round`. `turn.reasoning` is `False`.
    6. `answer_cut` again (`message.told`) when the round did not end cleanly below `MAX_ANSWER_TOKENS`: `done is None`, `done.stopped`, or `_at_cap(done, MAX_ANSWER_TOKENS)` (call 7).
    7. `next_prompt` counts `[*earlier, replace(this, answer=base + appended)]`.
- `messages.py`:
  - `NOT_CONTINUABLE: Final = "Only the latest answer cut at the length limit can be continued."` and `NOT_CONTINUABLE_CODE: Final = "not_continuable"`.
  - `_answer(..., continued: bool = False)` builds `Turn(..., continued=continued)`.
  - The route `POST /{chat_id}/messages/{message_id}/continue`, `continue_answer(chat_id, message_id, request, user, op: Annotated[Operation, Depends(audited("modify", "chat.continue", also=("ai_call",)))]) -> TurnResponse | JSONResponse`:
    - claim the chat, else `409 busy`;
    - `owned_chat`;
    - `409 not_continuable`, the claim ended, when `message_id` is not `last_answer`, its notices hold no `answer_cut`, or it has no question;
    - `op.container = chat_id` and `op.record(target=Target(kind="message", ids=(message_id,)))`, before anything is stored;
    - `_answer(..., question_id, answer_id=message_id, user_message_id=None, reasoning=False, continued=True)`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_agent_stream.py::test_a_continuation_continues_the_last_message_on_vllm`, parametrized:
    - `("vllm", CONTINUE_BODY)` and `("ollama", absent)`;
    - `max_tokens == 4_000` and no `tools` key.
  - `tests/test_agent_loop.py`:
    - `test_a_continuation_appends_to_the_answer`: the stored answer `"Kapitel 1 … und"` with `answer_cut`, and a round `" weiter."`. The content is `"Kapitel 1 … und weiter."`, and the notices are `[]`.
    - `test_on_vllm_the_last_message_is_the_answer_so_far`: the request's last message is `{"role": "assistant", "content": base}`, and there is no reasoning switch.
    - `test_elsewhere_the_model_is_asked_to_go_on`: ollama; the last two messages are the assistant's base, then `{"role": "user", "content": CONTINUE}`.
    - `test_a_continuation_replays_its_calls_in_history_form`: an answer with one step gives an assistant `tool_calls` message and a tool message cut to `HISTORY_RESULT_CHARS`.
    - `test_a_continuation_cut_again_keeps_answer_cut`.
    - `test_a_clean_continuation_keeps_the_other_notices`: `document_unread` stays and `answer_cut` goes.
    - `test_a_stopped_continuation_keeps_what_it_appended`: a `Gate` after two chunks, then a stop. The content is base plus the two chunks, and `answer_cut` stays.
    - `test_a_failed_continuation_changes_nothing_but_the_estimate`: a model error leaves the base and `answer_cut`.
    - `test_a_context_refusal_of_a_continuation_is_the_context_error`.
  - `tests/test_api_messages.py`:
    - `test_continue_streams_into_the_same_answer`: `start` is `{"message_id": answer, "user_message_id": None}`; the tokens follow; `final` carries `context_estimate`. `GET` shows one answer, appended.
    - `test_continue_needs_the_latest_answer_cut`, parametrized: an earlier answer, the latest without `answer_cut`, a question's id, and an unknown id each give `409 not_continuable` with nothing recorded. Another owner's chat gives 404.
    - `test_continue_twice_is_refused` (Review Focus 2):
      - while a turn holds the chat, `409 busy`;
      - after a clean continuation, `409 not_continuable`, the content unchanged.
    - `test_continue_is_recorded_before_anything_is_stored`:
      - `modify` / `chat.continue`, target `message` [answer id], then an `ai_call` `chat.answer` child;
      - with a failing stream, the generic 500 and the content unchanged.
    - `test_a_stop_ends_a_continuation`: `POST …/stop` mid-stream gives `final` `stopped: true`, and the appended text is kept.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_stream.py tests/test_agent_loop.py tests/test_api_messages.py -k continu -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `docs/audit.md` gains the row `POST /api/chats/{id}/messages/{message_id}/continue` | `modify` `chat.continue` | `message` [continued id] | `ai_call` `chat.answer` for its one round.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(messages): continue a cut answer in place, one round, recorded as chat.continue`

### Task 9: What the model is told of a long document

**Files:**
- Modify:
  - `ghostwriter/agent/context.py` (`attachment_facts` `:177-209`);
  - `ghostwriter/store.py` (`_prompt_text` `:1355-1380` passes `too_long=self._too_long(attachment)`);
  - `ghostwriter/agent/prompt.py` (`PAGING`, `system_prompt`).
- Test: `tests/test_agent_context.py`, `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: Task 6's `_too_long`.
- `context.LONGER_THAN_A_PROMPT: Final = "longer than one prompt: read it in parts and say which parts you read"`.
- `attachment_facts(kind, manifest, *, seen, name, too_long: bool = False)`. For a document with `too_long`: `f"{facts}; {LONGER_THAN_A_PROMPT}"`, or the phrase alone without facts. A picture or a clip is never `too_long`.
- `prompt.PAGING: Final = "A long document comes in parts: when read_document's answer has next_part, call it again with part set to next_part to read on, and say plainly which parts you did not read."`. `system_prompt` appends it right after the folder sentences, only while tools are offered.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_long_document_says_to_read_it_in_parts`, parametrized:
    - a PDF manifest of 500 pages gives `PDF, 500 pages; longer than one prompt: read it in parts and say which parts you read`;
    - no manifest gives the phrase alone;
    - not too long gives the line as before.
  - `test_the_long_line_rides_the_attachment_line` (store): `[Attached: long-report.txt (/workspace/<chat>/long-report.txt, 1234 bytes; longer than one prompt: read it in parts and say which parts you read)]`.
  - `test_the_prompt_says_how_to_page_with_tools_only`: `PAGING` is in the prompt with a folder, and not without one. Update the exact-prompt asserts in `tests/test_agent_context.py:465` and `tests/test_agent_loop.py:707`.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_context.py tests/test_agent_loop.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): tell the model a long document is read in parts, and how to page`

### Task 10: The SPA's notices, Continue and the context error, on infra-ui 0.28.0

**Files:**
- Create: `frontend/src/components/NoticeLines.tsx`
- Modify:
  - `frontend/package.json` (`@infra/ui` `:19`), `frontend/pnpm-lock.yaml`;
  - `frontend/src/api/types.ts` (`MessageOut` `:70-78`, `FinalFrame` `:151-157`, `ErrorFrame` `:159-162`, `NoticeCode` `:166`, `NoticeFrame` `:168-170`);
  - `frontend/src/hooks/chatStreams.tsx` (`DraftAnswer`, `StreamState`, `NOTICE_CODES` `:71`, `applyFrame` `:89-94`, `start` `:165-223`), `frontend/src/hooks/useChatStream.ts`;
  - `frontend/src/components/MessageView.tsx`, `frontend/src/components/Conversation.tsx` (`ERROR_KEYS`, `REFUSAL_KEYS`, `NoticeLine` removed, `:143-155`);
  - `frontend/src/test/fixtures.tsx` (`message()`), `frontend/src/i18n/en.ts`, `de.ts`.
- Test: `frontend/src/components/Conversation.test.tsx`, `frontend/src/hooks/useChatStream.test.tsx`

**Interfaces:**
- `package.json`: `"@infra/ui": "https://codeload.github.com/nos-tromo/infra-ui/tar.gz/<INFRA_UI_SHA>"`, where `<INFRA_UI_SHA>` is `git ls-remote https://github.com/nos-tromo/infra-ui refs/tags/v0.28.0^{}`, then `pnpm install`. It lands in this task because its notice lines use `text-warning`; Task 11 uses `ContextMeter` and `warning`.
- `types.ts`:
  - `NoticeCode = 'answer_cut' | 'document_partial' | 'document_unread' | 'images_dropped' | 'context_trimmed'`.
  - `MeasureUnit = 'page' | 'slide' | 'paragraph' | 'row' | 'line'`.
  - `NoticeOut { code: NoticeCode; file_id?: string | null; name?: string | null; read?: number | null; total?: number | null; unit?: MeasureUnit | null }`, and `NoticeFrame = NoticeOut`.
  - `MessageOut` gains `notices: NoticeOut[] | null` and `context_estimate: number | null`.
  - `ErrorFrame.code` gains `'context'`, and `FinalFrame` gains `context_estimate: number | null`.
- `chatStreams.tsx`:
  - `DraftAnswer.notices: NoticeOut[]`, deduplicated by `code` and `file_id`. An unknown code is ignored.
  - `StreamState.notices` is removed, and `ended` keeps a failure only (call 11).
  - `start(chatId: string, route: 'messages' | 'regenerate' | 'continue', body: MessageIn | RegenerateIn | Record<string, never>, pendingUser: PendingMessage | null, base?: MessageOut)`.
    - `'continue'` posts to `/chats/{chatId}/messages/{base.id}/continue` with `{}`.
    - Its draft starts as `{ text: base.content, steps: base.steps, results: base.results, notices: (base.notices ?? []).filter((n) => n.code !== 'answer_cut'), reasoning: false, error: null }`, and tokens append to it.
- `useChatStream.ts`: `ChatStream.continueAnswer(answer: MessageOut): Promise<void>`.
- `NoticeLines({ notices, live, onContinue }: { notices: readonly NoticeOut[]; live: boolean; onContinue?: () => void })`:
  - one line per answer-level notice, its text muted (`text-sm text-muted-foreground`) and its `WarningIcon` `text-warning`; `context_trimmed` is never shown here;
  - `role="status"` only when `live`;
  - the `answer_cut` line carries a ghost `Button` "Continue" when `onContinue` is given;
  - `document_partial` uses its details while `name`, `read`, `total` and `unit` are all set, and the generic text otherwise. `document_unread` does the same with `name`.
- `MessageView`'s `message` gains `'notices'`, and it gains `onContinue?: () => void`. It renders `<NoticeLines notices={message.notices ?? []} live={false} onContinue={onContinue} />` after the cards. `DraftView` renders `draft.notices` with `live`.
- `Conversation`:
  - `ERROR_KEYS.context = 'error.context'` and `REFUSAL_KEYS.not_continuable = 'chat.not_continuable'`;
  - `onContinue` goes to the `lastAnswer` while its notices hold `answer_cut`.
- i18n (en / de), exact:
  - `notice.answer_cut`: `The answer was cut at the length limit.` / `Die Antwort wurde an der Längengrenze abgeschnitten.`
  - `notice.continue`: `Continue` / `Weiter`
  - `notice.document_partial`: `Of '{name}', {read} of {total} {units} were read.` / `Von ‚{name}‘ wurden {read} von {total} {units} gelesen.`
  - `notice.document_partial_generic`: `A document was read only in part.` / `Ein Dokument wurde nur teilweise gelesen.`
  - `notice.document_unread`: `'{name}' was not read for this answer.` / `‚{name}‘ wurde für diese Antwort nicht gelesen.`
  - `notice.document_unread_generic`: `An attachment was not read for this answer.` / `Ein Anhang wurde für diese Antwort nicht gelesen.`
  - The units, `_one` for a `total` of 1, as `trail.steps_one`:
    - `unit.page` / `unit.page_one`: `pages` / `page`, `Seiten` / `Seite`;
    - `unit.slide` / `unit.slide_one`: `slides` / `slide`, `Folien` / `Folie`;
    - `unit.paragraph` / `unit.paragraph_one`: `paragraphs` / `paragraph`, `Absätze` / `Absatz`;
    - `unit.row` / `unit.row_one`: `rows` / `row`, `Zeilen` / `Zeile`;
    - `unit.line` / `unit.line_one`: `lines` / `line`, `Zeilen` / `Zeile`.
  - `error.context`: `This chat is too long for the model. Start a new chat or shorten your message.` / `Dieser Chat ist zu lang für das Modell. Beginnen Sie einen neuen Chat oder kürzen Sie Ihre Nachricht.`
  - `chat.not_continuable`: `Only the latest answer cut at the length limit can be continued.` / `Nur die letzte Antwort, die an der Längengrenze abgeschnitten wurde, kann fortgesetzt werden.`
  - `chat.images_dropped` stays as it is.
- `fixtures.message()` defaults `notices: null, context_estimate: null`.

- [ ] **Step 0: Pin infra-ui 0.28.0.** Set `<INFRA_UI_SHA>` as above and run `cd frontend && pnpm install`. Check that `grep -c ContextMeter node_modules/@infra/ui/dist/index.d.ts` is at least 1, then run `pnpm test`: everything is still green on the new pin.
- [ ] **Step 1: Write the failing tests.**
  - `useChatStream.test.tsx`: rewrite `keeps an images_dropped notice …` as `it('shows a notice live under the draft, then with the stored answer, and keeps it there')`:
    - the live line is `role="status"`; sent twice, it is said once; an unknown code is ignored;
    - after `final` the stored answer carries `notices: [{ code: 'images_dropped' }]`, its line has no `role`;
    - after the next message, the earlier answer still has its line.
  - `Conversation.test.tsx`:
    - `it('shows a stored answer's notices after a reload')`: `answer_cut`, `document_partial` (`long-report.pdf`, 9 of 500 page) and `document_unread`. Exact texts: `Of 'long-report.pdf', 9 of 500 pages were read.`; `context_trimmed` is not shown under the answer.
    - `it('says one page')`: `total: 1, read: 0` gives `0 of 1 page`.
    - `it('falls back to the generic text once the details expired')` (Review Focus 5): `{ code: 'document_partial' }` gives `A document was read only in part.`
    - `it('renders an answer stored before notices')` (Review Focus 1): `notices: null` gives no notice line and no Continue.
    - `it('continues a cut answer in place')`:
      - Continue shows only on the latest answer with `answer_cut`;
      - a click posts `…/messages/a2/continue`;
      - the draft shows the base text and then `Kapitel 1 … und weiter.`, never two answers;
      - after `final` there is no Continue.
    - `it('names a context refusal')`: an `error` frame `{code: 'context'}` gives the exact `error.context` text, `role="alert"`.
    - `it('names a refused continue')`: `409 not_continuable` gives the `chat.not_continuable` text.
    - In German: `Von ‚long-report.pdf‘ wurden 9 von 500 Seiten gelesen.`
- [ ] **Step 2: Run** `cd frontend && pnpm test src/components/Conversation.test.tsx src/hooks/useChatStream.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** `pnpm typecheck` names every fixture still missing a field.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm build && pnpm test`. Expected: PASS, with `i18n.test.ts` parity and "Sie" green.
- [ ] **Step 5: Commit** `build(frontend): pin @infra/ui 0.28.0` (Step 0 alone), then `feat(frontend): notices stored with their answer, Continue, and the context error`

### Task 11: The SPA's meter, chat note and tile warning

**Files:**
- Create: `frontend/src/lib/context.ts`, `frontend/src/hooks/useContextUse.ts`, `frontend/src/components/ContextNote.tsx`
- Modify:
  - `frontend/src/api/types.ts` (`AttachmentOut` `:25-35`, `AppConfig` `:88-113`);
  - `frontend/src/components/Composer.tsx` (props, `end` `:281-290`), `frontend/src/components/ChatPane.tsx`, `frontend/src/App.tsx`;
  - `frontend/src/hooks/useChats.ts` (`useStartChat`), `frontend/src/components/ChatList.tsx` (`start` `:67`), `frontend/src/components/AttachmentTiles.tsx` (`Tile` `:68-79`);
  - `frontend/src/hooks/useAttachments.ts` (`followed` `:381-408`, the effect `:427-434`), `frontend/src/test/fixtures.tsx` (`CONFIG`), `frontend/src/i18n/en.ts`, `de.ts`.
- Test: `frontend/src/components/Composer.test.tsx`, `frontend/src/lib/context.test.ts`

**Interfaces:**
- Consumes: Task 10's pin of infra-ui 0.28.0 (`ContextMeter`, `AttachmentTile.warning`, `text-warning`).
- `types.ts`:
  - `AttachmentLength { state: 'pending' | 'measured' | 'none'; total: number | null; unit: MeasureUnit | null; too_long: boolean }`;
  - `AttachmentOut.length: AttachmentLength | null`;
  - `AppConfig` gains `prompt_tokens: number` and `reasoning_prompt_tokens: number`.
- `lib/context.ts`:
  - `export const NEAR_FULL_RATIO = 0.8`;
  - `contextRatio(estimate: number | null, config: AppConfig, reasoning: boolean): number | null`: `null` without an estimate, else `Math.min(1, estimate / budget)`, never rounded.
  - `nearlyFull(ratio: number | null): boolean`: `ratio !== null && ratio >= NEAR_FULL_RATIO`. Like the meter's own level, it is decided on that unrounded ratio, so the ring and the note always agree.
- `useContextUse(chatId: string, stream: ChatStream, config: AppConfig, reasoning: boolean): { ratio: number | null; trimmed: boolean }`:
  - `ratio` is from the latest stored answer's `context_estimate`;
  - `trimmed`, while streaming, is the draft's `context_trimmed`; else the latest stored answer's.
- `ContextNote({ nearlyFull, trimmed, onNewChat }: { nearlyFull: boolean; trimmed: boolean; onNewChat: () => void })`: nothing when both are false. Otherwise the sentence(s), muted, with a `WarningIcon` in `text-warning`, and a ghost `Button` labelled `chat.new`. It is not a live region.
- `Composer` gains `onNewChat: () => void`:
  - `end` starts with `<ContextMeter value={ratio} label={t('context.meter', { n: Math.round(ratio * 100) })} warnAt={NEAR_FULL_RATIO} />` while `ratio !== null`. `ContextMeter` is imported from `@infra/ui`, and the same unrounded `ratio` goes to both;
  - `<ContextNote nearlyFull={nearlyFull(ratio)} trimmed={trimmed} onNewChat={onNewChat} />` sits above the pill.
- `useChats.useStartChat(onSelect: (id: string | null) => void): { start: () => void; pending: boolean; error: unknown }`, the `create.mutate(…, { onSuccess })` of `ChatList` moved. `ChatList` uses it, and `App` passes `onNewChat` through `OpenChat` and `ChatPane` to `Composer`.
- `AttachmentTiles.Tile`: `warning={file.attachment?.length?.too_long ? t('composer.too_long') : undefined}`. infra-ui shows it only on a `ready` tile, so Ghostwriter does not gate it on the status.
- `useAttachments`:
  - a file is followed while it is `processing`, or `done` with `attachment.length?.state === 'pending'`;
  - an answer no longer `processing` calls `ended(file, latest)` at once, so Send is enabled, and following goes on while it is still `pending`.
- `fixtures.CONFIG` gains `prompt_tokens: 34_000, reasoning_prompt_tokens: 26_000`, and each attachment fixture `length: null`.
- i18n (en / de), exact:
  - `context.meter`: `about {n} % of the context used` / `ca. {n} % des Kontexts belegt`
  - `context.nearly_full`: `This chat is nearly as long as the model can read at once. For a new topic, start a new chat.` / `Dieser Chat ist fast so lang, wie das Modell auf einmal lesen kann. Für ein neues Thema beginnen Sie besser einen neuen Chat.`
  - `context.trimmed`: `Earlier parts of this chat are no longer sent to the model.` / `Ältere Teile dieses Chats werden nicht mehr an das Modell geschickt.`
  - `composer.too_long`: `Very long: read only in parts` / `Sehr lang: wird nur in Teilen gelesen`

- [ ] **Step 1: Write the failing tests.**
  - `lib/context.test.ts`:
    - `contextRatio(20_400, CONFIG, false) === 0.6`;
    - `contextRatio(20_400, CONFIG, true)` is about `0.785`;
    - `contextRatio(40_000, CONFIG, false) === 1`;
    - `contextRatio(null, CONFIG, false) === null`;
    - `nearlyFull(0.799) === false` and `nearlyFull(0.8) === true`.
  - `Composer.test.tsx`:
    - `it('shows no ring in an empty chat')`.
    - `it('shows how full the chat is, by the switch')`:
      - the latest answer's `context_estimate: 20_400` gives a `meter` with the accessible name `about 60 % of the context used`;
      - the reasoning switch turns it to `about 78 % …`.
    - `it('shows the nearly-full note from 80 % with New chat')`: 27_200 of 34_000 (0.8) gives the exact `context.nearly_full` text. A click on New chat calls `onNewChat`.
    - `it('decides the note on the unrounded ratio, as the ring does')`: 27_166 (0.799) gives a meter with `aria-valuenow` `80` and the label `about 80 % of the context used`, but no note. 27_200 gives the note.
    - `it('shows the trimmed note for the latest answer only')`: an earlier answer with `context_trimmed` gives no note; the latest gives `context.trimmed`; both give both sentences.
    - `it('warns on the tile of a very long document')`: the upload answers `ready` with `length.state: 'pending'`. Send is enabled at once. The follow (`?wait=20`) answers `measured` with `too_long: true`, and the tile shows the text `Very long: read only in parts` (found by text; the document tile has a `div` face, so no `aria-describedby` is asserted).
    - `it('stops following once a document is measured')`: after `measured` there is no further `GET`.
    - `it('does not follow an attachment stored before measuring')`: `length: null`.
  - The `ChatList` tests stay green with `useStartChat`.
- [ ] **Step 2: Run** `cd frontend && pnpm test src/lib/context.test.ts src/components/Composer.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm build && pnpm test`. Expected: PASS.
- [ ] **Step 5: Check by eye.** Run `make dev` and look at the ring, the note and a tile warning in light and dark, at phone width.
- [ ] **Step 6: Commit** `feat(frontend): the context meter, the chat note and the long-document tile`

### Task 12: Docs and release

**Files:**
- Modify:
  - `docs/data-flow.md`, `docs/configuration.md` (§ Caps per message), `docs/retention.md`, `docs/audit.md` (check Tasks 6 and 8's rows), `CLAUDE.md`, `README.md`;
  - `pyproject.toml` (`version = "0.7.0"`), `uv.lock`, `tests/test_version.py`, `tests/test_api_meta.py` (`"version": "0.7.0"`).

- [ ] **Step 1: Docs.**
  - `docs/data-flow.md`:
    - `:104-108` takes Task 6's slots sentence: a conversion, a measure, a picture's prepare and a tool call each take one of workbench's slots, 4 by default from 0.10.0; at 3, a prepare, a conversion and a measure together take every slot;
    - "What is stored": an answer's notices (codes, a file's id, counts), its estimate, and an attachment's measure;
    - "Where data goes": workbench's measure of a document (a path out, counts back), and the Continue round (the chat as for any round).
  - `docs/configuration.md`, the caps: `TOOL_RESULT_CHARS` 16,384 (a paged result is cut by parts), the measure's wait (390 s, above workbench's 270 s for a measure at its defaults), `NEAR_FULL_RATIO` 0.8 (the SPA). Check Task 6's `MEASURE_SLOTS` row. Also say that `GHOSTWRITER_CONTEXT_TOKENS` above the model's real context ends in `error.context`, and how the budgets in `/api/config` follow it.
  - `docs/retention.md`, § Manifests and renders: a measure goes with its file. A new § "An answer's notices": their file ids and numbers follow the content deadline, then only the code stays. § The dry run: the new counts.
  - `CLAUDE.md`:
    - the module map gains `ghostwriter/agent/notices.py`, and the lines for `context.py` (`FitReport`, the next-prompt estimate, `continuation`), `loop.py` (Continue), `workbench.py` (`measure`) and `processing.py` (the measure step);
    - "The workbench seam" gains 0.10.0: the measure, on a gate of workbench's own and on `MEASURE_SLOTS` here, and `parts[].units`;
    - `:37`'s "`GHOSTWRITER_WORKBENCH_SLOTS` at 3 or more" takes Task 6's slots sentence;
    - "A message, end to end" step 4 gains the first round's report, and step 5's frames gain the notice details and `final`'s `context_estimate`; a Continue paragraph;
    - "What changes together" gains:
      - the notice codes: `schemas.NoticeCode`, `frontend/src/api/types.ts` and `NOTICE_CODES`, the i18n keys;
      - the measure units: `schemas.MeasureUnit`, workbench's units and the `unit.*` keys;
      - `CHARS_PER_TOKEN` / `prompt_tokens`, with `too_long` and `/api/config`'s budgets.
  - `README.md`: one line on notices, the meter and Continue.
- [ ] **Step 2: Version.** Bump to `0.7.0` and run `uv lock`. Run `uv run pytest tests/test_version.py tests/test_api_meta.py -q`. Expected: PASS.
- [ ] **Step 3: Gates.** `make verify && make test`. Expected: green. Paste the summary lines into the PR.
- [ ] **Step 4: Commit** `docs: notices, the measure, the meter and Continue` and `chore(release): 0.7.0`.
- [ ] **Step 5: Run the local end-to-end check (spec §7)** on `make dev` with workbench 0.10.0 and Ollama at 40k, with a synthetic document of invented text of about 500 pages. Record each check in the PR:
  - the tile warning;
  - "x of y pages";
  - "not read";
  - the ring growing, then "nearly full" and "trimmed" at the composer only;
  - `answer_cut`, then Continue appending;
  - everything still there after a reload;
  - light and dark.
- [ ] **Step 6: Push and open the PR.** `git push -u origin feat/limit-notices`, then a PR to `main`.
  - The PR's **Operators** line: "0.7.0 needs workbench 0.10.0 for the measure and the page counts (older: no tile warning and no 'x of y'). workbench 0.10.0 raises `GHOSTWRITER_WORKBENCH_SLOTS` to 4; keep it at 4 or more. The schema goes to 3 on start, with no down-migration. The staging run must check that the router passes `continue_final_message` and `add_generation_prompt`, and `error.context` with `GHOSTWRITER_CONTEXT_TOKENS` above the model's context."
  - Confirm CI ran on the last commit. Merging is the owner's call.

---

## Self-review

**Spec coverage.** Each section of the spec and the task that implements it:

| Spec | Task |
|---|---|
| §5.1 `answer_cut` | 3 (live), 4 (stored), 10 (line and Continue) |
| §5.1 `document_partial`, `document_unread` | 7 (computed), 10 (shown) |
| §5.1 `images_dropped` moves to the durable model | 3, 4, 10 |
| §5.1 `context_trimmed` | 3 (first round), 11 (composer only) |
| §5.1 meter, error `context` | 5 and 11; 2 and 10 |
| §5.2 storage and schema 3 | 4, and 6 for the attachment columns |
| §5.2 deadlines and sweep | 4 (notices), 6 (measure) |
| §5.2 API | 4 (`MessageOut`), 5 (`ConfigOut`, `final`), 6 (`AttachmentOut.length`) |
| §5.2 frames, audit | 3 and 5; 8 |
| §5.3 | 2, 3 |
| §5.4 measuring and `too_long` | 6 |
| §5.4 the structured cut | 1 |
| §5.4 `document_partial` | 7 |
| §5.4 the model told | 9 |
| §5.5 | 7 |
| §5.6 | 8 (backend), 10 (draft with a base) |
| §5.7 | 5, 11 |
| §5.8 | 10 (the pin, notice lines), 11 (meter, note, tile) |
| §6 step 3 order | Tasks 1–9 as steps 1–9; step 10 split into Tasks 10 and 11; step 11 is Task 12 |
| §7 gates and local end-to-end | each task's tests; Task 12 |
| §7 staging | left to the staging run, named in the PR |

**Type consistency.** Each name is defined once and used by later tasks under the same name:
- Task 3: `NoticeEvent` (`frame`, `stored`, `from_stored`), `FitReport`, `_Progress.told`, `_at_cap`.
- Task 4: `NoticeOut`, `PARAM_CODES`, `due_notices` and `strip_notices`. `finish_assistant_message(..., notices=, context_estimate=)` is Task 4's signature, and Task 5 fills `context_estimate`.
- Task 5: `TurnOutcome`, `next_prompt_tokens`, `finished_turn`.
- Task 6: `Measure`, `serves_measure`, `MeasureState`, `LengthOut`, `settle_measure`, `PrepareJob.prepare` and `.measure`, `MEASURE_SLOTS`, `_too_long`.
- Task 7: `SentFile`, `Reading`, `covered`, `chat_files`, `UserTurn.files`.
- Task 8: `Turn.continued`, `continuation`, `CONTINUE`, `CONTINUE_BODY`, `answer_notices`, `answered_turns`, `NOT_CONTINUABLE_CODE`.
- Tasks 10 and 11, the frontend: `NoticeOut`, `NoticeLines`, `continueAnswer`, `contextRatio`, `nearlyFull`, `NEAR_FULL_RATIO`, `useContextUse`, `ContextNote`, `useStartChat`.

**Proportion.** Code appears only for the structured cut and the union of spans. Everything else is signatures, values, exact copy and assertions.
