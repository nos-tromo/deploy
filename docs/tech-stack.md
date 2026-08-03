# nos-tromo federation — tech stack

Last updated: 2026-08-03. German version: [tech-stack.de.md](tech-stack.de.md).
Versions below are taken from each repo's manifests (`pyproject.toml`,
`package.json`, `compose.yaml`, Dockerfiles, `VERSION`) at time of writing;
the manifests remain the source of truth.

The federation is twelve independent repositories under the `nos-tromo`
GitHub account (a personal user account, not an organization), each with its
own git history, CI, and release cycle, joined at runtime by three external
Docker networks:

- **`inference-net`** — app backends ↔ `vllm-service` (LiteLLM router, alias `vllm-router`)
- **`data-net`** — app backends ↔ `data-plane` (aliases `neo4j`, `qdrant`)
- **`edge-net`** — edge gateway ↔ app frontends (aliases `chorus-frontend`, `docint-frontend`, `nextext-frontend`, `translator-frontend`, `open-webui`, `grafana`)

Bring-up order is inference → state → obs (optional) → apps → edge
(optional), automated by `deploy`. Everything is airgap-first: production
artifacts are versioned image-tarball bundles (`make bundle`), and nothing
fetches data, models, or telemetry at runtime.

---

## Stack at a glance

| Layer | Technology |
|---|---|
| Languages | Python 3.11 / 3.12 (per-repo pins), TypeScript ~6.0 |
| Backend framework | FastAPI + Uvicorn, Pydantic v2 |
| Frontend | React 19 + Vite 8 + Tailwind CSS v4, shared `@infra/ui` design system (v0.8.x) |
| Inference | LiteLLM Proxy router → vLLM v0.20.1 backends; Ray Serve for GLiNER |
| Databases | Neo4j 5.26 Community (graph), Qdrant v1.17 (vector) |
| Edge / auth | Caddy 2.11 (TLS, path routing) + Authelia 4.39 (forward-auth SSO, trusted `X-Auth-User`/`X-Auth-Email` headers) |
| Observability | Prometheus v3.13, Grafana OSS 13.0, Loki 3.7, Alloy v1.18 + node-exporter, cAdvisor, blackbox-exporter |
| Packaging / deps | `uv` (Python, `uv.lock`), `pnpm` 9.12 (JS) |
| Quality gates | ruff 0.15.14, pyrefly 1.1.1 (strict), pytest 9, pre-commit; ESLint 9 + `tsc` + vitest 4 |
| Containers | Docker Compose per repo; digest-pinned images; Make-driven lifecycle |
| CI / release | GitHub Actions via shared reusable workflows in `nos-tromo/.github` (`@v3`); auto-minted annotated semver tags |

---

## Tier: inference

### `vllm-service` (v0.1.3)

LiteLLM Proxy router fronting a fleet of vLLM model servers plus bespoke
FastAPI servers. Pure infrastructure — the only first-party Python is the
small FastAPI servers bundled into images (typed against `fastapi`,
`pydantic`, `numpy`; heavy ML deps live only in the images).

| Component | Technology |
|---|---|
| Router | `litellm/litellm:main-v1.83.10-stable` (digest-pinned), config-file routed, alias `vllm-router` on `inference-net` |
| Model servers (vLLM) | `vllm/vllm-openai:v0.20.1` — `chat`, `embed`, `rerank`, `asr` (Whisper large-v3 via `vllm serve`) |
| Bespoke servers | `clip`, `diarize` (pyannote.audio 3.x in prod image), `vad` (silero-vad) — FastAPI + Uvicorn |
| NER | `gliner` — GLiNER on Ray Serve (not vLLM); reached through the router's `/gliner` pass-through |
| Base images | CPU: `ghcr.io/astral-sh/uv:*-trixie-slim`; CUDA: `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime` |
| Profiles | Own `media` profile; CPU/CUDA Dockerfile variants per bespoke service |
| Dev tooling | ruff + pyrefly via pre-commit (no pytest suite); dev-only `eval` / `eval-run` dependency groups (pyannote.metrics, pyannote.audio 4.x) for the diarization eval harness |
| Networks | `inference-net` (router only) + internal `vllm-net` |

Model selection is env-driven (`.env`, e.g. `WHISPER_MODEL`); routing is by the
OpenAI `model` field. `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` by default.

---

## Tier: state

### `data-plane` (v0.1.1)

Sole owner of the federation's databases and their volumes. Docker Compose
only — no application code.

| Component | Technology |
|---|---|
| Graph DB | `neo4j:5.26.26-community` (digest-pinned), alias `neo4j` on `data-net` — used by chorus |
| Vector DB | `qdrant:v1.17.0` (CPU profile) / `qdrant:v1.17.0-gpu-nvidia` (CUDA profile), alias `qdrant` — used by docint |
| Volumes | All external (`neo4j-data/logs/import/plugins`, `qdrant-snapshots/storage`) so no app-repo `down -v` can destroy state |
| Lifecycle | Bespoke Makefile (not on `common.mk`): `make network` / `up` / `up-dev` / `bundle`; destructive teardown gated behind `make nuke` (interactive confirm) |
| Versioning | One-line `VERSION` file; `make bundle` builds the latest annotated tag (bundle-lib.sh); working-tree bundles via `DATA_PLANE_VERSION_OVERRIDE` (no `bundle-dev`) |
| Backups | Runbook + scripts under `backup/` |

---

## Tier: observability

### `obs-plane` (v0.2.0)

Airgap-first observability plane — pulled digest-pinned images only, no
application code. Joins `inference-net` and `data-net` read-only as a scraper
(a pure consumer that claims no aliases others depend on); owns its own
external volumes (data-plane blast-radius pattern). Grafana joins `edge-net`
(alias `grafana`) behind the gateway's admins-group `/grafana` gate.

| Component | Technology |
|---|---|
| Metrics | `prom/prometheus:v3.13.1`; `prom/node-exporter:v1.12.1` (host), `ghcr.io/google/cadvisor:v0.60.5` (containers), `prom/blackbox-exporter:v0.28.0` (probes) |
| Logs | `grafana/loki:3.7.4`, collected by `grafana/alloy:v1.18.0` |
| Dashboards | `grafana/grafana-oss:13.0.2` — dev override / SSH tunnel / `/grafana` via edge only |
| Alerting | Alert rules with no notification channel by design (airgap) |
| Lifecycle | Bespoke Makefile: `make bundle` (tag) but no `bundle-dev`; `OBS_PLANE_VERSION_OVERRIDE` for working-tree bundles; `make health` |

Known v1 gaps: Neo4j Community and LiteLLM metrics are license-gated.

---

## Tier: edge

### `edge-plane` (v0.4.3)

Federation entry point and the only published host ports in production
(`:443`; `:8443` for Open WebUI). Pure infra — pulled digest-pinned images.

| Component | Technology |
|---|---|
| Reverse proxy | `caddy:2.11.4` — TLS on `:443`, path routing to the frontend aliases on `edge-net`; strips client-supplied identity headers |
| Auth | `authelia:4.39.20` — forward-auth SSO; injects trusted `X-Auth-User` / `X-Auth-Email`; `Authorization` pass-through; file-backed users (`make user` / `make secret`); admins-group gate on `/grafana` |
| Self-service | Landing portal + password self-service with one-time-code viewer (`landing/`, `authcode/`; project-scoped `edge-notify` volume for short-lived codes) |
| TLS | Internal CA (`make ca-export`) or org-issued certs via `EDGE_TLS`; `EDGE_HOST` must be a dotted hostname or IP |
| State | External `edge-state` / `edge-ca` volumes |
| Routing | Each SPA under its canonical sub-path (`/chorus/`, `/docint/`, `/nextext/`, `/translator/`); Open WebUI (no sub-path support) on the dedicated `:8443` site |
| Networks | `edge-net` only — never `inference-net` / `data-net`. `edge-net` is a trusted zone: members accept the identity headers unverified |

---

## Tier: applications

All four Python apps share the same shape: FastAPI backend + React SPA
frontend, env-driven config in one `env_cfg.py` dataclass module, `uv`-managed
deps, docker files under `docker/` (production-shape `compose.yaml`, dev
overlay `compose.override.yaml` publishing ports), Makefile on the shared
`common.mk`, single CPU-only image, all inference over `inference-net`.
Frontends attach to `edge-net` and consume `X-Auth-User` fail-closed
(`*_DEFAULT_IDENTITY` unset in production). UI locale is env-driven
(`RESPONSE_LANGUAGE`, `en` | `de`).

Backend base image: `ghcr.io/astral-sh/uv:<py-version>-trixie-slim` (multi-stage).
Frontend image: `node:20-alpine` build → `nginx:1.27-alpine` serve (the app's
nginx strips the sub-path prefix). All digest-pinned.

### `chorus` — GraphRAG for social-network analysis (airgapped production)

| | |
|---|---|
| Version / Python | 0.3.0 · `>=3.12,<3.13` (3.12 only) |
| Backend | FastAPI, `neo4j` 6.x driver, `openai` SDK (→ LiteLLM router), httpx, tenacity, loguru; hatchling build |
| Frontend | React 19, React Router 7, TanStack Query 5, `@infra/ui` **ForceGraph** (graph viz — homegrown canvas force simulation in the design system), Recharts 3, react-markdown + remark-gfm |
| Data | Neo4j (via `data-plane`) |
| Tests | pytest + pytest-asyncio + `testcontainers[neo4j]` |
| Compliance | §76 BDSG; ADRs in `docs/decisions/` |

### `docint` — Document Intelligence (ingestion / retrieval / chat RAG)

| | |
|---|---|
| Version / Python | 1.1.2 · `>=3.11,<3.12` (3.11 only) |
| RAG engine | **LlamaIndex** 0.14 (OpenAI-compatible LLM/embeddings + HuggingFace embeddings + Qdrant vector store) |
| Document processing | **Docling** 2.x (+ LlamaIndex readers/node-parsers), pypdf, striprtf, WeasyPrint, python-magic, OpenCV headless, Pillow |
| Data / ML libs | qdrant-client 1.18, fastembed, transformers, pandas 3, numpy 2, pyarrow/fastparquet, numba, SQLAlchemy 2 (session DB) |
| Backend | FastAPI + Uvicorn; CLI entry points (`docint`, `ingest`, `query`, `verify`, …); setuptools build |
| Frontend | React 19, TanStack Query/Table/Virtual, Zustand 5, Recharts 3, react-markdown |
| State | Qdrant (via `data-plane`) + own external volumes (source-file store, session DB) |

### `Nextext` — audio/video transcription, translation, analysis

| | |
|---|---|
| Version / Python | 1.1.3 · `>=3.12,<3.13` (3.12 only) |
| Backend | FastAPI + SSE (`sse-starlette`), Typer CLI, watchdog; setuptools build |
| NLP / analysis | spaCy 3.8, NLTK, langdetect, camel-tools + pyarabic + arabic-reshaper + python-bidi (Arabic support), wordcloud, matplotlib |
| Inference clients | `openai` SDK; per-model dedicated endpoints `WHISPER_API_BASE` / `NER_API_BASE` / `DIARIZATION_API_BASE` falling back to `OPENAI_API_BASE` |
| Frontend | React 19, TanStack Query/Table/Virtual, Zustand 5, Recharts 3 |
| Tests | pytest + pytest-asyncio + respx |

### `translator` — thin text-translation service

| | |
|---|---|
| Version / Python | 1.1.0 · `>=3.11,<3.12` (3.11 only) |
| Backend | FastAPI + Uvicorn, `openai` SDK against an instruction-tuned Gemma-class model (env-selected via `TEXT_MODEL`, no local weights), langcodes/langdetect/pycountry |
| Frontend | Minimal React 19 SPA (TanStack Query only) |

### `open-webui-service` — chat UI (deployment wrapper, v0.1.2)

| | |
|---|---|
| Image | Pulled upstream `ghcr.io/open-webui/open-webui:0.11.0` (digest-pinned) — no app source |
| Config | `.env` as source of truth; bundled providers hard-disabled so all inference exits via the LiteLLM endpoint on `inference-net` |
| Entry | Via edge-plane's dedicated `:8443` site (no sub-path support), alias `open-webui` on `edge-net` |
| State | External `open-webui-data` volume; `make nuke` gate |
| Lifecycle | Bespoke Makefile (not on `common.mk`); one-line `VERSION` file |

---

## Tier: orchestration, shared frontend & tooling

### `deploy` — federation lifecycle layer

Make + shell only; owns no services, data, or images. Sequences the members'
own `make` targets: `make up` brings up inference → state → obs → apps → edge
in order, health-gated, on a single host; `make up-dev` does the dev-shape
equivalent (host ports published for state + apps). Host profile via
`federation.env` (`INFRA_ROOT`, tier dir lists, `DATA_PROFILE=cpu|cuda`).
Also fans out `network` / `volumes` / `down` / `bundle` / airgap `load`
across members.

### `infra-ui` — `@infra/ui` shared design system (v0.8.x)

| | |
|---|---|
| Stack | React 19 (peer dep), Tailwind CSS v4 tokens, class-variance-authority + clsx + tailwind-merge |
| Primitives | UI primitives + AppHeader, light/dark theming (`useTheme`, CSS vars), **ForceGraph** (canvas force simulation + graph export, consumed by chorus) |
| Build | tsup → **committed prebuilt `dist/`** (build-time only, no install-time rebuild); vitest + Testing Library + happy-dom; ESLint 9 + Prettier |
| Consumption | Pinned pnpm git dependency (release-tag URL, e.g. `#v0.8.1`) in all four app frontends; per-app theming via `--app-accent`; requires a Tailwind `@source` line |
| CI | Includes a dist-guard (dist must match src) |

### `pr-notify` — Telegram notifier (tooling)

Single stdlib-only Python script polling the GitHub search API for new
nos-tromo issues/PRs (split `is:issue` / `is:pull-request` searches), run by a
5-minute scheduled GitHub Actions workflow that commits its seen-state
(`seen_prs.json`) back to the repo, with a heartbeat against
scheduled-workflow auto-disable. Not a runtime federation member; none of the
shared `uv` / `common.mk` conventions apply.

---

## Shared toolchain & conventions

**Python (chorus, docint, Nextext, translator; lint-only for vllm-service):**
`uv` + `uv.lock` as dependency source of truth; ruff `0.15.14` (lint + format,
Google docstrings, 120-col) and pyrefly `1.1.1` strict preset — both mirroring
canonical configs in `nos-tromo/.github/configs/python-strict/` (drift fails
CI); pytest 9; pre-commit 4. `make verify` is the local pre-push gate
(pre-commit + frontend `pnpm lint`/`pnpm build` where present).

**Frontend (all four apps + infra-ui):** pnpm 9.12, TypeScript ~6.0 strict
(`tsc -b` in the build), Vite 8, Vitest 4 + Testing Library + happy-dom,
ESLint 9 flat config + typescript-eslint 8 (shared config in
`.github/configs/frontend-eslint/`), Tailwind v4 via `@tailwindcss/postcss`,
Inter via `@fontsource/inter`.

**Make / bundling:** app Makefiles `include` a vendored `make/common.mk`;
bundle scripts source a vendored `scripts/bundle-lib.sh` — both canonical in
`nos-tromo/.github` (currently v3.x line) and CI-drift-checked. `make bundle`
builds the latest annotated tag reachable from HEAD (tag-versioned artifact);
`make bundle-dev` bundles the working tree for dev/staging soak. `data-plane`,
`obs-plane`, and `open-webui-service` keep bespoke Makefiles.

**CI / release:** GitHub Flow (short-lived `feature/*`/`fix/*` → PR → CI →
`main`); reusable GitHub Actions workflows from `nos-tromo/.github`
(referenced `@v3`). On merge to `main`, the shared `release-tag` workflow
reads the declared version (`pyproject.toml` or `VERSION`) and mints the
annotated `vX.Y.Z` tag — idempotent, anti-downgrade guarded. The same bundle
artifact soaks on staging before promotion to production. Design doc:
`2026-07-02-federation-release-workflow-design.md` in the infra workspace
`docs/` (untracked); runbook: this repo's [README](../README.md) § Releasing.
