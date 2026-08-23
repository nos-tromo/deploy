# 0002 — open-webui-service as a lifecycle member via `OPENWEBUI_DIR`

Status: accepted (2026-06-28)
Date: 2026-06-28
Recorded: 2026-08-22 (retroactively, from the README's *Known integration points*)

## Context

`open-webui-service` is the odd member of the app tier. Every other app in
`APP_DIRS` (`chorus`, `docint`, `Nextext`, `translator`) is first-party: built
from source in this federation, on the shared `common.mk`, with the same
`network` / `volumes` / `up` / `down` / `bundle` target contract.
`open-webui-service` is none of those things — it is a thin deployment wrapper
around a **pulled upstream image**, and it kept a bespoke Makefile rather than
adopting `common.mk`.

That left two questions: does it belong in `APP_DIRS` alongside the first-party
apps, and is it a full lifecycle member at all or only a bundle/load target?

## Decision

**open-webui-service is folded in via `OPENWEBUI_DIR`, not `APP_DIRS`.** It is the
upstream chat UI — a pulled image with a bespoke Makefile (it skipped the
`common.mk` rollout) — so it is kept in its own variable rather than mixed into
the first-party `APP_DIRS`. But it is a full lifecycle member: `setup`, `up`,
`down`, `ps`, `logs`, and `bundle`/`load` all iterate `$(APP_DIRS) $(OPENWEBUI_DIR)`.
This works because it honors the same target contract as the apps — `.env`,
`docker/compose.yaml`, and the `network` / `volumes` / `down` / `bundle` targets
(its volume target was renamed from the singular `volume` to `volumes` to match).
It comes up in the app tier, attaching only to `inference-net` (like Nextext and
translator).

Set `OPENWEBUI_DIR` empty in `federation.env` to drop it from the federation
entirely. It still self-manages, so you can also run it standalone:

```bash
make -C ../open-webui-service network volumes   # one-time
make -C ../open-webui-service up                # detached, self-contained
```

## Alternatives considered

- **Fold it into `APP_DIRS`.** Simplest loop, but it erases a real
  distinction — an operator reading `APP_DIRS` would see a pulled upstream
  image listed as a first-party app, and a future `common.mk`-only assumption
  in the app loop would silently break it.
- **Keep it bundle/load-only**, outside the lifecycle loops, and require
  operators to run its Makefile by hand. Rejected: it would be the one service
  on the host that `make up` / `make down` / `make ps` do not cover, which
  defeats the point of a lifecycle layer.

## Consequences

- Positive: one variable is the whole opt-out. `OPENWEBUI_DIR=` in
  `federation.env` removes it from every loop with no other edit.
- Positive: the first-party/upstream distinction stays visible in the
  configuration surface rather than being buried in the Makefile.
- Negative: every app-tier loop must iterate `$(APP_DIRS) $(OPENWEBUI_DIR)`
  rather than a single list — a forgotten `$(OPENWEBUI_DIR)` in a new target is
  a silent omission, not an error.
- **Load-bearing constraint:** the plural `volumes` target name must stay
  aligned on the open-webui side, or `setup` (`make network volumes`) breaks.
  See [CLAUDE.md](../../CLAUDE.md) § *Cross-repo contract*.
