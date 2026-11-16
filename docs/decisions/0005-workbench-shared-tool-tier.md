# 0005 — workbench, the shared tool tier

Status: accepted (2026-11-15)
Date: 2026-11-15
Extended by [0006](0006-docint-calls-workbench-over-mcp.md).

## Context

Open WebUI can call tools, but its own tools and functions are Python that its
backend runs with `exec`, without a sandbox, with the whole database in reach.
Its code interpreter runs in the user's browser and keeps files where no
retention reaches them. open-webui-service v0.13.0 therefore switched tools,
functions, the code interpreter and code execution off.

The assistant still needs office tools: reading the documents and tables users
drop into a chat, analysing tables with Python, filling report templates, and
writing new tables and documents for download. No member offered them, and
each would have to run beside some member's data.

Open WebUI v0.13.3 has one integration that moves files both ways, the
"terminal server": the browser uploads chat attachments through Open WebUI's
proxy straight to the server, the model's tools are the operations of the
server's `/openapi.json`, and the server's files are shown in the chat with a
preview and a download. Its MCP integration passes only model arguments, no
files, and renders only images from results.

## Decision

**workbench is the federation's shared tool tier.** A new member,
nos-tromo/workbench (v0.1.0), with two services:

- `api`: FastAPI, uid 10001, the caller's door. It checks the caller's bearer
  key, keeps a workspace per user and a folder per chat, offers the tools,
  writes the audit chain and runs the retention sweep. It parses no user file
  itself.
- `executor`: no network. It runs every job that touches file content
  (reading, search, Python, templates, writing) as a fresh process under one
  of a pool of slot uids, with limits. The api reaches it over a Unix socket.

Open WebUI connects to it as a terminal server, with the bearer key
`OPEN_WEBUI_WORKBENCH_KEY`, the same value in both members (env-sync group
`workbench-key`).

**A new invariant: tool execution is shared, never embedded.** Apps run no
code sandbox or office tooling of their own; they call workbench, as they call
the router for inference. Compute tools are central: reading documents,
Python, templates, writing tables and documents. Data tools stay with the
data's owner: docint's `search`, `read`, `keyword` and `topics` stay in
docint, and workbench never joins `data-net`.

**A fourth network seam, `workbench-net`.** An external network, created by
the `make network` of each member that joins it. Its members are workbench's
`api` (alias `workbench-api`, port 8000, not published in production; the dev
overlay of `make up-dev` publishes it on the host's loopback), its callers
(`open-webui`) and obs-plane's scraper. The api joins no other seam and calls
no model. The executor has no network at all.

**A new data store, `workbench-data`.** An external volume owned by
workbench: `docker compose down -v` leaves it, and only workbench's
`make nuke`, after a typed confirmation, removes it. Every workspace file and
job record is deleted `WORKBENCH_RETENTION_DAYS` days after it was made (7 by
default), each deletion recorded in the audit trail (ADR 0003) before it
happens. A changed period applies at the next sweep, with no notice period.
open-webui-service's banner names the period; deploy's `.env` sync copies
workbench's value to it (env-sync group `workbench-period`, workbench the
source).

**Tier order: inference → data → obs → workbench → apps → edge.** deploy
brings workbench up after obs, so its bring-up is observed, and before the
apps, so Open WebUI finds it. The gate is `workbench-api:8000` on
`workbench-net`. workbench needs no other member to start; Open WebUI starts
and chats work without it, and only its workbench tools need it.
`WORKBENCH_DIR` empty runs a host without the tier.

**Where the two products sit.**

| | docint | open-webui-service + workbench |
|---|---|---|
| Role | data analysis platform for larger projects | chat agent and ad-hoc assistant |
| Data | lasting collections, vector search, Report Builder, stated purpose | short-lived workspace, 7 days, no collections |
| Typical use | set up a project, ingest a corpus, work it for weeks | drop files into a chat, ask, download the result |

The two products share no data store.

**What the executor needs from the host.** It requires cgroup v2 and a cgroup
namespace of its own, Docker's default on cgroup v2, because its watchdog
reads the container's shared memory (`memory.stat` `shmem`). It refuses to
start on cgroup v1, under `cgroupns: host`, and on a rootless daemon without
the memory controller delegated to it. Under any user-namespaced daemon
(userns-remap, or rootless Docker with the memory controller delegated, where
the executor starts) `/dev/zero` is the host's node, bind-mounted, and stays
writable to jobs, so a job can hold shared memory that no job's count shows.
A container-wide check then stops every running job, of every user, once more
than 64 MiB of such memory shows. That is a denial of service within one
user's limits; it reaches no other user's data (workbench `docs/sandbox.md`,
Residual risks).

## Alternatives considered

- **Tools inside Open WebUI.** Its tools and functions run in its backend
  with `exec`, without a sandbox, next to every user's chats; the code
  interpreter keeps files in the browser and stores its image output on the
  server. That is why open-webui-service v0.13.0 switched them off.
- **Tools inside docint.** docint keeps lasting collections on `data-net`,
  for a year by default. A sandbox there would run next to that store, chat
  files would enter a project's lifecycle instead of a 7-day workspace, and
  Open WebUI would need a path into docint. Under the invariant above, docint
  becomes a caller of workbench instead (version 2).
- **Upstream open-terminal.** It gives the model a shell, its key grants
  everything, and its `SECURITY.md` says it does not isolate users.
  Per-user containers need the separately licensed "Terminals" orchestrator.
  workbench implements only the part of the terminal contract Open WebUI
  calls, with typed tools and no shell.
- **MCP only.** Open WebUI passes no files over MCP and renders only images
  from its results, so the user's files could not reach the tools and the
  results could not reach the user.
- **workbench on `inference-net`, with the key as the only barrier.** No new
  seam, but every app backend and the router sit on `inference-net`, and each
  could reach the api. The api trusts the user id a caller sends once the key
  matches, so the key alone would stand between every member on that network
  and every user's files. On `workbench-net`, joining is an access decision
  of its own, as on `edge-net`.

## Consequences

- One more member to release and bundle. workbench is versioned in
  `pyproject.toml`, tagged by the shared `release-tag` workflow and built on
  the vendored `common.mk`. Its bundle has two images, `api` and `executor`,
  both built locally. deploy's `setup`, `up`, `up-dev`, `down`, `ps`, `logs`,
  `clone`, `pull`, `bundle`, `load`, `prune` and `env` cover it through
  `WORKBENCH_DIR`, and `copy-bundles.sh` ships it. The federation has twelve
  repositories.
- The Open WebUI terminal contract is pinned in workbench's CI. Its
  `contract` job runs workbench against the Open WebUI image pinned in
  `contract/compose.yaml`, so an image bump that changes what Open WebUI
  calls, sends or passes back fails there. That pin must be the tag and digest
  open-webui-service deploys; a human checks it before a bump merges.
- The members that join `workbench-net` change in their own pull requests:
  open-webui-service connects to workbench, and obs-plane scrapes its metrics
  and accepts its audit chain.
- Hosts need cgroup v2 with Docker's default cgroup namespace; the bring-up
  runbook checks it. Under a user-namespaced daemon (userns-remap or rootless)
  the `/dev/zero` residual above applies. The executor has not yet run on a
  userns-remap host: the first such bring-up is a check for the owner.
- Version 2, docint as a second caller over an MCP door with a key and a tool
  list per caller, is a separate decision with its own design round.
- This record states no legal conclusions. The legal classification, the
  purpose of the processing, the retention period and the impact assessment
  are decided and documented by the operator, outside these repositories, as
  in ADR 0003. workbench's `docs/data-flow.md`, `docs/permissions.md`,
  `docs/audit.md` and `docs/sandbox.md` give the facts for them.
