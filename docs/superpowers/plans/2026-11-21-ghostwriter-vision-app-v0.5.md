# Ghostwriter vision, stage 2: Office attachments — Ghostwriter v0.5.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ghostwriter v0.5.0 has workbench prepare Word, PowerPoint and OpenDocument text attachments in the background, never holding Send, and reads the `office` manifest with its `unit`. It tells the model `PowerPoint, 24 slides; slides 3, 7 have no text`, and at a fixed image cost asks for slides as strips. Next to workbench v0.7.0 it behaves as v0.3.0.

**Architecture:** three small changes in existing seams:
- the upload's Office suffixes, which stay `ready` at once and get a background prepare;
- the workbench client's manifest, with `unit` and a longer prepare wait;
- the turn's attachment facts and strips.

There is no schema change: `unit` rides in the stored manifest's JSON (`attachments.render`). There is no UI change.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, httpx (`MockTransport` in tests), loguru.

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§7.2 Office, §7.3 the attachment line, §8 stage 2). It consumes exactly `deploy/docs/superpowers/plans/2026-11-21-ghostwriter-vision-workbench-v0.8.md` § Contract for Ghostwriter.

Every path below is relative to the `ghostwriter` repo root. Line numbers are those of v0.3.0 (`feat/vision-stage1` at `0838f77`). v0.4.0, the favicon release, changed only the frontend and the version, so they hold on `main`, and every "as in 0.3.0" below holds for 0.4.0 too. Read `CLAUDE.md` (Invariants, Testing, What changes together) first.

## Decisions

These are the lead's calls; the owner may correct them. A line marked *(plan)* is this plan's refinement.

- **D8:**
  - a new `upload.OFFICE_SUFFIXES` (`.docx .dotx .pptx .potx .odt`) is prepared in the background (see **Waiting**); `upload.PREPARED_SUFFIXES`, the files Send waits for, is unchanged;
  - `workbench.Manifest` reads kind `office` and `unit`;
  - `STORED_MANIFEST_KEYS` gains `unit` (no schema change);
  - `context.attachment_facts` gives `PowerPoint, 24 slides; slides 3, 7 have no text` and `Word document, 12 pages; …`, naming the format by suffix: PowerPoint for pptx/potx, Word for docx/dotx, OpenDocument text for odt;
  - `agent/images.strips_for_path` gives 2 for Office suffixes at a fixed image cost (pages are drawn at 1600 px, as PDF pages);
  - docs; version `0.5.0`.
  - *(plan)* This amends spec §8's "Ghostwriter needs no change" for stage 2.
- **Compatibility:** next to workbench 0.7, an Office prepare answers kind `none` with no `unit`. It is stored as kind `none`, the attachment line says what 0.3.0 said, and nothing breaks.
- **Waiting (the owner's ruling, 2026-11-21): Send never waits for an Office file.**
  - An Office attachment is `ready` at once, as in v0.3.0, and workbench prepares it in the background.
  - The facts reach the model in the first turn read after the manifest is kept. A message sent sooner shows the attachment line of 0.3.0, and `read_document` names the slides without text, as before.
  - A tool call that needs a slide's picture while the prepare runs waits for it in workbench: both hold the source lock, and nothing is converted twice (v0.8 plan, Task 5).
  - Why: a Word file is sendable at once today; workbench prepares one file at a time for all of Ghostwriter (`workbench/api/jobs.py:21-23`), so a deck in that queue would hold back every other user's Send; and `read_document` attaches no page of a Word file, so waiting buys the first answer little.
- *(plan)* **Background slots:** a background prepare holds at most one of the processor's `PREPARE_SLOTS` (`BACKGROUND_SLOTS = 1`). A picture or a PDF attached meanwhile, which Send does wait for, therefore never queues behind two decks in Ghostwriter. In workbench all prepares share one gate in arrival order, so such a picture can wait for one conversion (a few seconds).
- *(plan)* **Restart:** a background prepare cut by a restart is not run again (its row is `ready`, never `processing`, so `requeue` does not find it). The deck keeps no facts, and workbench prepares it on its first `view_file` or `read_document`, as in 0.3.0.
- *(plan)* **Timeout:** `PREPARE_TIMEOUT_S` rises from 270 to 390 s, as the Contract says: one turn holds two job walls, the conversion and the drawing.
- **D9:** one PR, `make verify`, `make test`.

## Global Constraints

- Branch `feat/vision-stage2` from a fresh `main`; one PR. `pyproject.toml` `version = "0.5.0"` and `uv lock` happen in Task 3 only; the tag is minted on merge.
- Python 3.11 only. Ruff with Google docstrings (`D`) and annotations (`ANN`), pyrefly strict, 120-character lines. `make verify` and `make test` must be green before every push (`git add` new files first).
- One process, one uvicorn worker.
- Audit fail-closed, references only. Logs hold ids, counts, kinds and tokens, never a name or a path (loguru `{}` placeholders).
- No file bytes are stored. Every workbench call carries its chat's `X-Session-Id`. Images go to the model only while `Vision.images_offered` is true (the R15 header), as in v0.3.0.
- Vendored files are never edited: `ghostwriter/utils/audit_*.py`, `make/common.mk`, `scripts/bundle-lib.sh`, `frontend/eslint.config.js`, the `[tool.ruff]`/`[tool.pyrefly]` sections.
- Test data is invented (`tests/fakes.py` conventions; names such as `harbour-deck.pptx`, `quay-report.docx`). No committed file or message holds an absolute local path.
- No UI string changes, so the en/de parity is untouched.
- Spec and contract values:
  - Office suffixes `.docx .dotx .pptx .potx .odt`, `ready` at once and prepared in the background; `.xlsx .xlsm .ods` are never prepared;
  - one background prepare at a time in Ghostwriter (`BACKGROUND_SLOTS = 1`) within `PREPARE_SLOTS = 2`;
  - `unit` is `page` or `slide`, `page` when absent;
  - prepare timeout 390 s;
  - 2 strips for an Office page at a fixed cost.
- Commit messages are conventional and hold no real data.

## Review Focus

1. **A message sent while its deck is still being prepared.** Expect it to send at once (no 409) with the attachment line of 0.3.0, and the next message, once the manifest is kept, to carry `PowerPoint, 3 slides; slide 2 has no text`. Test in Task 2.
2. **Two decks and then a picture, attached in one go.** Expect the picture's prepare to start while the first deck's still runs, and its Send to wait for the picture only, never for a deck. Test in Task 1.
3. **A deck workbench cannot convert** (password-protected, damaged, a master without slides: `422 unreadable_file` / `unsupported_format`). Expect the attachment to stay `ready` without facts and never become `failed`, and `read_document` to answer as before. Test in Task 1.
4. **Ghostwriter 0.4 next to workbench 0.7.** Expect a docx's prepare to answer kind `none` without `unit`, stored as kind `none` and `unit` `page`, with the attachment line as in 0.3. Test in Task 1.
5. **A manifest from a later workbench with an unknown `unit`** (`"sheet"`) or kind. Expect `bad_answer` (the deck `ready` without facts), never a wrong noun in the line or a crash. Test in Task 1.

---

### Task 1: Office attachments are ready at once and prepared in the background, and their manifest keeps its unit

**Files:**
- Modify:
  - `ghostwriter/upload.py`: a new `OFFICE_SUFFIXES` and `prepared_later` beside `PREPARED_SUFFIXES` :67 and `needs_prepare` :183, both unchanged; the module docstring (:14);
  - `ghostwriter/workbench.py`: `PREPARE_TIMEOUT_S` :101-102, `_KINDS` :118, `Manifest` :316-345 and its `as_stored`, `_manifest` :905-925;
  - `ghostwriter/store.py`: `STORED_MANIFEST_KEYS` :60; a new `keep_manifest` beside `settle_attachment` :1204;
  - `ghostwriter/processing.py`: the module docstring (:1-30: a picture or a PDF is prepared before it is ready, an Office file after); `BACKGROUND_SLOTS` beside `PREPARE_SLOTS` :53; `PrepareJob` :67-86; `_run` :261-280; `_prepared` :361-407;
  - `ghostwriter/api/files.py`: `attach_file` :276-298; `_read_after_wait` :460-481;
  - `docs/configuration.md`: `PREPARE_TIMEOUT_S` :65; a `BACKGROUND_SLOTS` row after `PREPARE_SLOTS` :70;
  - `tests/fakes.py:269` (`stock_manifest`): v0.8's eight keys, with `"unit": "page"` for every kind. A `.pptx`/`.potx` is `kind: "office"`, `unit: "slide"`, `total: 3`, `pages_without_text: [2]`; a `.docx`/`.dotx`/`.odt` is `office`, `page`, 3 pages, none without text.
- Test: `tests/test_upload.py` (:238, :251), `tests/test_workbench.py` (from :869), `tests/test_store.py` (:625), `tests/test_processing.py` (beside `test_at_most_two_prepares_run_at_once` :128, whose way of holding a prepare open the new slot test reuses), `tests/test_api_files.py` (:676)

**Interfaces:**
- Consumes: workbench v0.8.0's Contract (an eight-key manifest; `kind: "office"`; `unit`; the 390 s wait).
- Produces:
  - `upload.OFFICE_SUFFIXES: Final = frozenset({".docx", ".dotx", ".pptx", ".potx", ".odt"})`. `PREPARED_SUFFIXES` and `needs_prepare` are unchanged, so an Office file is never `processing`.
  - `upload.prepared_later(name: str) -> bool`: whether the extension, in any case, is in `OFFICE_SUFFIXES`. `attachment_kind` still answers `document` for these files.
  - `ChatStore.keep_manifest(attachment_id: str, manifest: Mapping[str, Any]) -> bool`: keeps a background prepare's manifest, cut by `_stored_manifest` as `settle_attachment` does. Only a `ready` row without a manifest changes. A second keep, a `processing` or `failed` row and a deleted row change nothing and answer `False`.
  - `processing.PrepareJob` gains a last field `background: bool = False`: the attachment is `ready` already, and the job only adds its facts.
  - `processing.BACKGROUND_SLOTS: Final = 1`: `_run` has a background job take this semaphore before one of the `PREPARE_SLOTS`.
  - `_prepared` keeps a background job's manifest with `keep_manifest`, never `settle_attachment`. A failure records its `error` child as for any document; `_settle_failed` then changes no row, because `settle_attachment` changes only a `processing` one.
  - `attach_file`:
    - `later = offered and prepared_later(name)`;
    - the status is `processing` only for `needs_prepare`;
    - either gets a job: `PrepareJob(..., background=later)`.
  - `_read_after_wait` reads the row first and answers at once unless it is `processing`, so a status request never waits on a background job.
  - `requeue` is unchanged: it finds `processing` rows only.
  - `workbench.Kind = Literal["image", "pdf", "office", "none"]` and `Unit = Literal["page", "slide"]`. `_KINDS` gains `office`.
  - `Manifest.kind: Kind`, with a new last field `unit: Unit = "page"`. The existing positional constructions stay valid.
  - `as_stored()` adds `"unit"`, and `STORED_MANIFEST_KEYS = ("kind", "total", "pages_without_text", "renditions", "frames", "unit")`.
  - `_manifest`: a missing `unit` reads as `"page"` (workbench 0.7); `"page"` and `"slide"` are kept; any other value, or a non-string, gives `None`, which `prepare` raises as `bad_answer`.
  - `PREPARE_TIMEOUT_S: Final = 390.0`, its comment: workbench's 120 s of queue, two job walls of 120 s (an Office file is converted, then drawn, in one turn) and 30 s.

- [ ] **Step 0: Branch.** `git fetch origin && git switch main && git pull --ff-only && git switch -c feat/vision-stage2`.
- [ ] **Step 1: Write the failing tests:**
  - `tests/test_upload.py`:
    - :238 still expects `frozenset({".pdf", *IMAGE_TYPES})`, and the cases of `test_kinds_and_preparation_follow_the_extension` (:251) keep `quay-report.docx` unprepared;
    - `test_office_files_are_prepared_later`: `prepared_later` is `True` for `harbour-deck.PPTX`, `quay-master.potx`, `quay-form.dotx`, `survey-notes.odt` and `quay-report.docx`, and `False` for `harbour-figures.xlsx`, `survey.ods`, `harbour-scan.pdf` and `pier-photo.png`.
  - `tests/test_workbench.py`:
    - `test_prepare_reads_an_office_manifest_with_its_unit`: an eight-key answer of kind `office`, unit `slide`, gives `Manifest(kind="office", unit="slide", …)`, and `as_stored()["unit"] == "slide"`.
    - `test_a_manifest_without_unit_reads_as_page` (Review Focus 2): v0.7's seven keys for a PDF give `unit == "page"`, and its kind-`none` answer for a docx gives `kind == "none"`.
    - `test_a_malformed_manifest_is_a_bad_answer`'s parameters (:994) gain `{"unit": "sheet"}`, `{"unit": 1}` and `{"kind": "video"}` (Review Focus 5).
    - `test_a_prepare_waits_390_s`: the `MockTransport` handler sees `request.extensions["timeout"]["read"] == 390.0` on `POST /files/prepare`.
  - `tests/test_store.py`:
    - `test_a_settled_manifest_keeps_its_unit`: settling `{"kind": "office", "unit": "slide", …}` keeps `unit`, and a manifest stored by 0.3.0 without it still reads (`find_attachment(...).manifest` has no `unit` key).
    - `test_keep_manifest_fills_only_a_ready_row_without_one`: a `ready` row without a manifest takes it (`True`, `unit` kept). A second keep, a `processing` row and a deleted row answer `False` and change nothing.
  - `tests/test_processing.py`:
    - `test_an_office_attachment_is_prepared_in_the_background`: `harbour-deck.pptx`, added `ready` with vision on and submitted with `background=True`, is handed over (`disclose` / `chat.attachment`, `workbench:prepare`), stays `ready` throughout, and keeps the manifest.
    - `test_an_office_file_workbench_cannot_convert_stays_ready_without_facts` (Review Focus 3): the background prepare answers 422 `unreadable_file: …`, and in a second case `unsupported_format: …`. The attachment stays `ready` with no manifest and no failure token, and one `error` child is recorded.
    - `test_background_prepares_take_one_slot` (Review Focus 2): with every prepare held open, two background decks and then a picture are submitted. The fake sees the first deck's prepare and the picture's, and the second deck's only after the first deck's ends.
    - `test_a_ready_deck_is_not_requeued`: a `ready` deck without a manifest is not found by `requeue()`, which returns 0.
  - `tests/test_api_files.py`:
    - `test_a_pdf_gets_its_manifest_and_a_word_file_none` (:676) becomes `test_a_pdf_waits_and_a_word_file_is_ready_at_once`. `memo.docx` answers `("document", "ready")`, its prepare follows, and it then keeps `kind` `office` and `unit` `page`. `figures.xlsx` is `ready`, never prepared, with no manifest. Two prepares are made in all.
    - `test_a_status_request_for_a_ready_attachment_never_waits`: with a processor whose job for that attachment is running, `_read_after_wait(..., wait=20)` returns the `ready` row and never calls `processor.wait`.
    - `test_an_office_file_is_not_prepared_with_vision_off`: with the client that does not see, `harbour-deck.pptx` is `ready` at once and no job is submitted, as in v0.3.0.
- [ ] **Step 2: Run** `uv run pytest tests/test_upload.py tests/test_workbench.py tests/test_store.py tests/test_processing.py tests/test_api_files.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** as in Interfaces, then `stock_manifest`.
  - Existing exact asserts on a stored manifest gain `"unit": "page"`, and nothing else in them changes.
  - In `docs/configuration.md`, the caps row reads `PREPARE_TIMEOUT_S` 390: workbench's 120 s of queue, two job walls of 120 s (an Office file is converted, then drawn) and 30 s.
  - A new row reads `BACKGROUND_SLOTS` 1: Office files prepared at once in the background, within `PREPARE_SLOTS` (`processing.py`).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(workbench): Office attachments are prepared in the background, and their manifest keeps its unit`

### Task 2: The model is told what an Office attachment is, and gets its slides as strips

**Files:**
- Modify: `ghostwriter/agent/context.py:166` (`attachment_facts`), `ghostwriter/store.py:1284` (its call site passes the name), `ghostwriter/agent/images.py:149` (`strips_for_path`)
- Test: `tests/test_agent_context.py` (:534-541), `tests/test_agent_images.py` (:61-71), `tests/test_api_messages.py`. The replay tests that go through `store.py:1284` pass unchanged.

**Interfaces:**
- Consumes: Task 1 (`OFFICE_SUFFIXES`, the stored `unit`).
- Produces:
  - **`attachment_facts(kind: str, manifest: Mapping[str, Any] | None, *, seen: bool, name: str) -> str | None`**, `name` being the attachment's name. For a manifest of kind `pdf` it answers word for word as v0.3.0. For kind `office` it answers:
    - the format by the name's suffix: `PowerPoint` (.pptx, .potx), `Word document` (.docx, .dotx), `OpenDocument text` (.odt);
    - the noun by the manifest's `unit` (`page` when absent);
    - the same shapes as a PDF's: `PowerPoint, 24 slides; slides 3, 7 have no text`, `Word document, 1 page`, `PowerPoint, 5 slides; no slide has text`, `OpenDocument text, 3 pages; page 2 has no text`, and `…; slides 1, 2, …, and 4 more have no text` past `LISTED_PAGES`;
    - `seen` does not change it.
    - Any other kind, or `none`, gives `None` as before.
  - **`strips_for_path`:** at a fixed cost, `2` for `.pdf` and for every suffix in `OFFICE_SUFFIXES` (any case), and `None` without a fixed cost; pictures are as before.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_agent_context.py`:
    - the existing PDF and image cases pass `name=` and keep their strings;
    - `attachment_facts("document", _office_manifest("slide", 24, [3, 7]), seen=True, name="harbour-deck.pptx") == "PowerPoint, 24 slides; slides 3, 7 have no text"`;
    - `…("page", 12, []), name="quay-report.docx") == "Word document, 12 pages"`;
    - `…("page", 1, []), name="survey-notes.odt") == "OpenDocument text, 1 page"`;
    - `…("slide", 5, [1, 2, 3, 4, 5]), name="quay-master.potx") == "PowerPoint, 5 slides; no slide has text"`;
    - `…("slide", 9, [4]), name="harbour-deck.PPTX") == "PowerPoint, 9 slides; slide 4 has no text"`;
    - a kind-`none` manifest of a `.docx` gives `None`.
    - `_office_manifest(unit, total, blank)` is a new helper beside `_pdf_manifest`.
  - `tests/test_agent_images.py`: `strips_for_path` of `/workspace/c/harbour-deck.PPTX`, `/workspace/c/quay-master.potx` and `/workspace/c/quay-report.docx` is `2` with `fixed=280` and `None` with `fixed=None`; of `/workspace/c/harbour-figures.xlsx` it is `None`.
  - `tests/test_api_messages.py`: `test_a_message_sends_while_its_deck_is_prepared` (Review Focus 1).
    - A deck whose prepare still runs is a `ready` row without a manifest; the test makes one with `store.add_attachment(..., kind="document", status="ready")` for `harbour-deck.pptx`.
    - A message with it streams (no 409), and the model's prompt holds the attachment line of 0.3.0, with no facts.
    - After `store.keep_manifest(id, stock_manifest("harbour-deck.pptx"))`, the next message's prompt holds `PowerPoint, 3 slides; slide 2 has no text` for it.
- [ ] **Step 2: Run** `uv run pytest tests/test_agent_context.py tests/test_agent_images.py tests/test_api_messages.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The PDF branch and the Office branch share one helper that takes the format and the noun, so the PDF's words come from the same code.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): an Office attachment's line names its slides or pages, and its pages go as strips at a fixed cost`

### Task 3: Docs and release

**Files:**
- Modify:
  - `docs/data-flow.md`:
    - the `POST /files/prepare` row (:47) names Office files;
    - § "A picture or a PDF, after its upload" (:65) becomes "A picture, a PDF or an Office file". It says an Office file is `ready` at once and prepared in the background, one at a time, so Send never waits for it. Workbench converts the file once and keeps the PDF as its own derived file, deleted with the file. The manifest keeps `unit`, and a background prepare cut by a restart is not run again.
  - `CLAUDE.md`:
    - "What this repo is": an attached Word, PowerPoint or OpenDocument text file is prepared too, so its pages and slides can be seen (workbench 0.8);
    - "What changes together": an Office suffix → `upload.OFFICE_SUFFIXES`, `agent/images.strips_for_path`, `context.attachment_facts` and workbench's `renders.OFFICE_SUFFIXES`.
  - `README.md`: one line.
  - `pyproject.toml` (`version = "0.5.0"`), `uv.lock`, `tests/test_version.py` (`"0.5.0"`), `tests/test_api_meta.py` (`"version": "0.5.0"`).

- [ ] **Step 1: Docs** as above.
- [ ] **Step 2: Version.** Bump to `0.5.0` and run `uv lock`, then `uv run pytest tests/test_version.py tests/test_api_meta.py -q`. Expected: PASS.
- [ ] **Step 3: Gates.** `make verify && make test`. Expected: green; paste the summary lines into the PR.
- [ ] **Step 4: Commit** `docs: Office attachments are prepared, and their pages and slides seen` and `chore(release): 0.5.0`.
- [ ] **Step 5: Push and PR.** `git push -u origin feat/vision-stage2`. Open a PR to `main` with this **Operators** line: "0.5.0 prepares Word, PowerPoint and OpenDocument text attachments, so the model sees their pages and slides. It needs workbench 0.8.0 for that; next to 0.7.0 these files read as text, as before. An Office attachment is ready at once, as before: workbench converts it in the background, and Send never waits for it." Confirm that CI ran on the last commit. Merging is the owner's call.

---

## Self-review

- **Spec coverage:**
  - Office files prepared and their facts in the attachment line (§7.3: `24 slides; slides 3, 7 have no text`): Tasks 1–2;
  - strips for fixed-cost models (§6 S2): Task 2;
  - compatibility with workbench 0.7 (§8's "both orders are safe"): Task 1.
  - `view_file` steps of Office files need nothing new: the trail already names `view_file` steps, and labels such as `slide 3` are kept as workbench writes them.
- **Decisions made where the spec is silent:**
  - the 390 s wait;
  - Office attachments are prepared in the background and never hold Send (the owner's ruling), one at a time;
  - an unknown `unit` is a bad answer, not a guess.
- **Proportion:** no code blocks; signatures, values and test assertions only.
