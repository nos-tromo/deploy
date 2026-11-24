# Ghostwriter limit notices, part 1: workbench v0.10.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** workbench measures a document's length in its reader's own unit (`POST /files/measure`), and every part `read_document` answers says which units it covers (`units`) and whether workbench cut it (`cut`), so Ghostwriter can tell a user what the model did not read.

**Architecture:**
- **Units in the readers.** Each reader numbers its finest unit (page, slide, paragraph with a table as one, sheet row, line) and puts `(first, last)` on every `Part` it returns. The runner sends `units` and `cut` with each part; the api checks them, keeps them through its own fit, and sets `cut` on a part it cuts.
- **The measure.** A new runner, `measure_file`, walks the same readers to count `total` units and the characters a full read would show, stopping at `MEASURE_CHARS_CAP`. A new file route sends it to the executor on a gate of its own, with a slot of its own, records it before it answers, and remembers the result (or a refusal) per file in the api's memory.

**Builds on:** workbench v0.9.0 (`main` at the 0.9.0 merge). Line numbers below are v0.9.0's.

**Tech Stack:** Python 3.12, FastAPI, pydantic, SQLite; executor readers: pypdf, python-pptx, lxml, openpyxl, charset-normalizer, BeautifulSoup; tests: pytest, synthetic files built at test time (`tests/fixtures/make_fixtures.py`).

**Spec:** `deploy/docs/superpowers/specs/2026-11-24-ghostwriter-limit-notices-design.md`: §5.4 "workbench 0.10.0", §5.9 (what is left to the builder), §6 step 1, §7 Gates (workbench). The consumer is Ghostwriter 0.7.0 (§6 step 3.6 and 3.7).

Every path below is relative to the `workbench` repo root unless it starts with `deploy/`.

## Decisions

The spec leaves these to the builder (§5.9), or the code forces them:

1. **The readers number the units, in the executor.** `Part` gains `units: tuple[int, int] | None`. The runner sends `units` and `cut` on each part; the api validates them and passes them on.
2. **A table is one unit; a part with only some of its rows carries it only when it reaches the table's last row.** `read_blocks` pages a table by runs of rows (`table 2, rows 30-60`). A run that ends at the table's last row with text carries the table's number; any other run is `units: null`. Paging with `next_part` goes forward, so a table read to its end counts once, and one read halfway does not count.
3. **A sheet row is a row that holds something**, numbered through the sheets in their order (sheet 1's rows first). The readers never walk an empty row (`sheet.py:415-451`, `odf.py:289-336`), and a range starts at the next row with content, so blank rows are in neither `units` nor `total`; a full read then covers `1..total` with no gap. A range that does not start the workbook gets its offset from one more walk (`paging.rows_before`).
4. **Pages and slides without text are in no part**, as today (`pdf.py:98-109`, `pptx.py:107-119`). They carry no units and stay in `pages_without_text`. The coverage test pins `units ∪ pages_without_text = 1..total`.
5. **`cut` is true for a part cut with ` [cut]`**, by the reader (`DocumentText.cut`) or by the api's fit (`tools._cut_part`). A line or a cell the reader shortens with its own caps (`text.LINE_CAP` 2,000, marked `… [+N characters]`; `paging.CELL_CAP` 300, marked `…`) is not `cut`, as the contract defines it, and its units count as read. So a file of one huge line gets no answer notice, but its tile warns through the measure (Decision 7).
6. **The executor sizes a part as it is sent.** `paging.part_size` counts `units` and `cut`, so the api's fit does not drop a part the reader filled.
7. **`chars`** is the sum of the lengths, in characters, of the pieces a full read is made of, with no break counted between pieces:
   - a page's text; a slide's text;
   - a paragraph; a table row as `row R: a | b`;
   - a sheet row as `N: a | b`;
   - a line.

   The exception: a line or a cell that the reader shortens with its own caps (`LINE_CAP`, `CELL_CAP`) is counted at its full length, `len(line)` and cells read with `cap=None`. Reads and `units` stay as they are. So the measure says how long the text is, even where a read shows less of it.

   Counting stops once it passes the cap: `chars = min(count, cap)`, `capped = count > cap`. `total` is always whole: a PDF's and a deck's from their page count; a workbook's from a walk of every row, past the cap too.
8. **`MEASURE_CHARS_CAP: Final = 2_000_000`**, in `workbench/executor/runners/documents.py`. It is about 670,000 tokens at 3 characters a token, over three times any prompt budget Ghostwriter allows (tens of thousands of tokens), and it stops a PDF's text extraction after about a thousand dense pages. It is not a setting.
9. **The measure's job** is `measure_file`, with one input and no output. It runs on a **measure gate of its own**, within the caller's wall, with 3/4 of it as the readers' budget, as a prepare does. It takes no source lock: it makes no derived file.
   - The gate works like the prepare and conversion gates:
     - one job of a caller at a time, over all its users;
     - its own line of `PREPARE_WAITING_MAX` (32), counted apart from `WORKBENCH_QUEUE_MAX`, every share and the other two lines;
     - a wait of up to `PREPARE_QUEUE_WAIT_S` (120 s);
     - within the caller's slots and a global slot.
   - Why not the conversion gate, option (a): that gate is not background only. A view or a tool that converts an Office file on demand waits for it (`renders.RouteJobs.turn(converting=True)`, `tools_mcp.CallJobs`). So does a view of a deck whose background prepare holds the deck's lock while it waits for that gate. A measure there would sit in front of an interactive call, against the spec's anti-goal that a notice never delays a send.
   - Why not the prepare gate: a send waits for the prepares of pictures and PDFs, and a slow measure there would hold them up.
10. **One more slot by default, so that the measure's is its own.** At 3 caller slots, a prepare, a conversion and a measure running at once would take every slot, and every tool call of Ghostwriter's users would answer `wait_timeout` after 5 s. Five defaults change:
    - `GHOSTWRITER_WORKBENCH_SLOTS` 3 → **4**: a prepare, a conversion, a measure, and one left for the tool calls of all users while all three run. Every other gate keeps today's 3 even while a measure runs.
    - `WORKBENCH_EXECUTOR_SLOTS` 4 → **5**: Ghostwriter's slots must stay fewer than the executor's (`config.py:257`), so docint keeps one running slot, as it does today.
    - `WORKBENCH_QUEUE_MAX` 16 → **20**: each user's share of the waiting places is `queue_max // executor_slots`, and 20 // 5 keeps it at 4. docint keeps 8 places, Ghostwriter has 16.
    - `WORKBENCH_EXECUTOR_MEM_LIMIT` 6656m → **8192m** (5 × 1536 + 512), and `WORKBENCH_EXECUTOR_PIDS_LIMIT` 320 → **384** (5 × 64 + 64). `WORKBENCH_EXECUTOR_CPUS` stays 4.

    A host whose `.env` pins `GHOSTWRITER_WORKBENCH_SLOTS` at 3 gets the cost above, with no warning at start, as a host pinned at 2 does today. A host that pins `WORKBENCH_EXECUTOR_MEM_LIMIT=6656m` but not the slots has an executor that refuses to start (5 × 1536 > 6656). Both go into the release notes.
11. **Remembered in memory, beside the refusals** (`Store`, `MEASURES_MAX` 4096, LRU), by file id with its creation time and size. A cell holds either the answer's counts or a refusal. `unsupported_format` and `unreadable_file` are remembered at once: the readers' refusals are decided by the file at the job's fixed limits. `read_timeout`, `file_not_found`, busy and failures are not remembered. A cell goes with its file, and with a restart.
12. **Audit: `query` / `file.measure`.** prepare is `modify` because it makes renders; a measure makes nothing and answers counts.
    - It is recorded once the job has answered, before the answer goes (`record_answer`).
    - A refusal answered from memory is `error`, with `refused` and `remembered: true`, as prepare's is.
    - An answer from memory has no job and no event, as prepare's from its rows.
    - A picture or a clip is refused by its name before any job, with no event.
13. **A picture or a clip** (`kind_of` is `image` or `media`) is `422` with `NOT_A_DOCUMENT = "unsupported_format: a picture or a clip is not a document and has no text to measure"`.
14. **The body is `MeasureRequest`**, `{"path": str}` with no other key (the contract's JSON body; every other file route takes `path` as a query parameter).
15. **The shared vocabulary** (`ReadUnit`, `ReaderKind`, `READ_UNITS`) lives in `workbench/renders.py`, the module both sides import.

## Global Constraints

- Branch `feat/measure` from `main` at v0.9.0; one PR. Task 6 bumps `pyproject.toml` to `version = "0.10.0"` and runs `uv lock`; the release-tag workflow mints `v0.10.0` on merge. Workflow pins are unchanged.
- Python `>=3.12,<3.13`; ruff with `D` and `ANN`; pyrefly strict; 120-character lines. `make verify` and `make test` are green before every push (`git add` new files first). `make test-executor-root` and `make test-isolation` are green before the PR.
- The executor imports nothing of `workbench.api`. What both sides use lives in `workbench/renders.py`.
- The api parses no file: every count is made in the executor, by the readers `read_document` uses.
- Fail closed: a measure's answer, and its refusal, leave only after its `query` / `file.measure` event.
- Logs, events and the api's memory hold ids, counts and codes only. Never a name, a path, a label or a text.
- No new setting. Five defaults change (Decision 10, Task 4).
- Spec values, verbatim:
  - the route `POST /files/measure`, body `{"path": str}`, open to the `ghostwriter` caller only;
  - the answer `{"path", "kind", "unit", "total", "chars", "capped"}`, `unit` one of `page`, `slide`, `paragraph`, `row`, `line`;
  - the units: PDF page; PPTX slide; DOCX and ODT paragraph, a table counting as one; XLSX and ODS row, numbered through all sheets in sheet order; text, CSV, TSV, JSON, JSONL, MD, HTML and EML line (JSON: lines of the indented form);
  - each item of `parts`: `"units": [first, last] | null` (1-based, inclusive) and `"cut": bool`, both always present; nothing else in the answer changes.
- The goldens in `tests/api/golden/` are edited by hand, never regenerated.
- Test files are built at test time with invented content: no committed binary, no real name, no absolute local path. `.gitignore` ignores `*.txt`, so no `.txt` fixture is committed.
- Commit messages are conventional and hold no real data.

## Review Focus

1. **A long table in a Word document, read over several calls.** Expect every run of rows before the last to be `units: null`, the run that reaches the last row to carry the table's one unit, and the paragraphs around it their own. Tests in Tasks 1 and 3.
2. **A workbook with an empty first sheet, blank rows before and inside a sheet, and a range asked for in the middle.** Expect units counted over rows that hold something only, the second sheet going on from the first, and the parts of a full read joining to exactly `1..total` of the measure. Tests in Tasks 1 and 3.
3. **A PDF with a scanned page.** Expect the scan in `pages_without_text`, in no part, and `units ∪ pages_without_text = 1..total`. Tests in Tasks 1 and 3.
4. **A document too big to count in time**, or past the cap. Expect:
   - a PDF past the cap to stop extracting text while `total` stays whole;
   - a run past the budget to be `422 read_timeout`, not remembered, so the next call tries again.

   Tests in Tasks 3 and 5.
5. **A slow measure** (a long PDF, a large workbook) while other users attach pictures and PDFs, an Office file converts, and the model calls tools. Expect every prepare, the conversion and one tool call at a time to be admitted at once, beside the running measure, and a second measure to wait in its own line. Tests in Task 4.

## Contract for Ghostwriter (what Ghostwriter 0.7.0 consumes)

Headers on the route as on `POST /files/prepare`: `Authorization: Bearer <GHOSTWRITER_WORKBENCH_KEY>`, `X-User-Id`, `X-Session-Id`, `X-Audit-Purpose`, `X-Request-ID`. A workbench whose discovery names 0.10.0 or later (`serverInfo.version`) serves both changes below.

**`POST /files/measure`**, body `{"path": "/workspace/chat-1/harbour-report.pdf"}`. `200`:

```json
{"path": "/workspace/chat-1/harbour-report.pdf", "kind": "pdf", "unit": "page", "total": 3, "chars": 36, "capped": false}
```

- `kind` is the reader's: `pdf`, `docx`, `xlsx`, `csv`, `pptx`, `odt`, `ods`, `eml`, `html`, `json` or `text` (`.xlsm` is `xlsx`, `.dotx` `docx`, `.potx` `pptx`, `.tsv` `csv`, `.md` and `.jsonl` `text`).
- `unit` follows `kind`: `pdf` page, `pptx` slide, `docx` and `odt` paragraph, `xlsx` and `ods` row, every other kind line.
- `total` counts in `unit`: rows are those that hold something; paragraphs those with text, plus one per table with a row of text.
- `chars` counts a line or a cell that a read shows shortened at its full length. It stops at 2,000,000, with `capped: true` past it.
- A second call for the same file answers from memory, with no job.
- Statuses:
  - `400`: bad user or session.
  - `404 not found`: no such file, another chat's, or a `.derived` path; `file_not_found: …` when the file went before the job read it.
  - `422`:
    - `unsupported_format: a picture or a clip is not a document and has no text to measure`, with no job;
    - `unsupported_format: …` for a type no reader reads (the reader's text, for example `.xls cannot be read here; save it as .xlsx or .csv`);
    - `unreadable_file: …` (encrypted, damaged, a zip bomb, too large for the job's memory);
    - `read_timeout: …`, not remembered;
    - FastAPI's compact `422` for a body that is not exactly `{"path": str}`.
  - `428`: no purpose (`query` needs one by default).
  - `503 busy, retry later`.
  - `507`: `storage_full`.
- Recorded as `query` / `file.measure`, target the file. Criteria `refused: <code>` on a refusal, plus `remembered: true` when answered from memory.
- The measure waits on a gate of its own, never in front of a prepare, a conversion or a tool call (Decisions 9 and 10).
- Client timeout: the measure gate's 120 s, plus the caller's wall, plus 30 s: 270 s at the defaults, which Ghostwriter's 390 s (`PREPARE_TIMEOUT_S`) covers.

**`read_document`**: each item of `parts` is `{"label", "text", "units", "cut"}`:

```json
{"label": "table 1, rows 1-40", "text": "row 1: …", "units": null, "cut": false}
```

- `units` is `[first, last]` in the reader's unit, 1-based and inclusive. It is `null` only for a run of a table's rows that stops before the table's end.
- `cut` is `true` when workbench cut that part's text with ` [cut]`, in the reader or at the result limit.
- Pages and slides in `pages_without_text` are in no part.
- A clip's transcript is read by line, as a text file.

---

### Task 1: The readers number their units

**Files:**
- Modify:
  - `workbench/executor/readers/__init__.py:180-191` (`Part`);
  - `workbench/executor/readers/paging.py`: `part_size` `:55`, `Collector` `:68-162`, `read_blocks` `:228-296`, `read_sheets` `:413-495`, new `rows_before`;
  - `workbench/executor/readers/pdf.py:77-116`, `workbench/executor/readers/pptx.py:91-126`;
  - `workbench/executor/readers/text.py:121-168` (`read_lines`).
- Test: `tests/executor/test_read_units.py` (new)

**Interfaces:**
- Produces:
  - `Part.units: tuple[int, int] | None = None`: the first and last unit the part covers, from 1. `Part(label, text)` stays valid.
  - `paging.part_size(label: str, text: str, units: tuple[int, int] | None = None, *, cut: bool = False) -> int`: the compact JSON of `{"label", "text", "units", "cut"}` as the runner sends it, `units` left out when `None` (as the api's sizing leaves out `None`), plus 1 for the comma.
  - `Collector.add(label: str, text: str, units: tuple[int, int] | None = None) -> bool`. `grow` keeps the last part's units. `_fit` sizes with `cut=True`.
  - `paging.MAX_UNIT: Final = MAX_ROW * MAX_SHEETS`: the widest unit number a reservation sizes for.
  - `paging.rows_before(source: SheetSource, sheet: str, row: int) -> int`: the rows that hold something in the sheets before `sheet`, and in `sheet` before `row`. It uses one walk, `source.walk(None, 1, cap=1)`, which stops at the first row of `sheet` numbered `row` or more, or at the next sheet.
- The units:
  - `pdf.read`, `pptx.read`: a page or slide `n` carries `(n, n)`.
  - `read_blocks`:
    - blocks are numbered as they come: a paragraph is the next block, and a table is the next block at its first row with text (as `total_parts` counts them);
    - a paragraph carries `(b, b)`, and a run of a table is added with its table's `(b, b)`;
    - after the walk, a run whose last row is not its table's last row with text gets `units = None`.
  - `read_sheets`:
    - the one part carries `(offset + 1, offset + len(lines))`;
    - `offset` is 0 when `part` is empty, else `rows_before(source, current, first)`;
    - the reservation is sized with `(MAX_UNIT, MAX_UNIT)`.
  - `read_lines`: the part carries `(first, end)`, and the reservation is sized with `(total, total)`.

- [ ] **Step 1: Write the failing tests** (`tests/executor/test_read_units.py`; `read` as in `tests/executor/test_readers.py`).
  - The helpers:
    - `units_of(doc) -> list[tuple[str, tuple[int, int] | None]]`: the label and units of each part.
    - `reads(path, *, max_chars) -> list[DocumentText]`: every read of a whole document, from the start, following
      `next_part` until it is `None`. Task 3 imports it.
    - `gappy_xlsx(path) -> Path`, built with openpyxl, all text invented: a sheet `Empty` with nothing in it; `Figures` with two columns of text in rows 3-5 and 300-302; `Notes` with one cell, `A1`.

  ```python
  def test_pdf_pages_and_slides_are_their_own_units(tmp_path: Path) -> None:
      pdf = fx.make_pdf(tmp_path / "harbour-report.pdf", ["First page text\nsecond line", None, "Quay list"])
      doc = read(pdf)
      assert units_of(doc) == [("page 1", (1, 1)), ("page 3", (3, 3))]
      assert doc.pages_without_text == [2]
      assert units_of(read(fx.make_pptx(tmp_path / "harbour-deck.pptx"))) == [("slide 1", (1, 1)), ("slide 2", (2, 2))]

  def test_paragraphs_and_tables_are_numbered_together(tmp_path: Path) -> None:
      assert units_of(read(fx.make_docx(tmp_path / "review.docx"))) == [
          ("paragraph 1", (1, 1)), ("paragraph 2", (2, 2)), ("paragraph 3", (3, 3)),
          ("table 1, rows 1-3", (4, 4)), ("paragraph 4", (5, 5)),
      ]
      assert units_of(read(fx.make_odt(tmp_path / "survey.odt"))) == [
          ("paragraph 1", (1, 1)), ("paragraph 2", (2, 2)), ("table 1, rows 1-2", (3, 3)), ("paragraph 3", (4, 4)),
      ]

  def test_a_table_read_in_runs_counts_once_on_the_run_that_ends_it(tmp_path: Path) -> None:  # Review Focus 1
      path = fx.make_big_table_docx(tmp_path / "long-table.docx", rows=300)
      seen = [pair for doc in reads(path, max_chars=2048) for pair in units_of(doc)]
      runs = [(label, units) for label, units in seen if label.startswith("table 1")]
      assert len(runs) > 2
      assert [units for _, units in runs[:-1]] == [None] * (len(runs) - 1)
      assert runs[-1][0].endswith("-300") and runs[-1][1] == (2, 2)
      assert (seen[0], seen[-1]) == (("paragraph 1", (1, 1)), ("paragraph 2", (3, 3)))
      assert units_of(read(path, "table 1, rows 30-60")) == [("table 1, rows 30-60", None)]
      assert units_of(read(path, "table 1, row 290"))[0] == ("table 1, rows 290-300", (2, 2))

  def test_sheet_rows_are_numbered_through_all_sheets(tmp_path: Path) -> None:
      path = fx.make_xlsx(tmp_path / "quarterly-figures.xlsx", rows=5)
      assert units_of(read(path)) == [("Sheet1!A1:F6", (1, 6))]
      assert units_of(read(path, "Summary")) == [("Summary!A1:B2", (7, 8))]
      assert units_of(read(path, "Sheet1!A3")) == [("Sheet1!A3:F6", (3, 6))]

  def test_only_rows_that_hold_something_are_units(tmp_path: Path) -> None:  # Review Focus 2
      path = gappy_xlsx(tmp_path / "gappy.xlsx")
      first = read(path)
      assert (units_of(first), first.next_part) == ([("Figures!A3:B5", (1, 3))], "Figures!A300:B499")
      assert units_of(read(path, "Figures!A300:B499")) == [("Figures!A300:B302", (4, 6))]
      assert units_of(read(path, "Notes")) == [("Notes!A1:A1", (7, 7))]
      assert units_of(read(fx.make_ods(tmp_path / "depths.ods"))) == [("Depths!A1:B2", (1, 2))]

  def test_lines_are_their_own_units(tmp_path: Path) -> None:
      text = tmp_path / "pier-notes.txt"
      text.write_text("alpha\nbeta\n\ngamma\n", encoding="utf-8")
      assert units_of(read(text)) == [("lines 1-4", (1, 4))]
      assert units_of(read(text, "lines 2-3")) == [("lines 2-3", (2, 3))]
  ```

  - `test_every_line_reader_covers_its_lines`, parametrized over `berths.csv`, `berths.tsv`, `notes.md`, `log.jsonl`, a minified `berths.json`, `fx.make_html` and `fx.make_eml`: `units_of(doc) == [(doc.parts[0].label, (1, doc.total_parts))]`.
  - `test_a_part_the_reader_cut_keeps_its_units`. A line of 5,000 `x` read with `max_chars=512` gives `[("line 1", (1, 1))]` and `doc.cut == "line 1"`. A `.docx` whose one paragraph is 3,000 words gives `[("paragraph 1", (1, 1))]` and `doc.cut == "paragraph 1"`.
  - `test_a_part_is_sized_as_it_is_sent`:
    - `pg.part_size("page 3", "x", (3, 3)) == len('{"label":"page 3","text":"x","units":[3,3],"cut":false}') + 1`;
    - `pg.part_size("page 3", "x") == len('{"label":"page 3","text":"x","cut":false}') + 1`;
    - `pg.part_size("page 3", "x", cut=True) == pg.part_size("page 3", "x") + 1`.
  - `test_rows_before_counts_rows_with_content_only`: on `gappy_xlsx`, `rows_before(book, "Figures", 3) == 0`, `rows_before(book, "Figures", 300) == 3`, `rows_before(book, "Figures", 301) == 4`, `rows_before(book, "Notes", 1) == 6`.
- [ ] **Step 2: Run** `uv run pytest tests/executor/test_read_units.py -q`. Expected: FAIL (`Part` has no `units`).
- [ ] **Step 3: Implement** as in Interfaces. `rows_before` runs only for a range asked for by name, after the range's own walk has ended.
- [ ] **Step 4: Run** `uv run pytest tests/executor/test_read_units.py tests/executor/test_readers.py tests/executor/test_search.py -q`. Expected: PASS, with no existing reader test changed.
- [ ] **Step 5: Commit** `feat(readers): every part a read gives knows the units it covers`

### Task 2: `read_document`'s parts carry `units` and `cut`

**Files:**
- Modify:
  - `workbench/executor/runners/documents.py:122-172` (`read_document`'s `parts`);
  - `workbench/api/tools.py:286-296` (`DocumentPart`), `:619-640` (`_cut_part`);
  - `tests/api/golden/ghostwriter-modern.json`, `tests/api/golden/ghostwriter-legacy.json` (`DocumentPart`, by hand).
- Test: `tests/executor/test_search.py`, `tests/api/test_tools_documents.py`

**Interfaces:**
- Consumes: Task 1's `Part.units`; `DocumentText.cut` (the label of the part the reader cut).
- Produces:
  - Each runner part is `{"label": p.label, "text": p.text, "units": None if p.units is None else list(p.units), "cut": p.label == doc.cut}`.
  - `DocumentPart`:
    - `units: tuple[Annotated[int, Field(ge=1)], Annotated[int, Field(ge=1)]] | None = Field(default=None, description=...)`;
    - `cut: bool = Field(default=False, description=...)`;
    - a `model_validator(mode="after")` that refuses `units[0] > units[1]`;
    - the docstring's Attributes name both.

    The descriptions, in the golden schema:
    - `units`: "The first and last unit this part covers, counted from 1: pages, slides, paragraphs (a table counts as one), sheet rows that hold something (counted through all sheets) or lines; null for rows of a table that stop before its end."
    - `cut`: "True if this part's text was cut short; it then ends with ' [cut]'."
  - `_cut_part` builds its candidate as `part.model_copy(update={"text": part.text[:middle] + _CUT_MARK, "cut": True})`, so the units stay.
  - Nothing else in `DocumentRead` changes. The MCP door sends `None` fields (`tools_mcp.py:336-339`), so both keys are always present.

- [ ] **Step 1: Write the failing tests.**

  `tests/executor/test_search.py`:
  - `test_every_part_of_a_read_says_its_units_and_whether_it_was_cut`: on `fx.make_docx`, `[(p["label"], p["units"], p["cut"]) for p in read_doc(work, "review.docx")["parts"]]` is `[("paragraph 1", [1, 1], False), ("paragraph 2", [2, 2], False), ("paragraph 3", [3, 3], False), ("table 1, rows 1-3", [4, 4], False), ("paragraph 4", [5, 5], False)]`, and every part's keys are `{"label", "text", "units", "cut"}`.
  - `test_each_reader_gives_every_part_units_and_cut`, parametrized over `fx.make_pdf`, `make_pptx`, `make_docx`, `make_odt`, `make_xlsx`, `make_ods`, `make_html`, `make_eml` and a `.csv`, `.txt`, `.md` and minified `.json` written in the test. Every part has both keys; `units` is a list `[a, b]` with `1 <= a <= b`; `cut` is `False`.
  - `test_a_part_the_reader_cut_says_cut`: `pier-log.txt` holds 5,000 `x`, then `\nnext\n`. Read with `max_chars=3072`, its one part is `line 1`, `[1, 1]`, `cut: True`, and its text ends with `" [cut]"`.
  - `test_a_run_short_of_its_tables_end_has_null_units`: `fx.make_big_table_docx(rows=300)` at part `table 1, rows 30-60` gives `units is None`, and the key is present.

  `tests/api/test_tools_documents.py` (`Scripted`; payloads written with `units` and `cut`):
  - `test_units_and_cut_go_out_as_the_runner_gave_them`: the parts `("paragraph 1", "Quay", [1, 1], False)` and `("table 1, rows 1-30", "row 1: berth", None, False)` come back equal, `null` included.
  - `test_a_part_cut_at_the_result_limit_says_cut_and_keeps_its_units` (`small`, 1 KB). The parts are `paragraph 1` (`"word " * 1000`, `[1, 1]`) and `paragraph 2` (`"short"`, `[2, 2]`). The answer has `sent_size(body) <= 1024`; one part, `("paragraph 1", [1, 1], True)`, its text ending `" [cut]"`; and `next_part == "paragraph 2"`.
  - `test_an_answer_without_units_reads_as_null_and_not_cut`: `reading("x", ("paragraph 1", "Quay"))` gives `units is None` and `cut is False`.
  - `test_units_that_are_not_a_forward_pair_are_refused`, parametrized over `[0, 1]`, `[3, 2]`, `[1]` and `[1, 2, 3]`: `refusal(...) == ("refused", "the tool returned an answer that could not be read")`.
  - `test_a_pdf_read_with_its_pictures_keeps_each_pages_units` (`seeing`, the real runner). `harbour-report.pdf` of `["Harbour figures 2026", None, "Quay list"]` gives `[("page 1", [1, 1], False), ("page 3", [3, 3], False)]`, `pages_without_text == [2]` and `[s["n"] for s in body["shown"]] == [2]`.
  - The goldens: `tests/api/test_tools_golden.py` matches the served listings.
- [ ] **Step 2: Run** `uv run pytest tests/executor/test_search.py tests/api/test_tools_documents.py tests/api/test_tools_golden.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces, then edit `DocumentPart` in both Ghostwriter goldens by hand: the two properties and the docstring.
- [ ] **Step 4: Run** `uv run pytest tests/executor tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): read_document's parts carry units and cut`

### Task 3: The executor measures a document

**Files:**
- Modify:
  - `workbench/renders.py` (`ReadUnit`, `ReaderKind`, `READ_UNITS`);
  - `workbench/executor/readers/__init__.py` (`Measure`, `CharTally`, `measure`, `_Reader.measure`);
  - `workbench/executor/readers/paging.py` (`row_line`, `measure_blocks`, `measure_sheets`);
  - `workbench/executor/readers/text.py` (`measure_lines`, `measure`);
  - `measure` in each of `pdf.py`, `pptx.py`, `docx.py`, `odf.py`, `sheet.py`, `jsondoc.py`, `html.py`, `email.py`;
  - `workbench/executor/runners/documents.py` (`MEASURE_CHARS_CAP`, `measure_file`), `workbench/executor/runners/__init__.py` (`RUNNERS`).
- Test:
  - `tests/executor/test_measure.py` (new), `tests/test_renders.py`;
  - `tests/executor/test_job.py:381` (`test_the_registered_runners` gains `measure_file`);
  - `tests/executor/test_supervisor.py` (one root case).

**Interfaces:**
- Consumes: Task 1 (the units of a full read, for the coverage test).
- Produces:
  - In `workbench/renders.py`:
    - `ReadUnit = Literal["page", "slide", "paragraph", "row", "line"]`;
    - `ReaderKind = Literal["pdf", "docx", "xlsx", "csv", "pptx", "odt", "ods", "eml", "html", "json", "text"]`;
    - `READ_UNITS: Final[Mapping[ReaderKind, ReadUnit]]`: `pdf` page, `pptx` slide, `docx` and `odt` paragraph, `xlsx` and `ods` row, `csv`, `eml`, `html`, `json` and `text` line.
  - In `readers/__init__.py`:
    - `@dataclass(frozen=True) class Measure`: `kind: str`, `unit: str`, `total: int`, `chars: int`, `capped: bool`, and `@classmethod of(cls, kind: str, total: int, tally: CharTally) -> Measure`, with `unit = READ_UNITS[kind]`;
    - `class CharTally`: `__init__(self, cap: int)`; `add(self, text: str) -> bool` adds `len(text)` and answers whether counting goes on (`False` once past the cap); the properties `chars` (`min(count, cap)`) and `capped` (`count > cap`).
  - `readers.measure(path: Path, *, chars_cap: int, file_mb: int = 50) -> Measure`, under `read`'s guards (`_backstop`, `_prepare`, the same mapping of `MemoryError` and other exceptions). It raises `UnsupportedFormat`, `UnreadableFile` and `ReadTimeout`.
  - Each reader module gets `measure(path: Path, *, chars_cap: int) -> Measure`:
    - `pdf`, `pptx`: `total` from the page or slide count; text extracted page by page until the tally stops;
    - `docx`, `odt`: `pg.measure_blocks(kind, pieces, chars_cap=…)` over the reader's own pieces;
    - `xlsx`, `ods`: `pg.measure_sheets(kind, source, chars_cap=…)`;
    - `text` (kind `csv` or `text` by suffix), `json` (`_lines`), `html` (`html_to_lines`), `eml` (`lines_of`): `txt.measure_lines(kind, lines, chars_cap=…)`.
  - The helpers:
    - `pg.measure_blocks(kind: str, pieces: Iterable[Part], *, chars_cap: int) -> Measure`: `total` is paragraphs plus tables with a row, as `read_blocks` counts `total_parts`; it tallies each paragraph and each row as `f"row {r}: {text}"`.
    - `pg.row_line(number: int, row: list[str]) -> str`: `f"{number}: " + " | ".join(cells)`, trailing empty cells dropped. It is taken out of `read_sheets`, which uses it.
    - `pg.measure_sheets(kind: str, source: SheetSource, *, chars_cap: int) -> Measure`: one walk `source.walk(None, 1, cap=None)`, so every cell is whole; it tallies `row_line(number, row)`; `total` counts every row, and the tally stops at the cap.
    - `txt.measure_lines(kind: str, lines: Sequence[str], *, chars_cap: int) -> Measure`: `total = len(lines)`; it tallies each `line` whole, never `_shown(line)`.
  - In `runners/documents.py`:
    - `MEASURE_CHARS_CAP: Final = 2_000_000`, read at call time, so a test can patch it;
    - `measure_file(args: dict[str, Any], ctx: JobContext) -> dict[str, Any]`:
      - `args` holds `path` (relative to the chat folder) and `budget_s`;
      - it answers `{"path": "/workspace/<chat>/<name>", "kind", "unit", "total", "chars", "capped"}`;
      - or an error: `file_not_found`, `unsupported_format`, `unreadable_file` or `read_timeout`, each with `read_document`'s texts.
  - `RUNNERS["measure_file"] = measure_file`.

- [ ] **Step 1: Write the failing tests.**

  `tests/executor/test_measure.py`. The helpers:
  - `measure(path, chars_cap=2_000_000)` calls `readers.measure(path, chars_cap=chars_cap)`;
  - `read` is `test_readers.read`; `reads` and `gappy_xlsx` come from `tests/executor/test_read_units.py`;
  - `write(path, data: bytes) -> Path` writes the bytes and gives the path back;
  - `SHEET1_LINES` and `SUMMARY_LINES` are the six and the two row lines that
    `test_readers.test_small_workbook_reads_its_first_sheet_and_points_to_the_next` asserts.

  ```python
  def test_a_pdf_is_measured_in_pages(tmp_path: Path) -> None:
      pdf = fx.make_pdf(tmp_path / "harbour-report.pdf", ["First page text\nsecond line", None, "Quay list"])
      assert measure(pdf) == readers.Measure("pdf", "page", 3, len("First page text\nsecond line") + len("Quay list"), False)

  def test_a_deck_is_measured_in_slides(tmp_path: Path) -> None:
      deck = fx.make_pptx(tmp_path / "harbour-deck.pptx")
      assert measure(deck) == readers.Measure("pptx", "slide", 2, sum(len(p.text) for p in read(deck).parts), False)

  def test_a_word_document_counts_a_table_as_one_paragraph(tmp_path: Path) -> None:
      shown = ["Quarterly figures review", "The harbour project is on schedule.", "Budget notes follow in the table below.",
               "row 1: Item | Planned | Actual", "row 2: Dredging | 120 | 118", "row 3: Piers | 340 | 351",
               "Contact the invented office for questions."]
      assert measure(fx.make_docx(tmp_path / "review.docx")) == readers.Measure("docx", "paragraph", 5, sum(map(len, shown)), False)
      survey = measure(fx.make_odt(tmp_path / "survey.odt"))
      assert (survey.kind, survey.unit, survey.total) == ("odt", "paragraph", 4)

  def test_a_workbook_counts_its_rows_through_every_sheet(tmp_path: Path) -> None:
      book = measure(fx.make_xlsx(tmp_path / "quarterly-figures.xlsx", rows=5))
      assert (book.kind, book.unit, book.total, book.capped) == ("xlsx", "row", 8, False)
      assert book.chars == sum(map(len, SHEET1_LINES + SUMMARY_LINES))  # the eight lines test_readers asserts
      assert measure(gappy_xlsx(tmp_path / "gappy.xlsx")).total == 7
      assert measure(fx.make_ods(tmp_path / "depths.ods")).total == 2

  def test_text_is_measured_in_lines_and_characters(tmp_path: Path) -> None:  # characters, not bytes; \r\n is one break
      notes = write(tmp_path / "pier-notes.txt", b"alpha\nbeta\n\ngamma\n")
      assert measure(notes) == readers.Measure("text", "line", 4, 14, False)
      assert measure(write(tmp_path / "berths.csv", b"a,b\n1,2\n")) == readers.Measure("csv", "line", 2, 6, False)
      assert measure(write(tmp_path / "greeting.txt", "Grüße\r\n東京".encode())) == readers.Measure("text", "line", 2, 7, False)

  def test_a_json_file_is_measured_in_the_lines_it_is_read_as(tmp_path: Path) -> None:
      value = {"harbour": "north", "piers": [1, 2]}
      path = write(tmp_path / "berths.json", json.dumps(value, separators=(",", ":")).encode())
      shown = json.dumps(value, indent=2, ensure_ascii=False).splitlines()
      assert measure(path) == readers.Measure("json", "line", 7, sum(map(len, shown)), False)

  def test_the_count_stops_at_the_cap_and_the_total_does_not(tmp_path: Path) -> None:  # Review Focus 4
      path = write(tmp_path / "log.txt", "".join(f"{n:04d} " + "x" * 45 + "\n" for n in range(100)).encode())
      assert measure(path, chars_cap=120) == readers.Measure("text", "line", 100, 120, True)
      assert measure(path, chars_cap=5000) == readers.Measure("text", "line", 100, 5000, False)
  ```

  - `test_html_and_mail_are_measured_in_the_lines_they_read_as`: for `fx.make_html` and `fx.make_eml`, `(m.unit, m.total, m.chars)` is `("line", doc.total_parts, sum(len(line) for line in doc.parts[0].text.split("\n")))`.
  - `test_a_line_or_a_cell_a_read_shortens_is_measured_whole` (Decisions 5 and 7):
    - `one-line.txt`, 50,000 `x` with no line break, measures `readers.Measure("text", "line", 1, 50_000, False)`;
    - `read(path)` shows it shortened, `parts[0].text == "x" * 2000 + "… [+48000 characters]"`, with `doc.cut is None`;
    - through the runner (`test_search.read_doc`), that part has `units == [1, 1]` and `cut is False`;
    - a workbook whose only cell, `A1`, holds 1,000 `y` measures `chars == len("1: ") + 1000`, while its read shows the cell clipped at 300.
  - `test_a_pdf_stops_extracting_once_past_the_cap` (Review Focus 4): ten pages of `"y" * 50` with `chars_cap=120` give `(total, capped) == (10, True)`, and a wrapped `pdf._page_text` was called 3 times.
  - `test_measure_refuses_what_read_refuses`:
    - parametrized over the seven kinds of `test_readers.test_broken_and_encrypted_files_fail_cleanly` (`hostile_files`), with `file_mb=1`: each raises `UnreadableFile`, and its message names neither `tmp_path` nor the file;
    - `ledger.xls` raises `UnsupportedFormat` with `"save it as .xlsx or .csv"`.
  - `test_paging_through_a_document_covers_exactly_its_measure` (Review Focus 1-3):
    - parametrized over: a PDF with a scanned page 2; `fx.make_pptx`; `fx.make_big_table_docx(rows=300)`; `fx.make_odt`; `fx.make_xlsx(rows=100)`; `gappy_xlsx`; `fx.make_ods`; a 300-line `.txt`; a minified `.json`; `fx.make_html`; `fx.make_eml`;
    - over `reads(path, max_chars=2048)` (Task 1), the units of every part not cut, joined with every `pages_without_text`, are exactly `set(range(1, measure(path).total + 1))`.
  - The runner:
    - `test_measure_file_answers_the_counts`: `RUNNERS["measure_file"]({"path": "review.docx", "budget_s": 30}, context(work))` equals `{"path": "/workspace/chat-1/review.docx", "kind": "docx", "unit": "paragraph", "total": 5, "chars": <as above>, "capped": False}`.
    - `test_measure_file_reads_its_cap_when_it_runs`: with `monkeypatch.setattr(documents, "MEASURE_CHARS_CAP", 10)`, the answer has `chars == 10` and `capped is True`.
    - `test_measure_file_says_what_went_wrong`:
      - a missing file gives `{"error": "file_not_found: /workspace/chat-1/missing.txt is not in this conversation's workspace"}`;
      - `quay-photo.jpg` gives an error starting `unsupported_format:`;
      - `fx.make_big_table_docx(rows=5000)` with `budget_s` 0.000001 gives an error starting `read_timeout:`.

  Also:
  - `tests/test_renders.py::test_each_reader_kind_has_one_unit`: `READ_UNITS` has the 11 kinds above, and `set(READ_UNITS.values()) == {"page", "slide", "paragraph", "row", "line"}`.
  - `tests/executor/test_supervisor.py::test_as_root_a_document_is_measured_in_a_slot`, following `test_as_root_a_photo_and_a_scan_are_prepared_in_a_slot`:
    - `review.docx` and `harbour-report.pdf` in the chat; `tool="measure_file"`, `args={"path": name, "budget_s": 100}`, production limits;
    - each is `ok`, with the kind, unit and total above, and `answer.outputs == []`.
- [ ] **Step 2: Run** `uv run pytest tests/executor/test_measure.py tests/test_renders.py tests/executor/test_job.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces. In `pdf` and `pptx`, `check()` runs before each page or slide, as in `read`.
- [ ] **Step 4: Run** `uv run pytest tests/executor tests/test_renders.py -q`, then `make test-executor-root`. Expected: PASS, in both `/dev/shm` shapes.
- [ ] **Step 5: Commit** `feat(executor): measure a document in its reader's units`

### Task 4: A gate of its own for measures, and one more slot

**Files:**
- Modify:
  - `workbench/api/jobs.py`:
    - `Gate` `:123-125` gains `"measure"`;
    - `submit` `:728` gains `gate`;
    - the docstrings of `_UserGate` `:435`, `turn`, `_turn` `:1037`, `_shared_gate` `:1116`, `_admit`, `_join_line` and `_report_length` name the measure gate beside the prepare and conversion gates.
  - `workbench/config.py`:
    - `GHOSTWRITER_DEFAULT_SLOTS` `:49` 3 → 4;
    - the default of `WORKBENCH_EXECUTOR_SLOTS` `:219` 4 → 5;
    - the default of `WORKBENCH_QUEUE_MAX` `:264` 16 → 20.
  - `docker/compose.yaml`:
    - `:52` (`WORKBENCH_EXECUTOR_SLOTS:-5`), `:53` (`WORKBENCH_QUEUE_MAX:-20`), `:113` (`GHOSTWRITER_WORKBENCH_SLOTS:-4`);
    - `:205-211`: `pids_limit` 384 and `mem_limit`/`memswap_limit` 8192m, with their comments' arithmetic.
  - `.env.example` `:48`, `:51-54`, `:86-90` and `:112-122`: the same defaults and arithmetic; Ghostwriter's slots comment names the measure, "keep 4 or more".
  - `docs/configuration.md`:
    - the rows of the four variables, `GHOSTWRITER_WORKBENCH_SLOTS` `:36` rewritten for four (a prepare, a conversion, a measure, one for the tool calls; the cost at 3 and at 2; the start rule);
    - the shares `:86-101` ("4 + 2 slots of 5, 16 + 8 places of 20"; the user's share stays 4);
    - after the prepare-gate and conversion-gate paragraphs `:102-125`, one on the measure gate (one measure of a caller at a time, its own line of 32, 120 s, a client timeout of 120 s + the wall + 30 s);
    - the formulas `:225-240` (5 × 1536 + 512 = 8192 MiB; 5 × 64 + 64 = 384).
- Test:
  - `tests/api/test_jobs.py`;
  - `tests/test_config.py` (`:350`, the executor's default 5; the queue's 20; Ghostwriter's 4);
  - `tests/test_compose_defaults.py`: `SPEC_DEFAULTS` `:28` 5 and `QUEUE_MAX` 20; `GHOSTWRITER_DEFAULTS` `:55` 4; `test_executor_memory_cannot_swap_past_its_limit` `:192` 8192m and `8192 == slots * job_mb + 512`.

**Interfaces:**
- Produces:
  - `Gate = Literal["user", "prepare", "conversion", "measure"]`. `measure` is a caller's shared gate, as `prepare` and `conversion` are: `_shared_gate` makes it (one job at a time over the caller's users), and `_join_line` counts its waiting calls against its own `PREPARE_WAITING_MAX`. A job on it then takes the caller's gate and a global slot, in that order, as any job does.
  - `JobQueue.submit(self, p, tool, args, input_files, keeper=None, *, call: CallLimits, previews: bool = False, gate: Gate = "user")` passes `gate` to `self._turn(p, call, gate=gate)`. Task 5 sends the measure with `gate="measure"`.
  - The defaults of Decision 10.
- Ghostwriter: nothing in its `config.py` or `.env.example` changes, since it sets none of workbench's slots. Three of its texts say `GHOSTWRITER_WORKBENCH_SLOTS` "at 3 or more" (`ghostwriter/CLAUDE.md:37`, `docs/data-flow.md:106`, `ghostwriter/processing.py:29`). That still holds for a conversion beside a prepare; a measure beside both needs 4, workbench 0.10.0's default.

- [ ] **Step 1: Write the failing tests.**

  `tests/api/test_jobs.py`. Ghostwriter's caller has 4 slots and the executor 5, the new defaults. `FakeClient.hold` keeps a job running until the test lets it go, as in `test_a_prepare_never_takes_a_users_turn`.
  - `test_a_measure_runs_beside_a_prepare_a_conversion_and_a_tool_call` (Review Focus 5):
    - one job held on each of the `prepare`, `conversion` and `measure` gates;
    - a `user`-gate job of `user-b` is admitted at once, with no wait;
    - a second `measure` job waits until the first is let go, then runs.
  - `test_a_prepare_and_a_tool_call_never_wait_for_a_measure`: with a `measure` job held, a `prepare` job and a `user` job each start at once.
  - `test_measures_wait_in_a_line_of_their_own`:
    - with one measure held and 32 waiting, the 33rd measure is `Busy("queue_full")` at once;
    - a prepare and a user's call are still admitted, and `queue_length` counts the 32.
  - `test_one_measure_of_a_caller_runs_at_a_time`: the measures of `user-a` and `user-b`, one caller's, run one after the other.

  `tests/test_config.py` and `tests/test_compose_defaults.py`:
  - the defaults are 5 executor slots, a queue of 20 and 4 Ghostwriter slots;
  - `GHOSTWRITER_WORKBENCH_SLOTS=5` with the executor's default is still refused, as `ghostwriter_slots >= slots` refuses it today;
  - the executor container is `8192m` and `384` processes.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_jobs.py tests/test_config.py tests/test_compose_defaults.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces, then `.env.example` (check it with `python3 -B ../deploy/scripts/check_env_examples.py .`) and `docs/configuration.md`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(jobs): a gate of its own for measures, and a fifth executor slot by default`

### Task 5: `POST /files/measure`

**Files:**
- Create: `workbench/api/measure.py`
- Modify:
  - `workbench/api/auth.py:54-63` (`MEASURE_PATH`, `FILE_PATHS`);
  - `workbench/api/audit.py:43-60` (`OPERATIONS["measure"]`);
  - `workbench/api/jobs.py`: `TOOLS` `:333`;
  - `workbench/api/store.py`: `MEASURES_MAX`, `remember_measure`, `measure_of`; `_forget_refusals` `:1650` forgets measures too;
  - `workbench/api/files.py`: the route, and the module docstring;
  - `workbench/api/errors.py:11-16`, whose docstring now says four routes raise `Busy`, the measure among them.
- Docs:
  - `docs/permissions.md`:
    - the callers table `:16` and the bullet `:22-25` say "eight file routes", adding `/files/measure`;
    - a row in § File routes;
    - § Which runs are audited.
  - `docs/audit.md`: the `measure` row in § Every event key; § Not recorded gains a measure answered from memory and the refusal of a picture or a clip by its name.
  - `docs/data-flow.md`:
    - § What enters: the length of a document, counted in the executor by the readers, counts only;
    - § Where it is stored: the counts in the api's memory, by file id, until the file goes or a restart; nothing on the volume;
    - § What leaves: the counts go to Ghostwriter.
  - `docs/configuration.md`: `:33` says "eight file routes"; the measure gate's paragraph (Task 4) names `POST /files/measure`. No new variable.
- Test:
  - `tests/api/test_files_measure.py` (new), `tests/api/test_store.py`;
  - `tests/api/test_audit.py:28` (`EXPECTED_OPERATIONS`);
  - `tests/api/test_files_ghostwriter.py:278`, where `…_six_paths` becomes `…_seven_paths` and adds `/files/measure`.

**Interfaces:**
- Consumes: Task 3's `measure_file` runner and its answer; Task 4's `submit(..., gate="measure")`; `READ_UNITS`, `ReadUnit`, `ReaderKind`; `tools._payload`, `_parse`, `_readable`, `record_answer`, `refusal`; `files._wall_of`; `kind_of`.
- Produces:
  - `auth.MEASURE_PATH: Final = "/files/measure"`, in `FILE_PATHS`.
  - `audit.OPERATIONS["measure"] = ("query", "file.measure")`.
  - `jobs.TOOLS` gains `"measure_file"`.
  - The store:
    - `MEASURES_MAX: Final = 4096`;
    - `Store.remember_measure(self, source: FileRecord, outcome: Mapping[str, object] | str) -> None`: the five counts, or a refusal's detail. It is kept by the source's id with its creation time and size, and not kept for a chat deleted within `DELETED_CHAT_HOLD_S`, as `remember_refusal`;
    - `Store.measure_of(self, source: FileRecord) -> Mapping[str, object] | str | None`: `None` for nothing held, or for a cell of another time or size, which is dropped;
    - `remove`, `remove_upload` and `delete_chat` forget the measure with the refusal.
  - In `workbench/api/measure.py`:
    - `class MeasureRequest(BaseModel)`, with `model_config = ConfigDict(extra="forbid")` and `path: str = Field(min_length=1, max_length=1024)`.
    - `class Measured(BaseModel)`:
      - `path: str`, `kind: ReaderKind`, `unit: ReadUnit`, `total: int = Field(ge=0)`, `chars: int = Field(ge=0)`, `capped: bool`;
      - a `model_validator(mode="after")` that refuses `unit != READ_UNITS[kind]`.
    - `NOT_A_DOCUMENT: Final = "unsupported_format: a picture or a clip is not a document and has no text to measure"`.
    - `async def measured(p: Principal, source: FileRecord, *, settings: Settings, store: Store, audit: AuditLog, queue: JobQueue, call: CallLimits) -> Measured`, in order:
      1. `kind_of(source.name)` is `image` or `media`: `HTTPException(422, NOT_A_DOCUMENT)`, with no job and no event.
      2. `store.measure_of(source)`:
         - a mapping: `Measured.model_validate({**held, "path": source.virtual_path})`, with no job and no event;
         - a string: `audit.record("measure", p, file_ids=[source.id], outcome="error", criteria={"refused": <code>, "remembered": True})`, then `HTTPException(422, held)`.
      3. `queue.submit(p, "measure_file", {"path": source.name, "budget_s": max(1, settings.job_wall_s * 3 // 4)}, [source.name], call=call, gate="measure")`.
      4. The payload is `{**result.result, "path": source.virtual_path}`. `record_answer(audit, "measure", p, [source.id], {}, result, Measured, payload)` runs before anything goes out.
      5. `_payload(result, "file")`. A raised `HTTPException` whose `refusal(result)` is `unsupported_format` or `unreadable_file` is first remembered (`store.remember_measure(source, detail)`); every other one is raised as it is.
      6. `answer = _parse(Measured, payload)`, then `store.remember_measure(source, answer.model_dump(mode="json", exclude={"path"}))`. It returns `answer`.
  - The route, in `workbench/api/files.py`, `@router.post(MEASURE_PATH) async def measure_file(body: MeasureRequest, p: FilePrincipalDep, caller: CallerDep, settings: SettingsDep, store: StoreDep, audit: CallAuditDep, jobs: JobsDep) -> dict[str, object]`, in order:
    1. `audit.check("measure", p)`;
    2. `store.get(p, body.path)`;
    3. `wall, budgeted = _wall_of(settings, caller)`;
    4. `measured(..., settings=budgeted, queue=jobs, call=CallLimits(wall, PREPARE_QUEUE_WAIT_S, caller.slots))`: the measure gate waits as long as the prepare gate;
    5. it answers `.model_dump(mode="json")`.

- [ ] **Step 1: Write the failing tests.**

  `tests/api/test_files_measure.py`:
  - The setup:
    - the fixtures `executor` (`Local`), `queue`, `app` and `built`, as in `tests/api/test_files_renders.py`;
    - `HEADERS = {**HG, "X-Audit-Purpose": "reason=ghostwriter+chat", "X-Request-ID": "req-1"}`;
    - `measure(client, path, headers=HEADERS)` posts `json={"path": path}`.
  - `test_a_pdf_is_measured_and_recorded_as_a_query`:
    - `harbour-report.pdf` of `["First page text\nsecond line", None, "Quay list"]` answers `200`, `{"path": src.virtual_path, "kind": "pdf", "unit": "page", "total": 3, "chars": 36, "capped": False}`;
    - one event: `("query", "file.measure", "ok")`, `target.ids == [src.id]`, `purpose == {"reason": "ghostwriter chat"}`, `request_id == "req-1"`;
    - one job, `measure_file`, whose `input_files == [src.name]`.
  - `test_a_word_document_with_a_table_is_measured_in_paragraphs`: `("docx", "paragraph", 5)`.
  - `test_a_workbook_of_two_sheets_is_measured_in_rows_across_both`: `fx.make_xlsx(rows=5)` gives `("xlsx", "row", 8)`.
  - `test_text_and_json_are_measured_in_lines`: `pier-notes.txt` gives `("text", "line", 4, 14, False)`; a minified `berths.json` gives `("json", "line", 7)`.
  - `test_the_count_stops_at_the_cap`: with `documents.MEASURE_CHARS_CAP` patched to 10, `pier-notes.txt` gives `(total, chars, capped) == (4, 10, True)`.
  - `test_a_picture_or_a_clip_is_not_a_document`: `quay-photo.jpg` and `harbour-tour.mp4` each give `(422, {"detail": NOT_A_DOCUMENT})`; `executor.requests == []`, and there is no `file.measure` event.
  - `test_a_second_measure_is_answered_from_memory`: the same body twice, one job, one event.
  - `test_a_refusal_is_remembered_and_a_timeout_is_not`:
    - `ledger.xls` (invented bytes) is `422` twice, its detail starting `unsupported_format: .xls cannot be read here`. There is one job, and the events' `(outcome, criteria["refused"], criteria.get("remembered"))` are `[("error", "unsupported_format", None), ("error", "unsupported_format", True)]`.
    - With the executor answering `status "error"` and `{"error": "read_timeout: reading the file took longer than 1 s; it is too large or too complex"}`, `pier-notes.txt` is `422` twice and runs two jobs.
  - `test_a_file_taken_back_is_forgotten`: measure, `DELETE /files/upload`, then measure again: `(404, {"detail": "not found"})`, and `store.measure_of(src) is None`.
  - `test_measure_needs_a_purpose`: with `require_purpose={"query"}` and `HG` alone, the answer is `(428, {"detail": "a stated purpose is required"})`. One `file.measure` event, `denied`, with `refused == "purpose_required"`, and no job.
  - `test_another_chats_file_a_missing_file_or_a_derived_path_is_404`: `NOT_FOUND` each, with no job and no event.
  - `test_docint_finds_no_measure_route`: `HD` gives `(404, {"detail": "not found"})`, with no job.
  - `test_the_body_is_a_path_and_nothing_else`: `{}`, `{"path": ""}` and `{"path": p, "strips": 2}` are each `422`, with no job.
  - `test_a_measure_waits_on_the_callers_measure_gate`: a spy on `queue.submit` sees `("measure_file", gate "measure", call.wait_s == PREPARE_QUEUE_WAIT_S)`. The budget follows prepare's rule (`test_a_callers_wall_bounds_the_runners_budget`).
  - `test_a_busy_queue_is_503_and_not_remembered`: the executor busy gives `BUSY`; the next call, with room, answers `200`.

  Elsewhere:
  - `tests/api/test_store.py`:
    - `test_a_measure_is_remembered_by_file_and_forgotten_with_it`:
      - `remember_measure(rec, counts)`, then `measure_of(rec) == counts`;
      - a copy of `rec` with another `size` gives `None`;
      - after `remove_upload(rec, unrecorded)`, `None`;
      - a refusal string round-trips.
    - `test_measures_are_bounded_and_not_kept_for_a_deleted_chat`: with `MEASURES_MAX` patched to 2, the first of three is forgotten; after `delete_chat`, `remember_measure` keeps nothing.
  - `tests/api/test_audit.py`: `EXPECTED_OPERATIONS["measure"] == ("query", "file.measure")`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_files_measure.py tests/api/test_store.py tests/api/test_audit.py tests/api/test_files_ghostwriter.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces, then the four docs. Each follows its neighbouring row or paragraph for prepare.
- [ ] **Step 4: Run** `uv run pytest -q`, which includes `tests/test_docs.py` (`file.measure` in `docs/audit.md`). Expected: PASS.
- [ ] **Step 5: Commit** `feat(files): POST /files/measure answers a document's length in its reader's units`

### Task 6: Design record and release

**Files:**
- Modify:
  - `CLAUDE.md`:
    - the design-history paragraph gains "v0.10.0's (a document's length, and the units of a read) are deploy's spec `docs/superpowers/specs/2026-11-24-ghostwriter-limit-notices-design.md` and its plan `docs/superpowers/plans/2026-11-24-ghostwriter-limit-notices-workbench-v0.10.md`";
    - the `renders.py` list gains `READ_UNITS`;
    - the `workbench/api/files.py` bullet gains the measure (its own gate, counts only, remembered in memory);
    - the paragraph on the prepare gate and the conversion gate gains the measure gate, and why a measure has a slot of its own (Decision 10);
    - `read_document`'s paragraph gains `units` and `cut`;
    - § Callers says "eight file routes";
    - the Gotcha "A refusal is remembered" adds that a measure is remembered likewise (`Store.measure_of`).
  - `README.md:16`: "eight file routes", adding "measure a document".
  - `pyproject.toml`: `version = "0.10.0"`; and `uv.lock`.

- [ ] **Step 1: Docs.** Run `uv run pytest tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 2: Version.** Run `uv lock`, `uv sync --group dev --extra executor`, then `uv run pytest tests/test_version.py tests/api/test_tools_golden.py -q`. Expected: PASS.
- [ ] **Step 3: Gates.** Run `make verify && make test`, then `make test-executor-root` and `make test-isolation`. Expected: each is green; paste the summary lines into the PR.
- [ ] **Step 4: Commit** `docs: the measure and the units of a read in the design record`, then `chore(release): 0.10.0`.
- [ ] **Step 5: Push and PR.** `git push -u origin feat/measure`; a PR to `main`.
  - **Operators** lines:
    - "0.10.0 adds `POST /files/measure` (Ghostwriter's caller only), and `units` and `cut` on each part `read_document` answers. No new variable. Ghostwriter 0.7.0 uses both; an older caller sees two more keys per part."
    - "Five defaults change so that a measure runs on a slot of its own: `WORKBENCH_EXECUTOR_SLOTS` 5, `GHOSTWRITER_WORKBENCH_SLOTS` 4, `WORKBENCH_QUEUE_MAX` 20, `WORKBENCH_EXECUTOR_MEM_LIMIT` 8192m, `WORKBENCH_EXECUTOR_PIDS_LIMIT` 384. The executor may now use up to 1.5 GiB more."
    - "A `.env` that pins `WORKBENCH_EXECUTOR_MEM_LIMIT=6656m` but not the slots stops the executor at start: raise it with the slots, or pin both. One that pins `GHOSTWRITER_WORKBENCH_SLOTS=3` keeps working, but while a prepare, a conversion and a measure run, Ghostwriter's tool calls answer busy."
  - Confirm CI ran on the last commit.

---

## Self-review

- **Spec coverage**, §5.4 "workbench 0.10.0", §6 step 1 and §7's workbench gates:
  - the route, the ghostwriter caller only, run in the executor with the existing readers, `{path, kind, unit, total, chars, capped}`, `MEASURE_CHARS_CAP`, remembered, audited: Tasks 3 and 5;
  - the anti-goal "a notice never blocks or delays a send": the measure's own gate and slot, Task 4;
  - the units table, one unit per reader for both the route and `read_document`: Tasks 1 and 3;
  - `parts[].units` and `cut`: Tasks 1 and 2;
  - `docs/permissions.md` (eight file routes), `docs/data-flow.md` and `docs/configuration.md`: Tasks 4 and 5; the design record and the version: Task 6;
  - the gates: tests for PDF, docx, xlsx with several sheets, txt and capped (Tasks 3 and 5); `parts[].units` and `cut` (Tasks 1 and 2).
- **Type consistency:**
  - `Part.units` and `part_size(…, units, cut=)` are Task 1's, used in Tasks 2 and 3;
  - `READ_UNITS`, `ReadUnit` and `ReaderKind` are Task 3's, used in Task 5;
  - `Gate`'s `"measure"` and `submit(..., gate=)` are Task 4's, used in Task 5;
  - `measure_file` (the runner and the route's handler share the name, as `prepare_file`'s do), `MEASURE_CHARS_CAP` and `Measure` are Task 3's;
  - `Measured`, `MeasureRequest`, `NOT_A_DOCUMENT`, `MEASURE_PATH` and `remember_measure`/`measure_of` are Task 5's.
- **Proportion:** code appears only in tests. The block-numbering and sheet-offset rules are stated in Task 1's Interfaces; everything else is signatures, values and assertions.
- **Outside this plan:** `infra/CLAUDE.md` and ADR 0007 list Ghostwriter's file routes; they are brought up to date in spec §6 step 4.
