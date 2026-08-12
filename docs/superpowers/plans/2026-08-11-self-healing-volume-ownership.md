# Self-Healing Volume Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every volume mounted read-write by a uid-10001 container becomes correctly owned as a side effect of `make up`, by replicating docint's `volume-permissions` one-shot into vllm-service, chorus, Nextext, and open-webui-service, removing `make migrate-cache`, and rewriting deploy's hardening runbook.

**Architecture:** One `volume-permissions` service per compose entrypoint whose services mount affected volumes read-write: the member's own image run as root, `find <mountpoints> -xdev ! -user 10001 -exec chown -h 10001:10001 {} +`, gating mounting services via `depends_on: condition: service_completed_successfully`. docint's copy (`docint/docker/compose.yaml`) is the canonical reference — do not deviate from its shape.

**Tech Stack:** Docker Compose YAML, GNU Make, Markdown. No test suites exist in any of these repos; verification is `docker compose config` rendering plus each repo's lint gate.

**Spec:** `docs/superpowers/specs/2026-08-11-self-healing-volume-ownership-design.md`

## Global Constraints

- The chown target is always exactly `10001:10001`; the find form is verbatim: `find <mountpoints> -xdev ! -user 10001 -exec chown -h 10001:10001 {} +`.
- Only members whose containers run as uid 10001 get a one-shot. **Never** touch data-plane, obs-plane, or edge-plane volumes (third-party uids; chowning them to 10001 breaks them).
- Never commit to `main` in any repo — every repo's change goes on a branch and lands via PR (user's standing rule).
- deploy's CLAUDE.md confidentiality rule applies in every repo: no real data, no absolute local paths in anything committed. Only relative project paths.
- All five repos are siblings: each task's working directory is the named repo checkout next to `deploy/` (the `infra/` workspace layout).
- Tasks 1–4 are independent of each other; Task 5 (deploy docs) goes last and references the member PRs.

---

### Task 1: vllm-service — one-shot in every compose shape, remove migrate-cache

**Files:**
- Modify: `vllm-service/docker/compose.yaml` (add anchor + service; add dep to 9 backends)
- Modify: `vllm-service/docker/compose.gliner-only.yaml`, `compose.rerank-only.yaml`, `compose.clip-only.yaml`, `compose.diarize-only.yaml`, `compose.asr-only.yaml`, `compose.vad-only.yaml`, `compose.embed-only.yaml` (add service + dep each)
- Modify: `vllm-service/Makefile` (remove `migrate-cache` from `.PHONY` line 62 and its help line ~106; delete target body lines ~369–391)
- Modify: `vllm-service/CLAUDE.md` (line ~77 mentions `make migrate-cache`)

**Interfaces:**
- Produces: a compose service named `volume-permissions` in each shape; Task 5's runbook names this service and the vad-image reuse.

- [ ] **Step 1: Create a branch**

```bash
cd ../vllm-service && git switch main && git pull --ff-only
git switch -c feat/self-healing-volume-ownership
```

- [ ] **Step 2: Add the dep anchor and one-shot service to `docker/compose.yaml`**

Insert after the `x-hardened` block (line ~67), before `services:`:

```yaml
x-volume-permissions-dep: &volume-permissions-dep
  volume-permissions:
    condition: service_completed_successfully
```

Insert as the first service under `services:` (before `router`):

```yaml
  ##########################################################
  # Volume ownership (deploy ADR 0001, self-healing)
  #
  # Backends run as uid 10001, but external volumes keep whatever
  # ownership they were created with (root, on a fresh `docker volume
  # create`) — image-build chowns cannot help, since the volume mount
  # shadows the image path at runtime. This one-shot root container
  # fixes ownership before any backend starts; it reuses the vad image
  # so the airgap bundle needs no extra image. The find form only
  # touches wrong-owner entries, so a boot over an already-owned
  # multi-GB huggingface-cache writes nothing. Canonical shape:
  # docint/docker/compose.yaml.
  ##########################################################
  volume-permissions:
    image: vllm-service-vad:${VLLM_SERVICE_VERSION:-latest}
    logging: *default-logging
    user: "0:0"
    read_only: true
    security_opt:
      - no-new-privileges:true
    network_mode: none
    # Root is the mountpoint itself — its parent dirs live on the
    # read-only image rootfs and must not (and cannot) be chowned.
    entrypoint:
      - /bin/sh
      - -c
      - >-
        find /home/app/.cache/huggingface/hub
        -xdev ! -user 10001 -exec chown -h 10001:10001 {} +
    volumes: *vllm-volumes
    restart: "no"
```

- [ ] **Step 3: Gate all nine hf-cache-mounting backends on it**

The nine services with `volumes: *vllm-volumes` are `chat`, `embed`, `embed-sparse`, `rerank`, `clip`, `asr`, `diarize`, `vad`, `gliner`. `chat` has no `depends_on` today — add one:

```yaml
    depends_on:
      <<: *volume-permissions-dep
```

The other eight already have a `depends_on` mapping (the daisy-chained GPU startup); merge the anchor into each existing block, e.g. for `embed`:

```yaml
    depends_on:
      <<: *volume-permissions-dep
      chat:
        condition: service_healthy
```

Do NOT touch `router`'s depends_on (it mounts no volume) and do NOT reorder the existing chain.

- [ ] **Step 4: Add the one-shot to each of the seven `*-only` compose files**

Same service block as Step 2 in each file, with two substitutions per file: the `image:` is that shape's own image, and `volumes:` is the literal mount (these files have no `x-vllm-volumes` anchor — check each; if one defines its own anchor, use it):

| File | image |
|---|---|
| `compose.gliner-only.yaml` | `vllm-service-gliner-cpu:${VLLM_SERVICE_VERSION:-latest}` |
| `compose.rerank-only.yaml` | `vllm-service-rerank-only:${VLLM_SERVICE_VERSION:-latest}` |
| `compose.clip-only.yaml` | `vllm-service-clip-cpu:${VLLM_SERVICE_VERSION:-latest}` |
| `compose.diarize-only.yaml` | `vllm-service-diarize-cpu:${VLLM_SERVICE_VERSION:-latest}` |
| `compose.asr-only.yaml` | `vllm-service-asr-cpu:${VLLM_SERVICE_VERSION:-latest}` |
| `compose.vad-only.yaml` | `vllm-service-vad-cpu:${VLLM_SERVICE_VERSION:-latest}` |
| `compose.embed-only.yaml` | `vllm-service-embed-only:${VLLM_SERVICE_VERSION:-latest}` |

```yaml
    volumes:
      - huggingface-cache:/home/app/.cache/huggingface/hub
```

Each file's single backend service gets `depends_on:` with the merge key (add the `x-volume-permissions-dep` anchor to each file too, after its `x-hardened` block). Do not modify the `*.override.yaml` files.

- [ ] **Step 5: Remove `migrate-cache` from the Makefile**

Three removals: (a) the word `migrate-cache` from the `.PHONY:` line (~62); (b) the help line `@echo "  make migrate-cache    one-time ADR 0001 migration: ..."` (~106); (c) the whole target body (~369–391, from `migrate-cache:` through `echo "OK: huggingface-cache fully owned by 10001:10001"`). Grep for leftover `SNAPSHOT` references afterwards — none should remain.

- [ ] **Step 6: Update CLAUDE.md**

Line ~77 references `make migrate-cache` (snapshot + chown + verify). Replace that passage with: ownership is self-healing — the `volume-permissions` one-shot in every compose shape chowns wrong-owner entries at each `up`; cautious airgap operators may snapshot the hf-cache mountpoint manually first (deploy `docs/hardening-migration.md`).

- [ ] **Step 7: Verify every shape renders and the Makefile parses**

```bash
for f in docker/compose.yaml docker/compose.*-only.yaml; do
  docker compose -f "$f" config -q || echo "FAIL: $f"
done
make help >/dev/null && make -n up >/dev/null
grep -rn "migrate-cache" . --include="*.md" --include="Makefile"   # must print nothing
```

Expected: no FAIL lines, no grep hits. Also run the repo's own lint gate if present (`make pre-commit` per common.mk).

- [ ] **Step 8: Commit, push, open PR**

```bash
git add -A && git commit -m "feat: self-heal volume ownership at up; drop migrate-cache

Adopts the docint volume-permissions one-shot (deploy ADR 0001) in the
main stack and all seven -only shapes: a root one-shot reusing the
shape's own image find-chowns wrong-owner hub entries before any
backend starts. Supersedes make migrate-cache, which is removed."
git push -u origin feat/self-healing-volume-ownership
gh pr create --fill
```

---

### Task 2: chorus — one-shot for chorus-state

**Files:**
- Modify: `chorus/docker/compose.yaml`

**Interfaces:**
- Produces: `volume-permissions` service gating `backend`.

- [ ] **Step 1: Create a branch**

```bash
cd ../chorus && git switch main && git pull --ff-only
git switch -c feat/self-healing-volume-ownership
```

- [ ] **Step 2: Add the one-shot as the first service under `services:`**

The file already defines `&default-logging` and `&hardened` anchors. Insert:

```yaml
  ##########################################################
  # Volume ownership (deploy ADR 0001, self-healing)
  #
  # The backend runs as uid 10001 with a read-only rootfs, but external
  # volumes keep whatever ownership they were created with (root, on a
  # fresh `docker volume create`). This one-shot root container fixes
  # ownership before the backend starts; it reuses the backend image so
  # the airgap bundle needs no extra image. The find form only touches
  # wrong-owner entries. Canonical shape: docint/docker/compose.yaml.
  ##########################################################
  volume-permissions:
    image: chorus-backend:${CHORUS_VERSION:-latest}
    logging: *default-logging
    user: "0:0"
    read_only: true
    security_opt:
      - no-new-privileges:true
    network_mode: none
    # Root is the mountpoint itself — its parent dirs live on the
    # read-only image rootfs and must not (and cannot) be chowned.
    entrypoint:
      - /bin/sh
      - -c
      - >-
        find /var/lib/chorus
        -xdev ! -user 10001 -exec chown -h 10001:10001 {} +
    volumes:
      - chorus-state:/var/lib/chorus
    restart: "no"
```

- [ ] **Step 3: Gate the backend**

Add to the `backend` service (it has no `depends_on` today; if it does, merge):

```yaml
    depends_on:
      volume-permissions:
        condition: service_completed_successfully
```

- [ ] **Step 4: Verify**

```bash
docker compose -f docker/compose.yaml config -q
make -n up >/dev/null
```

Expected: silent success. Run the repo lint gate if present (`make pre-commit`).

- [ ] **Step 5: Commit, push, open PR**

```bash
git add docker/compose.yaml
git commit -m "feat: self-heal chorus-state ownership at up (deploy ADR 0001)"
git push -u origin feat/self-healing-volume-ownership
gh pr create --fill
```

---

### Task 3: Nextext — one-shot for nltk-cache, spacy-cache, tmp-jobs

**Files:**
- Modify: `Nextext/docker/compose.yaml`

**Interfaces:**
- Produces: `volume-permissions` service gating `backend` (frontend already gates on backend healthy — transitively covered).

- [ ] **Step 1: Create a branch**

```bash
cd ../Nextext && git switch main && git pull --ff-only
git switch -c feat/self-healing-volume-ownership
```

- [ ] **Step 2: Add the one-shot as the first service under `services:`**

The file already defines `&default-logging` and `&hardened` anchors. Insert:

```yaml
  ##########################################################
  # Volume ownership (deploy ADR 0001, self-healing)
  #
  # The backend runs as uid 10001, but external volumes keep whatever
  # ownership they were created with (root, on a fresh `docker volume
  # create`). This one-shot root container fixes ownership before the
  # backend starts; it reuses the backend image so the airgap bundle
  # needs no extra image. The find form only touches wrong-owner
  # entries. Canonical shape: docint/docker/compose.yaml.
  ##########################################################
  volume-permissions:
    image: nextext-backend:${NEXTEXT_VERSION:-latest}
    logging: *default-logging
    user: "0:0"
    read_only: true
    security_opt:
      - no-new-privileges:true
    network_mode: none
    # Roots are the three mountpoints themselves — their parent dirs
    # live on the read-only image rootfs and must not (and cannot) be
    # chowned. The volume shadows the image's /tmp inside this
    # container, so chowning it is safe.
    entrypoint:
      - /bin/sh
      - -c
      - >-
        find /home/app/nltk_data /home/app/.cache/spacy /tmp
        -xdev ! -user 10001 -exec chown -h 10001:10001 {} +
    volumes:
      - nltk-cache:/home/app/nltk_data
      - spacy-cache:/home/app/.cache/spacy
      - tmp-jobs:/tmp
    restart: "no"
```

- [ ] **Step 3: Gate the backend**

Add to the `backend` service (merge if a `depends_on` already exists):

```yaml
    depends_on:
      volume-permissions:
        condition: service_completed_successfully
```

Do not touch `frontend` — it already has `depends_on: backend: service_healthy`.

- [ ] **Step 4: Verify**

```bash
docker compose -f docker/compose.yaml config -q
make -n up >/dev/null
```

Expected: silent success. Run the repo lint gate if present (`make pre-commit`).

- [ ] **Step 5: Commit, push, open PR**

```bash
git add docker/compose.yaml
git commit -m "feat: self-heal cache/tmp volume ownership at up (deploy ADR 0001)"
git push -u origin feat/self-healing-volume-ownership
gh pr create --fill
```

---

### Task 4: open-webui-service — one-shot for open-webui-data

**Files:**
- Modify: `open-webui-service/docker/compose.yaml`

**Interfaces:**
- Produces: `volume-permissions` service gating `open-webui`; the pinned image string becomes a YAML anchor so it stays single-source.

- [ ] **Step 1: Create a branch**

```bash
cd ../open-webui-service && git switch main && git pull --ff-only
git switch -c feat/self-healing-volume-ownership
```

- [ ] **Step 2: Anchor the pinned image and rewrite the ownership comment**

On the `open-webui` service, change:

```yaml
    image: ghcr.io/open-webui/open-webui:0.11.0@sha256:72c0ba641ba75e7aa52655cb242570906ececd09b1140fb736483038a22b3228
```

to:

```yaml
    image: &open-webui-image ghcr.io/open-webui/open-webui:0.11.0@sha256:72c0ba641ba75e7aa52655cb242570906ececd09b1140fb736483038a22b3228
```

In the hardening comment above `user: "10001:10001"`, replace the sentence "(which existing hosts must chown -R 10001:10001 once — AFTER the first mount, which re-copies image content root-owned into an empty volume)" with: "(ownership is self-healed by the volume-permissions one-shot below on every up — including the root-owned re-copy a first mount writes into an empty volume)".

- [ ] **Step 3: Add the one-shot as the first service under `services:`**

This compose has no logging anchor; copy the service's inline logging block. Insert before `open-webui`:

```yaml
  ##########################################################
  # Volume ownership (deploy ADR 0001, self-healing)
  #
  # open-webui runs as uid 10001 (user: override below; the image
  # defaults to root), but the external volume keeps whatever ownership
  # it was created with — including the root-owned copy the image writes
  # into an empty volume on first mount. This one-shot root container
  # fixes ownership before the app starts; it reuses the same pinned
  # image (anchor) so the airgap bundle needs no extra image. The find
  # form only touches wrong-owner entries. Canonical shape:
  # docint/docker/compose.yaml.
  ##########################################################
  volume-permissions:
    image: *open-webui-image
    user: "0:0"
    read_only: true
    security_opt:
      - no-new-privileges:true
    network_mode: none
    logging:
      driver: "local"
      options:
        max-size: "50m"
        max-file: "5"
        compress: "true"
    # Root is the mountpoint itself — its parent dirs live on the
    # read-only image rootfs and must not (and cannot) be chowned.
    entrypoint:
      - /bin/sh
      - -c
      - >-
        find /app/backend/data
        -xdev ! -user 10001 -exec chown -h 10001:10001 {} +
    volumes:
      - open-webui-data:/app/backend/data
    restart: "no"
```

NOTE: YAML forward-references fail — the `&open-webui-image` anchor from Step 2 is defined on the `open-webui` service, which must come BEFORE `volume-permissions` in the file for `*open-webui-image` to resolve. If placing the one-shot first, move the anchor definition onto the one-shot's `image:` line and the alias onto `open-webui`'s instead.

- [ ] **Step 4: Gate the app**

Add to the `open-webui` service (merge if a `depends_on` already exists):

```yaml
    depends_on:
      volume-permissions:
        condition: service_completed_successfully
```

- [ ] **Step 5: Verify**

```bash
docker compose -f docker/compose.yaml config -q
docker compose -f docker/compose.yaml config | grep -c 'ghcr.io/open-webui'   # expect 2 (anchor resolved twice)
make -n up >/dev/null
```

Expected: silent config, count 2. Run the repo lint gate if present.

- [ ] **Step 6: Commit, push, open PR**

```bash
git add docker/compose.yaml
git commit -m "feat: self-heal open-webui-data ownership at up (deploy ADR 0001)"
git push -u origin feat/self-healing-volume-ownership
gh pr create --fill
```

---

### Task 5: deploy — rewrite the runbook and README pointer

**Files:**
- Modify: `deploy/docs/hardening-migration.md` (full rewrite)
- Modify: `deploy/README.md` (lines ~149–152)

**Interfaces:**
- Consumes: the `volume-permissions` service name and per-member coverage from Tasks 1–4 (reference their PR numbers in the commit message once known).

- [ ] **Step 1: Work on the existing PR #27 branch**

```bash
cd ../deploy && git switch docs/hardening-migration-runbook
```

- [ ] **Step 2: Replace `docs/hardening-migration.md` wholesale with:**

````markdown
# Hardening volume ownership: self-healing at `make up` (ADR 0001)

As of the hardening wave every first-party container runs as the non-root
`app` user (uid `10001`) — but external volumes keep whatever ownership they
were created with: root, on a fresh `docker volume create`, or on hosts
populated before the wave. Image-build chowns cannot help, since the volume
mount shadows the image path at runtime.

**There is no manual migration step.** Every member whose uid-10001
containers mount volumes read-write ships a `volume-permissions` one-shot in
its compose file: a root container reusing the member's own image (no extra
airgap import) that runs

```sh
find <mountpoints> -xdev ! -user 10001 -exec chown -h 10001:10001 {} +
```

before the mounting services start (`depends_on:
service_completed_successfully`). The find form touches only wrong-owner
entries, so a boot over an already-owned multi-GB volume writes nothing, and
a volume deleted and re-created later (root-owned again) heals on the next
`up` instead of regressing. The canonical implementation is
`docint/docker/compose.yaml`; copy it verbatim (mounts adjusted) for any
future member.

## Coverage

| Member | Volumes self-healed |
| --- | --- |
| vllm-service | huggingface-cache (main stack and every `*-only` shape) |
| docint | docling-cache, huggingface-cache (shared), sessions-storage, source-preview-cache, pipeline-storage |
| chorus | chorus-state |
| Nextext | nltk-cache, spacy-cache, tmp-jobs |
| open-webui-service | open-webui-data |

Out of scope — **never chown these to 10001**: data-plane, obs-plane, and
edge-plane volumes. Their third-party images manage their own uids, and
their `user:`/`read_only:` hardening is deferred. docint's `ollama-cache`
is excluded for the same reason.

## Symptom of a missing one-shot

The failure is **not** a clear ownership error. vLLM starts, then spams

```
Ignoring corrupted tree cache file ... Permission denied
```

and the pooling backends (embed, embed-sparse, rerank) later die during
EngineCore startup. If you see that log line on a hardened host, the shape
you started is missing its `volume-permissions` service (or the one-shot
failed — check `docker compose ps -a` for its exit code).

## Optional snapshot before the first hardened boot

The chown is ownership metadata only — no data moves. Cautious airgap
operators can still snapshot the model weights first (they are expensive to
re-obtain; plain tar — weights don't compress):

```sh
HUB=$(docker volume inspect huggingface-cache -f '{{.Mountpoint}}')
sudo tar -C "$HUB" -cf ./huggingface-cache-pre-hardening.tar .
```

Delete the tar once the stack is healthy. `make migrate-cache` (the earlier,
manual form of this migration) has been removed; the one-shot supersedes it.

## After the first boot

Nothing recurs and nothing needs verifying by hand — but to check:

```sh
HUB=$(docker volume inspect huggingface-cache -f '{{.Mountpoint}}')
sudo find "$HUB" \( ! -uid 10001 -o ! -gid 10001 \) | head -5   # must print nothing
```

`unpack-model.sh` chowns extracted models to the owner of the destination
directory, so model transfers land correctly owned either way.
````

- [ ] **Step 3: Update the README pointer**

Replace the paragraph at `README.md` lines ~149–152 ("Hosts deployed before vllm-service 0.4.0 ... `make migrate-cache` in the vllm-service checkout ...") with:

```markdown
Volume ownership for the hardened (uid 10001) members is self-healing: each
member's compose file ships a `volume-permissions` one-shot that fixes
wrong-owner entries at every `up` — no manual migration step, also not on
hosts populated before the hardening wave. See `docs/hardening-migration.md`.
```

- [ ] **Step 4: Run deploy's lint gate**

```bash
shellcheck scripts/*.sh
make help >/dev/null && make -n ps >/dev/null
yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
```

Expected: all silent/pass.

- [ ] **Step 5: Commit and push (updates PR #27)**

```bash
git add docs/hardening-migration.md README.md
git commit -m "docs: rework ADR 0001 runbook — ownership is self-healing at up

Supersedes the manual migrate-cache runbook: every uid-10001 member now
ships a volume-permissions one-shot (see the member PRs), so the runbook
documents coverage, the missing-one-shot symptom, and an optional
weights snapshot instead of a manual procedure."
git push
```

Then update the PR #27 title/body (`gh pr edit 27`) to match the new scope: title "docs: ADR 0001 self-healing volume ownership runbook", body listing the four member PRs by number.
