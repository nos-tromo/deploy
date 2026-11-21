# Ghostwriter vision, stage 2: workbench v0.8.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** workbench shows the pages and slides of Word, PowerPoint and OpenDocument text files as the user would see them. LibreOfficeKit converts each file once into a PDF that is kept as a derived file. Stage 1's page renders, `view_file` and `read_document` then work on that PDF.

**Architecture:**
- A new executor runner, `convert_office`, checks the file first (zip guard, encryption). It then loads LibreOfficeKit in a forked child of the job process and writes `converted.pdf`. The parent kills the child if it runs past its deadline.
- The api keeps the PDF as a derived file of its source (role `pdf`, already reserved). The existing `prepare_file` runner reads that PDF as its input and draws its pages; its facts now say kind `office` and a `unit` (`page` or `slide`).
- Both jobs of a prepare run in one turn of the queue.

**Tech Stack:** Python 3.12, FastAPI, pydantic, SQLite; executor: LibreOffice (Debian trixie, `nogui`) via LibreOfficeKit over ctypes, pypdfium2, Pillow; fonts Carlito, Caladea, Liberation 2, DejaVu, Noto; tests: pytest, python-pptx, python-docx, odfpy, reportlab.

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§6 S1, §7.2 Office, §8 stage 2, §9–§12). It extends stage 1's plan, `deploy/docs/superpowers/plans/2026-11-20-ghostwriter-vision-workbench-v0.7.md`, and that plan's Contract.

Every path below is relative to the `workbench` repo root unless it starts with `deploy/`. Line numbers are those of v0.7.0 (`feat/vision-stage1` at `8be3f44`). Read `CLAUDE.md` before Task 1.

## Decisions

These are the lead's calls (stage 2). The owner may correct any of them before execution. A line marked *(plan)* is a refinement this plan makes inside the call.

- **D1 Formats:** `.docx .dotx .pptx .potx .odt`, as `OFFICE_SUFFIXES` in `workbench/renders.py`. These are left out:
  - xlsx/xlsm/ods: LibreOffice paginates them by print area, and `view_file` keeps saying spreadsheets are not shown;
  - doc/ppt/rtf: the readers refuse them with "save it as";
  - odp: it has no text reader and is no Ghostwriter attachment type.
- **D2 Wire:** a new kind `office`, plus `unit: Literal["page", "slide"]` (default `"page"`) on `RenderFacts` and on `Manifest`. pptx and potx are `slide`; docx, dotx and odt are `page`. *(plan)* Ghostwriter 0.3.0 never prepares an Office file (`ghostwriter/upload.py:67`) and reads `view_file` answers by `shown` alone, so it is unaffected. 0.4.0 reads `office` and `unit`.
- **D3 Convert once, render many:**
  - `convert_office` is a runner of its own (`RUNNERS`, `jobs.TOOLS`). It forks a child for LibreOfficeKit, one conversion per job process, and writes `converted.pdf`: a derived file, role `pdf`, `application/pdf`, signature `%PDF-`, at most `CONVERTED_MAX_BYTES = 32 MiB`, with no width or height.
  - `prepare_file` draws the pages from that PDF. Prepare means: convert when there is no `pdf` row, then draw. On-demand renders reuse the `pdf` row, and a row that is gone is converted again under the source lock.
  - Impress filter data: `ExportHiddenSlides` true and `ExportNotesPages` false, so that slide N is page N.
  - *(plan)* The PDF reaches the render job as its one input at its derived path, `.derived/<source-id>/<id>.pdf`. The api sets that path from the record, never from a caller's string; the supervisor copies inputs from the chat folder by path (`workbench/executor/supervisor.py:1328`), and the name is neutral already.
  - *(plan)* The two jobs of one prepare run in one turn of their gate (`JobQueue.turn`), so a burst of prepares does not queue each file twice.
  - *(plan)* Both filters also downsample pictures to 150 dpi. The renders are 1600 px, and this keeps photo decks within 32 MiB.
- **D3 facts:** `kind` `office`, `unit` from the suffix, `total` = the PDF's pages, `pages_without_text` from pdfium on the PDF. When a deck's PDF has a different number of pages than the reader has slides, the hint says so.
- **D4 Tools:**
  - `view_file` shows Office pages and slides, labelled by unit (`slide 3`, `page 3`). For unit `slide` it takes `slide N` / `slides N-M` (either word) and bare numbers; for unit `page` it takes `page N` / `pages N-M`.
  - `read_document` on pptx/potx attaches the renders of the slides without text, as it does for PDF pages. On docx/dotx/odt it attaches nothing and says that `view_file` shows the pages.
  - The descriptions, both goldens, `NOT_SHOWN` ("spreadsheets") and the docs change with it. `WORKSPACE_INSTRUCTIONS` needs no change (`workbench/api/mcp_wire.py:58-62` already says pages come back as images).
  - *(plan)* The executor's slide hint mirrors the PDF one (`…; do not treat them as blank.`), and the api names the slides that come as images. The hint therefore never promises an image that could not be made.
  - R15 stands: none of this is offered or attached without `X-Accept-Images: 1`.
- **D5 Image:**
  - One apt layer: LibreOffice `core`/`writer`/`impress`/`calc` `-nogui` and the five font packages, `--no-install-recommends`, then `fc-cache -f`.
  - A profile baked at build time in `/opt/lo-profile`, copied into each conversion's tmp as `/opt/mpl-cache` is.
  - `SAL_USE_VCLPLUGIN=svp` is set in the child. *(plan)* `HOME` and `TMPDIR` are the job's already (`workbench/executor/sandbox.py:301-316`, `supervisor.py:1138-1139`).
  - The image grows by about 515 MB (S1).
- **D6 Limits:**
  - `readers.check_zip`, the OLE/encrypted refusal and *(plan)* an ODF encryption check run before LibreOffice sees a file.
  - No macros, no network, the seccomp filter unchanged.
  - A hung C call is stopped only by killing the child, at the parent's deadline.
  - `RLIMIT_AS` stays 1536 MB per process. **If LibreOfficeKit cannot start under it in Task 1, stop and report: a per-tool limit is the owner's ruling.**
  - Timeouts answer `read_timeout`. LibreOffice's files go to the job's home and tmp; `out/` holds only `converted.pdf`.
- **D7 Tests:** real LibreOffice only in the image: root cases in `tests/executor/test_supervisor.py` (`make test-executor-root`) and an isolation case. Host tests inject a fake converter. Fixtures are synthetic only.
- **D9:** version `0.8.0`, one PR, `make verify`, `make test`, `make test-executor-root` and `make test-isolation`. The release note says the image grows by about 515 MB, and `docs/README.md` carries the LibreOffice and font licences.

## Global Constraints

- Branch `feat/vision-stage2` from `main` once v0.7.0 is merged. GitHub Flow, one PR. `pyproject.toml` `version = "0.8.0"` and `uv lock` happen in Task 7 only; the tag is minted on merge, never by hand. Workflow pins stay unchanged.
- Python `>=3.12,<3.13`. Ruff with Google docstrings (`D`) and annotations (`ANN`), pyrefly strict, 120-character lines.
- `make verify` and `make test` must be green before every push (`git add` new files first). `make test-executor-root` and `make test-isolation` must be green before the PR opens.
- The executor imports nothing of `workbench.api`. The rules both sides use live in `workbench/renders.py`, which imports no imaging library and nothing of LibreOffice.
- The api never decodes or converts: it imports neither `PIL`, `pypdfium2`, `pi_heif` nor anything of LibreOffice. It checks a derived file's signature and size only.
- `workbench/executor/render/office.py` imports only the standard library and `workbench.executor.readers`' errors. LibreOffice is loaded in the forked child alone, never in the job process itself.
- Executor rules:
  - no network; the seccomp filter unchanged (S1: LibreOfficeKit in-process needs no socket; `soffice` is never run);
  - `RLIMIT_AS` 1536 MB per process; no new capability;
  - logs and events carry ids, counts, codes and exception types only: never a file name, a path, a page's text, an image byte, or LibreOffice's own error text (`getError` may name the path).
- No macro ever runs:
  - documents load with `Batch=true`;
  - the baked profile sets macro security to very high, disables macros and never updates links;
  - the image has no Java and no Python script provider (`--no-install-recommends`).
- LibreOffice sees a file only after `readers.check_zip` (bombs, OLE = encrypted or legacy) and the ODF encryption check. A file refused there is `unreadable_file`, and no child is forked.
- Fail closed: the converted PDF and every render become visible only after their `modify` / `file.prepare` event. Images leave only after their `query` or `disclose` event (unchanged).
- R15: `view_file`, and every image of `read_document`, go only to a call that carries `X-Accept-Images: 1`. Without it, an Office file is read and described word for word as in v0.7.0.
- Stage-1 rulings that bind:
  - a new `RenderFacts` field has a default, and `_facts` reads leniently;
  - a render's width and height are trusted only within bounds;
  - a PDF's sentences keep their v0.7 words: every stage-1 test passes unchanged, except those a task here names.
- Airgap: LibreOffice and the fonts come from the pinned base image's trixie apt at build time and go into `make bundle`'s tarball. Nothing is fetched at runtime.
- Test files are built at test time with invented content (`tests/fixtures/`). No binary fixture is committed, and no committed file holds an absolute local path. Goldens in `tests/api/golden/` are edited by hand.
- Spec values:
  - LibreOfficeKit `libreofficekit_hook_2` from `libmergedlo.so`, falling back to `libsofficeapp.so`, in `/usr/lib/libreoffice/program`;
  - `converted.pdf` at most 32 MiB; derived files at most 64 MiB per source, the PDF included;
  - pages at 1600 px long side, the first 20 at prepare;
  - `view_file` at most 8 images and 2.5 MiB; `read_document` at most 4 images;
  - S1: a 30-slide pptx in 2.8 s, a 24-page docx in 1.8 s, peak RSS 237 MB.
- Commit messages are conventional (`feat(executor): …`) and hold no real data.

## Review Focus

1. **A password-protected file:** an encrypted `.docx`/`.pptx` (an OLE container) or an `.odt` whose members are encrypted. Expect `422 unreadable_file: …password-protected…` before LibreOffice sees it, no derived row, and `read_document` answering as before. Tests in Tasks 3 and 5.
2. **A deck with a hidden slide.** Expect the hidden slide drawn, so slide N is page N. If a LibreOffice build drops it anyway, expect a hint in the manifest and in `view_file`, and no mismatched image in `read_document`. Tests in Tasks 1, 3 and 6.
3. **A photo-heavy deck.** Expect the PDF downsampled to 150 dpi and within 32 MiB. Past the cap, expect `unreadable_file` naming 32 MB, never a 500 or a stored half-file. Tests in Tasks 1 and 3.
4. **A slide master with layouts but no slides** (a `.potx` from `make_master(examples=0)`). Expect `422 unsupported_format: the deck has no slides…` at once, with no fork, and `view_file` passing the refusal on. Tests in Tasks 3 and 5.
5. **A file that hangs or crashes LibreOffice.** Expect the child killed and reaped at the budget, `read_timeout` (or `unreadable_file` for a crash), and no process of the slot left. Tests in Tasks 1 and 3.

## Contract for Ghostwriter (what Ghostwriter v0.4.0 consumes)

Everything in v0.7.0's Contract holds; this adds to it.

**`POST /files/prepare`:**
- Every manifest has eight keys now; `unit` is added after `kind`.
- A `.docx`, `.dotx`, `.pptx`, `.potx` or `.odt` answers `kind: "office"`. `unit` is `"slide"` for `.pptx`/`.potx` and `"page"` otherwise; it is `"page"` for `image`, `pdf` and `none` as well.
- `total` is the number of pages or slides drawn. `pages_without_text` lists the pages or slides with no text (a picture-only slide, a scanned page in a document). `renditions` are as for a PDF: the first 20 after prepare, at 1600 px. `frames` is `null`.
- `hint` may say `the deck has {n} slides and {m} were drawn, so a picture's number may not be its slide's`.
- `.xlsx`, `.xlsm`, `.ods` and every other type stay `kind: "none"`.
- 422 tokens:
  - `unreadable_file`: damaged, password-protected, an old binary format, could not be converted, or the PDF would pass 32 MB;
  - `unsupported_format`: a deck with no slides;
  - `read_timeout`: the conversion passed its budget.
- An Office prepare runs two jobs (convert, then draw) in one turn of the prepare gate. A client timeout must therefore cover 120 s of queue, two job walls (2 × `GHOSTWRITER_WORKBENCH_WALL_S`, 120) and 30 s: **390 s**.

```json
{"path": "/workspace/chat-1/harbour-deck.pptx", "kind": "office", "unit": "slide", "total": 24,
 "pages_without_text": [3, 7], "renditions": [{"n": 1, "width": 1600, "height": 900, "mime": "image/jpeg", "bytes": 151002}],
 "frames": null, "hint": null}
```

**`GET /files/view?path=&rendition=n[&strips=k&strip=i]`:** an Office file is served as a PDF is. The file name in `Content-Disposition` is `<stem>-slide-3.jpg` for a deck and `<stem>-page-3.jpg` for a document.

**MCP `view_file`:**
- `kind` gains `"office"`.
- A new key `unit` is `"page"`, `"slide"`, or `null` for a picture and for kind `none`.
- Labels read `slide 3` and `slide 3, strip 2 of 3`.
- On a deck, `part` takes `slide 3`, `slides 2-5`, `3` or `2-5`; `page 3` is refused there with `no_such_part`. `next_part` reads `slides 9-12`.
- A `view_file` of an Office file not prepared yet runs both jobs inside the call: a few seconds (S1), within `WORKBENCH_TIMEOUT`.

**MCP `read_document` (with `X-Accept-Images: 1`):**
- On a `.pptx`/`.potx` it attaches the slides without text inside its window, as images. They come with `shown` labels `slide N`, and `next_part` `slide N` when the images end the answer.
- On a `.docx`/`.dotx`/`.odt` it attaches nothing. Its first read (no `part`) ends its hint with `view_file shows its pages as images, as they would print.`

**Strips:** pages and slides of Office files are drawn at 1600 px, like PDF pages. At a fixed image cost send `strips: 2`, as for a PDF.

---

### Task 1: The executor image and LibreOfficeKit, smoke-tested under production's limits

**Files:**
- Modify: `docker/Dockerfile.executor`:
  - an apt layer in the runtime stage, after the `/workspace` link (:59) and before the set-uid sweep (:63);
  - the profile bake after the font cache (:79-80), which now also scans the new fonts.
- Create: `workbench/executor/render/office.py`, `tests/executor/test_office.py`
- Modify:
  - `tests/fixtures/make_fixtures.py`: `make_deck`, `make_long_docx`, `make_macro_odt`;
  - `tests/executor/test_supervisor.py`: root cases after `test_as_root_a_photo_and_a_scan_are_prepared_in_a_slot` (:2369).

**Interfaces:**
- Consumes:
  - `readers.ReadTimeout`, `readers.UnreadableFile`;
  - stage 1's `_runner`, `_chat`, `_request` and `_settings` helpers of `tests/executor/test_supervisor.py`;
  - `make_images.make_noise`.
- Produces in `office.py` (all `Final`; standard library only):
  - **Paths:** `PROGRAM = Path("/usr/lib/libreoffice/program")`; `LIBRARIES = ("libmergedlo.so", "libsofficeapp.so")`, tried in order with `RTLD_GLOBAL`; `PROFILE = Path("/opt/lo-profile")`.
  - **The ctypes vtables, as S1 used them** (LibreOfficeKit's ABI: these fields, in this order, and no others are needed):
    - `_DocClass`: `nSize: c_size_t`, `destroy: CFUNCTYPE(None, DocP)`, `saveAs: CFUNCTYPE(c_int, DocP, c_char_p url, c_char_p format, c_char_p filter_options)`.
    - `_OfficeClass`: `nSize`, `destroy: CFUNCTYPE(None, OfficeP)`, `documentLoad: CFUNCTYPE(DocP, OfficeP, c_char_p)`, `getError: CFUNCTYPE(c_char_p, OfficeP)`, `documentLoadWithOptions: CFUNCTYPE(DocP, OfficeP, c_char_p url, c_char_p options)`.
    - `_Doc` and `_Office` each hold one field, `pClass`, a pointer to its class.
    - `libreofficekit_hook_2(c_char_p install_path, c_char_p profile_url) -> OfficeP`.
    - `getError`'s text is never read into a log or an answer.
  - **Options and exit codes:** `LOAD_OPTIONS = b"Batch=true"`; `WRITER_OPTIONS: bytes` and `IMPRESS_OPTIONS: bytes`, the PDF filter data below; `PROFILE_SETTINGS: str`, the `registrymodifications.xcu` below; exit codes `EXIT_OK = 0`, `EXIT_NO_LIBRARY = 3`, `EXIT_NO_START = 4`, `EXIT_NO_LOAD = 5`, `EXIT_NO_SAVE = 6`, `EXIT_FAILED = 70`.
  - **`run_in_child(work: Callable[[], int], deadline: float, *, poll_s: float = 0.05) -> int`** (below). It returns the child's exit code, or `-N` when signal N ended it. It raises `readers.ReadTimeout` at the deadline, after the child is killed and reaped.
  - **`seed_profile(*, under: Path | None = None, prebuilt: Path = PROFILE) -> Path`:**
    - copies `prebuilt` into a new `tempfile.mkdtemp(prefix="lo-", dir=under)`, as `runners/python.py:_seed_font_cache` (:351) does;
    - where there is no `prebuilt` (outside the image) it makes `user/registrymodifications.xcu` from `PROFILE_SETTINGS` instead;
    - returns the folder.
  - **`convert(source: Path, target: Path, *, deck: bool, deadline: float) -> None`:**
    - seeds a profile, then `run_in_child(partial(_convert_in_child, source, target, profile, IMPRESS_OPTIONS if deck else WRITER_OPTIONS), deadline)`;
    - returns on `EXIT_OK` with `target` a regular file;
    - `EXIT_NO_LOAD` raises `UnreadableFile("LibreOffice could not open the file: it is damaged, or not what its extension says")`;
    - `EXIT_NO_SAVE` or a signal raises `UnreadableFile("the file could not be converted to pages")`;
    - the other codes raise `RuntimeError("LibreOffice could not start")`;
    - `ReadTimeout` passes through.
  - **`_convert_in_child(source, target, profile, options) -> int`:**
    - sets `os.environ["SAL_USE_VCLPLUGIN"] = "svp"`, loads the library and calls the hook with `str(PROGRAM)` and `profile.as_uri()`;
    - then `documentLoadWithOptions(source.as_uri(), LOAD_OPTIONS)` and `saveAs(target.as_uri(), b"pdf", options)`;
    - returns its code. It never calls `destroy` (S1: it can hang); the child ends in `os._exit`.
  - **`bake(profile: Path) -> None`** and `main(argv) -> int` for `python -m workbench.executor.render.office bake <dir>`: writes `user/registrymodifications.xcu`, then starts LibreOfficeKit once in a child (no document, deadline 120 s), so that every job copies a warm profile.

`run_in_child`, the fork, wait and kill (its order is not fixed by the tests):

```python
pid = os.fork()
if pid == 0:                                   # the child never returns into the job's code
    code = EXIT_FAILED
    try:
        code = work()
    except BaseException:                      # noqa: BLE001 - whatever happens, the child ends here
        pass
    os._exit(code)
reaped = False
try:
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            reaped = True
            return os.waitstatus_to_exitcode(status)
        if time.monotonic() >= deadline:
            raise readers.ReadTimeout
        time.sleep(poll_s)                     # the job's own limit_until may raise here too
finally:
    if not reaped:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
```

`PROFILE_SETTINGS` (the values are officecfg's: Writer's `Link` 2 = never, Calc's `Link` 1 = never, `MacroSecurityLevel` 3 = very high):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Recovery/AutoSave"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Recovery/RecoveryInfo"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseLocking" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
```

The filter data, as LibreOffice's JSON filter options, encoded with `json.dumps(..., separators=(",", ":")).encode()`:

```python
_SMALL_PICTURES = {"ReduceImageResolution": {"type": "boolean", "value": "true"},
                   "MaxImageResolution": {"type": "long", "value": "150"}}
WRITER = _SMALL_PICTURES
IMPRESS = {**_SMALL_PICTURES, "ExportHiddenSlides": {"type": "boolean", "value": "true"},
           "ExportNotesPages": {"type": "boolean", "value": "false"}}
```

The image (exact):

```dockerfile
# LibreOffice for convert_office: LibreOfficeKit in-process, never soffice; no
# Java and no Python script provider. Carlito and Caladea stand in for Calibri
# and Cambria at the same metrics.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-core-nogui libreoffice-writer-nogui libreoffice-impress-nogui libreoffice-calc-nogui \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 fonts-dejavu-core fonts-noto-core \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
```

The bake goes after the font cache:

```dockerfile
RUN python -m workbench.executor.render.office bake /opt/lo-profile \
    && chmod -R a+rX,go-w /opt/lo-profile
```

Its comment says that the profile is root's and read-only, like the font cache, and that each conversion copies it into its private tmp (`office.seed_profile`).

- [ ] **Step 0: Branch.** `git fetch origin && git switch main && git pull --ff-only && git switch -c feat/vision-stage2`.
- [ ] **Step 1: Fixtures** in `tests/fixtures/make_fixtures.py` (python-pptx, python-docx, odfpy; invented text only):
  - **`make_deck(path, slides=30, *, chart_on: int | None = 1, picture_only: Sequence[int] = (), hidden: Sequence[int] = (), photo: tuple[int, int] | None = None) -> Path`:**
    - each slide has the title `Invented slide N` and one bullet;
    - a picture-only slide holds one `make_noise` PNG and no text frame;
    - `hidden` sets `show="0"` on the slide element;
    - `photo` puts a noise JPEG of that size on every slide;
    - slide `chart_on` holds a clustered column chart of invented figures.
  - **`make_long_docx(path, pages=24, *, picture_on: int | None = None) -> Path`:** each page has the heading `Invented section N`, two paragraphs and a page break; page `picture_on` holds a picture alone.
  - **`make_macro_odt(path, marker: str) -> Path`:** an odt whose Basic macro `Standard.Module1.Main` writes the file `Environ("HOME") & "/" & marker`.
    - The macro is bound to the document's `dom:load` event in `content.xml`'s `office:event-listeners`.
    - The zip carries `Basic/script-lc.xml`, `Basic/Standard/script-lb.xml` and `Basic/Standard/Module1.xml`, each listed in `META-INF/manifest.xml`.
- [ ] **Step 2: Write the failing host tests** (`tests/executor/test_office.py`). Each runs `run_in_child` in a fresh `python -I -c` interpreter (`subprocess.run`, 20 s timeout), so that no thread of pytest's is forked:
  - `test_a_child_returns_its_exit_code`: `work` returns 6, so the result is `6`.
  - `test_a_child_that_raises_exits_70`.
  - `test_a_child_past_its_deadline_is_killed_and_reaped`: `work` sleeps 30 s and the deadline is 0.5 s away. `ReadTimeout` comes within 2 s, then `os.waitpid(-1, os.WNOHANG)` raises `ChildProcessError`.
  - `test_a_child_ended_by_a_signal_reports_it`: `work` sends itself `SIGKILL`, so the result is `-9`.
  - `test_a_profile_is_seeded_into_tmp`:
    - with a `prebuilt` folder holding `user/a.xcu`, the copy is a new folder under `under` holding it;
    - without `prebuilt`, the new folder's `user/registrymodifications.xcu` equals `PROFILE_SETTINGS`, and `xml.etree` parses it into nine `item`s.
  - `test_the_filter_data_is_json`: `json.loads(IMPRESS_OPTIONS)["ExportHiddenSlides"]["value"] == "true"`, and `"ExportHiddenSlides" not in json.loads(WRITER_OPTIONS)`.
- [ ] **Step 3: Run** `uv run pytest tests/executor/test_office.py -q`. Expected: FAIL (no module).
- [ ] **Step 4: Implement** `office.py` as in Interfaces, then the two Dockerfile layers.
- [ ] **Step 5: Run** the same command. Expected: PASS.
- [ ] **Step 6: Write the root cases** (`tests/executor/test_supervisor.py`).
  - Each is marked `skipif(not (ROOT_ON_LINUX and office.PROGRAM.is_dir()))`.
  - Each uses `_runner` with `Limits(wall_s=120, cpu_s=120, memory_mb=1536, file_mb=50)`.
  - Each job body first does `import pptx, pypdfium2` (the real job's imports at fork time) and converts into `pathlib.Path(os.environ["TMPDIR"])`.
  - The cases:
    - `test_as_root_libreofficekit_converts_a_deck_and_a_document_in_a_slot`:
      - inputs: `make_deck(30, chart_on=1, picture_only=[2])` as `deck.pptx`, the same through `make_templates.as_potx` as `deck.potx`, and `make_long_docx(24)` as `report.docx`;
      - `result` holds each PDF's pages (pypdfium2), seconds, `RLIMIT_AS`, `RUSAGE_CHILDREN` peak in MB, and the files and bytes under `HOME`;
      - asserts: `ok`; `address_space_mb == 1536`; pages `30`, `30`, `24`; each conversion under 30 s; peak under 1024 MB; under 2,000 files and 200 MB in `HOME`; page 2 of the deck's PDF has no text (`get_textpage().get_text_range().strip() == ""`).
    - `test_as_root_a_hidden_slide_is_drawn_and_pictures_are_downsampled` (Review Focus 2, 3): `make_deck(3, hidden=[2])` gives a 3-page PDF; `make_deck(5, photo=(3000, 2000))` gives a PDF smaller than half the pptx's size.
    - `test_as_root_a_document_macro_never_runs`: `make_macro_odt(…, "macro-ran")` converts, and `$HOME/macro-ran` does not exist afterwards.
    - `test_as_root_a_hung_child_is_stopped_at_its_deadline` (Review Focus 5): under the seccomp filter, `office.run_in_child(lambda: time.sleep(60) or 0, time.monotonic() + 2)` raises `ReadTimeout`; the job answers `ok` with `{"timed_out": true}` within 10 s.
    - `test_the_baked_profile_names_only_settings_libreoffice_knows` (`IN_THE_IMAGE`; root not needed): for every `oor:name` of a `prop` in `/opt/lo-profile/user/registrymodifications.xcu`, the text of the `.xcd` files under `/usr/lib/libreoffice/share/registry/` holds `oor:name="<name>"`.
- [ ] **Step 7: Run** `make test-executor-root`. Expected: every case passes in **both** shapes (production `/dev/shm` and `/tmp`, then Docker's default).
  - **STOP rule (D6):** if LibreOfficeKit does not start under `RLIMIT_AS` 1536 MB (`RuntimeError` or a signal in the first case, in either shape), stop and report to the lead. Report the shape, the exit code and the peak RSS. No per-tool address-space limit is added without the owner's ruling.
  - **Second stop:** if the hidden-slide or the downsampling assertion fails, LibreOfficeKit did not take the JSON filter data. Stop and report with the version line of `/usr/lib/libreoffice/program/versionrc`.
- [ ] **Step 8: Measure** the image with `docker image ls` before and after (`docker build --target runtime`). Note the growth for the PR (S1: about 515 MB).
- [ ] **Step 9: Commit** `feat(executor): LibreOfficeKit in a forked child, a baked profile and the fonts in the image`

### Task 2: Shared rules for Office files

**Files:**
- Modify: `workbench/renders.py` (:17-22 suffixes and `Kind`, :70-84 `kind_of`, :220-236 `RenderFacts`)
- Modify, to keep v0.7's behaviour until Tasks 3 and 5:
  - `workbench/executor/runners/render.py:324`: `prepare_file` refuses kind `office` as it refuses `none`;
  - `workbench/api/renders.py:387`: `prepared` answers kind `office` as `none` (Task 5 replaces this guard).
- Test: `tests/test_renders.py`; its assertion `kind_of("deck.pptx") == "none"` (:113) becomes `"office"`.

**Interfaces:**
- Produces (all `Final` where constant):
  - **Suffixes and kinds:**
    - `OFFICE_SUFFIXES = frozenset({".docx", ".dotx", ".pptx", ".potx", ".odt"})`, `SLIDE_SUFFIXES = frozenset({".pptx", ".potx"})`;
    - `Kind = Literal["image", "pdf", "office", "none"]`; `kind_of` answers `office` for those suffixes in any case;
    - `Unit = Literal["page", "slide"]`; `unit_of(name: str) -> Unit` is `slide` for `SLIDE_SUFFIXES`, else `page`.
  - **The converted PDF:** `CONVERTED_NAME = "converted.pdf"`, `CONVERTED_MIME = "application/pdf"`, `CONVERTED_SIGNATURE = b"%PDF-"`, `CONVERTED_MAX_BYTES = 32 * 1024 * 1024` (a converted PDF is at most this).
  - **`class ConvertAnswer(_Strict)`:** `name: Literal["converted.pdf"]`, `pages: int` (≥ 1), `slides: int | None = None` (≥ 1; the reader's count for a deck, `None` for a text document).
  - **`class OfficeRequest(_Strict)`:** `unit: Unit`, `slides: int | None = None` (≥ 1). It is the `office` argument of a `prepare_file` job over a converted PDF.
  - **`RenderFacts`:** `kind: Literal["image", "pdf", "office"]`, and `unit: Unit = "page"` after `kind`. A head kept by v0.7 still reads.

- [ ] **Step 1: Write the failing tests:**
  - `kind_of` answers `office` for `Deck.PPTX`, `master.potx`, `report.docx`, `form.dotx` and `notes.odt`; it answers `none` for `.xlsx`, `.ods`, `.odp`, `.doc`, `.ppt` and `.rtf`.
  - `unit_of("a.POTX") == "slide"`, `unit_of("a.odt") == "page"`.
  - `RenderFacts.model_validate` of v0.7's five keys gives `unit == "page"`; `kind="office", unit="slide"` passes; `unit="sheet"` is refused.
  - `ConvertAnswer` refuses `name="a.pdf"`, `pages=0` and an extra key. `OfficeRequest` refuses `unit="sheet"` and `slides=0`.
- [ ] **Step 2: Run** `uv run pytest tests/test_renders.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces, with the two guards. The module docstring names Office files: they are converted first, then drawn as PDF pages.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. Every stage-1 test is unchanged except :113.
- [ ] **Step 5: Commit** `feat(renders): Office files are of kind office, by page or by slide`

### Task 3: The `convert_office` runner, and `prepare_file` over a converted PDF

**Files:**
- Create: `workbench/executor/runners/convert.py`, `tests/executor/test_convert.py`
- Modify:
  - `workbench/executor/runners/__init__.py:30` (`RUNNERS["convert_office"]`), `workbench/api/jobs.py:277` (`TOOLS`);
  - `workbench/executor/runners/render.py`: `_Request.of` (:64) takes `office`; `prepare_file` (:298) gives Task 2's guard its own message and answers kind `office`;
  - `tests/fixtures/make_fixtures.py` (`make_encrypted_odt`), `tests/conftest.py` (the `fake_office` fixture).
- Test: `tests/executor/test_convert.py`, `tests/executor/test_render.py`, `tests/executor/test_supervisor.py`

**Interfaces:**
- Consumes: Task 1 (`office.convert`) and Task 2; `readers.check_zip`, `readers.count_pages`, `readers.set_deadline`, `limit_until`, `pages.open_pdf`, `documents.error`.
- Produces:
  - `class Converter(Protocol)`: `__call__(self, source: Path, target: Path, *, deck: bool, deadline: float) -> None`.
  - **`convert_office(args: dict[str, Any], ctx: JobContext, *, convert: Converter | None = None) -> dict[str, Any]`.** `None` means `office.convert`, looked up at call time so that a test can patch it. Its args are `path` and `budget_s` (default 90). In this order:
    1. `file_not_found` as `prepare_file` answers it.
    2. A suffix outside `OFFICE_SUFFIXES` is `unsupported_format: only Word, PowerPoint and OpenDocument text files are converted`.
    3. `readers.check_zip(real, ctx.file_mb)`.
    4. An `.odt` whose `META-INF/manifest.xml` (read through `zipfile`, at most 1 MiB) holds `encryption-data` is `unreadable_file: the file is password-protected (encrypted)`.
    5. For a deck, `slides = readers.count_pages(real, file_mb=ctx.file_mb)`. `0` is `unsupported_format: the deck has no slides; inspect_template lists the layouts of a slide master`.
    6. `convert(real, staged, deck=…, deadline=…)`, where `staged` is `converted.pdf` in a new `tempfile.mkdtemp()` folder.
    7. The staged file must be a regular file of at most `CONVERTED_MAX_BYTES` (else `unreadable_file: the converted file is larger than 32 MB`) that starts with `CONVERTED_SIGNATURE` (else `unreadable_file: the file could not be converted to pages`). `pages.open_pdf` counts its pages.
    8. `os.replace` moves it to `ctx.out_dir / CONVERTED_NAME`, and the runner answers `ConvertAnswer(...).model_dump()`.
  - All of it runs under `readers.set_deadline(deadline)` and `limit_until(deadline, readers.ReadTimeout)`, with errors mapped as `prepare_file` maps them:
    - `ReadTimeout` is `read_timeout: converting the file took longer than {budget:g} s; it is too large or too complex`;
    - `UnreadableFile` is `unreadable_file`;
    - a `RuntimeError` propagates (the job's error).
  - A refusal leaves `out/` empty. Steps 3–5 run before any fork.
  - **`prepare_file`** refuses an input of kind `office`: `unsupported_format: an Office file is converted first`. It gains the argument `office`, an `OfficeRequest` as JSON:
    - with it, the input must be a PDF (anything else is `ValueError`, the caller's mistake);
    - the answer's `kind` is `office` and its `unit` the request's;
    - when `slides` is set and differs from `total`, the hint gains `the deck has {slides} slides and {total} were drawn, so a picture's number may not be its slide's`;
    - its two notes name the unit: `{unit}s after 1000 were not checked for text` and `{unit}s without text are listed up to {unit} {n}`. A PDF's words are unchanged.
  - **`make_encrypted_odt(path) -> Path`:** `make_odt`, then `META-INF/manifest.xml` gains a `manifest:encryption-data` element on `content.xml`'s entry.
  - **`fake_office`** (fixture, `tests/conftest.py`): it patches `workbench.executor.render.office.convert` with a converter that writes `make_fixtures.make_pdf` pages.
    - For a deck it writes one page per slide (python-pptx), `None` for a slide with no text frame, and leaves out the slides listed in `fake_office.drop`.
    - For a document it writes `fake_office.pages` (default 3) text pages.
    - It counts its calls in `fake_office.calls`.

- [ ] **Step 1: Write the failing tests** (`tests/executor/test_convert.py`; the `Job`-style context of `tests/executor/test_render.py`):
  - `test_a_deck_is_converted_to_one_pdf_in_out`: with `make_deck(3, chart_on=None)` the answer is `{"name": "converted.pdf", "pages": 3, "slides": 3}`; `out/` holds only `converted.pdf`, which starts `%PDF-`; the converter got `deck=True`.
  - `test_a_word_file_is_converted_without_a_slide_count`: `make_docx` gives `slides is None` and `deck=False`. The same holds for `make_odt` and for `as_template` (`.dotx`).
  - `test_encrypted_files_never_reach_libreoffice` (Review Focus 1): `make_ole` named `.docx` and `make_encrypted_odt` give `unreadable_file` naming "password-protected". A converter that raises when called is not called, and `out/` is empty.
  - `test_a_zip_bomb_never_reaches_libreoffice`: `make_zip_bomb` named `.pptx`.
  - `test_a_deck_without_slides_is_unsupported` (Review Focus 4): `as_potx(make_master(…, examples=0))` gives `unsupported_format: the deck has no slides…`, and the converter is not called.
  - `test_a_pdf_over_32_mib_is_refused` (Review Focus 3): the converter writes `%PDF-` and 32 MiB of zeros. The answer is `unreadable_file` naming 32 MB, and `out/` is empty.
  - `test_a_conversion_past_its_budget_is_read_timeout` (Review Focus 5): the converter raises `ReadTimeout`.
  - `test_a_crash_is_unreadable_and_a_missing_libreoffice_is_an_error`: `UnreadableFile` from the converter becomes `unreadable_file`; `RuntimeError` propagates.
  - `test_a_converted_file_that_is_no_pdf_is_refused`: the converter writes text.
  - `test_a_spreadsheet_is_unsupported`; `test_the_answer_names_no_path`.
  - In `tests/executor/test_render.py`:
    - `test_pages_of_a_converted_pdf_answer_as_office`: `office={"unit": "slide", "slides": 3}` over a 3-page PDF whose page 2 is `None` gives `kind == "office"`, `unit == "slide"`, `pages_without_text == [2]` and entries of role `page`.
    - `test_a_slide_count_that_differs_says_so` (Review Focus 2): `slides: 4` over 3 pages gives the hint sentence.
    - `test_office_needs_a_pdf_input`: `office` with a JPEG input raises `ValueError`.
    - `test_an_office_file_is_drawn_only_after_conversion`: `prepare_file` on a `.pptx` gives `unsupported_format: an Office file is converted first`.
  - Root, in `tests/executor/test_supervisor.py`: `test_as_root_a_deck_is_converted_and_drawn_in_slots`.
    - The real entry point with `tool="convert_office"` on `make_deck(5, picture_only=[2])` gives `ok` and one staged output, `converted.pdf`.
    - That output then lies in a chat folder as `converted.pdf`. `prepare_file` with `office={"unit": "slide", "slides": 5}` gives `kind` `office`, `total` 5, `pages_without_text == [2]` and five renders.
- [ ] **Step 2: Run** `uv run pytest tests/executor -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the runner, the registrations, `prepare_file`'s `office` argument, `make_encrypted_odt` and `fake_office`.
- [ ] **Step 4: Run** `uv run pytest tests/executor -q`, then `make test-executor-root`. Expected: PASS in both shapes.
- [ ] **Step 5: Commit** `feat(executor): convert_office writes one PDF, and prepare_file draws it as an Office file's pages`

### Task 4: The converted PDF in the store, and one turn for a conversion and its renders

**Files:**
- Modify:
  - `workbench/api/store.py`: `_RENDER_NAME` :77 and `_RENDER_EXTENSIONS` :80, now `_DERIVED_EXTENSIONS`; `register_derived` :1581;
  - `workbench/api/jobs.py`: `submit_renders` :647; new `Turn`, `turn` and `submit_conversion`; `_converted_place` beside `_render_place` :1448;
  - `workbench/api/tools_mcp.py:103` (`CallJobs`).
- Test: `tests/api/test_store.py`, `tests/api/test_jobs.py`

**Interfaces:**
- Consumes: Task 2 (`CONVERTED_*`, `ConvertAnswer`); stage 1's `DerivedSpec`, `register_derived`, `_read_regular`, `_Invalid`, `_turn`.
- Produces:
  - **Store:**
    - `_DERIVED_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}`; `_RENDER_NAME` matches `pdf` too, so the orphan sweep names an orphan PDF by its id;
    - `register_derived` takes role `pdf` only with `application/pdf` and no width or height, and `application/pdf` only for role `pdf` (else `PathError`).
  - **`@dataclass(frozen=True) class Turn`:** `caller: str`, `user_id: str`, `gate: Gate`. It proves that the holder is inside a turn of the queue.
  - **`JobQueue.turn(self, p: Principal, *, call: CallLimits, gate: Gate = "user") -> AbstractAsyncContextManager[Turn]`:** `_turn` as a public context that yields its `Turn`.
  - **`JobQueue.submit_renders(..., turn: Turn | None = None, converted: DerivedRecord | None = None)`:**
    - with `turn`, it runs inside that turn and takes none; a turn of another caller, user or gate is `ValueError`;
    - with `converted` (it must be the source's own `pdf` row, else `ValueError`), the job's one input and its `path` are `converted.relative_path`;
    - without either, it behaves as in v0.7.
  - **`JobQueue.submit_conversion(self, p, source, args, keeper, *, call, gate="user", turn=None) -> tuple[JobRecord, JobResult, list[DerivedRecord]]`:** tool `convert_office`, input `[source.name]`, `{**args, "path": source.name}`; each output is kept through `_converted_place`.
  - **`_converted_place(self, source, result) -> Place[DerivedRecord]`:**
    - the answer must read as `ConvertAnswer`, and the output's name be `CONVERTED_NAME`;
    - its first bytes must start with `CONVERTED_SIGNATURE` and its size be at most `CONVERTED_MAX_BYTES` (any of these failing is `_Invalid`);
    - then `register_derived(source, staged, DerivedSpec("pdf", 0, CONVERTED_MIME, None, None, {"pages": a.pages, "slides": a.slides}), file_id=…, quota_bytes=…)`.
  - **`CallJobs`** gains `turn(p)`, `submit_conversion(p, source, args, keeper, *, turn=None)` and `submit_renders(..., *, turn=None, converted=None)`, with its call's limits on the user's own turn.

- [ ] **Step 1: Write the failing tests:**
  - `tests/api/test_store.py`:
    - `test_a_converted_pdf_is_a_derived_file`: `DerivedSpec("pdf", 0, "application/pdf", None, None, {"pages": 3})` lands at `.derived/<sid>/<id>.pdf`, mode 0600, and counts in `usage`. A second one raises `Conflict`.
    - `test_a_pdf_role_needs_its_type`: `("pdf", "image/jpeg")` and `("page", "application/pdf")` raise `PathError`.
    - `test_an_orphan_pdf_is_swept_by_its_id`.
  - `tests/api/test_jobs.py` (its `FakeClient`, with a scripted `convert_office` answer that stages a small PDF):
    - `test_a_conversion_keeps_one_pdf_after_its_record`: the keeper sees the planned id before any row exists. Afterwards `derived_of(source)` holds one `pdf` row with `meta["pages"]`, and `files_in_chat` only the source.
    - `test_a_conversion_output_that_is_no_pdf_is_not_kept`: another name, a JPEG signature, and a size of 32 MiB + 1 each keep no row, and `again` names their ids.
    - `test_renders_of_a_converted_pdf_read_it_as_their_input`: the request's `input_files == [pdf.relative_path]` and `args["path"] == pdf.relative_path`.
    - `test_a_render_input_must_be_the_sources_pdf`: another source's `pdf` row and a `page` row raise `ValueError`.
    - `test_one_turn_holds_a_conversion_and_its_renders`: under the prepare gate, user A's conversion is held running while user B's prepare queues. The requests then run in the order A `convert_office`, A `prepare_file`, B `prepare_file`.
    - `test_a_turn_serves_only_its_own_user`: `ValueError`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_store.py tests/api/test_jobs.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/api -q`. Expected: PASS, every existing queue and store test unchanged.
- [ ] **Step 5: Commit** `feat(jobs): a converted PDF is a derived file, and its renders read it in the same turn`

### Task 5: Office files in the api's renders and the file routes

**Files:**
- Modify:
  - `workbench/api/renders.py`: module docstring; `NOT_SHOWN` :65; `_PART` and `_GRAMMAR` :80-81; `Manifest` :102; `RenderJobs` :137; `RouteJobs` :173; `manifest_of` :247; `prepared` :353; `renders_for` :455; `label` :591; `shown_image` :607; `page_window` :628;
  - `workbench/api/files.py`: `_rendition_disposition` :317, called with the noun;
  - `docs/audit.md` (the `prepare` row, :51), `docs/permissions.md` (§ File routes, the prepare row).
- Test: `tests/api/test_renders.py`, `tests/api/test_files_renders.py`. Their Word-file cases (`test_a_word_file_is_kind_none_without_a_job` :258, `test_prepare_of_a_word_file_is_kind_none_without_a_job` :206) become spreadsheet cases (`make_xlsx`).

**Interfaces:**
- Consumes: Tasks 2 and 4; `fake_office` (Task 3).
- Produces:
  - **The manifest:** `Manifest.kind: Literal["image", "pdf", "office", "none"]`, and `unit: Unit = "page"` after `kind`, always serialised. `manifest_of` takes `unit` from the facts.
  - **Nouns and labels:**
    - `Noun = Literal["image", "page", "slide"]`; `noun_of(manifest: Manifest) -> Noun` gives `image`, `page` for a PDF, or the unit of an Office file;
    - `label(noun: Noun, n, strip, strips)` gives `slide 3` and `slide 3, strip 2 of 3`, and stage 1's labels as before; `shown_image(noun: Noun, rec)`.
  - **`page_window(part, total, *, default_last, unit: Unit = "page")`:**
    - for `slide` it takes `slide`/`slides` (alike) or bare numbers;
    - its errors are `no_such_part: give a slide as 'slide 3', or slides as 'slides 2-5'` and `no_such_part: this document has {total} slides` (`slide` when 1);
    - for `page` it is unchanged.
  - **`NOT_SHOWN`** reads `"This kind of file, such as a spreadsheet, is not shown as pictures; read it with read_document."`.
  - **`converted_of(rows: Sequence[DerivedRecord]) -> DerivedRecord | None`:** the row of role `pdf`.
  - **`_convert(p, source, *, settings, store, audit, jobs, turn: Turn, on_demand: bool) -> DerivedRecord`:**
    - one `submit_conversion` with `budget_s = _budget(settings)`, and the keeper `OutputKeeper(audit, p, "prepare", [source.id], decide)`. Its criteria are `{"renders": 0, "strips": 1, "on_demand": on_demand, "converted": True, "outputs": n}` plus `judged`'s;
    - `_payload(result, "file")` raises the runner's refusal (its code's status), then `_parse(ConvertAnswer, …)`;
    - when no row was kept it raises `_lost(result.message)`.
  - **`prepared()`, for kind `office`** (in place of Task 2's guard): under the source lock and after `audit.check`, `async with jobs.turn(p) as turn:`
    1. it takes `converted_of(rows)`, or `_convert(…, on_demand=False)` when there is none;
    2. it runs the render job with `converted=pdf` and `turn=turn`, its args gaining `"office": {"unit": unit_of(source.name), "slides": pdf.meta.get("slides")}`.
    - Kind `none` and the rows' shortcut are as in v0.7.
  - **`renders_for()`:**
    - inside the source lock, it runs every job of the call in one `jobs.turn(p)`;
    - for kind `office` it takes the `pdf` row, or `_convert(…, on_demand=True)` when the row is gone, and gives each job `converted` and the `office` argument (`facts: False`, as before).
  - **`RenderJobs`** gains `turn(p) -> AbstractAsyncContextManager[Turn]` and `submit_conversion(p, source, args, keeper, *, turn)`, and its `submit_renders` takes `turn` and `converted`. `RouteJobs` implements them with its call and gate.
  - **`files._rendition_disposition(source, render, noun)`** names a slide's render `<stem>-slide-3.jpg`. Kind `office` passes the view route's `kind == "none"` check.

- [ ] **Step 1: Write the failing tests** (`tests/api/test_renders.py` with the `Local` executor and `fake_office`):
  - `test_a_deck_is_converted_once_and_drawn`:
    - `make_deck(3, picture_only=[2])` gives `kind office`, `unit slide`, `total 3`, `pages_without_text [2]` and renditions n 1–3;
    - the rows are one `pdf` and three `page` renders;
    - two `modify` / `file.prepare` events are written: `[source, pdf]` with `converted: True`, then `[source, r1, r2, r3]`.
  - `test_a_word_file_is_drawn_as_pages`: `make_docx` gives `kind office`, `unit page`.
  - `test_a_prepared_deck_answers_from_the_rows`: no request, no event.
  - `test_a_deck_converted_before_is_not_converted_again`: a `pdf` row and no head, so `prepared` runs only `prepare_file` and `fake_office.calls == 0`.
  - `test_later_slides_reuse_the_pdf`: a 22-slide deck; `renders_for([21], 1)` runs one `prepare_file` whose input is the `pdf` row, and no `convert_office`.
  - `test_a_lost_pdf_is_converted_again`: after `remove_derived(pdf)`, `renders_for([21], 1)` runs one conversion and one render job in one turn.
  - `test_two_prepares_of_a_deck_convert_once`: `asyncio.gather` gives one conversion and one render job.
  - `test_an_encrypted_deck_is_422_with_nothing_kept` (Review Focus 1): `make_ole` named `.pptx` gives `HTTPException` 422 `unreadable_file:`, no derived row and one `error` event.
  - `test_a_pdf_past_the_quota_is_507`.
  - `test_slide_window_grammar`: `("slide 3", 10, unit="slide") → (3, 3)`, `("slides 2-5") → (2, 5)`, `("3") → (3, 3)`; `("page 3", unit="slide")` → 422 naming slides; `("slide 11", 10, unit="slide")` → `this document has 10 slides`; the page grammar is unchanged.
  - `test_labels_name_slides`: `label("slide", 3, 1, 1) == "slide 3"`, `label("slide", 3, 2, 3) == "slide 3, strip 2 of 3"`; `image` and `page` are as before.
  - `test_a_v07_head_reads_as_unit_page`: a head whose facts lack `unit` gives a manifest of `unit` `page`.
  - `tests/api/test_files_renders.py`:
    - `test_prepare_of_a_deck_answers_kind_office_and_unit_slide`: keys exactly `{"path", "kind", "unit", "total", "pages_without_text", "renditions", "frames", "hint"}`.
    - `test_every_manifest_carries_unit`: a picture and a PDF give `"unit": "page"`, and so does an `.xlsx` (`kind: none`).
    - `test_a_slide_rendition_is_named_by_its_slide`: the file name in `Content-Disposition` is `harbour-deck-slide-2.jpg`.
    - `test_prepare_of_an_encrypted_odt_is_422` (Review Focus 1): the detail starts `unreadable_file:`.
    - `test_prepare_of_a_master_without_slides_is_422_unsupported_format` (Review Focus 4).
- [ ] **Step 2: Run** `uv run pytest tests/api/test_renders.py tests/api/test_files_renders.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, then the docs:
  - `docs/audit.md`'s `prepare` row: `converted: true` on the job that converts an Office file (target the source, then its PDF; `renders: 0`), absent on every other.
  - `docs/permissions.md`'s prepare row: an Office file is converted once to a PDF, kept as a derived file of it, then drawn.
- [ ] **Step 4: Run** `uv run pytest tests/api tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): Office files are converted once and drawn, by page or by slide`

### Task 6: `view_file` and `read_document` show Office pages and slides

**Files:**
- Modify:
  - `workbench/api/tools_views.py`: the module docstring (:1-30, "not rendered yet"); `UNSHOWN`–`UNDRAWABLE` :92-97; `_PAGE` :103; `ViewFileRequest.part` :116; `ViewedFile` :130; `_part` :230; `_not_shown_after` :243; `view_file` :278; `_page_of` :411; `_pages` :424; `_cut_note` :436; `_notes` :478; `_attach_pages` :558; `_read_pdf` :635, now `_read_paged`; `read_document` :791;
  - `workbench/api/tool_table.py` (`view_file` :175-191; `read_document`'s `description` :104-117 only, not `text_description`);
  - `workbench/executor/runners/documents.py:96-102`: `_hint`'s slide sentence, which changes here because the api attaches slides from this task on;
  - both `tests/api/golden/ghostwriter-*.json`; `docs/permissions.md` § What the model is told.
- Test: `tests/api/test_tools_views.py`, `tests/api/test_tools_documents.py`, and `tests/executor/test_render.py` (the `_hint` cases at :1087-1110).
  - `test_view_file_on_a_word_file_says_read_it` (:411) becomes the spreadsheet case.
  - `test_a_pptx_attaches_nothing_yet` (:1042) becomes `test_without_images_an_office_read_is_as_in_v07`.

**Interfaces:**
- Consumes: Task 5 (`noun_of`, `label`, `shown_image`, `page_window(unit=)`, `NOT_SHOWN`, `prepared`, `renders_for`), Task 3's slide hint.
- Produces:
  - **`ViewedFile`:** `kind: Literal["image", "pdf", "office", "none"]`; `unit: Literal["page", "slide"] | None = None` after `kind`, described "slide for a deck, page for a PDF or a document; null for a picture or a file not shown".
  - **Every sentence that names pages takes the noun:** `_part(first, last, noun)`, `_not_shown_after(last, total, noun)`, `_pages(numbers, noun)`, `_cut_note(cut, noun)`, `_notes(…, noun)` and `_page_of(label, noun)` (`(page|slide) (\d{1,9})`). The `UNSHOWN`…`UNDRAWABLE` texts say `slides` for a deck. For a PDF every sentence keeps its v0.7 words.
  - **`MISMATCH`** = `"The slides without text cannot be matched to their pictures here; view_file shows the slides as drawn."`; **`PAGES_VIEWABLE`** = `"view_file shows its pages as images, as they would print."`.
  - **`documents._hint`**, with images, for a deck: `"{slides} {holds|hold} no text (pictures only?); do not treat them as blank."`. Without images it is unchanged.
  - **`read_document`**, with images:
    - PDF: `_read_paged(noun="page")`, which is v0.7's `_read_pdf`.
    - Office with unit `slide`: the read job (the pptx reader), then `_read_paged(noun="slide")`. `_attach_pages` attaches nothing, with `MISMATCH` as its `unshown`, when `manifest.total != answer.total_parts`.
    - Office with unit `page`: read as text alone. The first read (`part` is `None`) has its hint joined with `PAGES_VIEWABLE` before `_fit_read`; the read is recorded with `images: 0`.
    - Without images every Office read is as in v0.7.0.
  - **Descriptions** (keep `test_the_schema_stays_small`: at most 3,000 characters):
    - `view_file`'s title: "Look at a picture, or the pages or slides of a file".
    - Its "Shown:" sentence names "pictures (PNG, JPEG, WebP, GIF, BMP, TIFF, HEIC), the pages of a PDF, Word (DOCX, DOTX) or OpenDocument text (ODT) file and the slides of a PowerPoint (PPTX, POTX) file, drawn as they would print".
    - The part sentence: "'page 3' or 'pages 2-5', for a deck 'slide 3' or 'slides 2-5'".
    - "Word, PowerPoint and spreadsheets are not shown yet" becomes "Spreadsheets are not shown: read them with read_document."
    - `ViewFileRequest.part`: "Which pages or slides to show: 'page 3', 'pages 2-5', for a deck 'slide 3', 'slides 2-5'; left out: the picture, or the first four."
    - In `read_document`'s description, "Such PDF pages come back as images…" becomes "Such PDF pages and PowerPoint slides come back as images in the same answer (shown names them): read them from the images. To look at any page or slide, use view_file."

- [ ] **Step 1: Write the failing tests** (`Local` executor, `fake_office`, MCP calls with `X-Accept-Images: 1`):
  - `view_file`:
    - `test_view_file_shows_the_slides_of_a_deck`: a 6-slide deck gives `kind office`, `unit slide`, labels `slide 1`…`slide 4`, and the hint `Slides 5-6 are not shown; call view_file with part "slides 5-6".`
    - `test_view_file_pages_a_deck_by_slides`: `slides 1-12` of a 12-slide deck gives 8 blocks, `next_part == "slides 9-12"`.
    - `test_view_file_refuses_a_page_of_a_deck`: `page 2` gives `refused` and `no_such_part` naming slides.
    - `test_view_file_shows_a_word_file_as_pages`: `unit page`, label `page 1`.
    - `test_view_file_says_a_spreadsheet_is_not_shown`: kind `none`, the `NOT_SHOWN` hint.
    - `test_view_file_names_a_slide_count_that_differs` (Review Focus 2): with `fake_office.drop = {2}` on 3 slides, the hint carries the facts' sentence.
    - `test_view_file_shows_a_deck_written_in_the_chat`: a `make_deck` file registered as an output is shown.
  - `read_document`:
    - `test_a_picture_only_slide_comes_back_as_an_image`: `make_deck(3, picture_only=[2])` gives one block; `shown == [{"label": "slide 2", "n": 2, …}]`; parts `slide 1` and `slide 3`; the hint holds the executor's `slide 2 holds no text (pictures only?); do not treat them as blank.` and `Slide 2 comes with this answer as an image.`
    - `test_a_deck_window_ends_before_a_slide_that_does_not_fit`: 6 picture-only slides give 4 blocks, `next_part == "slide 5"`, and the cut note `…cut before slide 5…part="slide 5"…`.
    - `test_a_deck_whose_pictures_do_not_match_attaches_nothing` (Review Focus 2): `fake_office.drop = {1}` gives no block, and `MISMATCH` in the hint.
    - `test_a_word_file_reads_as_text_and_points_to_view_file`: no block, `images: 0`, the hint ending with `PAGES_VIEWABLE`; with `part="paragraph 2"`, no `PAGES_VIEWABLE`.
    - `test_without_images_an_office_read_is_as_in_v07`: no header gives the v0.7 answer and event, word for word.
    - `test_a_busy_conversion_still_answers_the_text`: `Busy` on the conversion gives the parts, `shown []` and `The slides without text could not be shown now; view_file shows them.`
  - The `_hint` cases in `tests/executor/test_render.py` (:1087-1088) now expect `slide 3 holds no text (pictures only?); do not treat them as blank.` and `slides 3, 7 hold no text (pictures only?); do not treat them as blank.`. The `images=False` case (:1110) is unchanged.
  - Existing: every stage-1 PDF test in both api files passes unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_tools_views.py tests/api/test_tools_documents.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Edit both goldens by hand: `view_file` (title, description, `part`'s description, `kind`'s enum, `unit` in `outputSchema`) and `read_document` (description). Copy the served JSON once, after reading it line by line. In `docs/permissions.md` § What the model is told, name the slides.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): view_file and read_document show the pages and slides of Office files`

### Task 7: Docs, isolation, release

**Files:**
- Modify:
  - **`CLAUDE.md`:**
    - :24, office tooling: Office files are converted in the executor;
    - the shared modules: `renders.py` names Office files;
    - the module lists gain `runners/convert.py` and `render/office.py`;
    - derived files gain the `pdf` role;
    - the pipeline (:134-175): an Office prepare converts, then draws, in one turn;
    - the boundaries (:242-249): every conversion runs in the executor too;
    - Gotchas (:351): the LibreOffice profile is baked (`/opt/lo-profile`) and copied per conversion, as the font cache is; LibreOfficeKit starts once per process, so each conversion forks a child of the job; a hung LibreOffice call is stopped only by killing that child.
  - **`README.md`:** one line that Office pages and slides are shown.
  - **`docs/README.md` § Third-party licences:** LibreOffice (Debian's packages, unmodified, replaceable in the image; MPL-2.0, with parts under LGPL-3.0-or-later and other free licences), and Carlito, Caladea, Liberation 2, DejaVu and Noto. Each is written as `/usr/share/doc/<package>/copyright` in the image states it; read those in the image, never from memory.
  - **`docs/sandbox.md`:**
    - § Residual risks (:398): LibreOffice's OOXML and ODF import filters and its PDF export are a new parser of hostile input, in the executor only. They run in a forked child with the job's limits, after the zip guard and the encryption checks, with macros off and no network. `run_python` could load the same library through ctypes, under the same sandbox.
    - § No sockets (:130): LibreOfficeKit in-process needs none; `soffice`'s pipe would, so `soffice` is never run.
    - § Limits (:192): a conversion is a child of the job, with its own `RLIMIT_AS`. It counts against `NPROC` and in the watchdog's sum, and the parent's deadline kills it.
  - **`docs/data-flow.md`:** `converted.pdf` is a derived file of its source with its creation time, counted in the quota and deleted with it; only renders leave.
- Modify: `tests/isolation/test_isolation.py`, a case after :954, `test_a_deck_and_a_document_are_converted_and_shown_in_the_sandbox`:
  1. Upload `make_deck(4, picture_only=[2])` as `harbour-deck.pptx` and `make_long_docx(3)` as `quay-report.docx`.
  2. `POST /files/prepare` each: `office`, `slide` with 4 and `[2]` among `pages_without_text`; `office`, `page` with 3.
  3. Call `view_file` with `part: "slide 2"` (`X-Accept-Images: 1`): one block, label `slide 2`.
  4. `read_document` of the deck shows `slide 2`.
  5. `GET /files/view?rendition=1` of the document is `image/jpeg`.
  6. Delete the chat: the view answers 404, and no `.derived` folder is left on the volume.
- Modify: `pyproject.toml` (`version = "0.8.0"`), `uv.lock` (`uv lock`).

- [ ] **Step 1: Write the isolation case** after the stage-1 vision case's pattern.
- [ ] **Step 2: Docs.** Run `uv run pytest tests/test_docs.py -q`. Expected: PASS.
- [ ] **Step 3: Version.** Run `uv lock`, then `uv run pytest tests/test_version.py -q`. Expected: PASS.
- [ ] **Step 4: Gates.** Run `make verify && make test`, then `make test-executor-root` and `make test-isolation`. Expected: each green. Paste the summary lines and Task 1's image size into the PR.
- [ ] **Step 5: Commit** `docs: Office conversion in the design record, the sandbox and the licences`, then `chore(release): 0.8.0`.
- [ ] **Step 6: Push and PR.** `git push -u origin feat/vision-stage2`. Open a PR to `main` with this **Operators** line: "0.8.0 shows the pages and slides of Word, PowerPoint and OpenDocument text files, converting each once with LibreOfficeKit in the executor. The executor image grows by about 515 MB (LibreOffice nogui and fonts). No setting changes. Ghostwriter 0.3.0 is unaffected; 0.4.0 prepares Office attachments and needs this release for their pages." Confirm that CI ran on the last commit.

---

## Self-review

- **Spec coverage (§6 S1, §7.2 Office, stage 2):**
  - LibreOfficeKit in-process under the unchanged filter, the image, fonts and profile: Task 1;
  - `converted.pdf` as a derived file: Tasks 2 and 4;
  - pages as for PDF: Tasks 3 and 5;
  - `view_file` and `read_document`: Task 6;
  - §9's residual risks (new parsers, image size): Task 7.
  - `.odp` is left out by D1, against §2's list; the owner can reverse that in the Decisions.
- **Decisions made where the spec and the lead are silent:**
  - the PDF is the render job's input at its derived path;
  - one turn holds a prepare's two jobs;
  - pictures downsampled to 150 dpi;
  - an ODF encryption check;
  - a deck with no slides is `unsupported_format`;
  - `ViewedFile.unit`;
  - the slide hint mirrors the PDF one;
  - docx's `PAGES_VIEWABLE` only on the first read;
  - the client timeout of 390 s in the Contract.
- **Proportion:** code appears only for the fork, wait and kill order, the profile's entries, the filter data and the apt layer (exact copy). Everything else is signatures, values and test assertions.
