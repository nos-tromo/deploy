# workbench v0.4.0 — the terminal door goes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Open WebUI's terminal door and the `open-webui` caller from workbench, while docint's and Ghostwriter's MCP tools stay byte-identical.

**Architecture:** First freeze what the MCP door lists today (a golden `tools/list` test). Then move shared test helpers, so later deletions take no shared coverage with them. Then build the workspace tools from an explicit table instead of from the REST tool routes, and port the tool tests to the MCP door. Then delete the door, the contract stack and the `open-webui` caller. Docs and the version close it.

**Tech Stack:** Python 3 (FastAPI, pydantic, pytest), `uv`, Docker Compose (CI's isolation and load stacks).

**Spec:** `docs/superpowers/specs/2026-11-17-open-webui-retirement-design.md` (§ 6.3), in deploy.

## Global Constraints

- Branch `feat/retire-open-webui` from workbench's `origin/main`, in a clean worktree outside the main checkout. The main checkout holds someone else's uncommitted work (`workbench/api/store.py`, `workbench/executor/readers/`): never touch it.
- `pyproject.toml` `version = "0.4.0"`, then `uv lock`. The tag is minted on merge; never tag by hand.
- Ghostwriter's and docint's `tools/list` answers stay byte-identical to the golden fixtures of Task 1. Never regenerate those fixtures after Task 1.
- Kept as they are: the routes `/mcp`, `/files/list`, `/files/upload`, `/files/view`, `/files/chat`, `/healthz`, `/metrics`; `ViewHardening`; the `workspace` file form and its confinement; the `default` chat folder; retention; the executor; `BodyLimit`, `EncodableRoute`, `redirect_slashes=False`; the metrics labels.
- Every assertion on a tool's behaviour that today runs through the terminal key survives, through the MCP door with Ghostwriter's key (`HG`) or docint's (`HD`). Only tests of the terminal door itself go.
- Never run `make up`, `up-dev`, `nuke`, `bundle` or `bundle-dev`. The isolation and load stacks (cgroup v2, Docker) run in CI; locally, run them only if each target uses its own compose project and removes only its own resources.
- Gate before every commit: `make verify` and `make test`. Docs stay minimal. Synthetic data only; no local paths.

## Review Focus

1. A host's `.env` sets only `OPEN_WEBUI_WORKBENCH_KEY`: the api refuses to start with `DOCINT_WORKBENCH_KEY or GHOSTWRITER_WORKBENCH_KEY must be set to at least <n> characters`, and the message names no Open WebUI key (Task 4, test).
2. A host's `.env` sets `OPEN_WEBUI_WORKBENCH_KEY` beside Ghostwriter's: the old key is ignored; presenting it answers `401` on every path but `/healthz` and `/metrics` (Task 4, test).
3. A peer still calls a removed path (`/openapi.json`, `/api/config`, `/files/read`, `/tools/run_python`) with Ghostwriter's key: `404`, and nothing is recorded as a tool call (Task 4, test).
4. A workspace left by the `open-webui` caller on a dev host's volume still ages out under retention (Task 4, test: a file under the `open-webui` caller's folder past the period is swept).
5. A model's call with the MCP wording of a text that used to name `display_file` reaches the same tool: the golden test covers the names; the tool tests cover the calls (Tasks 1 and 3).

---

### Task 1: freeze the MCP door's tool lists

**Files:**
- Create: `tests/api/test_tools_golden.py`, `tests/api/golden/ghostwriter-modern.json`, `tests/api/golden/ghostwriter-legacy.json`, `tests/api/golden/docint-modern.json`, `tests/api/golden/docint-legacy.json`

**Interfaces:**
- Consumes: `rpc`, `result` and `MODERN` from `tests/api/test_mcp.py`; `HD`, `HG` from `tests/api/conftest.py`; the `client` fixture.
- Produces: the golden test, which every later task must keep green.

- [ ] **Step 1: Write the test**

```python
GOLDEN = Path(__file__).parent / "golden"

@pytest.mark.parametrize(("caller", "headers"), [("ghostwriter", HG), ("docint", HD)])
@pytest.mark.parametrize("era", ["modern", "legacy"])
def test_tools_list_is_frozen(client: TestClient, caller: str, headers: Headers, era: str) -> None:
    """The MCP door lists each caller's tools exactly as workbench v0.3.0 did."""
    answer = result(rpc(client, "tools/list", headers=headers, version=MODERN if era == "modern" else <legacy>))
    served = json.dumps(answer, indent=2, ensure_ascii=False) + "\n"
    assert served == (GOLDEN / f"{caller}-{era}.json").read_text(encoding="utf-8")
```
Use `test_mcp.py`'s own way to speak the legacy revision (`version=None` after an `initialize`, or what its legacy tests do).

- [ ] **Step 2: Write the fixtures once, on the unchanged code**

On the branch's first commit, before any other change, produce each fixture with the same call and the same `json.dumps(…, indent=2, ensure_ascii=False) + "\n"`, from a throwaway script outside the repo. Check by eye: the Ghostwriter files list eight tools without `display_file`; the docint files list `run_python`.

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/api/test_tools_golden.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/api/test_tools_golden.py tests/api/golden/
git commit -m "test: freeze the MCP door's tool lists"
```

---

### Task 2: shared test helpers and app tests move out of the door's modules

**Files:**
- Modify: `tests/api/conftest.py` (gains `H`, `CHAT`, `ME`, `put`, `tick`, `AuditLines` from `tests/api/test_files.py`), every module importing them from `test_files` (13 modules; import from `tests.api.conftest`)
- Create: `tests/api/test_app.py` (the shared tests of `tests/api/test_contract.py`: no key is 401, the constant-time key compare, the 2 MiB body limit, `/healthz`, the service routes, store errors, lifespan, `app_factory`, no built-in docs routes)
- Modify: `tests/api/test_contract.py` (loses what moved)

**Interfaces:**
- Produces: the helpers in `tests.api.conftest` under the same names. `H` stays the terminal key's headers until Task 4 removes it.

- [ ] **Step 1: Move, without changing a test**

Move the helpers and the shared tests verbatim; only imports change.

- [ ] **Step 2: Run the whole suite**

Run: `make test`
Expected: the same number of tests passing as before the move (compare with the count on `origin/main`).

- [ ] **Step 3: Commit**

```bash
git add -A tests/
git commit -m "test: shared helpers and app tests leave the terminal door's modules"
```

---

### Task 3: the workspace tools from an explicit table

**Files:**
- Create: `workbench/api/tool_table.py`
- Modify: `workbench/api/tools.py`, `workbench/api/tools_output.py`, `workbench/api/tools_templates.py` (the tool functions lose their `@router.post` decorators and their FastAPI `Depends` annotations; their texts and models' `Field` descriptions take the MCP door's wording), `workbench/api/tools_mcp.py` (`workspace_tools()` reads the table; `MCP_TEXT`, `mcp_text`, `mcp_schema` and the pass-through branch go), `workbench/api/registry.py` (`workspace_specs`, `ToolGuard`, `_guarded`, `_model`, `MCP_WORKSPACE_TOOLS`, `display_file` in `WORKSPACE_TOOLS`, the `door` argument of `allowed_tools` go), `workbench/api/main.py` (no `tools.router`; the registry is built from the table), `workbench/api/deps.py` (dependency aliases only the tool routes used)
- Delete: `workbench/api/openapi.py` (its `tool_routes`, `tool_openapi` and `TOOL_TAG` have no user left), and with it the `/openapi.json` route in `workbench/api/contract.py`, which serves `tool_openapi`. The rest of `contract.py` goes in Task 4.
- Test: `tests/api/test_tools_documents.py`, `test_tools_python.py`, `test_tools_templates.py`, `test_tools_outputs.py`, `test_tools_mcp.py`, `test_audit.py`, `test_internal_errors.py`, `test_registry.py`, `tests/test_docs.py`

**Interfaces:**
- Produces, in `workbench/api/tool_table.py`:

```python
@dataclass(frozen=True)
class WorkspaceTool:
    """A workspace tool as the table describes it."""
    name: str
    title: str
    description: str
    input_model: type[BaseModel] | None      # None: the tool takes no arguments (list_files)
    output_model: type[BaseModel]
    function: Callable[..., Any]             # takes, by name, any of: body, p, settings, store, audit, jobs

WORKSPACE_TABLE: Final[tuple[WorkspaceTool, ...]]   # the eight tools, in name order
```
- `tools_mcp.workspace_tools() -> tuple[ToolSpec, ...]` (no `app` argument): one spec per table row, built as today (`workspace_run(function, name)`, `NoArguments` for `None`, `with_outputs(output_model)`, both schemas from the models).
- `registry.allowed_tools(files: Files) -> frozenset[str]`; `WORKSPACE_TOOLS` is the eight names.

- [ ] **Step 1: Port the tool tests to the MCP door**

Each test that calls `/tools/<name>` with `H` calls the tool on the MCP door instead, with `acall`-style helpers from `tests/api/test_tools_mcp.py` and `HG`, and asserts the same facts on `structuredContent` (or the error result's token). REST-transport tests (a `422` on a malformed body, a `401` without a key on `/tools/…`, the `/openapi.json` checks) go where `test_mcp.py` / `test_tools_mcp.py` already pin the MCP equivalent; otherwise they become that MCP equivalent. `tests/test_docs.py`'s tool matrix reads the table instead of `tool_openapi`.

Run: `make test`
Expected: PASS (the REST routes still exist; the ported tests go through `/mcp`).

- [ ] **Step 2: Write the table's own test**

In `tests/api/test_registry.py`:
```python
def test_the_table_holds_the_eight_tools_in_name_order() -> None:
    assert [tool.name for tool in WORKSPACE_TABLE] == sorted(EIGHT)

def test_each_function_takes_only_known_parameters() -> None:
    for tool in WORKSPACE_TABLE:
        assert set(inspect.signature(tool.function).parameters) <= KNOWN, tool.name
```
(`KNOWN` as in `test_tools_mcp.py`: `body, p, settings, store, audit, jobs`.)

Run: `uv run pytest tests/api/test_registry.py -v`
Expected: FAIL (no `tool_table` module).

- [ ] **Step 3: Build the table and drop the routes**

Create the table from the routes' `operation_id`, `summary`, `description`, body model and response model. Write each text in the MCP door's words (what `mcp_text` / `mcp_schema` produce today), in the source, then delete `MCP_TEXT`, `mcp_text`, `mcp_schema`. Remove the decorators, `tools.router`, `files.tool_router` (if it carries tool routes), and what the Files list names in `registry.py`.

- [ ] **Step 4: Run the suite and the golden test**

Run: `make test`
Expected: PASS, `test_tools_golden.py` included, byte-identical. A golden failure means a text or schema moved: fix the source, never the fixture.

- [ ] **Step 5: Gate and commit**

```bash
make verify && make test
git add -A
git commit -m "refactor: the workspace tools come from a table, not from the terminal door's routes"
```

---

### Task 4: the terminal door and the `open-webui` caller go

**Files:**
- Delete: `workbench/api/contract.py` (after `/healthz` moves), `workbench/api/system_prompt.md`, `contract/`, `tests/contract/`, the terminal-only tests in `tests/api/test_contract.py` (the file, once empty), the terminal-only tests in `tests/api/test_files.py` (read, delete, mkdir, move, display, the root listing) and `tests/api/test_callers.py`
- Create: `workbench/api/health.py` (a router with `/healthz`, the same answer as today)
- Modify: `workbench/api/files.py` (no `/files/read`, `/files/delete`, `/files/mkdir`, `/files/move`, `display_file`, nor their helpers `DisplayedFile`, `MkdirRequest`, `MoveRequest`, `_media_type`, `_is_text`, `_TEXT_SUFFIXES`), `workbench/api/store.py` (`mkdir`, `move` if nothing else calls them), `workbench/api/auth.py` (`Door` and `Caller.door` go; `confined` becomes `files == "workspace"`; the `open-webui` row, the terminal branches of `door_allows` and `require_file_caller`, `require_caller`, `require_principal`, `_ToolOnList`, `require_tool` go), `workbench/api/deps.py` (the plain-log branch of `call_audit`), `workbench/api/audit.py` (the `read`, `display`, `delete` operations), `workbench/api/main.py` (`health.router` in place of `contract.router`), `workbench/config.py` (`OPEN_WEBUI_WORKBENCH_KEY`, `open_webui_key`; the no-key message), docstrings that name Open WebUI or the terminal door (`models.py`, `paths.py`, `tools_inline.py`, `tools.py`, `hardening.py`, `files.py`, `audit.py`, `registry.py`)
- Modify (plumbing): `.github/workflows/ci.yml` (the `contract` job), `.github/dependabot.yml` (the `/contract` entry), `Makefile` (`test-contract` and the lines that name the contract stack), `pyproject.toml` (the `contract` marker, its `addopts` part, `system_prompt.md` in package-data), `.dockerignore` (the contract and system-prompt lines), `docker/compose.yaml`, `docker/compose.test.yaml`, `docker/compose.override.yaml` (the Open WebUI key and comments)
- Test (port, not delete): `tests/isolation/conftest.py` and `tests/isolation/test_isolation.py` (run `run_python` with Ghostwriter's key; the case of one workspace caller against another goes, the cases between users and between chats stay), `tests/load/test_load.py`, and the swap-only modules (`test_config.py`, `tests/api/conftest.py`, `test_retention.py`, `test_jobs.py`, `test_store.py`, `test_paths.py`), `tests/test_compose_defaults.py` (the contract-stack tests go), `tests/api/test_registry.py`, `tests/test_docs.py` (`len(OPERATIONS)` follows the three removed keys)

**Interfaces:**
- Consumes: Task 3's table; `health.router`.
- The `config.audit` event keeps naming each caller's door, now always `"mcp"`.

- [ ] **Step 1: Write the failing tests (Review Focus 1–4)**

In `tests/test_config.py`:
```python
def test_the_old_key_alone_starts_nothing() -> None:
    """With only OPEN_WEBUI_WORKBENCH_KEY set, the api refuses, naming the two keys that count."""
    with pytest.raises(ConfigError) as e:
        load_settings({**<the module's base env without caller keys>, "OPEN_WEBUI_WORKBENCH_KEY": "x" * 43}, role="api")
    assert str(e.value) == NO_CALLER_KEY
    assert NO_CALLER_KEY.startswith("DOCINT_WORKBENCH_KEY or GHOSTWRITER_WORKBENCH_KEY must be set")
    assert "OPEN_WEBUI" not in NO_CALLER_KEY
```
In `tests/api/test_callers.py` (or `test_app.py`):
```python
def test_the_old_key_beside_ghostwriters_is_a_stranger(...) -> None:
    """OPEN_WEBUI_WORKBENCH_KEY set beside Ghostwriter's key: presenting it is 401 on /mcp and /files/list."""

@pytest.mark.parametrize("path", ["/openapi.json", "/api/config", "/files/read", "/tools/run_python"])
def test_a_removed_path_is_404_for_ghostwriter(client: TestClient, audit_lines: AuditLines, path: str) -> None:
    """A removed route answers 404 to Ghostwriter's key, and no tool event is written."""
```
In `tests/api/test_retention.py`:
```python
def test_a_workspace_left_by_open_webui_still_ages_out(...) -> None:
    """A file under the open-webui caller's folder past the period is swept like any other."""
```
Run: `uv run pytest tests/test_config.py tests/api/test_callers.py tests/api/test_retention.py -v`
Expected: the new config test and the 404 test fail (the key still counts; the routes still answer); the retention test may pass already: keep it as a pin.

- [ ] **Step 2: Move `/healthz`, then delete the door**

As listed under Files. The compose healthcheck, `make health` and `OPEN_PATHS` keep `/healthz` unchanged.

- [ ] **Step 3: Port the isolation and load stacks; swap the keys**

As listed under Test. The isolation job in CI must still run `run_python` in the executor.

- [ ] **Step 4: Remove the contract stack's plumbing**

As listed under Modify (plumbing).

- [ ] **Step 5: Run the suite**

Run: `make verify && make test`
Expected: PASS, golden test included.
Then: `git grep -I -i -n -E "open[-_ ]?webui|OPEN_WEBUI|openwebui|terminal door" -- workbench/ tests/ docker/ contract/ Makefile pyproject.toml .github/ .dockerignore` prints only the retention test's `open-webui` folder name (Review Focus 4).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: the terminal door and the open-webui caller go"
```

---

### Task 5: docs, example and release

**Files:**
- Modify: `.env.example` (the `OPEN_WEBUI_WORKBENCH_KEY` block goes; "one of the three keys" becomes two; the retention period's tag becomes `[shared: ghostwriter]`; the "Open WebUI cannot state one" sentence goes), `docs/permissions.md`, `docs/audit.md`, `docs/data-flow.md`, `docs/configuration.md`, `docs/retention.md`, `docs/README.md`, `README.md`, `CLAUDE.md`, `pyproject.toml` (`version = "0.4.0"`), `uv.lock`
- Do not modify: `docs/superpowers/`.
- Test: `tests/test_compose_defaults.py` or wherever a test asserts the `.env.example` tag line (it changes with it)

- [ ] **Step 1: The example and its test**

Change the tag test's expectation to `# [shared: ghostwriter]`, then the example. Run: `uv run pytest tests/test_compose_defaults.py -v` → PASS.
Check against deploy's table: `python3 -B <deploy worktree or ../deploy>/scripts/check_env_examples.py .` prints nothing (deploy v0.13.0's table; before it merges, use its branch).

- [ ] **Step 2: The docs**

Two callers: docint (MCP, inline data) and Ghostwriter (MCP, workspace files, four file routes). Remove the terminal door's sections, the `display_file` row, the browser-route rows and the handshake. `CLAUDE.md`'s caller paragraph names two callers; the diagram loses Open WebUI.

- [ ] **Step 3: Version, gate, commit, push, PR**

```bash
uv lock
make verify && make test
git add -A
git commit -m "docs: two callers; release v0.4.0"
git push -u origin feat/retire-open-webui
```
PR title: `feat: the terminal door goes; release v0.4.0`. The body names the host consequence (a host with only the old key must set Ghostwriter's, deploy's runbook `retire-open-webui.md`) and that the golden `tools/list` test is unchanged since v0.3.0.
