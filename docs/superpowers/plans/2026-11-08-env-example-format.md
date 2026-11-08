# One format for every `.env.example` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every federation `.env.example` follows one checked standard. `make env` then brings every host `.env` into that shape without changing any running value.

**Architecture:** deploy gains `scripts/envsync/conventions.py`, a stdlib checker for the standard, with a CLI `scripts/check_env_examples.py`. It also gets two env-sync rule refinements and its own reformatted `federation.env.example`. Each member is then reformatted in its own repo and PR, checked with deploy's CLI and verified with an upgrade dry run.

**Tech Stack:** Python ≥ 3.9 standard library, `unittest`, GNU make, docker compose, `gh`.

**Spec:** `docs/superpowers/specs/2026-11-08-env-example-format-design.md`. Its appendix holds the survey and the full checker rule list (F/H/S/T/V/M/X); rule ids here refer to it.

## Global Constraints

**Applies everywhere**
- Python ≥ 3.9, stdlib only, `from __future__ import annotations`. Tests run with `python3 -B -m unittest discover -s tests -p '<file>'`.
- Findings and reports never print a value. A `Finding` carries `(lineno, rule, key)` only.
- Never run `make env` / `make env-check` against the sibling repos, and never touch `deploy/.env-sync/`, before Task 12. Dry runs use temp dirs with explicit `--state` / `--table`.
- No real data and no absolute local paths in committed files.

**Example files**
- Banner rule line: `"# " + "=" * 77`. Section titles, in this order: `Required`, `Auth & identity`, `Network & proxy`, `Inference`, `<member>` (the directory name; `deploy` for federation.env), `Audit & retention`, `Development only`.
- Header: exactly the spec's template. `<env>` / `<example>` are `.env` / `.env.example`, or `federation.env` / `federation.env.example` for deploy.
- Tags are written `# [required] [secret] [shared: …] [dev]`, in that order. Shared items are separated by `, `, and an item is `member` or `member KEY`.
- The principle: the value hosts run today wins. A line that turns from active to commented needs an equal effective default; otherwise that default is aligned in compose, the Makefile or the code.

**Git and releases**
- Each repo uses its own branch `feat/env-example-format`, conventional commits, a version bump in its own `chore(release)` commit, and one PR. Confirm CI on the latest push.

## Review Focus

1. **An inherited, non-held active default turns commented.** A host line identical to the old example is removed by `make env`, and the effective value must stay equal. The per-member dry run checks this (Tasks 5-11, step "Dry run").
2. **A moved key changes reference order.** A key referenced through `${X}` moves above or below the line that references it, and env-sync then refuses the host file. Rule V10 checks this, and the dry run must show no `refused`.
3. **Member CI copies the example as `.env`.** Empty required values break `docker compose config` / `up-dev` in CI. CI fills them (Task 6; check every member's workflow in its task).
4. **An inline comment lands on a key a member Makefile greps**, which corrupts the value. Rules V11 and X4 cover this, with a unit test in Task 2.
5. **A fresh dev host meets empty required secrets.** `make up-dev` must fail fast with a clear `:?` message, never start with an empty secret. Each member task verifies it with `docker compose config` (expect the error), and Tasks 6, 7 and 11 add a compose `:?` where the spec asks for it.

---

### Task 1: env-sync rule refinements (deploy)

**Files:**
- Modify: `scripts/envsync/dotenv.py` (`is_secret_like`), `scripts/envsync/shared.py` (`_Pass.read`, the source rule)
- Test: `tests/test_envsync_dotenv.py`, `tests/test_envsync_shared.py`
- Docs: the `source` sentence in `docs/runbooks/env-sync.md` (Shared values)

**Interfaces:**
- Produces: `is_secret_like(key)` with the new rule; a source row counts as set only when its active line has a non-empty value.

- [ ] **Step 1: Write the failing tests**
  - `test_is_secret_like` gains these cases:
    - True: `NEXTEXT_SERVICE_TOKENS`, `HF_TOKEN`, `AUDIT_EDGE_SECRET`, `QDRANT_API_KEY`.
    - False: `TOKENIZERS_PARALLELISM`, `EMBED_TOKENIZER_REPO`, `HATE_SPEECH_WINDOW_TOKENS`, `HATE_SPEECH_CONTEXT_TOKENS`, `EMBED_CTX_TOKENS`, `CHAT_MAX_NUM_BATCHED_TOKENS`, `MAX_TOKENS`, `TOKEN_LIMIT`.
  - `test_empty_source_counts_as_unset`: edge-plane `EDGE_HOST=` (source, empty), obs-plane `EDGE_HOST=gw.example.invalid` → no conflict; edge-plane is filled from obs-plane.
  - `test_empty_source_with_generate`: in a gen group whose source is empty, `ENV_GENERATE` generates for every row.
- [ ] **Step 2: Run** `python3 -B -m unittest discover -s tests -p 'test_envsync_dotenv.py'` and `-p 'test_envsync_shared.py'` → the new cases FAIL.
- [ ] **Step 3: Implement.** In `is_secret_like`, use `(^|_)TOKENS?(_|$)` for TOKEN and exclude keys with a `(^|_)(MAX|NUM|LIMIT|CTX|CONTEXT|WINDOW)(_|$)` segment. SECRET, PASSWORD, PASSPHRASE and the `_KEY` suffix stay unchanged. In `shared.py`, a source row is set iff it has an entry whose value is non-empty.
- [ ] **Step 4: Run** the full suite → all PASS.
- [ ] **Step 5: Commit** `fix(env-sync): token counts are not secrets; an empty shared source counts as unset`.

---

### Task 2: the format checker (deploy)

**Files:**
- Create: `scripts/envsync/conventions.py`, `scripts/check_env_examples.py` (executable entry like `env_sync.py`)
- Test: `tests/test_envsync_conventions.py`

**Interfaces:**
- Consumes: `envsync.dotenv` (`parse`, `Line`, `is_secret_like`, `references`), `envsync.example.load_example`, `envsync.shared` (`Group`, `Row`, `load_table`).
- Produces (`envsync.conventions`):
  - `RULE = "# " + "=" * 77`; `SECTIONS: Tuple[str, ...]` (the 7 titles, `"<member>"` as a placeholder).
  - `header(member: str, env_name: str, example_name: str) -> List[str]`: the template lines.
  - `@dataclass(frozen=True) class Finding: lineno: int; rule: str; key: Optional[str]`
  - `check(text: str, member: str, syntax: str = "dotenv", groups: Sequence[Group] = (), grep_keys: FrozenSet[str] = frozenset(), make_defaults: Optional[Dict[str, str]] = None) -> List[Finding]`: rules F1-F4, H1-H3, S1-S5, T1-T6, V1-V11, M1-M4 (M only for `syntax == "make"`).
  - `is_migrated(text: str, member: str) -> bool`: the first line equals `header(...)[0]`.
  - `makefile_grep_keys(makefile_text: str) -> FrozenSet[str]`: every `grep -E… '^KEY=` pattern.
  - `makefile_defaults(makefile_text: str) -> Dict[str, str]`: every `KEY ?= value`, stripped.
- Produces (`scripts/check_env_examples.py [--table FILE] DIR...`):
  - For each DIR, the member is the basename and the example is `.env.example`, or `federation.env.example` with make syntax.
  - It runs `check` with that member's `env-sync.conf` groups, the Makefile's grep keys and the Makefile `?=` defaults (deploy only), plus the compose checks of Task 3.
  - Output, one line per finding: `<dir-name>/<example>:<lineno>: <rule> <key or ->`, followed by a fixed per-rule message.
  - Exit 0 when there are no findings, 1 when there are, 2 on usage errors.

- [ ] **Step 1: Write the failing tests** in `tests/test_envsync_conventions.py`.
  - `VALID`: a synthetic, fully compliant member example (header, Required with one `[required] [secret] [shared: edge-plane]` key, Network & proxy, `<member>` with a `# --- topic ---` subsection, Development only with one `[dev]` key). `check(VALID, "obs-plane", groups=...) == []`.
  - One test per rule id, each mutating `VALID` minimally and asserting that the expected rule id (and key) appears among the findings:
    - F2 leading blank; F3 an 80-character prose line (and no finding for a long digest value); F4 `# name=x`;
    - H1 a changed header line; H2 a setting inside the header;
    - S1 an unknown title; S2 swapped sections; S3 a stray `# ==`; S4 `# -- x --`;
    - T1 a tag line with no setting below; T2 `[secret] [required]`; T3 an active line outside Required, and a commented line inside it; T4 a secret-like key without `[secret]`; T5 `[shared]` missing, or naming the wrong member; T6 `[dev]` outside Development only;
    - V1 `KEY=x` active; V2 two `# KEY=` lines; V3 `#KEY=`; V4 `# KEY=<x>`; V5 `# K=${K}`; V6 `# SOME_SECRET=x`; V7 `# K=   # c`; V8 a tab before `#`; V9 an unquoted prose value; V10 a reference above its target; V11 an inline comment on a key in `grep_keys`;
    - M1-M4 on a make-syntax sample (inline comment, `K = v`, an active line, a value that differs from `make_defaults`).
  - `test_findings_carry_no_values`: a sentinel value in a broken line never appears in `repr(findings)` or in the CLI output.
  - `test_makefile_helpers`: `makefile_grep_keys` and `makefile_defaults` on a small Makefile text, including the data-plane-style `$(shell test -f .env && grep -E '^PROFILE=' .env | cut -d= -f2)`.
- [ ] **Step 2: Run** `python3 -B -m unittest discover -s tests -p 'test_envsync_conventions.py' -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `conventions.py` and the CLI.
  - A section is the nearest banner above a line (S5).
  - A tag line is the line directly above a setting or commented setting.
  - T5 builds the expected item list from the group (source/non-source as in the spec).
- [ ] **Step 4: Run** the module, then the full suite → PASS.
- [ ] **Step 5: Commit** `feat(env-sync): a checker for the .env.example format`.

---

### Task 3: sibling checks and deploy's own example (deploy)

**Files:**
- Modify: `scripts/envsync/conventions.py` (compose checks), `federation.env.example`, `tests/test_envsync_make.py` (replace `test_example_matches_makefile_defaults`)
- Create: `tests/test_envsync_examples.py`

**Interfaces:**
- Produces:
  - `check_compose(example_text: str, compose_text: str, override_text: Optional[str]) -> List[Finding]`, covering:
    - X3: every simple `${K:-v}` / `${K-v}` (one level, no nested `$`) equals the commented value; `[dev]` keys are compared against the override; `${K:?…}` ⇒ K is `[required]`;
    - X5: every interpolated variable except `*_VERSION` is documented (active or commented).
  - `check_group_required(examples: Dict[str, str], groups) -> List[Finding]` for X2.
  - The CLI runs these for member dirs that have `docker/compose.yaml` (X4 comes from `makefile_grep_keys`).

- [ ] **Step 1: Write the failing tests.**
  - `test_compose_default_mismatch` (X3).
  - `test_required_key_from_colon_question` (X3, `${K:?}`).
  - `test_undocumented_compose_variable` (X5); `*_VERSION` is ignored.
  - `test_group_required_disagrees` (X2).
  - In `tests/test_envsync_examples.py`:
    - `test_deploy_example_follows_the_format`: `check(federation.env.example, "deploy", "make", make_defaults=makefile_defaults(Makefile))` returns `[]`;
    - `test_migrated_siblings_follow_the_format`: for each member in `env-sync.conf` and the deploy Makefile's member list whose `../<member>/.env.example` exists and `is_migrated`, run the CLI checks → `[]`. The test is skipped when no sibling is migrated.
  - In `tests/test_envsync_make.py`, delete `test_example_matches_makefile_defaults`; the M4 rule covers it.
- [ ] **Step 2: Run** → FAIL: the compose checks are missing, and `federation.env.example` is not in the format yet.
- [ ] **Step 3: Implement** the compose checks.
- [ ] **Step 4: Rewrite `federation.env.example`** per the spec: header with the deploy variant; only the `deploy` section; subsections Members, Clone, Health gates, One-shot switches; every value commented and equal to the Makefile `?=` default; the switches written as `# KEY=` with their meaning in the comment; no inline comments.
- [ ] **Step 5: Run** the full suite, `make help >/dev/null && make -n ps >/dev/null && make -n env >/dev/null`, and `python3 -B scripts/check_env_examples.py .` → exit 0.
- [ ] **Step 6: Commit** `feat(env-sync): compose checks; federation.env.example in the shared format`.

---

### Task 4: docs, release and PR (deploy)

**Files:** `docs/runbooks/env-sync.md` ("For member authors" becomes the standard with the checker command), `CLAUDE.md` (the cross-repo contract's conventions, now pointing to the spec and `scripts/check_env_examples.py`), `docs/README.md` (index the spec), `VERSION` → `0.8.0`.

- [ ] **Step 1: Write the docs.** Point to the spec; do not restate the rule list. Include the checker invocation for member authors: `python3 -B ../deploy/scripts/check_env_examples.py .`
- [ ] **Step 2: Verify:**
  - the full suite;
  - `shellcheck scripts/*.sh`;
  - `uvx --quiet --from yamllint yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/`;
  - `grep -rnE "/Users/|/home/" docs CLAUDE.md README.md` shows only the policy text.
- [ ] **Step 3: Commit** the docs, then `chore(release): v0.8.0`.
- [ ] **Step 4: Push and open the PR** with `gh pr create`, then confirm CI on the latest commit.

---

### Tasks 5-11: one member each (same procedure)

Each task runs in that member's repo. Before starting, read its `CLAUDE.md` for the branch rules, where the version lives and the local checks.

**The procedure, used by every member task:**
- [ ] **Step 1: Branch** `feat/env-example-format` from an up-to-date `main`.
- [ ] **Step 2: Red.** `python3 -B ../deploy/scripts/check_env_examples.py .` → findings (the example is not migrated yet).
- [ ] **Step 3: Rewrite `.env.example`** per the spec and this task's notes, plus the spec appendix entry for the member. Keep every key; settings that are fixed by compose or set per run follow the spec's builder decision 9.
- [ ] **Step 4: Align defaults** where this task lists them, in `docker/compose.yaml` (or the CI workflow).
- [ ] **Step 5: Green.** The checker exits 0. When the deploy branch is checked out locally, deploy's `test_migrated_siblings_follow_the_format` passes for this member.
- [ ] **Step 6: Dry run for an untouched host.**
  - Set up a temp dir `T` with:
    - `T/<member>/.env.example` = the new example;
    - `T/<member>/.env` = `git show main:.env.example`;
    - `T/state/base/<member>.example` = the same old example.
  - Run `python3 -B ../deploy/scripts/env_sync.py --check --state T/state --table ../deploy/env-sync.conf T/<member>`.
  - Expect no `refused`. Every `followed`, `unset` and `removed` line must keep its effective value: check each against the compose default and note them in the PR body.
- [ ] **Step 7: The member's own checks:**
  - its local gate (`make verify` / `make test` where the repo has them, else its CI's commands);
  - `docker compose --env-file .env.example -f docker/compose.yaml config -q` must fail with the intended `:?` message where required values are empty;
  - with the required values filled, it must pass.
- [ ] **Step 8: Docs and pointer.** Add a one-line pointer in the member's `CLAUDE.md`: "`.env.example` follows deploy's shared format (deploy `docs/runbooks/env-sync.md`, For member authors); check with `python3 -B ../deploy/scripts/check_env_examples.py .`". Fix any member docs that quote changed example lines.
- [ ] **Step 9: Commits.** The format commit `docs(env): .env.example in the federation format` (or `feat(env): …` where compose defaults change), then the version bump in its own `chore(release)` commit. Push, open the PR and confirm CI.

| Task | Member | Notes (the full survey is in the spec appendix) | Version |
|---|---|---|---|
| 5 | data-plane | Format only. Keep the CI-greped line `QDRANT_API_KEY=` byte-identical. No inline comments on the Makefile-grepped keys. | patch |
| 6 | edge-plane + obs-plane (two PRs, merged together) | **Placeholders become empty:** the three Authelia keys, `GRAFANA_ADMIN_PASSWORD`, `AUDIT_EDGE_SECRET`. **edge-plane:** `EDGE_HOST` is the empty source; `AUDIT_EDGE_SECRET` is `[required]` with compose `:?`; the per-app edge secrets and `GRAFANA_EDGE_SECRET` become commented. **Fix commented values:** `EDGE_TLS` → `internal`, `RESPONSE_LANGUAGE` → `en`, `EDGE_SUPPORT_CONTACT` empty, `EXTRA_NO_PROXY` empty. **CI** fills the required values (edge `ci.yml:37,84`, obs `ci.yml:41`, the data-plane pattern). | edge minor, obs patch |
| 7 | open-webui-service | `OPENAI_API_KEY` is empty `[required] [shared: vllm-service]`. Document the 7 undocumented compose variables. `OPEN_WEBUI_HOST_PORT` shows 3000; the PR announces the dev-port move from 8080. `WHISPER_API_BASE` is empty. | patch |
| 8 | translator | Compose `OPENAI_API_BASE: ${OPENAI_API_BASE:-http://vllm-router:4000/v1}`. Fix commented values: `TEXT_MODEL`, `RESPONSE_LANGUAGE` → `en`, `AUDIT_REQUIRE_PURPOSE` empty, `TRANSLATOR_DEFAULT_IDENTITY` → `dev`, `OPENAI_API_KEY` (secret, default `dummy` in prose). Document `EDGE_NET`. | minor |
| 9 | docint | Compose defaults `INFERENCE_PROVIDER: ${INFERENCE_PROVIDER:-vllm}` and `OPENAI_API_BASE: ${OPENAI_API_BASE:-http://vllm-router:4000/v1}`. `QDRANT_API_KEY` is `[required]`, with its comment on its own line. Rewrite the 22 trap lines. Map the 20 sections onto the 7 (spec appendix). Document `DATA_NET` / `EDGE_NET`. Keep `OPENAI_API_KEY` above its references. Note the new compose defaults in `docs/configuration.md`. | minor |
| 10 | Nextext (start only after `feat/face-detection` is merged) | Compose router defaults (`OPENAI_API_BASE`, `INFERENCE_PROVIDER=vllm`). The three provider blocks become one line per key plus prose. Add `# renamed: NEXTEXT_RESPONSE_LANGUAGE -> RESPONSE_LANGUAGE`. `TEXT_MODEL` is `[required]`. `NEXTEXT_SERVICE_TOKENS` is empty. Keep `tests/test_compose_config.py` green. | minor |
| 11 | vllm-service (start only after `feat/faces-service` is merged) | Fix about 27 commented values to match compose (spec appendix table); move GPU-layout hints into prose. Router, topics and faces get `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` `${…:-1}` and the model-name defaults. Router `OPENAI_API_KEY: ${OPENAI_API_KEY:?…}`. Add two `# renamed:` lines. `HF_TOKEN` is empty. Document `NER_HOST_PORT`. | minor |

---

### Task 12: bring the dev Mac along (lead, with the user's go-ahead)

- [ ] **Step 1: Check out the migrated mains.** After each merge, `make pull` in deploy (it returns every repo to `main`).
- [ ] **Step 2: Preview.** `make env-check` → expect `would write` with comment and layout changes only, plus the announced `unset` lines. Show the report to the user.
- [ ] **Step 3: Apply.** `make env`, then `make env-check` → `env: 9 files in sync`, and a second `make env` changes nothing.
