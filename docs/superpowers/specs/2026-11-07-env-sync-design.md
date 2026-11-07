# Design: keeping every `.env` in step with its `.env.example` (`make env`)

Date: 2026-11-07
Status: approved

## Problem

Every release changes some member's `.env.example`, and the operator then edits
each host's `.env` by hand: eight members plus deploy's own `federation.env`,
usually over SSH on the headless airgap host. Over six months the examples saw
about 170 commits, about 355 keys added and 90 removed, several of them
renames. The audit-trail upgrade made this concrete: step C5 of
`docs/runbooks/upgrade-to-audit-trail.md` is "Edit every `.env`", including
secrets that must hold the same value in two repos (its table B2).

Drift is silent. A host whose translator `.env` still sets `TRANSLATE_MODEL`
keeps a value nothing reads since the rename to `TEXT_MODEL`; an obs-plane
`.env` copied before the audit trail lacks the whole audit block and only fails
when a required value is missing.

## Decision

A stdlib-only Python script in deploy, `scripts/env_sync.py`, driven by two
targets:

- `make env` brings every `.env` (and `federation.env`) in step with its
  example and prints a report.
- `make env-check` runs the same planner without writing anything. It is the
  preview, and `make up` / `make up-dev` run it first: the bring-up stops before
  any tier starts when a `.env` is behind.

No new tool (Ansible templates, sops/dotenvx, Vault, layered env files). Those
solve other problems — multi-host rollout, secret encryption and distribution —
or need every member's compose wiring rebuilt, and none of them handles renames
or values shared between repos. The script needs no git, so it runs on the
airgap host; `copy-bundles.sh` already ships deploy's `scripts/`.

## Goals

- One command updates every `.env`: new example keys arrive, values the
  operator set never change.
- `make up` never starts with a `.env` that is behind its example.
- Renamed keys carry their value over when the rename is new to this host.
- Defaults the operator never touched follow the example, except where that
  could delete data or break a secret.
- Values that must be equal across members (edge secrets, the Qdrant key, the
  router key, ...) are checked, filled in, and generated on request.

## Non-goals

- Multi-host configuration management (the scope boundary in `CLAUDE.md`).
- Layered env files or any change to how members read their `.env`.
- Following defaults in `federation.env`.
- Mechanical enforcement of the example conventions in the hub's CI (possible
  later).

## Anti-goals (a failure even if it technically works)

- A set value changes, or a secret is printed, logged or written anywhere other
  than its `.env` and deploy's state directory.
- A file appears inside a member directory other than its `.env` (the build
  host is a git checkout; a stray secrets file could be committed).
- A value that was ignored for months starts to apply without the operator
  seeing it.
- `make -n` or `make env-check` writes anything.

## Constraints

- Python ≥ 3.9, standard library only; no installation on the airgap host.
- The members read `.env` through `docker compose --env-file .env` (interpolation)
  and the apps also through `env_file:`. Several bespoke Makefiles grep values
  with `grep -E '^KEY=' .env | cut -d= -f2`, so duplicate keys break them.
- auditd watches each member's `.env` and `federation.env` (`-p wa`); a watch
  survives the file being replaced by rename.
- `make -n` still runs recipe lines that mention `$(MAKE)`.

## Behaviour

### Reading a file (compose-go dotenv semantics)

- A line whose first non-blank character is `#` is a comment, including
  edge-plane's indented continuation lines.
- `export KEY=` and blanks around `=` are accepted; keys match
  `[A-Za-z_][A-Za-z0-9_]*`.
- Unquoted value: leading blanks trimmed; an inline comment starts only at a
  blank followed by `#`; trailing blanks trimmed. Consequence: `KEY=   # text`
  has the value `# text`. A value starting with `#` therefore counts as unset,
  and the lint warns about the line.
- Quoted values on one line are accepted; multi-line quotes are refused.
- Refused (the file stays untouched, the error names the line number but never
  its content): multi-line values, YAML-style `KEY: v`, a bare `KEY`, duplicate
  keys, a byte-order mark, CRLF line ends.
- `${X}` inside a `.env` resolves against the shell environment and only
  earlier lines of the same file. Any change in the order of a reference and
  its definition is refused (self-references are exempt).
- `federation.env` is make syntax (the Makefile `-include`s it). Only `KEY=v`,
  `KEY = v` and `# KEY=v` are accepted; anything else (`+=`, `?=`, `:=`,
  `export`, conditionals) is refused. Inline comments are linted, because make
  keeps the blanks before them.

### Rebuilding a file

- The output walks the current example line by line. At each key's anchor line
  the operator's line goes in **byte for byte**, inline comment included;
  without one the example line stays.
- Anchor of a key, in order: the example's active line; a commented line whose
  text equals the operator's line; one with the same value; the first that does
  not read as prose; the first. Commented candidates are `#KEY=` and `# KEY=`;
  `#   KEY=` is prose.
- Keys the example does not know go, unchanged and with the comment lines
  directly above them, into a block at the end headed `# [env-sync] ...`. They
  are never deleted.
- Comments come from the example. The report counts the operator's comment
  lines that were dropped, and the previous file stays in the backup.
- Removed versus never there: a key that was active in the last synced example
  and that the operator commented out or deleted stays unset (it is written
  commented at its anchor and is not re-added on every run). Without a synced
  example on record, a missing key is added and a commented-out one stays unset
  with a notice.
- A missing `.env` is created from the example, mode 0600.

### Renames

- Directive in the member's example, kept forever: `# renamed: OLD -> NEW`.
- Validation: the grammar; OLD is no longer a key of the example; the end of a
  chain is one; no cycles; no OLD twice. A file where another line references
  `${OLD}` is refused.
- The value is carried over only when the last synced example still documents
  OLD (the rename is new to this host) and the operator's OLD line is not an
  inherited default. Carrying rewrites the key prefix only; the rest of the line
  stays byte for byte.
- Otherwise OLD goes into the end block with "renamed to NEW, value not
  carried": a value ignored for months does not silently start to apply.
- When both OLD and NEW are set, NEW wins and OLD goes into the end block with a
  warning.

### Changed defaults

- deploy keeps, per file, a copy of the example it was last synced against.
- A key is **inherited** when the operator's line is byte-identical to the
  active line of that copy (trailing blanks ignored). Any edit, including an
  added `# pinned` comment, makes the value the operator's own.
- Inherited keys follow the new example: a new value; only commented now means
  unset; gone from the example means removed (reported).
- Held, never followed, only reported: secret-like names (`SECRET`, `PASSWORD`,
  `PASSPHRASE`, `TOKEN`, or ending in `_KEY`), any name containing `RETENTION`
  (lowering a retention deletes data), keys of shared groups, and the whole of
  `federation.env` (there absent keys are only added, so the member list never
  changes while `make env` runs).
- First run without a synced example: nothing follows. The report lists the
  keys whose value differs from the example's default, by name only.

### Shared values (`env-sync.conf`, tracked in deploy)

Each row names a group, a member, a key and flags.

| Group | Members | Flags |
|---|---|---|
| edge-host | edge-plane `EDGE_HOST`, obs-plane, open-webui-service | edge-plane `source` |
| docint-, nextext-, translator-, open-webui-edge | edge-plane `*_EDGE_SECRET` and the same name in the app | `gen` |
| grafana-edge, audit-edge | edge-plane and obs-plane | `gen` |
| qdrant-key | data-plane and docint `QDRANT_API_KEY` | `gen` |
| openwebui-jwt | open-webui-service `OPEN_WEBUI_FORWARD_JWT_SECRET`, vllm-service `AUDIT_OPENWEBUI_JWT_SECRET` | `gen` |
| nextext-token | docint `NEXTEXT_API_KEY`, Nextext `NEXTEXT_SERVICE_TOKENS` | `gen`, `entry=docint` |
| router-key | vllm-service `OPENAI_API_KEY`; docint, Nextext, translator, open-webui-service `OPENAI_API_KEY` | vllm-service `source` |

The router key is LiteLLM's master key (`master_key: os.environ/OPENAI_API_KEY`
in vllm-service), and every app reaches inference through `vllm-router`, so
vllm-service is the authority.

- A row is **unset** when it has no active line, its value is empty, starts with
  `#`, contains `<...>`, or equals any example value of any row of the group
  (current or synced copy, active or commented). Placeholders such as
  `change-me` or `sk-1234567890` therefore never spread.
- A `source` row counts as set whenever it has an active line.
- A set value fills the unset rows. Two different set values are a conflict:
  nothing in the group changes and `env-check` fails.
- `entry=docint` reads the `docint=` entry of the comma-separated list; it never
  appends to a list the operator set and fills only a wholly unset list.
- Only values made of `[A-Za-z0-9._~:/@+=,-]` are copied.
- `ENV_GENERATE=1` generates a fresh `secrets.token_hex(32)` per `gen` group that
  is unset everywhere. Secrets of a single member (Authelia's keys) are never
  generated; they are reported as placeholders.
- Values are never printed: "filled (from edge-plane)", "conflict between ...".
  Members not on this host are skipped. A table key that the member's example
  does not know is a warning.

### `env-check`

- Same planner, writes nothing anywhere; the same report in "would" wording,
  compact when everything is in step (`env: 9 files in sync`).
- **Behind** means the decoded key-to-value maps of the current and the planned
  file differ; comment and order differences do not count.
- Fails on: behind, a shared conflict, a refused file, a missing `.env` or
  example, no synced example on record ("never synced: run `make env` once").
- Warns on: unknown keys, rename conflicts, held defaults, lint findings,
  placeholders, groups unset everywhere.
- `ENV_ALLOW_DRIFT=1` overrides, one-shot, with a loud warning.

### Writing and state

- State lives in `deploy/.env-sync/` (0700, gitignored, never shipped by
  `copy-bundles.sh`): `base/` for the synced examples, `backup/<file>/<UTC>.env`
  (0600, the last 5 per file), and a lock.
- Atomic write: a temporary file in the state directory, then `os.replace` onto
  the `.env`. Same filesystem required; mode and owner carried over; symlinks,
  hard links and files changed since they were read are refused, and so are
  other users' files unless running as root. Nothing is ever created in a member
  directory except the `.env` itself. A world-readable `.env` is a warning.
- Self-checks before every write; any failure refuses the file: the output
  parses cleanly; its decoded map equals the planned one; every kept line
  appears exactly once byte for byte; reference order is unchanged; planning the
  output again yields it unchanged.
- auditd records each write as `fed-config` under the login user. The state
  directory gets an `rwa` watch, since the backups hold secrets.

### Report and exit codes

```
== obs-plane/.env            written (backup: .env-sync/backup/obs-plane/20261107T201500Z.env)
   added      AUDIT_RETENTION=8760h, AUDIT_VERIFY_INTERVAL=300
   filled     AUDIT_EDGE_SECRET (from edge-plane)
   held       GRAFANA_ADMIN_PASSWORD: example placeholder changed; yours kept
== vllm-service/.env         written
   renamed    AUDIO_GPU_MEMORY_UTILIZATION -> ASR_GPU_MEMORY_UTILIZATION (value carried)
   unknown    SPARSE_MODEL VISION_MODEL (kept at the end)
== shared    conflict EDGE_HOST: open-webui-service differs from edge-plane
summary: 3 written, 5 unchanged, 0 refused, 1 conflict. Changes apply with the next 'make up'.
```

- A value is printed only when it comes from an example and the key is not
  secret-like.
- Exit 0 ok; 1 a refused file, a conflict, or (check) behind; 2 a usage or
  table error or a held lock, with nothing changed.

## Conventions for member `.env.example` files

Canonical in deploy's `CLAUDE.md` (cross-repo contract) and the runbook:

- Settings are commented as `# KEY=value`.
- Prose and alternatives never start with `KEY=` right after `# `.
- Never `KEY=   # comment`; put the comment on its own line.
- A rename is recorded as `# renamed: OLD -> NEW`.
- A new or renamed shared key means an edit to deploy's `env-sync.conf`.

## Rollout

1. deploy, one PR `feat/env-sync`: the script, the table, the targets and the
   `up` gate, tests, CI, docs; tried on a dev host before merging; release
   v0.7.0.
2. Member PRs, each with a patch release (an example reaches the airgap host
   only through a tagged bundle):
   - vllm-service: `# renamed: AUDIO_CUDA_VISIBLE_DEVICES -> ASR_CUDA_VISIBLE_DEVICES`,
     `# renamed: AUDIO_GPU_MEMORY_UTILIZATION -> ASR_GPU_MEMORY_UTILIZATION`;
     reword the prose line that starts with `# NER_MODEL=`.
   - Nextext: `# renamed: NEXTEXT_RESPONSE_LANGUAGE -> RESPONSE_LANGUAGE`; reword
     the prose lines that start with `# INFERENCE_PROVIDER=` and
     `# RESPONSE_LANGUAGE=`.
   - docint: put the comment of `QDRANT_API_KEY=   # ...` on its own line (today
     compose reads the comment as the value); reword the prose lines that start
     with `# AUDIT_OUTPUT=` and `# INFERENCE_PROVIDER=`. No directives:
     `PIPELINE_MAX_WORKERS` and `INGESTION_CHUNK_SIZE` were never read by code.
   - translator: no directive. `TRANSLATE_MODEL` became `TEXT_MODEL` with a wider
     role (it also detects the source language) and another default; the report
     shows the old key as no longer read.
   - Each adds a one-line pointer to the conventions in its `CLAUDE.md`.
3. First adoption on the airgap host: replace deploy's files first and run
   `make env` against the still-old member examples (this records the synced
   examples), then replace the member files and run `make env` again, so the
   first upgrade already follows defaults and carries renames. On the build
   host, run `make env` after `make pull`, not between `make bundle` and
   `make pull`.

## Left to the builder

- Module layout inside `scripts/env_sync.py`, the exact report wording, and how
  the prose heuristic for anchors is phrased.
- How the table file is parsed, as long as it validates on every run (at least
  two rows per group, at most one `source`, each key in at most one group).
- The CI matrix for Python 3.9, as long as every action is pinned to a full
  commit SHA.
