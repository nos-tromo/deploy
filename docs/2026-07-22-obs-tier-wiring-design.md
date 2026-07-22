# deploy: obs tier wiring (design)

Status: approved design, pre-implementation
Date: 2026-07-22
Scope: this repo (`deploy`) only — one PR. No changes to obs-plane or any
other member.

## Purpose

`obs-plane` (the federation observability plane: Prometheus + Grafana +
Loki + collectors) shipped as a standalone member. This change makes it a
first-class tier in the federation lifecycle so `make up` / `down` /
`ps` / `logs` / `pull` / `bundle` / `load` cover it like every other
member, health-gated in order.

## Decisions (settled)

- **Tier position: after state, before apps** — bring-up order becomes
  `inference -> data -> obs -> apps`. Observability is watching before
  the app tier starts, so app bring-up logs land in Loki and a
  crash-looping app is visible from its first scrape. (Supersedes the
  obs-plane design doc's follow-up note that said "after apps" — that
  wording was never a considered decision.)
- **First-class tier variable, not an app-loop member** — `OBS_DIR`
  sits beside `VLLM_DIR` / `DATA_DIR`, with explicit lines per target,
  matching the Makefile's tier style. Folding it into the app loop was
  rejected: wrong ordering (it must precede apps) and wrong semantics
  (infrastructure, not an app).
- **Opt-out** — `OBS_DIR` set empty in `federation.env` drops the tier
  entirely; every reference must tolerate the empty value.
- **Health gate: TCP on `prometheus:9090` over `data-net`** —
  obs-plane's `prometheus` service joins the external `data-net` and
  carries its service-name alias there, so the existing
  `scripts/wait-healthy.sh` mechanism works unchanged. Prometheus
  TCP-ready is the gate; Grafana/Loki sit on obs-plane's internal
  network and are not probeable via a shared network (obs-plane's own
  `make health` covers them for operators).
- **Mode-sensitive tier** — the obs tier uses `$(MODE_UP)`: `up-dev`
  publishes Grafana on the host (obs-plane's dev override), production
  `up` publishes nothing.

## Changes

### Makefile

- `OBS_DIR ?= obs-plane` beside the tier vars, with a comment noting it
  is a pulled-image member with a bespoke Makefile (data-plane pattern)
  and can be set empty to run without observability.
- `setup`: after the data-plane line —
  `[ -z "$(OBS_DIR)" ] || $(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) network volumes`
  (guard style for all obs tier lines; the existing `for`-loops already
  tolerate an empty variable).
- `up` / `up-dev` (shared recipe): new block between state and apps:

  ```
  == obs tier (obs-plane $(MODE_UP)) ==
  make -C $(INFRA_ROOT)/$(OBS_DIR) $(MODE_UP)
  ./scripts/wait-healthy.sh data-net prometheus:9090
  ```

  Both lines skipped when `OBS_DIR` is empty.
- `down`: reverse order — apps, **then obs**, then state, then
  inference (obs watches the apps shut down; its own volumes are
  external, `down` never touches them).
- `ps` / `logs`: obs block via the existing `compose` helper (obs-plane
  keeps the standard `docker/compose.yaml` layout, and `.env` exists on
  a configured host, same contract as every member).
- `pull`: obs-plane added to the repo list.
- `bundle`: `$(MAKE) -C $(INFRA_ROOT)/$(OBS_DIR) bundle` (no PROFILE —
  obs-plane has no profiles).
- `load`: obs-plane's `*.tar.gz` added to the glob list.
- `help`: order string becomes
  `inference -> data -> obs -> apps`; the summary line reports the obs
  dir (or that it is disabled).
- Header comment: bring-up order note updated to the four-tier order.

### federation.env.example

Add beside the other tier vars:

```
# Observability plane (Prometheus + Grafana + Loki). Pulled-image member,
# bespoke Makefile. Set empty to run this host without observability.
OBS_DIR=obs-plane
```

### README.md

- Tier order / diagram updated to `inference -> data -> obs -> apps`
  (and reverse for down).
- Member/tier table (if present) gains obs-plane.
- Airgap-load flow mentions obs-plane's `obs-plane-pulled-<ver>.tar.gz`.
- Note the Grafana access model in one line: production shape publishes
  no ports; `make up-dev` publishes Grafana on the host (obs-plane's
  README has the details).

## Error handling

Unchanged model: a failing member `make` aborts the sequence at that
tier; `wait-healthy.sh` timing out prints its DNS-vs-TCP diagnosis and
exits non-zero. An empty `OBS_DIR` skips the tier silently by design.

## Testing

1. `make -n up` / `make -n down` show the four-tier order (and the
   correct reverse order).
2. `make -n up OBS_DIR=` shows the obs tier fully skipped, no errors.
3. Live partial check on the dev machine (no GPU, so the inference tier
   cannot run): bring up state + obs directly via their repos, then run
   `./scripts/wait-healthy.sh data-net prometheus:9090` — must pass;
   `make ps` / `make logs` show the obs block.
4. Existing deploy CI (shellcheck / yamllint / Makefile parse) green.
