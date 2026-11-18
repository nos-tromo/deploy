# 0008 — Retire Open WebUI from the federation

Status: accepted (2026-11-17)
Date: 2026-11-17

## Context

Ghostwriter replaces Open WebUI ([0007](0007-ghostwriter-replaces-open-webui.md)).
Since open-webui-service v0.15.0 and edge-plane v0.17.0, Open WebUI is marked as
being retired; 0007 left its removal to a phase 2.

Open WebUI never ran in production. No chats need to be kept or exported, no
notice period applies, and no audit chain from operation has to stay
verifiable. Everything it holds can be deleted.

## Decision

**Open WebUI leaves the federation.**

- deploy drops `OPENWEBUI_DIR` and its use in every target, its rows and groups
  in `env-sync.conf` and its bundle in `copy-bundles.sh`.
- edge-plane no longer publishes `:8443`, routes `/webui`, sends
  `OPEN_WEBUI_EDGE_SECRET` or shows an Open WebUI tile in the portal.
- workbench closes its terminal door and drops the `open-webui` caller and
  `OPEN_WEBUI_WORKBENCH_KEY`. docint's MCP door and Ghostwriter's stay as they
  are.
- vllm-service no longer verifies Open WebUI's user token
  (`AUDIT_OPENWEBUI_JWT_SECRET`) and drops the cache partitions `user:<email>`
  and `open-webui`.
- obs-plane no longer collects Open WebUI's log lane or lists
  `open-webui-service` / `retention` among the audit emitters.
- The hub's profile page shows Ghostwriter in place of Open WebUI.
- What Open WebUI holds on a host is deleted, not migrated, once per host
  ([retire-open-webui.md](../runbooks/retire-open-webui.md)).
- The repository nos-tromo/open-webui-service is archived.

## Alternatives considered

- **Unhook first, clean up later.** Small PRs take Open WebUI out of bring-up
  and the portal first, and a second round removes the dead code. That is two
  rounds of PRs in deploy and edge-plane, and dead code until the second.
- **Keep workbench's terminal door for now.** Without
  `OPEN_WEBUI_WORKBENCH_KEY` the door is already closed. It would save the
  largest part of the work, but leave an unused door, a contract stack against
  the Open WebUI image and its Dependabot bumps in the code.

## Consequences

- The federation has twelve repositories.
- edge-plane publishes `:443` and `:80` only.
- ADR 0003 continues to apply without Open WebUI's retention sidecar as a
  source. A host rolls out obs-plane v0.16.0 only after its Open WebUI is
  stopped, or the verifier reports the sidecar as an unlisted source.
- workbench has two callers, docint and Ghostwriter. On a host, workbench
  v0.5.0 starts only with a key of one of them.
- ADR 0002 lapses and 0005 loses its first caller. ADRs 0003, 0006 and 0007
  keep their text; where they name Open WebUI, they describe the federation at
  the time they were written.
- The repository and its pull requests stay on GitHub, archived, so the links
  keep working.
- Open WebUI's data on the hosts is deleted, with no way back.
