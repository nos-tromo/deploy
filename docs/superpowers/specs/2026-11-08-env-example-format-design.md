# Design: one format for every `.env.example`

Date: 2026-11-08
Status: approved

## Problem

The nine example files — eight members plus deploy's `federation.env.example` — each have their own shape:

| Shape | Files |
|---|---|
| Numbered banners | docint |
| Banners without numbers | Nextext |
| Plain headings | vllm-service |
| `# ---- … ----` dividers | data-plane |
| No structure at all | obs-plane, edge-plane, open-webui-service, translator, `federation.env.example` |

They also follow two conventions. Some show defaults as active lines (obs-plane, edge-plane, open-webui-service, data-plane, deploy); the others keep them commented.

As a result, an operator reading any file first has to search for where things are.

Since deploy v0.7.0, `make env` rebuilds every host `.env` from its example. One format in the examples therefore reaches every `.env` automatically.

## Decision

Every example follows one standard: a fixed header, fixed sections in a fixed order, one rule for active versus commented lines, and fixed tags.

A checker in deploy (`scripts/envsync/conventions.py`) enforces it. Its tests run against deploy's own `federation.env.example` everywhere, and against the sibling member examples wherever they sit next to deploy.

## Goals

- In every file, the same kind of setting is in the same place under the same heading.
- What a host must set is visible at a glance: the `Required` section, `[required]` tags, active lines.
- Every commented value is the real default, so reading the example tells the truth.
- No host changes behaviour when `make env` brings its `.env` into the new format.

## Non-goals

- Renaming keys for consistency (e.g. `INFERENCE_NET` vs `INFERENCE_NETWORK`).
- Fixing the 8501 dev-port clash between Nextext and translator.
- A CI validator in the hub (possible later; the checker is written so it can be reused).

## The standard

### Header

The same in every file; `<env>` / `<example>` are `.env` / `.env.example`, or `federation.env` / `federation.env.example` in deploy. Member-specific notes may follow it, as comment lines.

```
# <member> — example configuration
#
# deploy's `make env` writes <env> from this file and keeps it in step
# (by hand: cp <example> <env>).
#
#   KEY=value     set per host
#   # KEY=value   optional; the value shown is the default
#
# Tags, on the line above a setting:
#   [required]          must be set on every host
#   [secret]            never printed; your value never follows the example
#   [shared: M [KEY]]   same value as member M (its KEY); make env fills it
#   [dev]               read only by the dev overlay (make up-dev)
```

### Sections

Each section opens with a banner of three lines: a rule (`# ` followed by 77 `=`, 79 columns in all), the section title, and the rule again.

Titles are fixed and appear in this order. A section a member does not need is left out.

1. `Required`
2. `Auth & identity`
3. `Network & proxy`
4. `Inference`
   - For apps: provider, endpoints, models.
   - For vllm-service: model ids, revisions, HF flags.
   - Absent in data-plane, obs-plane and edge-plane.
5. `<member>`
   - The app- or layer-specific section, titled with the directory name (`deploy` in `federation.env.example`).
   - Subsections are written `# --- <topic> ---`.
6. `Audit & retention`
7. `Development only`

### Values

- **Active lines** are only for values that must be set per host.
  - They are empty, never placeholders.
  - They always sit in `Required` and carry `[required]`.
- **Everything else is commented** and shows the real default of the production compose shape.
  - A `[dev]` key shows the dev overlay's value.
  - A secret never shows a value; the comment above states its default.
- **Defaults that depend on the deployment shape or the provider** are written as indented prose: `#   KEY=value`.
- **Each key has at most one `# KEY=` line.** Prose never starts with `KEY=` right after `# `.
- **References come after their target.** A key referenced by another default appears above it.

### Tags

Tags go on one line directly above the setting, in this order:

- **`[required]`** marks exactly the active lines, which are exactly the settings in `Required`.
- **`[secret]`** marks exactly the keys `dotenv.is_secret_like` names.
- **`[shared: …]`** marks exactly the keys that have a row in `env-sync.conf`.
  - A row that is not the group's source names only the source.
  - The source names all other rows.
  - A partner's key is named only when it differs.
- **`[dev]`** marks exactly the settings in `Development only`.

### Form

- English, at most 79 columns. A value with no blank in it may run longer (a digest, for example).
- Inline comments are allowed only on commented lines that carry a value. Never:
  - on active lines;
  - after an empty value;
  - in `federation.env`;
  - on keys a member Makefile reads with `grep '^KEY=' .env | cut -d= -f2`.

### federation.env.example

- Only the `deploy` section, and no active line.
- Every commented value equals the Makefile's `?=` default.
- The one-shot switches show `# KEY=`, with their meaning in the comment.

## The principle for every conversion

The value hosts run today wins. When a line that is active today becomes commented, its effective default — in the compose file, the Makefile or the code — must equal today's example value. Where it does not, the default is aligned, so that `make env` changes no running host. Two checks guard this:

- **Checker rule X3:** every simple compose default `${K:-v}` equals the commented value.
- **A dry run for an untouched host:** the new example, with the stored copy and the `.env` both set to the old example. Every line that would be followed or unset must keep its effective value.

## Decisions made with the user

- Fixed sections in a fixed order, unnumbered.
- One active/commented rule.
- Fixed tags.
- A checker in deploy.
- The router defaults for the apps live in their compose files: `INFERENCE_PROVIDER=vllm` and `OPENAI_API_BASE=http://vllm-router:4000/v1` for docint, Nextext and translator. Runs outside Docker keep their code defaults.
- Shared sources stay empty in the examples, with two guards:
  - deploy's shared pass treats an empty source as unset;
  - vllm-service gets a compose `:?` check, so the router never starts without a master key.

## Decisions made by the builder (to check while reading)

1. The checker ships in its own deploy PR after #70, as v0.8.0.
2. **`is_secret_like` counts `TOKEN` only as a whole name segment** (`(^|_)TOKENS?(_|$)`).
   - `CTX`, `CONTEXT` and `WINDOW` join the count words.
   - This removes five false positives, such as `TOKENIZERS_PARALLELISM`.
3. Every variable a compose file interpolates is documented (rule X5), except `*_VERSION`.
4. `AUDIT_EDGE_SECRET` is `[required]` on both sides, and edge-plane's compose gets `:?`. The per-app edge secrets and `GRAFANA_EDGE_SECRET` stay optional.
5. `OPEN_WEBUI_HOST_PORT` moves once from 8080 to its compose default 3000 on dev hosts. This ends the clash with docint's 8080; the member PR announces it.
6. There is no `[build]` tag. Build-time keys say "needs `make build`" in prose.
7. `TEXT_MODEL` is `[required]` where the code refuses to run without it. There is no new shared group for it for now.
8. For docint's `CLIP_API_KEY` / `NER_API_KEY` / `TOPICS_API_KEY` and `NEXTEXT_API_BASE`, the example documents what the code actually does. The code does not change.
9. **Keys and lines that do not fit a normal setting:**
   - Keys that compose fixes (paths, `AUDIT_OUTPUT`) go to `Development only`, noted "outside Docker only".
   - Per-run keys (`AUDIT_CLI_USER` and the like) become prose.
10. Nextext and vllm-service follow once their open feature branches have merged.

## Rollout

1. **deploy** (`feat/env-example-format`, v0.8.0):
   - the checker and its tests;
   - the `is_secret_like` fix;
   - the empty-source rule in `shared.py`;
   - `federation.env.example` in the new format;
   - the conventions in `CLAUDE.md` and in `docs/runbooks/env-sync.md` ("For member authors");
   - a replacement for the active-lines comparison in `tests/test_envsync_make.py`, which becomes checker rule M4.
2. **Members**, one PR each, each with a patch release and a one-line pointer to the standard in its `CLAUDE.md`:
   1. data-plane;
   2. edge-plane and obs-plane together;
   3. open-webui-service;
   4. translator;
   5. docint;
   6. Nextext;
   7. vllm-service.

   The already planned rename directives and example fixes (vllm-service, Nextext, docint) are folded in.
3. **One airgap transfer** with the known two-pass adoption. `make env` then brings every host `.env` into the new format.

## Appendix: survey of the current files

Paths are relative to the infra/ workspace. Nextext and vllm-service line numbers come from their feature branches (`feat/face-detection`, `feat/faces-service`). Legend: ✓ the shown value equals the real default; ✗ mismatch; ⚠ `make env` would change a running host whose line still equals the old example.

### Findings that shape the plan

1. An empty `[required]` line is safe only for held keys. A non-held inherited key follows the new example: it gets the empty value, or is removed if commented. docint `INFERENCE_PROVIDER=` empty → docint refuses to start (`docint/docint/utils/env_cfg.py:2076-2083`); commented → the code default `ollama` applies (`env_cfg.py:169,2022`). Resolution (user-approved): compose defaults point at the router.
2. A `source` row counts as set whenever it has an active line, even an empty one (`deploy/scripts/envsync/shared.py:291-293`). Resolution (user-approved): an empty source counts as unset, plus a startup check for the router master key.
3. The vllm router receives `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` (topics too) and `TEXT_MODEL` / `EMBED_MODEL` / `RERANK_MODEL` only through `env_file` (`vllm-service/docker/compose.yaml:147,172-185`; `docker/litellm.config.yaml:24-60`). Commenting out the offline flags would drop them from the router. Resolution: add router, topics and faces env defaults.
4. `is_secret_like` false positives: `HATE_SPEECH_WINDOW_TOKENS`, `HATE_SPEECH_CONTEXT_TOKENS` (Nextext and docint), `EMBED_CTX_TOKENS`, `EMBED_TOKENIZER_REPO`, `TOKENIZERS_PARALLELISM`.
5. The trap `# KEY=   # comment` also exists on commented lines:
   - docint: 66, 67, 81, 97, 122, 146, 147, 152-157, 177-180, 193, 273, 326, 327, 444;
   - Nextext: 65, 84, 187, 189, 191;
   - vllm-service: 324, 325, 381.
6. Member CI copies the example as `.env` (`edge-plane/.github/workflows/ci.yml:37,84`; `obs-plane/.github/workflows/ci.yml:41`). With empty required values, `${X:?}` stops `compose config` / `up-dev`, so CI must fill them, as data-plane does (`data-plane/.github/workflows/ci.yml:38-39`).
7. `deploy/tests/test_envsync_make.py:59-72` compares only active federation.env lines and fails once all are commented. It is replaced by checker rule M4.

### Per file

### deploy/federation.env.example (make syntax)
- **Section:** `deploy` only, with subsections Members, Clone, Health gates, One-shot switches.
- **Active → commented**, all ✓ against the Makefile `?=`: `INFRA_ROOT` (Makefile:32), `VLLM_DIR` (33), `DATA_DIR` (34), `APP_DIRS` (36), `OPENWEBUI_DIR` (45), `OBS_DIR` (51), `EDGE_DIR` (58), `DATA_PROFILE` (59), `GIT_REMOTE` (66). These are held, so hosts keep their values and get a one-time `held` report of 9 keys.
- `BUNDLE_FORCE=1`, `PRUNE_UPSTREAM=1`, `ENV_GENERATE=1`, `ENV_ALLOW_DRIFT=1` become `# KEY=`; the "1 = …" meaning goes into the comment.
- `WAIT_TIMEOUT=180` ✓ (`scripts/wait-healthy.sh:16`); `WAIT_PROBE_*` ✓ (Makefile:78-79). The digest line runs past 79 columns, which the F3 exception allows.

### data-plane
- **Required:** `QDRANT_API_KEY=` (24) `[required] [secret] [shared: docint]`; compose.yaml:69 `:?`.
- **Network & proxy:** `DATA_NET`.
- **data-plane:** `PROFILE`; subsection Backups: `BACKUP_RECIPIENTS`.
- **Audit & retention:** `BACKUP_RETENTION_DAYS`, `RETENTION_NOTICE_DAYS`, `AUDIT_REQUIRE_PURPOSE` (compose:160).
- **Development only:** `QDRANT_HTTP_HOST_PORT` / `QDRANT_GRPC_HOST_PORT` `[dev]`.
- **Active → commented**, all ✓: `PROFILE=cpu` (Makefile:18 fallback), `DATA_NET` (Makefile:21, compose:53), ports 6333 and 6334 (compose.override.yaml:14-15, 19-20).
- **Makefile greps:** `PROFILE`, `DATA_NET`, `BACKUP_RECIPIENTS`, `BACKUP_RETENTION_DAYS`, `RETENTION_NOTICE_DAYS` — no inline comments on these.
- **CI:** ci.yml:38 greps the exact line `QDRANT_API_KEY=`; keep it byte-identical.

### obs-plane
- **Required:**
  - `GRAFANA_ADMIN_PASSWORD` `[required] [secret]` (compose:95 `:?`), placeholder → empty;
  - `AUDIT_EDGE_SECRET` `[required] [secret] [shared: edge-plane]` (compose:459 `:?`, `scripts/edge_secret_check.sh` ≥ 32 characters), `change-me` → empty;
  - `EDGE_HOST` `[required] [shared: edge-plane]`, 127.0.0.1 → empty. ✗ compose `${EDGE_HOST:-localhost}` (103, 404); held.
- **Auth & identity:** `GRAFANA_ADMIN_USER` ✓ (compose:94); `GRAFANA_VIEWER_ROLE` ✓ (117); `GRAFANA_EDGE_SECRET` `[secret] [shared: edge-plane]`, active-empty → commented (held).
- **Network & proxy:** `INFERENCE_NET`, `DATA_NET`, `EDGE_NET` ✓ (Makefile:12-14 grep; compose:50-56).
- **obs-plane:** `COMPOSE_PROFILES` ✓ (empty). Makefile:138 greps `^COMPOSE_PROFILES=.*gpu`, so no inline comment.
- **Audit & retention:** `PROMETHEUS_RETENTION` (compose:69), `LOKI_RETENTION` (182), `AUDIT_RETENTION` (358, 492), `AUDIT_VERIFY_INTERVAL`, `AUDIT_SILENCE_S`, `AUDIT_EMITTERS` (489-491) ✓.
- **Development only:** `GRAFANA_HOST_PORT` ✓ (override:7).
- Example line 42 (`AUDIT_EMITTERS`) runs past 79 columns.

### edge-plane
- **Required:**
  - `EDGE_HOST`, the source of edge-host (compose:72, 148 `:?`): 127.0.0.1 → empty, inline comment and its continuation lines → above the setting;
  - `AUTHELIA_*` ×3 (7-9) `[required] [secret]` (compose:156-158 `:?`), `insecure-dev-*` → empty;
  - `AUDIT_EDGE_SECRET` `[required]` (call 4), compose:94 `:-` → `:?`.
- **Auth & identity:** the per-app edge secrets (17-20, 23) `[secret] [shared: <app>]`, active-empty → commented; `EDGE_AUTH_POLICY=two_factor` ✓ (`authelia/configuration.yml:46`); `EDGE_SESSION_*` 12h / 4h / 2M ✓ (configuration.yml:85-87).
- **Network & proxy:** `EDGE_NET` ✓ (Makefile:13). ✗ `EDGE_TLS` (34) shows the cert pair; the default is `internal` (compose:76, `caddy/Caddyfile:73`). ✗ `EXTRA_NO_PROXY` (58); the default is empty.
- **edge-plane:** ✗ `RESPONSE_LANGUAGE` (38) shows `de`; the default is `en` (compose:79). ✗ `EDGE_SUPPORT_CONTACT` (42); the default is empty (compose:82).
- **Audit & retention:** `AUDIT_POLL_INTERVAL` ✓ (compose:195).
- **Development only:** none; the dev overlay's `one_factor` (override:24) goes into prose.

### open-webui-service
- **Required:**
  - `OPENAI_API_KEY` `[required] [secret] [shared: vllm-service]`, `sk-1234567890` → empty; held;
  - `EDGE_HOST` `[required] [shared: edge-plane]`. ✗ the compose default is `localhost` (227); held.
- **Auth & identity:** `OPEN_WEBUI_EDGE_SECRET` `[secret] [shared: edge-plane]` (compose:277); `OPEN_WEBUI_FORWARD_JWT_SECRET` `[secret] [shared: vllm-service AUDIT_OPENWEBUI_JWT_SECRET]` (168-169). Both active-empty → commented.
- **Network & proxy:** `INFERENCE_NET`, `EDGE_NET` ✓ (Makefile:21, 27). ✗ `EXTRA_NO_PROXY` (15); the default is empty.
- **Inference:** `OPENAI_API_BASE_URL` ✓ (compose:88), active → commented. ✗ `WHISPER_API_BASE` (10); the default falls back (compose:131).
- **Undocumented compose variables:** `TEXT_MODEL` (93), `EMBED_MODEL` (118, `bge-m3:latest`), `WHISPER_API_KEY` (132), `WHISPER_MODEL` (133), `JWT_EXPIRES_IN` (223), `WEBUI_AUTH_SIGNOUT_REDIRECT_URL` (227), `DATABASE_URL` (333).
- **Audit & retention:** lines 40, 45, 49, 51 ✓ (compose:322-330).
- **Development only:** ⚠ `OPEN_WEBUI_HOST_PORT=8080` (5) vs override `:-3000` (compose.override.yaml:4). Call 5: accept the move to 3000 and announce it.

### translator (nothing active today, so no running host changes)
- **Required:**
  - `OPENAI_API_BASE`: compose:55 has no default, and `translator/engine.py:185-189` raises. Resolution: compose default to the router;
  - `OPENAI_API_KEY` `[required] [secret] [shared: vllm-service]`.
- **Auth & identity:** `TRANSLATOR_EDGE_SECRET` ✓.
- **Network & proxy:** `INFERENCE_NETWORK` ✓. `EDGE_NET` (compose:132) is undocumented. ✗ `EXTRA_NO_PROXY`.
- **Inference:** ✗ `TEXT_MODEL=${TEXT_MODEL}` (6) refers to itself; the default is `cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit` (compose:57). ✗ `OPENAI_API_KEY=ollama` (5); the default is `dummy` (compose:56).
- **translator:** ✗ `RESPONSE_LANGUAGE=de` (11); the default is `en` (compose:60). `DEFAULT_TARGET_LANGUAGE` ✓ (build arg, compose:103).
- **Audit & retention:** ✗ `AUDIT_REQUIRE_PURPOSE=ai_call` (33); the default is empty (compose:72).
- **Development only:** the two ports ✓ (override:13, 22). ✗ `TRANSLATOR_DEFAULT_IDENTITY=dev-user` (25); the overlay default is `dev` (override:19).

### Nextext (feat/face-detection; nothing active today)
- **Required:** `OPENAI_API_BASE` (compose:121, no default; resolution: router default); `TEXT_MODEL` (compose:124 `:-`, `nextext/core/openai_cfg.py:155-157` raises → `[required]`); `OPENAI_API_KEY` `[required] [secret] [shared: vllm-service]` (compose:122).
- **Auth & identity:** `NEXTEXT_AUTH_HEADER` ✓; `NEXTEXT_EDGE_SECRET` `[secret] [shared: edge-plane]`; `NEXTEXT_SERVICE_TOKENS` `[secret] [shared: docint NEXTEXT_API_KEY]`, ✗ `docint=<token>` (101) → empty.
- **Network & proxy:** `INFERENCE_NETWORK`; `EDGE_NET` undocumented (206); ✗ `EXTRA_NO_PROXY`.
- **Inference:** `INFERENCE_PROVIDER` as one line (compose:102 / env_cfg.py:414; resolution: compose default `vllm`); the per-stage `*_API_BASE` / `*_TIMEOUT` keys; ✗ `OLLAMA_THINK=0`, which defaults to unset (env_cfg.py:360-380).
- **Nextext:** backend tuning, keyframes, visual context, faces, sentence restoration, summary and hate speech. ✗ `RESPONSE_LANGUAGE=de` (222); the default is `en` (env_cfg.py:56).
- **Audit & retention:** 128, 133, 140, 141 ✓. `AUDIT_OUTPUT=stdout` is fixed by compose to `container` (compose.yaml:89) → Development only, outside Docker. `AUDIT_CLI_USER` / `AUDIT_CASE_REF` / `AUDIT_REASON` (145-147), set per run → prose.
- **Development only:** `NEXTEXT_HOST_PORT=8501` ✓ (collides with translator; out of scope); `NEXTEXT_DEFAULT_IDENTITY`.
- **Several lines per key:** `INFERENCE_PROVIDER` 167, 242, 252, 260; `OPENAI_API_BASE` 243, 253, 261; `OPENAI_API_KEY` 244, 254, 262; `TEXT_MODEL` 245, 255, 263; `RESPONSE_LANGUAGE` 216, 222.
- **Prose that reads as a setting:** 96 (`# name=token`), 167, 216.
- **Placeholders:** 96, 101, 254 (`<…>`), 262 (`sk-...`).
- **Rename:** `# renamed: NEXTEXT_RESPONSE_LANGUAGE -> RESPONSE_LANGUAGE`. The code still honours the old name (env_cfg.py:96-105).
- **Order risk:** `OPENAI_API_*` / `TEXT_MODEL` move to the top. An operator line above them that contains `${OPENAI_API_KEY}` would flip the order and be refused. Rare.

### docint
- **Active today:**
  - ⚠ `INFERENCE_PROVIDER=vllm` (28), code default ollama → compose default `vllm`;
  - ⚠ `OPENAI_API_BASE=http://vllm-router:4000/v1` (29), code default localhost:11434 → compose default router;
  - `OPENAI_API_KEY=sk-no-key-required` (30) ✓ (env_cfg.py:2017), held → `[required]`;
  - `QDRANT_API_KEY=` + trailing comment (95): the comment is read as the value today. → `[required] [secret] [shared: data-plane]`, comment on its own line.
- **Mapping:**
  - §1 → Required.
  - §2 and §7-17 and §19 → `docint` subsections.
  - §3 → Auth & identity (`DOCINT_DEFAULT_IDENTITY` → Development only; the audit part → Audit & retention).
  - §5-6 and the OCR endpoint/model → Inference.
  - §18: paths, `QDRANT_HOST` and `SESSIONS_DB_PATH` are fixed by compose (113-123) → Development only, outside Docker. `EXTRACT_RETENTION_DAYS`, `DOCUMENT_RETENTION_DAYS` and `RETENTION_NOTICE_DAYS` → Audit & retention.
  - §20: `INFERENCE_NET` → Network & proxy, plus the undocumented `DATA_NET` / `EDGE_NET` (compose:234, 240); `DOCINT_HOST_PORT` → Development only.
- **Commented ✗:** `NEXTEXT_API_BASE` (326) says "unset = disabled", but compose:111 `:-http://nextext-backend:8000/api/v1` means it cannot be switched off (document the actual behaviour). `CLIP_API_KEY` / `NER_API_KEY` / `TOPICS_API_KEY=$OPENAI_API_KEY` (144, 149, 159) default to none (env_cfg.py:1524, 1767, 3137); document that.
- **Order:** `OPENAI_API_KEY` stays above 144, 149, 159.
- **Several lines / prose that reads as a setting:** `AUDIT_OUTPUT` 71 and 81; `INFERENCE_PROVIDER` 28 and 239; `REASON` 80.

### vllm-service (feat/faces-service)
- **Active today:**
  - `INFERENCE_NETWORK` ✓ (`:-inference-net` in every compose file) → commented;
  - `OPENAI_API_KEY=sk-no-key-required` (66), the router master key (litellm.config.yaml:91, `env_file` only) → `[required]`, empty, the source of router-key; add a compose `:?` startup check;
  - ⚠ `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` (124-125): router and topics get them only via `env_file`, faces only `HF_HUB_OFFLINE` → add `${…:-1}` defaults.
- **Commented ✗ vs compose.yaml:**
  - `CHAT_CUDA_VISIBLE_DEVICES` 0,1 vs 0; `CHAT_TENSOR_PARALLEL_SIZE` 2 vs 1; `CHAT_GPU_MEMORY_UTILIZATION` 0.55 vs 0.5; `CHAT_MAX_MODEL_LEN` 40000 vs 8192; `CHAT_MAX_NUM_BATCHED_TOKENS` 4608 vs 16384;
  - `CHAT_ENFORCE_EAGER` / `REASONING_PARSER` / `TEMPLATE_CONTENT_FORMAT` / `DEFAULT_CHAT_TEMPLATE_KWARGS` / `LIMIT_MM_PER_PROMPT` / `CPU_OFFLOAD_GB` / `KV_CACHE_DTYPE` / `SPECULATIVE_CONFIG` show values, but the defaults are empty;
  - `EMBED_CUDA_VISIBLE_DEVICES` 1 vs 0; `EMBED_GPU_MEMORY_UTILIZATION` 0.1 vs 0.15; `EMBED_HF_OVERRIDES` shows a value, default empty;
  - `RERANK_CUDA_VISIBLE_DEVICES` 1 vs 0; `RERANK_GPU_MEMORY_UTILIZATION` 0.1 vs 0.15;
  - `ASR_CUDA_VISIBLE_DEVICES` 0,1 vs 0;
  - `WHISPER_MODEL` turbo vs `openai/whisper-large-v3` (174, 527);
  - `DIARIZE_VAD_GATE` off vs true;
  - `NER_ENABLE_FLASHDEBERTA`, `NER_ENABLE_SEQUENCE_PACKING`, `NER_DISABLE_COMPILE` true vs false; `NER_QUANTIZATION` int8 vs empty;
  - `DIARIZE_FB` / `DIARIZE_FA` show values, default unset.
  - Tuning hints for a specific GPU layout go into prose.
- **Self-referencing model lines** 86-94 (`# TEXT_MODEL=${TEXT_MODEL:-…}`).
- **Several lines per key:** `NER_MODEL` 7 (prose) and 90; `EMBED_MODEL` 88 and 198; `CLIP_DEVICE` 213 and 216; `DIARIZE_DEVICE` 312 and 315.
- **Placeholder:** `HF_TOKEN=hf_your_token_here` (123) → empty. `NER_HOST_PORT` is undocumented.
- **Sections:**
  - Required: `OPENAI_API_KEY` `[shared: docint, Nextext, translator, open-webui-service]`.
  - Network & proxy: `INFERENCE_NETWORK`, `EXTRA_NO_PROXY` (empty).
  - Inference: model ids, revisions, HF flags, `HF_TOKEN`.
  - vllm-service: one subsection per backend; the CPU-only shapes in prose.
  - Audit & retention: `AUDIT_ROUTER_CALL_TYPES`, `AUDIT_OPENWEBUI_JWT_SECRET` `[secret] [shared: open-webui-service OPEN_WEBUI_FORWARD_JWT_SECRET]`.
  - Development only: `ROUTER_HOST_PORT` and the `*_HOST_PORT` keys 8002-8009.
- **Renames:** `AUDIO_CUDA_VISIBLE_DEVICES -> ASR_CUDA_VISIBLE_DEVICES`, `AUDIO_GPU_MEMORY_UTILIZATION -> ASR_GPU_MEMORY_UTILIZATION`.

### Checker rules (proposed; `scripts/envsync/conventions.py`)

Each finding names a line number, a rule and a key, never a value.

**File (F)**
- F1: `load_example` parses the file, and `dotenv.lint` is clean.
- F2: Every line starts with `#` or is `KEY=…` at column 0; no tabs, no trailing blanks.
- F3: At most 79 characters, except a line whose value contains no blank.
- F4: Keys match `^[A-Z][A-Z0-9_]*$`.

**Header (H)**
- H1: The lines before the first banner equal the template, optionally followed by member-specific notes.
- H2: The header holds no setting.
- H3: Legend lines are indented with two or more blanks.

**Sections (S)**
- S1: A banner is exactly 3 lines: a rule, the title from the fixed list (`<member>` = the directory name, `deploy` for federation.env), a rule.
- S2: The section titles are unique and in increasing order.
- S3: `# ==` appears only in banners.
- S4: Subsections match `^# --- \S.* ---+$`.
- S5: A key's section is the nearest banner above it.

**Tags (T)**
- T1: The tag-line grammar, directly above a setting.
- T2: The order is required < secret < shared < dev; no duplicates, no unknown tags.
- T3: `[required]` ⇔ active ⇔ in the Required section.
- T4: `[secret]` ⇔ `is_secret_like`.
- T5: `[shared]` ⇔ an env-sync.conf row. A non-source names only the source; the source lists all others. The KEY is named only when it differs.
- T6: `[dev]` ⇔ in the Development only section.

**Values (V)**
- V1: An active line is `KEY=` with nothing after it.
- V2: At most one commented line per key, and none for an active key.
- V3: `# KEY=` with one blank.
- V4: No `<…>`.
- V5: No line refers to its own key.
- V6: A secret-like key has an empty value.
- V7: An empty value has no inline comment.
- V8: An inline comment needs a value and is preceded by a blank, never a tab.
- V9: Prose-like values are quoted (dotenv).
- V10: A reference to a documented key appears below that key's line.
- V11: Keys a member Makefile greps have no inline comment.

**Make syntax (M)**
- M1: No inline comments.
- M2: `KEY=value` with no blanks around `=`.
- M3: No active line.
- M4: Each commented value equals the Makefile `?=` default; a key with no `?=` is shown empty, except `WAIT_TIMEOUT=180`.

**Siblings (X)**, run only where the sibling exists and is migrated (H1)
- X1: F to V pass with its env-sync.conf rows.
- X2: Rows of the same group agree on `[required]`.
- X3: A simple `${K:-v}` / `${K-v}` in docker/compose.yaml equals the commented value; `[dev]` keys are checked against compose.override.yaml; `${K:?}` means `[required]`.
- X4: The keys a member Makefile greps equal V11's set.
- X5: Every compose-interpolated variable is documented, except `*_VERSION`.
