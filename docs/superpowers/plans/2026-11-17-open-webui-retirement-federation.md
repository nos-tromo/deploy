# Open WebUI retirement — federation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take Open WebUI out of deploy, edge-plane, vllm-service, obs-plane, the hub and the workspace map, and leave open-webui-service as a retirement note ready to archive.

**Architecture:** One release PR per repo, in the spec's § 7 order: deploy first, then the members in any order, then the hub, then open-webui-service's last PR, then the local map. Each task removes what only Open WebUI used and edits the lines that name it; history stays. workbench v0.4.0 has its own plan (`2026-11-17-open-webui-retirement-workbench-v0.4.md`).

**Tech Stack:** Make, Docker Compose, Caddy, Alloy, Python (stdlib tests in deploy and obs-plane, pytest in vllm-service and the hub), Markdown.

**Spec:** `docs/superpowers/specs/2026-11-17-open-webui-retirement-design.md`

## Global Constraints

- One branch per repo, `feat/retire-open-webui`, from that repo's `origin/main`, in a clean worktree outside the repo's main checkout (edge-plane's and workbench's main checkouts hold someone else's uncommitted work: never touch it).
- Versions, set in the release PR: deploy `0.13.0` (`VERSION`), edge-plane `0.18.0` (`VERSION`), vllm-service `0.12.0` (`pyproject.toml`, then `uv lock`), obs-plane `0.16.0` (`VERSION`). The hub and open-webui-service get no version bump. Tags are minted on merge; never tag by hand.
- Never run `make up`, `up-dev`, `nuke`, `bundle` or `bundle-dev`; never touch a real `.env`, `federation.env` or a Docker volume. `make -n …` is fine.
- History stays as it is: `docs/decisions/*` (except the status lines this plan names), dated documents (`docs/20*.md`), everything under `docs/superpowers/`, and the vendored `make/common.mk` comment.
- Done means: `git grep -I -i -n -E "open[-_ ]?webui|OPEN_WEBUI|openwebui" -- . ':!docs/decisions' ':!docs/20*' ':!docs/superpowers' ':!make/common.mk'` prints nothing in the repo, except the lines a task names as kept.
- `.env.example` tags follow deploy's rule T5. Until deploy's PR merges, a member checks its example with deploy's branch: `python3 -B <deploy worktree>/scripts/check_env_examples.py .` must print nothing.
- Docs stay minimal: a removal deletes the lines, it does not explain them. One line in each changelog-like place (README release notes, if a repo keeps them) at most.
- Data confidentiality: synthetic values only; no local paths in anything git sees.
- Single, non-compound `gh` commands; no attribution lines in commits or PR bodies. The owner merges every PR.

## Review Focus

1. A host upgrades deploy while its `federation.env` still sets `OPENWEBUI_DIR=open-webui-service`: the variable is inert, `make -n up` names no open-webui-service (Task 1, test).
2. A host's member `.env` files still hold a retired key (`OPEN_WEBUI_EDGE_SECRET`, `AUDIT_OPENWEBUI_JWT_SECRET`, `OPEN_WEBUI_WORKBENCH_KEY`): `make env-check` does not fail on it; env-sync keeps it as "not in the example" (Task 1, test).
3. edge-plane's `.env` still sets `OPEN_WEBUI_EDGE_SECRET`: compose config stays valid, `check-edge-secrets.sh` still passes, nothing is published on `8443` (Task 2, test).
4. A request reaches the router with stray `X-OpenWebUI-User-Jwt` / `X-OpenWebUI-User-*` headers: it is served, lands in the `service` cache partition, and its audit event names only the caller (Task 3, test).
5. vllm-service's `.env` still sets `AUDIT_OPENWEBUI_JWT_SECRET`: the router starts, and `config.audit` carries no `openwebui_jwt` key (Task 3, test).

---

### Task 1: deploy v0.13.0

**Files:**
- Modify: `Makefile` (the `OPENWEBUI_DIR ?= open-webui-service` block near the top, and every `$(OPENWEBUI_DIR)` in `ENV_DIRS`, `help`, `setup`, `up`, `down`, `ps`, `logs`, `clone`, `pull`, `bundle`, `load`, `prune`; the header comment), `federation.env.example` (the `OPENWEBUI_DIR` note), `env-sync.conf`, `scripts/copy-bundles.sh` (the `copy_project … open-webui-service` call and its comment), `scripts/bundle-exists.sh` (comment), `VERSION`
- Modify (docs): `docs/runbooks/{bring-up,env-sync,audit,releasing,host-audit,volume-reown,upgrade-to-audit-trail}.md`, `docs/tech-stack.md`, `docs/tech-stack.de.md` (the Open WebUI section goes whole), `docs/hardening-migration.md`, `CLAUDE.md`, `README.md`, `docs/README.md`
- Modify (decisions): `docs/decisions/0002-open-webui-lifecycle-member.md` (status line only), `docs/decisions/0005-workbench-shared-tool-tier.md` (one line beside "Extended by")
- Create: `docs/decisions/0008-retire-open-webui.md`, `docs/runbooks/retire-open-webui.md`
- Test: `tests/test_envsync_shared.py`, `tests/test_envsync_conventions.py`, `tests/test_envsync_make.py`, `tests/test_envsync_examples.py`

**Interfaces:**
- Produces: `env-sync.conf` without open-webui-service. After it: `edge-host` keeps edge-plane's source row and obs-plane; `router-key` names `docint, Nextext, translator, ghostwriter`; `workbench-period` names `ghostwriter`; the groups `open-webui-edge`, `openwebui-jwt` and `workbench-key` are gone. Tasks 2–4 tag their examples against this table.

- [ ] **Step 1: Write the failing tests**

In the real-table tests:
- `test_envsync_shared.py`: `GROUP_NAMES` drops `open-webui-edge`, `openwebui-jwt`, `workbench-key`; the row count assertion becomes `29` (from `38`); the workbench set and the `workbench-period` list drop open-webui-service.
- `test_envsync_conventions.py`: the expected tags become `"[shared: docint, Nextext, translator, ghostwriter]"` for the router key and `"[shared: ghostwriter]"` for the retention period.
- `test_envsync_make.py`: the `ps` needle no longer lists open-webui-service; `test_envsync_examples.py`: no `OPENWEBUI_DIR`.
- New, in `test_envsync_make.py`:

```python
def test_a_leftover_openwebui_dir_is_inert(self) -> None:
    """A federation.env that still sets OPENWEBUI_DIR changes nothing: no target names the retired member."""
    out = self.make_n("up", "OPENWEBUI_DIR=open-webui-service")
    self.assertNotIn("open-webui-service", out)
```

- New, in `test_envsync_shared.py` (or the module that runs `env-check` end to end on a temp tree): a member `.env` holding a key its example no longer documents (use `OPEN_WEBUI_EDGE_SECRET=` with a synthetic value in edge-plane's) → `env-check` exits 0 and reports the key as not in the example.

Use the module's existing helpers for running `make -n` and building temp trees; name them as they are.

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: FAIL on the row count, the group names, the tags and the leftover-`OPENWEBUI_DIR` test. The stale-key test may already pass; keep it as a pin.

- [ ] **Step 3: Remove Open WebUI from the Makefile, the table and the bundles**

Delete the `OPENWEBUI_DIR` definition and every use; delete the rows and groups named under Interfaces (a group goes whole, `load_table` rejects a one-row group); delete the `copy_project` call. `VERSION` → `0.13.0`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: OK (the sibling-reading tests skip or pass).

- [ ] **Step 5: The decisions and the runbook**

- `0008-retire-open-webui.md`, shaped like `0004-retire-chorus.md` (Status: accepted (2026-11-17); Context, Decision, Alternatives considered, Consequences). Decision bullets, one per repo, as the spec's § 6; "The repository nos-tromo/open-webui-service is archived." Alternatives: the spec's § 4 B and C. Consequences: twelve repositories; edge-plane publishes `:443` and `:80` only; ADR 0003 applies without Open WebUI's retention sidecar as a source; workbench has two callers.
- `0002`: status becomes `Lapsed (2026-11-17, [0008](0008-retire-open-webui.md))`.
- `0005`: one line beside "Extended by": `Open WebUI's caller lapsed with [0008](0008-retire-open-webui.md).`
- `retire-open-webui.md`, shaped like `retire-chorus.md`, the spec's § 6.1 steps 1–4, with these commands on the host: `make down` and `make nuke` in `../open-webui-service`; `make env ENV_GENERATE=1` in deploy where workbench's `.env` holds only `OPEN_WEBUI_WORKBENCH_KEY`; then delete `../open-webui-service` and its `*.tar.gz`. Note that obs-plane v0.16.0 rolls out only after the host's Open WebUI is stopped.
- `docs/README.md` indexes 0008 and the runbook, and marks 0002 lapsed.

- [ ] **Step 6: The docs**

Remove Open WebUI from the files listed above: as a lifecycle member, a bring-up step, an env-sync group, an audit source, a bundle, a volume to re-own, a release member. In `upgrade-to-audit-trail.md`, follow how chorus was removed there. The tech-stack pages lose the Open WebUI section and count twelve repositories.

- [ ] **Step 7: Verify**

Run, from the worktree:
```bash
shellcheck scripts/*.sh
make help >/dev/null && make -n ps >/dev/null
uvx yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
python3 -B -m unittest discover -s tests -p 'test_*.py'
make -n up | grep -c open-webui-service   # expected: 0
```
Then the Global Constraints' grep. Expected: no output beyond history.

- [ ] **Step 8: Commit, push, PR**

```bash
git add -A
git commit -m "feat: retire Open WebUI from the federation; release v0.13.0"
git push -u origin feat/retire-open-webui
```
PR title: `feat: retire Open WebUI; release v0.13.0`. Body: what goes, the new ADR and runbook, the merge order (this first; then edge-plane, workbench, vllm-service, obs-plane).

---

### Task 2: edge-plane v0.18.0

**Files:**
- Modify: `caddy/Caddyfile` (the `@webui` matcher and its `redir … :8443` block; the whole `https://{$EDGE_HOST}:8443` site), `docker/compose.yaml` (`OPEN_WEBUI_EDGE_SECRET`, the `"8443:8443"` port and its comment, `open-webui` in `NO_PROXY`/`no_proxy`), `.env.example` (the secret's block; `EDGE_HOST`'s tag and comment), `scripts/check-edge-secrets.sh` (the `open-webui:8080 OPEN_WEBUI` upstream), `landing/index.html` (the Open WebUI tile), `info/help.example.md`, `info/ai.example.md`, `info/privacy.example.md` (their Open WebUI rows), `audit/edge_audit.py` (`request_target()` docstring), `Makefile` (`:443/:80/:8443` → `:443/:80`), `VERSION`
- Modify (docs): `docs/identity-contract.md`, `docs/configuration.md`, `docs/audit.md`, `README.md`, `CLAUDE.md`, `docs/decisions/0002-open-webui-dedicated-port.md` (status line only)
- Test: `audit/tests/test_edge_audit.py`

**Interfaces:**
- Consumes: Task 1's table: `EDGE_HOST` is tagged `[required] [shared: obs-plane]`.

- [ ] **Step 1: Write the failing tests**

- `audit/tests/test_edge_audit.py`: rename `test_dedicated_site_keeps_its_port` to `test_an_explicit_port_is_kept` and use a synthetic port that is not `8443` (e.g. `9443`); the `:8443` case in the parametrised list becomes the same port. The assertion stays: the recorded target keeps `host:port`.
- A check for the published ports, as a shell assertion in the verify step (edge-plane has no compose test module): `docker compose -f docker/compose.yaml config --format json` lists exactly the published ports `443` and `80`.

- [ ] **Step 2: Remove the site, the port, the secret, the tile and the rows**

As listed under Files. `check-edge-secrets.sh` needs no count change (it prints `len(expected)`). `VERSION` → `0.18.0`. ADR 0002's status: `Superseded (2026-11-17) by deploy ADR 0008`.

- [ ] **Step 3: The docs**

Remove the `:8443` site, `/webui`, the Open WebUI secret and the guard paragraph from the listed docs; "the only published ports" become `:443` and `:80`.

- [ ] **Step 4: Verify**

The repo's local CI equivalents (`CLAUDE.md`, "Local CI equivalents"), with a synthetic `.env` copied from `.env.example` in the worktree (never the real one):
```bash
docker compose --env-file .env -f docker/compose.yaml config --quiet
docker run --rm -e EDGE_HOST=127.0.0.1 -v "$PWD/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v "$PWD/caddy/conf.d:/etc/caddy/conf.d:ro" <the pinned caddy image from docker/compose.yaml> \
  caddy validate --config /etc/caddy/Caddyfile
./scripts/check-edge-secrets.sh                      # expected: ok: 6 upstreams
./scripts/check-tokens-vendor.sh
./scripts/check-no-literal-dimensions.sh
python3 -m unittest discover -s audit/tests -t audit
# Review Focus 3: a leftover secret changes nothing, and only 443 and 80 are published
OPEN_WEBUI_EDGE_SECRET=synthetic-leftover-secret-value-0000000000 docker compose --env-file .env -f docker/compose.yaml config --format json \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); print(sorted({str(p["published"]) for s in c["services"].values() for p in s.get("ports", [])}))'
# expected: ['443', '80']
python3 -B <deploy worktree>/scripts/check_env_examples.py .   # expected: no output
```
Then the Global Constraints' grep. `scripts/smoke.sh` runs in CI's smoke job (it needs `make up-dev`, which agents never run).

- [ ] **Step 5: Commit, push, PR**

```bash
git add -A
git commit -m "feat: retire Open WebUI's :8443 site and tile; release v0.18.0"
git push -u origin feat/retire-open-webui
```

---

### Task 3: vllm-service v0.12.0

**Files:**
- Modify: `docker/router/router_audit.py` (remove `OPENWEBUI_JWT_HEADER`, `OPENWEBUI_CHAT_HEADER`, `OPENWEBUI_ISSUER`, `_OPENWEBUI_PLAIN_HEADERS`, `JWT_LEEWAY_S`, `MAX_JWT_LENGTH`, `_B64URL`, `_number`, `_b64url`, `verify_openwebui_token`, `_verify_openwebui_token`, `_jwt_secret`, the `openwebui_jwt` key of `_mode()`; `_identity` returns the caller and `{}`; drop the then-unused `base64`, `json`, `hmac` imports — keep `hashlib`), `docker/router/cache_salt.py` (the Open WebUI imports, secret and partitions; `_partition` answers `scope:<x>` or `service`), `docker/litellm.config.yaml` (two comments; keep the `encoding_format: float` pin), `.env.example` (the `AUDIT_OPENWEBUI_JWT_SECRET` block; `OPENAI_API_KEY`'s comment and tag), `pyproject.toml` (`version = "0.12.0"`), `uv.lock`
- Modify (docs): `docs/audit.md` (the "Open WebUI's users" section and its table row), `docs/configuration.md`, `docs/architecture.md`, `docs/api-reference.md`, `docs/README.md`, `README.md`, `CLAUDE.md`
- Test: `eval/tests/test_router_audit.py`, `eval/tests/test_cache_salt.py`

**Interfaces:**
- Consumes: Task 1's table: `OPENAI_API_KEY` is tagged `[shared: docint, Nextext, translator, ghostwriter]` (keep its other tags as they are).
- Kept unchanged: `X-Cache-Scope` partitioning, `X-Request-ID` handling (`audit_runtime.REQUEST_ID_HEADER`, `clean_request_id`), `_header`, `_proxy_headers`.

- [ ] **Step 1: Write the failing tests**

In `eval/tests/test_router_audit.py` (use the module's existing helpers to build the audit hook and a call):
```python
def test_stray_openwebui_headers_name_only_the_caller() -> None:
    """A call with leftover X-OpenWebUI-* headers is recorded as the caller alone, with no identity criteria."""
    # headers: X-OpenWebUI-User-Jwt with any synthetic string, X-OpenWebUI-User-Email: someone@example.org
    # expected: the event's actor is the caller; criteria hold no user/email/identity keys; no error.

def test_a_leftover_jwt_secret_changes_nothing() -> None:
    """AUDIT_OPENWEBUI_JWT_SECRET in the environment is ignored; config.audit has no openwebui_jwt key."""
    # expected: the mode/criteria dict equals {"call_types": [...]} as for an environment without it.
```
In `eval/tests/test_cache_salt.py`:
```python
def test_stray_openwebui_headers_fall_into_the_service_partition() -> None:
    """Without X-Cache-Scope, a call carrying X-OpenWebUI-* headers is partitioned as service."""
```
And a pin that the existing scope partitions keep their salts: the salts of `scope:docint` and `service` equal the values the current tests already assert (reuse them).

Remove the tests of the removed code (the JWT tests, `_token`, `SECRET`, `OPEN_WEBUI`), and drop the `x-openwebui-*` keys from the `planted` dicts.

- [ ] **Step 2: Run them to see the new ones fail**

Run: `uv run --group eval pytest eval/tests/`
Expected: FAIL on the leftover-secret test (`openwebui_jwt` still in the mode) and the cache test (a user partition); the stray-header audit test fails on the identity criteria.

- [ ] **Step 3: Remove the attribution and the partitions**

As listed under Files. Version `0.12.0`; `uv lock`.

- [ ] **Step 4: Run the tests to see them pass**

Same command. Expected: all pass.

- [ ] **Step 5: The docs**

Remove the JWT attribution and `AUDIT_OPENWEBUI_JWT_SECRET`. Where the docs list the callers that send `X-Cache-Scope`, add Ghostwriter. `CLAUDE.md`'s release note says "bump VERSION file" though this repo versions in `pyproject.toml`: fix that line while there.

- [ ] **Step 6: Verify**

```bash
make verify
uv run --group eval pytest eval/tests/
python3 -B <deploy worktree>/scripts/check_env_examples.py .   # expected: no output
```
Then the Global Constraints' grep. vllm-service#105 (an open OCR PR) touches the same files; do not rebase onto it.

- [ ] **Step 7: Commit, push, PR**

```bash
git add -A
git commit -m "feat: drop Open WebUI's user attribution at the router; release v0.12.0"
git push -u origin feat/retire-open-webui
```

---

### Task 4: obs-plane v0.16.0

**Files:**
- Modify: `alloy/config.alloy` (the open-webui `stage.match` block; the "These two carry no chain" comment becomes one), `alloy/lanes-test/harness.alloy` (its open-webui line), `alloy/lanes-test/expected.txt` (regenerated), `audit/verifier.py` (the `("open-webui-service", "retention")` entry of the default emitters), `.env.example` (the `AUDIT_EMITTERS` example), `grafana-audit/dashboards/audit-trail.json` (the example text), `VERSION`
- Delete: `alloy/lanes-test/lines/owui.txt`
- Modify (docs): `docs/audit.md`, `docs/coverage.md`, `docs/grafana-access.md`, `docs/hardening.md` and `CLAUDE.md` (describe `grafana-guard` directly, not by Open WebUI's guard), `README.md`
- Test: `audit/tests/test_verifier.py`

**Interfaces:**
- Consumes: nothing from other tasks. Rolls out on a host only after its Open WebUI is stopped (Task 1's runbook says so).

- [ ] **Step 1: Write the failing test**

In `audit/tests/test_verifier.py`, replace the open-webui expectation (around its default-emitter test) with:
```python
def test_open_webui_is_no_listed_source(self) -> None:
    """The retired retention sidecar is not among the default emitters."""
    self.assertNotIn(("open-webui-service", "retention"), DEFAULT_EMITTERS)
```
(Use the module's own name for the default list.)

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --no-project --python 3.12 --with pytest python -m pytest audit/tests -q`
Expected: FAIL.

- [ ] **Step 3: Remove the lane, the case and the emitter**

As listed under Files. Regenerate the lanes test's expectation with `scripts/check_alloy_lanes.sh --update`, and check its diff drops only the open-webui lines. `VERSION` → `0.16.0`.

- [ ] **Step 4: Verify**

```bash
uv run --no-project --python 3.12 --with pytest python -m pytest audit/tests -q
./scripts/check_alloy_lanes.sh
docker run --rm -v "$PWD/alloy/config.alloy:/cfg/config.alloy:ro" <the pinned alloy image from CLAUDE.md> fmt /cfg/config.alloy > /dev/null
```
Expected: all pass. Then the Global Constraints' grep. The query-string redaction, `lines/docint-frontend.txt` and `edge-net` stay.

- [ ] **Step 5: Commit, push, PR**

```bash
git add -A
git commit -m "feat: drop Open WebUI's audit lane and emitter; release v0.16.0"
git push -u origin feat/retire-open-webui
```

---

### Task 5: the hub, nos-tromo/.github

**Files:**
- Modify: `profile/claims.toml` (delete the claim `open-webui-pinned-image`; in `edge-only-port-publisher`, replace `"open-webui-service"` with `"ghostwriter"` in `in`), `profile/README.md`, `README.md` (the `infra-validation.yml` row's repo list), `docs/workflows.md`, `docs/vendored-files.md`, `scripts/validate_bundle_lib.py` and `scripts/validate_make_common.py` (docstring examples: `open-webui` → `deploy` / `data-plane`)
- Do not modify: `configs/make-common/common.mk` and the fixtures that copy it (`tests/fixtures/mk-*/make/common.mk`).

**Interfaces:**
- Consumes: Ghostwriter's files on its `main` (for the new claim).

- [ ] **Step 1: The page**

In `profile/README.md`: the diagram's `webui` node becomes `ghostwriter["ghostwriter<br/>chat assistant"]` in the Applications subgraph, with `ghostwriter -->|inference-net| vllm`; the open-webui-service row leaves the Platform table; a row joins the Applications table:

`| **[ghostwriter](https://github.com/nos-tromo/ghostwriter)** | Agentic chat assistant — plain chat with a per-message reasoning switch; a file dropped into a chat goes to the shared tool tier, and the result downloads in the chat. |`

- [ ] **Step 2: The claim for the new row**

Add one `[[claim]]` for the Ghostwriter row, in the Applications section of `claims.toml`: `repo = "ghostwriter"`, a `quote` taken verbatim from the row, a `says`, and a `file`/`matches` pair that holds in Ghostwriter's `main` (for example the reasoning switch's setting or the workbench network in `docker/compose.yaml`). Follow the existing claims' shape.

- [ ] **Step 3: Verify**

From the hub worktree, with a ghostwriter checkout as a sibling (the workspace layout):
```bash
python3 scripts/validate_profile_claims.py --members-root .. --member .github=.
python3 scripts/validate_profile_claims.py --list-repos      # expected: ghostwriter listed, open-webui-service not
uv run --with pytest python -m pytest tests/test_profile_claims.py -q
VER=$(grep '^ruff:' configs/python-strict/precommit-versions.yaml | awk '{print $2}' | tr -d '"' | sed 's/^v//')
uvx "ruff@$VER" check  --config configs/python-strict/ruff.toml scripts/
uvx "ruff@$VER" format --config configs/python-strict/ruff.toml --check scripts/
```
Expected: all clean. Then `git grep -I -i -n -E "open[-_ ]?webui|openwebui" -- . ':!docs/superpowers' ':!configs/make-common' ':!tests/fixtures'` prints nothing.

- [ ] **Step 4: Commit, push, PR**

```bash
git add -A
git commit -m "docs(profile): Ghostwriter in place of Open WebUI"
git push -u origin feat/retire-open-webui
```

---

### Task 6: open-webui-service, the last PR

Runs after Tasks 1–5 and the workbench plan have merged.

**Files:**
- Modify: `README.md` (whole file becomes the retirement note)

- [ ] **Step 1: The note**

`README.md` becomes, in full:

```markdown
# open-webui-service

**Retired (2026-11).** Open WebUI was replaced by [Ghostwriter](https://github.com/nos-tromo/ghostwriter)
and never ran in production. This repository is archived and kept as history; see deploy's
[ADR 0008](https://github.com/nos-tromo/deploy/blob/main/docs/decisions/0008-retire-open-webui.md).
```

`CLAUDE.md` and the code stay as they are, as history.

- [ ] **Step 2: Verify, commit, push, PR**

```bash
python3 -m unittest discover -s retention/tests -t retention   # unchanged code; expected: OK
git add README.md
git commit -m "docs: retired in favour of Ghostwriter"
git push -u origin feat/retire-open-webui
```
After the owner merges it, the owner archives the repository (`gh repo archive nos-tromo/open-webui-service`), or the controller does on the owner's explicit OK.

---

### Task 7: the workspace map, `infra/CLAUDE.md`

A local file, not a git repository: no branch, no PR. Runs last.

- [ ] **Step 1: Edit the map**

- The intro and heading count twelve repositories; the `open-webui-service` row leaves the table.
- The diagram: `open-webui*` leaves the app backends; the `workbench-net` block has two callers (`docint-backend`, `ghostwriter-backend`); the `edge-net` block drops `· open-webui` and `:8443`.
- The invariants: *Tool execution* has two callers and drops Open WebUI's terminal door and "Open WebUI's own tools … stay switched off"; *Four network seams* drops open-webui; *Production entry* names `:443` and `:80` only and drops the Open WebUI sub-path sentence; the bring-up paragraph drops Open WebUI.
- "Where to look": the `open-webui-service/CLAUDE.md` entry goes; the edge-plane entry drops the `:8443` ADR sentence; the workbench entry drops the terminal door.
- The release-workflow and bespoke-Makefile lists drop `open-webui-service`.

- [ ] **Step 2: Verify**

```bash
grep -n -i -E "open[-_ ]?webui|openwebui|8443" CLAUDE.md
```
Expected: no output. `grep -n '/Users/\|/home/' CLAUDE.md` shows only the two placeholder lines of the confidentiality rule.
