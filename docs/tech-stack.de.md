# nos-tromo-Föderation — Tech-Stack

Stand: 2026-08-03. Englische Version: [tech-stack.md](tech-stack.md).
Die unten genannten Versionen stammen aus den Manifesten der einzelnen Repos
(`pyproject.toml`, `package.json`, `compose.yaml`, Dockerfiles, `VERSION`)
zum Zeitpunkt der Erstellung; die Manifeste bleiben die maßgebliche Quelle.

Die Föderation besteht aus zwölf unabhängigen Repositories unter dem
GitHub-Account `nos-tromo` (ein persönlicher User-Account, keine
Organisation), jedes mit eigener Git-Historie, CI und eigenem Release-Zyklus,
zur Laufzeit verbunden über drei externe Docker-Netzwerke:

- **`inference-net`** — App-Backends ↔ `vllm-service` (LiteLLM-Router, Alias `vllm-router`)
- **`data-net`** — App-Backends ↔ `data-plane` (Aliase `neo4j`, `qdrant`)
- **`edge-net`** — Edge-Gateway ↔ App-Frontends (Aliase `chorus-frontend`, `docint-frontend`, `nextext-frontend`, `translator-frontend`, `open-webui`, `grafana`)

Die Startreihenfolge ist Inference → State → Obs (optional) → Apps → Edge
(optional), automatisiert durch `deploy`. Alles ist Airgap-first:
Produktionsartefakte sind versionierte Image-Tarball-Bundles (`make bundle`),
und nichts lädt zur Laufzeit Daten, Modelle oder Telemetrie nach.

---

## Stack im Überblick

| Ebene | Technologie |
|---|---|
| Sprachen | Python 3.11 / 3.12 (pro Repo gepinnt), TypeScript ~6.0 |
| Backend-Framework | FastAPI + Uvicorn, Pydantic v2 |
| Frontend | React 19 + Vite 8 + Tailwind CSS v4, gemeinsames Designsystem `@infra/ui` (v0.8.x) |
| Inference | LiteLLM-Proxy-Router → vLLM-v0.20.1-Backends; Ray Serve für GLiNER |
| Datenbanken | Neo4j 5.26 Community (Graph), Qdrant v1.17 (Vektor) |
| Edge / Auth | Caddy 2.11 (TLS, Pfad-Routing) + Authelia 4.39 (Forward-Auth-SSO, vertrauenswürdige Header `X-Auth-User`/`X-Auth-Email`) |
| Observability | Prometheus v3.13, Grafana OSS 13.0, Loki 3.7, Alloy v1.18 + node-exporter, cAdvisor, blackbox-exporter |
| Paketierung / Abhängigkeiten | `uv` (Python, `uv.lock`), `pnpm` 9.12 (JS) |
| Qualitätssicherung | ruff 0.15.14, pyrefly 1.1.1 (strict), pytest 9, pre-commit; ESLint 9 + `tsc` + vitest 4 |
| Container | Docker Compose pro Repo; Digest-gepinnte Images; Make-gesteuerter Lebenszyklus |
| CI / Release | GitHub Actions über gemeinsame wiederverwendbare Workflows in `nos-tromo/.github` (`@v3`); automatisch erzeugte annotierte Semver-Tags |

---

## Tier: Inference

### `vllm-service` (v0.1.3)

LiteLLM-Proxy-Router vor einer Flotte von vLLM-Modellservern plus
maßgeschneiderten FastAPI-Servern. Reine Infrastruktur — der einzige eigene
Python-Code sind die kleinen, in die Images gebündelten FastAPI-Server
(typisiert gegen `fastapi`, `pydantic`, `numpy`; schwere ML-Abhängigkeiten
leben nur in den Images).

| Komponente | Technologie |
|---|---|
| Router | `litellm/litellm:main-v1.83.10-stable` (Digest-gepinnt), Routing per Konfigurationsdatei, Alias `vllm-router` auf `inference-net` |
| Modellserver (vLLM) | `vllm/vllm-openai:v0.20.1` — `chat`, `embed`, `rerank`, `asr` (Whisper large-v3 via `vllm serve`) |
| Eigene Server | `clip`, `diarize` (pyannote.audio 3.x im Prod-Image), `vad` (silero-vad) — FastAPI + Uvicorn |
| NER | `gliner` — GLiNER auf Ray Serve (nicht vLLM); erreichbar über den `/gliner`-Pass-through des Routers |
| Basis-Images | CPU: `ghcr.io/astral-sh/uv:*-trixie-slim`; CUDA: `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime` |
| Profile | Eigenes `media`-Profil; CPU-/CUDA-Dockerfile-Varianten pro eigenem Service |
| Dev-Tooling | ruff + pyrefly via pre-commit (keine pytest-Suite); Dev-only-Dependency-Gruppen `eval` / `eval-run` (pyannote.metrics, pyannote.audio 4.x) für die Diarisierungs-Evaluierung |
| Netzwerke | `inference-net` (nur Router) + internes `vllm-net` |

Die Modellauswahl ist env-gesteuert (`.env`, z. B. `WHISPER_MODEL`); geroutet
wird über das OpenAI-Feld `model`. Standardmäßig `HF_HUB_OFFLINE=1` /
`TRANSFORMERS_OFFLINE=1`.

---

## Tier: State

### `data-plane` (v0.1.1)

Alleiniger Eigentümer der Datenbanken der Föderation und ihrer Volumes.
Nur Docker Compose — kein Anwendungscode.

| Komponente | Technologie |
|---|---|
| Graph-DB | `neo4j:5.26.26-community` (Digest-gepinnt), Alias `neo4j` auf `data-net` — genutzt von chorus |
| Vektor-DB | `qdrant:v1.17.0` (CPU-Profil) / `qdrant:v1.17.0-gpu-nvidia` (CUDA-Profil), Alias `qdrant` — genutzt von docint |
| Volumes | Alle extern (`neo4j-data/logs/import/plugins`, `qdrant-snapshots/storage`), sodass kein `down -v` eines App-Repos Daten zerstören kann |
| Lebenszyklus | Eigenes Makefile (nicht auf `common.mk`): `make network` / `up` / `up-dev` / `bundle`; destruktiver Abbau hinter `make nuke` (interaktive Bestätigung) |
| Versionierung | Einzeilige `VERSION`-Datei; `make bundle` baut den letzten annotierten Tag (bundle-lib.sh); Working-Tree-Bundles via `DATA_PLANE_VERSION_OVERRIDE` (kein `bundle-dev`) |
| Backups | Runbook + Skripte unter `backup/` |

---

## Tier: Observability

### `obs-plane` (v0.2.0)

Airgap-first-Observability-Ebene — ausschließlich gepullte, Digest-gepinnte
Images, kein Anwendungscode. Tritt `inference-net` und `data-net` nur lesend
als Scraper bei (ein reiner Konsument, der keine Aliase beansprucht, von
denen andere abhängen); besitzt eigene externe Volumes
(Blast-Radius-Muster wie data-plane). Grafana hängt an `edge-net`
(Alias `grafana`) hinter dem Admins-Gruppen-Gate `/grafana` des Gateways.

| Komponente | Technologie |
|---|---|
| Metriken | `prom/prometheus:v3.13.1`; `prom/node-exporter:v1.12.1` (Host), `ghcr.io/google/cadvisor:v0.60.5` (Container), `prom/blackbox-exporter:v0.28.0` (Probes) |
| Logs | `grafana/loki:3.7.4`, eingesammelt von `grafana/alloy:v1.18.0` |
| Dashboards | `grafana/grafana-oss:13.0.2` — nur Dev-Override / SSH-Tunnel / `/grafana` über die Edge |
| Alerting | Alert-Regeln bewusst ohne Benachrichtigungskanal (Airgap) |
| Lebenszyklus | Eigenes Makefile: `make bundle` (Tag), aber kein `bundle-dev`; `OBS_PLANE_VERSION_OVERRIDE` für Working-Tree-Bundles; `make health` |

Bekannte Lücken in v1: Neo4j-Community- und LiteLLM-Metriken sind
lizenzbeschränkt.

---

## Tier: Edge

### `edge-plane` (v0.4.3)

Eintrittspunkt der Föderation und die einzigen veröffentlichten Host-Ports in
Produktion (`:443`; `:8443` für Open WebUI). Reine Infrastruktur — gepullte,
Digest-gepinnte Images.

| Komponente | Technologie |
|---|---|
| Reverse Proxy | `caddy:2.11.4` — TLS auf `:443`, Pfad-Routing zu den Frontend-Aliasen auf `edge-net`; entfernt client-seitig mitgesendete Identitäts-Header |
| Auth | `authelia:4.39.20` — Forward-Auth-SSO; injiziert vertrauenswürdige `X-Auth-User` / `X-Auth-Email`; `Authorization`-Pass-through; dateibasierte Benutzer (`make user` / `make secret`); Admins-Gruppen-Gate für `/grafana` |
| Self-Service | Landing-Portal + Passwort-Self-Service mit Einmalcode-Anzeige (`landing/`, `authcode/`; projektgebundenes Volume `edge-notify` für kurzlebige Codes) |
| TLS | Interne CA (`make ca-export`) oder organisationseigene Zertifikate via `EDGE_TLS`; `EDGE_HOST` muss ein Hostname mit Punkt oder eine IP sein |
| State | Externe Volumes `edge-state` / `edge-ca` |
| Routing | Jede SPA unter ihrem kanonischen Unterpfad (`/chorus/`, `/docint/`, `/nextext/`, `/translator/`); Open WebUI (kein Unterpfad-Support) auf der dedizierten `:8443`-Site |
| Netzwerke | Nur `edge-net` — niemals `inference-net` / `data-net`. `edge-net` ist eine vertrauenswürdige Zone: Mitglieder akzeptieren die Identitäts-Header ungeprüft |

---

## Tier: Anwendungen

Alle vier Python-Apps haben dieselbe Form: FastAPI-Backend + React-SPA-
Frontend, env-gesteuerte Konfiguration in einem `env_cfg.py`-Dataclass-Modul,
`uv`-verwaltete Abhängigkeiten, Docker-Dateien unter `docker/`
(produktionsförmige `compose.yaml`, Dev-Overlay `compose.override.yaml` mit
veröffentlichten Ports), Makefile auf dem gemeinsamen `common.mk`, ein
einziges CPU-only-Image, alle Inference über `inference-net`. Frontends
hängen an `edge-net` und konsumieren `X-Auth-User` fail-closed
(`*_DEFAULT_IDENTITY` bleibt in Produktion ungesetzt). Die UI-Sprache ist
env-gesteuert (`RESPONSE_LANGUAGE`, `en` | `de`).

Backend-Basis-Image: `ghcr.io/astral-sh/uv:<py-version>-trixie-slim`
(Multi-Stage). Frontend-Image: `node:20-alpine`-Build → `nginx:1.27-alpine`
zum Ausliefern (das nginx der App entfernt das Unterpfad-Präfix). Alle
Digest-gepinnt.

### `chorus` — GraphRAG für Social-Network-Analyse (Produktion im Airgap)

| | |
|---|---|
| Version / Python | 0.3.0 · `>=3.12,<3.13` (nur 3.12) |
| Backend | FastAPI, `neo4j`-6.x-Treiber, `openai`-SDK (→ LiteLLM-Router), httpx, tenacity, loguru; hatchling-Build |
| Frontend | React 19, React Router 7, TanStack Query 5, `@infra/ui` **ForceGraph** (Graph-Visualisierung — eigene Canvas-Kraftsimulation im Designsystem), Recharts 3, react-markdown + remark-gfm |
| Daten | Neo4j (über `data-plane`) |
| Tests | pytest + pytest-asyncio + `testcontainers[neo4j]` |
| Compliance | §76 BDSG; ADRs in `docs/decisions/` |

### `docint` — Document Intelligence (Ingestion / Retrieval / Chat-RAG)

| | |
|---|---|
| Version / Python | 1.1.2 · `>=3.11,<3.12` (nur 3.11) |
| RAG-Engine | **LlamaIndex** 0.14 (OpenAI-kompatible LLM/Embeddings + HuggingFace-Embeddings + Qdrant-Vektorspeicher) |
| Dokumentverarbeitung | **Docling** 2.x (+ LlamaIndex-Reader/Node-Parser), pypdf, striprtf, WeasyPrint, python-magic, OpenCV headless, Pillow |
| Daten-/ML-Bibliotheken | qdrant-client 1.18, fastembed, transformers, pandas 3, numpy 2, pyarrow/fastparquet, numba, SQLAlchemy 2 (Session-DB) |
| Backend | FastAPI + Uvicorn; CLI-Einstiegspunkte (`docint`, `ingest`, `query`, `verify`, …); setuptools-Build |
| Frontend | React 19, TanStack Query/Table/Virtual, Zustand 5, Recharts 3, react-markdown |
| State | Qdrant (über `data-plane`) + eigene externe Volumes (Quelldatei-Speicher, Session-DB) |

### `Nextext` — Audio-/Video-Transkription, Übersetzung, Analyse

| | |
|---|---|
| Version / Python | 1.1.3 · `>=3.12,<3.13` (nur 3.12) |
| Backend | FastAPI + SSE (`sse-starlette`), Typer-CLI, watchdog; setuptools-Build |
| NLP / Analyse | spaCy 3.8, NLTK, langdetect, camel-tools + pyarabic + arabic-reshaper + python-bidi (Arabisch-Support), wordcloud, matplotlib |
| Inference-Clients | `openai`-SDK; dedizierte Endpunkte pro Modell `WHISPER_API_BASE` / `NER_API_BASE` / `DIARIZATION_API_BASE` mit Fallback auf `OPENAI_API_BASE` |
| Frontend | React 19, TanStack Query/Table/Virtual, Zustand 5, Recharts 3 |
| Tests | pytest + pytest-asyncio + respx |

### `translator` — schlanker Textübersetzungsdienst

| | |
|---|---|
| Version / Python | 1.1.0 · `>=3.11,<3.12` (nur 3.11) |
| Backend | FastAPI + Uvicorn, `openai`-SDK gegen ein instruction-getuntes Modell der Gemma-Klasse (env-gewählt via `TEXT_MODEL`, keine lokalen Gewichte), langcodes/langdetect/pycountry |
| Frontend | Minimale React-19-SPA (nur TanStack Query) |

### `open-webui-service` — Chat-UI (Deployment-Wrapper, v0.1.2)

| | |
|---|---|
| Image | Gepulltes Upstream-Image `ghcr.io/open-webui/open-webui:0.11.0` (Digest-gepinnt) — kein App-Quellcode |
| Konfiguration | `.env` als Single Source of Truth; mitgelieferte Provider hart deaktiviert, sodass alle Inference über den LiteLLM-Endpunkt auf `inference-net` läuft |
| Zugang | Über die dedizierte `:8443`-Site von edge-plane (kein Unterpfad-Support), Alias `open-webui` auf `edge-net` |
| State | Externes Volume `open-webui-data`; `make nuke`-Gate |
| Lebenszyklus | Eigenes Makefile (nicht auf `common.mk`); einzeilige `VERSION`-Datei |

---

## Tier: Orchestrierung, gemeinsames Frontend & Tooling

### `deploy` — Lebenszyklus-Ebene der Föderation

Nur Make + Shell; besitzt keine Services, Daten oder Images. Sequenziert die
eigenen `make`-Targets der Mitglieder: `make up` fährt Inference → State →
Obs → Apps → Edge der Reihe nach hoch, health-gated, auf einem einzelnen
Host; `make up-dev` ist das Dev-Pendant (Host-Ports für State + Apps
veröffentlicht). Host-Profil über `federation.env` (`INFRA_ROOT`,
Tier-Verzeichnislisten, `DATA_PROFILE=cpu|cuda`). Verteilt außerdem
`network` / `volumes` / `down` / `bundle` / Airgap-`load` auf die Mitglieder.

### `infra-ui` — gemeinsames Designsystem `@infra/ui` (v0.8.x)

| | |
|---|---|
| Stack | React 19 (Peer-Dependency), Tailwind-CSS-v4-Tokens, class-variance-authority + clsx + tailwind-merge |
| Primitives | UI-Primitives + AppHeader, Light-/Dark-Theming (`useTheme`, CSS-Variablen), **ForceGraph** (Canvas-Kraftsimulation + Graph-Export, genutzt von chorus) |
| Build | tsup → **committetes, vorgebautes `dist/`** (nur zur Build-Zeit, kein Rebuild bei der Installation); vitest + Testing Library + happy-dom; ESLint 9 + Prettier |
| Konsum | Gepinnte pnpm-Git-Dependency (Release-Tag-URL, z. B. `#v0.8.1`) in allen vier App-Frontends; Theming pro App via `--app-accent`; erfordert eine Tailwind-`@source`-Zeile |
| CI | Enthält einen dist-Guard (dist muss zu src passen) |

### `pr-notify` — Telegram-Notifier (Tooling)

Ein einzelnes, rein auf der Standardbibliothek basierendes Python-Skript, das
die GitHub-Such-API nach neuen nos-tromo-Issues/PRs abfragt (getrennte
`is:issue`- / `is:pull-request`-Suchen), ausgeführt von einem alle fünf
Minuten laufenden GitHub-Actions-Workflow, der seinen Gesehen-Zustand
(`seen_prs.json`) zurück ins Repo committet — mit Heartbeat gegen das
automatische Deaktivieren geplanter Workflows. Kein Laufzeit-Mitglied der
Föderation; keine der gemeinsamen `uv`- / `common.mk`-Konventionen gilt hier.

---

## Gemeinsame Toolchain & Konventionen

**Python (chorus, docint, Nextext, translator; nur Lint für vllm-service):**
`uv` + `uv.lock` als maßgebliche Quelle für Abhängigkeiten; ruff `0.15.14`
(Lint + Format, Google-Docstrings, 120 Spalten) und pyrefly `1.1.1` im
Strict-Preset — beide spiegeln die kanonischen Konfigurationen in
`nos-tromo/.github/configs/python-strict/` (Abweichung lässt die CI
fehlschlagen); pytest 9; pre-commit 4. `make verify` ist das lokale
Pre-Push-Gate (pre-commit + Frontend-`pnpm lint`/`pnpm build`, wo vorhanden).

**Frontend (alle vier Apps + infra-ui):** pnpm 9.12, TypeScript ~6.0 strict
(`tsc -b` im Build), Vite 8, Vitest 4 + Testing Library + happy-dom, ESLint 9
Flat Config + typescript-eslint 8 (gemeinsame Konfiguration in
`.github/configs/frontend-eslint/`), Tailwind v4 via `@tailwindcss/postcss`,
Inter via `@fontsource/inter`.

**Make / Bundling:** App-Makefiles `include`n ein vendortes `make/common.mk`;
Bundle-Skripte sourcen ein vendortes `scripts/bundle-lib.sh` — beide kanonisch
in `nos-tromo/.github` (aktuell v3.x-Linie) und per CI auf Drift geprüft.
`make bundle` baut den letzten von HEAD aus erreichbaren annotierten Tag
(tag-versioniertes Artefakt); `make bundle-dev` bündelt den Working Tree für
Dev-/Staging-Soak. `data-plane`, `obs-plane` und `open-webui-service` behalten
eigene Makefiles.

**CI / Release:** GitHub Flow (kurzlebige `feature/*`/`fix/*` → PR → CI →
`main`); wiederverwendbare GitHub-Actions-Workflows aus `nos-tromo/.github`
(referenziert `@v3`). Beim Merge nach `main` liest der gemeinsame
`release-tag`-Workflow die deklarierte Version (`pyproject.toml` oder
`VERSION`) und erzeugt den annotierten `vX.Y.Z`-Tag — idempotent, mit
Anti-Downgrade-Schutz. Dasselbe Bundle-Artefakt wird auf Staging getestet,
bevor es in die Produktion promotet wird. Design-Dokument:
`2026-07-02-federation-release-workflow-design.md` im `docs/`-Verzeichnis des
infra-Workspace (nicht versioniert); Runbook: die [README](../README.md)
dieses Repos, § Releasing.
