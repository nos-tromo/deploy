# Design: retiring Open WebUI from the federation

Date: 2026-11-17
Status: approved by the owner

## 1. Why

Ghostwriter v0.1.0 replaces Open WebUI ([ADR 0007](../../decisions/0007-ghostwriter-replaces-open-webui.md)). Since open-webui-service v0.15.0 and edge-plane v0.17.0, Open WebUI is marked as being retired. The Ghostwriter design left the removal to this phase 2 (its § 6.1): the repo and the `open-webui-data` volume, workbench's terminal door, edge-plane's `:8443`, `/webui` and ADR 0002, deploy's rows, obs-plane's rules and vllm-service's JWT attribution.

**Open WebUI never ran in production.** No user chats need to be kept or exported, no notice period applies, and no audit chain from operation has to stay verifiable. Everything it holds can be deleted. The removal follows the retirement of chorus ([ADR 0004](../../decisions/0004-retire-chorus.md), [retire-chorus.md](../../runbooks/retire-chorus.md)).

## 2. Goals, non-goals, anti-goals

### Goals

- No repo of the federation starts, routes, syncs, observes or documents Open WebUI as a live member. Two exceptions: history (ADRs, dated design documents, `docs/superpowers/`), and the comment in the hub's `make/common.mk` (§ 6.6).
- workbench's terminal door and its `open-webui` caller are gone.
- edge-plane publishes only `:443` and `:80`; `:8443` is gone.
- The `open-webui-data` volume is deleted on every host, and the repo nos-tromo/open-webui-service is archived.

### Non-goals

- An export or import of Open WebUI chats.
- A redirect from `/webui` or `:8443` to `/ghostwriter/`. Nothing ran in production, so there are no bookmarks.
- A refresh of the hub's profile page beyond replacing Open WebUI with Ghostwriter.

### Anti-goals

The removal fails, even if everything runs, when:
- Ghostwriter or docint lose a workbench tool, or a tool's name, description or schema changes;
- a `main` goes red;
- history is deleted (ADRs, specs, plans);
- an agent runs `make nuke`, `up`, `up-dev` or `bundle`. Those stay with the owner.

## 3. Constraints

- One release PR per repo, with its version bump; the release tag is minted on merge. The owner merges every PR.
- Branch and PR, never a commit to `main`.
- `.env.example` follows deploy's shared format; rule T5 requires every `[shared: …]` tag to match deploy's `env-sync.conf` exactly. No member CI runs that check: only deploy's CI does, against its own table. The check runs locally, against the sibling checkouts (`python3 -B ../deploy/scripts/check_env_examples.py .`).
- `make/common.mk` and `scripts/bundle-lib.sh` are vendored from the hub and drift-checked against the hub revision each repo pins.
- Docs stay minimal: a removal deletes lines, it does not explain them.

## 4. Approaches

- **A (chosen): one pass.** Each repo removes its share in one release PR, in an order that keeps every check green. No dead code is left behind.
- **B: unhook first, clean up later.** Small PRs take Open WebUI out of bring-up and the portal first; a second round removes the dead code. Two rounds of PRs in deploy and edge-plane, and dead code until the second.
- **C: as A, but keep workbench's terminal door for now.** Without `OPEN_WEBUI_WORKBENCH_KEY` the door is already closed. It would save the largest part of the work, but leave an unused door, a contract stack against the Open WebUI image and its Dependabot bumps in the code.

## 5. The owner's decisions

1. Everything may be deleted: the app never ran in production (2026-11-17).
2. Approach A.
3. The repo nos-tromo/open-webui-service is **archived**, not deleted, after a last README change. The links from the ADRs keep working.

## 6. Design

### 6.1 deploy v0.13.0

- **Makefile.** `OPENWEBUI_DIR` and every use of it go: `ENV_DIRS`, `help`, `setup`, `up`, `down`, `ps`, `logs`, `clone`, `pull`, `bundle`, `load`, `prune`, and the header comment. `federation.env.example` drops its `OPENWEBUI_DIR` note.
- **env-sync.conf.**
  - The open-webui-service rows leave the groups `edge-host`, `router-key` and `workbench-period`.
  - The groups `open-webui-edge`, `openwebui-jwt` and `workbench-key` go whole: a group needs at least two rows, and in each of them open-webui-service was the only partner.
  - The tests that read the real table change in the same PR: `tests/test_envsync_shared.py`, `test_envsync_conventions.py`, `test_envsync_make.py`, `test_envsync_examples.py`.
- **Bundles.** `scripts/copy-bundles.sh` no longer copies open-webui-service; `scripts/bundle-exists.sh` drops its mention.
- **Decisions.**
  - A new **ADR 0008 "Retire Open WebUI"**, shaped like 0004.
  - ADR 0002 (Open WebUI as a lifecycle member) becomes "Lapsed (0008)".
  - ADR 0005 gains a line: its first caller, Open WebUI, lapsed with 0008.
  - ADRs 0003, 0006 and 0007 stay as they are.
- **Runbook.** A new `docs/runbooks/retire-open-webui.md`, shaped like `retire-chorus.md`, for every host:
  1. take `OPENWEBUI_DIR` out of `federation.env`, if it is set;
  2. in open-webui-service, `make down`, then `make nuke` (interactive confirm);
  3. delete the checkout, its `.env` and its tarballs;
  4. where workbench's `.env` sets only `OPEN_WEBUI_WORKBENCH_KEY`, set `GHOSTWRITER_WORKBENCH_KEY` (`make env ENV_GENERATE=1`), since workbench v0.4.0 starts only with a key of a remaining caller.
- **Docs.** The runbooks (`bring-up`, `env-sync`, `audit`, `releasing`, `host-audit`, `volume-reown`, `upgrade-to-audit-trail`), `tech-stack.md` and `tech-stack.de.md` (the Open WebUI section goes), `hardening-migration.md`, `CLAUDE.md`, `README.md`, `docs/README.md`.

### 6.2 edge-plane v0.18.0

- **Caddy.** The `:8443` site and the `@webui` matcher with its redirect go.
- **Compose.** The `8443` port, `OPEN_WEBUI_EDGE_SECRET`, and `open-webui` in `NO_PROXY` go. The Makefile names `:443/:80`.
- **`.env.example`.** The secret's block goes; `EDGE_HOST` is tagged `[required] [shared: obs-plane]`.
- **`scripts/check-edge-secrets.sh`.** The Open WebUI upstream goes; the check reports six upstreams.
- **Portal.** The Open WebUI tile in `landing/index.html` and its rows in `info/help.example.md`, `ai.example.md` and `privacy.example.md` go.
- **Audit.** `audit/edge_audit.py` keeps recording a request's port; its docstring and its test describe an explicit port in general, not the Open WebUI site.
- **Docs.** `identity-contract.md`, `configuration.md`, `audit.md`, `README.md`, `CLAUDE.md`. ADR 0002 (the dedicated port) becomes "Superseded by deploy ADR 0008".

### 6.3 workbench v0.4.0

The terminal door and the `open-webui` caller go. docint's MCP door with inline data, and Ghostwriter's MCP door with its four file routes, stay exactly as they are.

- **Removed.**
  - `workbench/api/contract.py`: the door's handshake routes and `system_prompt.md`. Its `/healthz` moves to a router of its own; the compose healthcheck and `make health` use it.
  - The terminal-only file routes: `/files/read`, `/files/delete`, `/files/mkdir`, `/files/move`, and the tool `display_file`.
  - The REST tool routes under `/tools/…` and `openapi.tool_openapi`.
  - In `auth.py`: the door `terminal`, the `open-webui` row, and the guards only that door uses.
  - `OPEN_WEBUI_WORKBENCH_KEY`, and the audit operations only the door emits.
  - `contract/`, `tests/contract/`, the CI job `contract`, the Dependabot entry for `/contract`, and the Make target `test-contract`.
- **Kept.** Ghostwriter's file routes (`/files/list`, `/files/upload`, `/files/view`, `/files/chat`) with their hardening; the `workspace` file form and its confinement; the `default` folder (docint's calls without a chat); retention; the executor; the MCP door.
- **The tool specs.** Ghostwriter's MCP tools are built today from the REST tool routes (`tools_mcp.workspace_tools` → `registry.workspace_specs` → `openapi.tool_routes`). They are built instead from an explicit table: name, summary, description, request and response models, and the function. Before the change, a golden test records `tools/list` for the callers `ghostwriter` and `docint`; after it, the output must be byte-identical.
- **Tests.** Tests that exercise shared code through the terminal key are ported to a remaining key, not deleted: the tool tests, the isolation stack (it runs `run_python`) and the load test. The isolation stack's case of one workspace caller against another goes, as Ghostwriter is the only workspace caller left; its cases between users and between chats stay. The isolation and load stacks run in CI only (they need cgroup v2 and Docker). Shared helpers in `tests/api/test_files.py` move to `conftest.py`; the shared tests in `test_contract.py` move before the file goes.
- **Docs.** `.env.example` (the retention period's tag becomes `[shared: ghostwriter]`), `docs/*.md`, `README.md`, `CLAUDE.md`.
- **On a host,** workbench v0.4.0 refuses to start when `OPEN_WEBUI_WORKBENCH_KEY` is its only key. The runbook (§ 6.1) covers it.

### 6.4 vllm-service v0.12.0

- **Removed.** The Open WebUI JWT attribution in `docker/router/router_audit.py` (the token check, its constants and helpers, and `openwebui_jwt` in `config.audit`); the partitions `user:<email>` and `open-webui` in `docker/router/cache_salt.py`; `AUDIT_OPENWEBUI_JWT_SECRET` in `.env.example`; their tests in `eval/tests/`.
- **Kept.** `X-Cache-Scope` and `X-Request-ID`. The other callers' partitions and salts do not change, so their caches stay warm.
- **Docs.** `docs/audit.md`, `configuration.md`, `architecture.md`, `api-reference.md`, `README.md`, `CLAUDE.md`, and the comments in `docker/litellm.config.yaml`. Where the docs list the callers that send `X-Cache-Scope`, Ghostwriter joins them.

### 6.5 obs-plane v0.16.0

- **Removed.** The Open WebUI lane in `alloy/config.alloy`, its case in `alloy/lanes-test/`, and the emitter `open-webui-service` / `retention` in `audit/verifier.py` with its test.
- **Docs.** `docs/audit.md`, `coverage.md`, `grafana-access.md`, `hardening.md`, `README.md`, `CLAUDE.md`, `.env.example`, and the example text in the audit dashboard.
- obs-plane has no scrape job, probe or alert for Open WebUI.

### 6.6 The hub, nos-tromo/.github

- **`profile/claims.toml`.** The claim `open-webui-pinned-image` and the `open-webui-service` entry of `edge-only-port-publisher` go.
- **`profile/README.md`.** Open WebUI is replaced by Ghostwriter, in the diagram and the tables. The validator wants every table row backed by a claim, so the Ghostwriter row gets one, checked against Ghostwriter's own files.
- **Docs.** The mentions in `README.md`, `docs/workflows.md`, `docs/vendored-files.md` and two validator docstrings.
- **`configs/make-common/common.mk` keeps its comment** naming open-webui-service. A change would make seven repos re-vendor the file at once.

### 6.7 open-webui-service

A last PR turns the README into a retirement note: retired in 2026-11, replaced by Ghostwriter, archived. No release. Then the owner archives the repo on GitHub.

### 6.8 `infra/CLAUDE.md`

The local workspace map (not in git) drops Open WebUI from the table, the diagram, the invariants, "Where to look" and the bring-up order, and counts twelve repositories. "Production entry" names `:443` and `:80` only.

## 7. Delivery order

0. deploy: this spec and one plan per repo PR.
1. **The owner, on each host:** in open-webui-service, `make down` and `make nuke`. Until deploy v0.13.0 is there, `OPENWEBUI_DIR=` in `federation.env` keeps deploy's targets off it.
2. deploy v0.13.0. It goes first, so each member's local T5 check runs against a `main` without the Open WebUI rows.
3. edge-plane v0.18.0, workbench v0.4.0, vllm-service v0.12.0, obs-plane v0.16.0, in any order. obs-plane rolls out on a host only after step 1, or its verifier reports the retention sidecar as an unlisted source.
4. The hub.
5. open-webui-service's last PR, then the archive.
6. `infra/CLAUDE.md`, and the local checkout of open-webui-service, once the owner agrees.

## 8. Residual risks

- **A host keeps only the old workbench key.** workbench v0.4.0 then does not start; the runbook names the fix.
- **A stale comment.** The hub's `common.mk` still names open-webui-service as a pulled-image member until it changes for another reason.
- **The profile page stays stale elsewhere.** Only the Open WebUI part is replaced.
- **An open PR in the same files.** vllm-service#105 (an OCR backend) edits `.env.example`, `CLAUDE.md`, `README.md` and `docker/litellm.config.yaml` too. Whichever merges second rebases.

## 9. End-to-end checks (after the merges)

1. deploy's `make up` brings the federation up without Open WebUI, and `make -n up` names no open-webui-service.
2. Ghostwriter: a chat with an attached file runs workbench's tools and offers the result for download.
3. docint's `run_python` analysis still works.
4. Nothing listens on `:8443`.
5. obs-plane reports no `AuditChainProblem`.
6. `grep -riE 'open[-_ ]?webui|openwebui'` across the repos finds only history and the `common.mk` comment.

## 10. Calls made in the design

These were made by the designer, not the owner:

- workbench's tool specs come from an explicit table, checked against a golden `tools/list`, instead of unreachable routes.
- `/healthz` moves to a router of its own.
- No redirect from `/webui` or `:8443`.
- The hub's `common.mk` comment stays.
- The profile page only swaps Open WebUI for Ghostwriter.
- A new ADR 0008 and a runbook, after the chorus retirement; 0002 lapses, 0005 gains a line.
- This spec lives in deploy, because it spans the federation.

## 11. Left to the plans

The plans settle:
- the exact lines in each file, from each repo's own search;
- the shape of workbench's tool table and the golden test's fixture;
- which shared workbench tests move where;
- the Ghostwriter claim on the hub page.

There is one plan per PR, in the order of § 7.
