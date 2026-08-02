# Federation `make clone` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `make clone` target that populates a bare host with every missing federation member repo, so onboarding becomes `make clone && make setup && make up`.

**Architecture:** Per the approved design (`docs/2026-08-02-clone-target-design.md`): one new variable `GIT_REMOTE ?= https://github.com/nos-tromo` supplies an account *prefix*, and each member's clone URL is derived as `$(GIT_REMOTE)/<dir>.git` from the directory names the Makefile already carries. The target is an inline shell loop adjacent to `pull` — same shape, same warn-and-continue-then-exit-non-zero failure contract. Existing directories are skipped (refreshing stays `pull`'s job), making the target idempotent. Clones are never shallow, because every member's `make bundle` builds the latest annotated tag reachable from HEAD.

**Tech Stack:** GNU Make, POSIX shell, `git`.

## Global Constraints

- **Data confidentiality (hard rule):** no real data and no local dev-machine absolute paths (`/Users/...`, `/home/...`, `C:\Users\...`) in anything committed — including this plan, commit messages, and any pasted command output. Scratch paths in verification steps are written as `"$SCRATCH"`; set that variable in your shell, never inline a literal path into a committed file.
- Variable name exactly `GIT_REMOTE`, default exactly `https://github.com/nos-tromo` (no trailing slash, no `.git`).
- Clone URL form exactly `$(GIT_REMOTE)/<dir>.git` — prefix-shaped so `git@github.com:nos-tromo` works unchanged.
- Never `--depth` / `--single-branch` on the clone. Shallow clones carry no tags and would break every member's tag-based `make bundle`.
- Member list exactly `$(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) $(OPENWEBUI_DIR)` — the same set `pull` iterates, minus the leading `.` (deploy itself). Empty vars skip for free.
- `clone` never modifies an existing directory. No `git pull`, no `git fetch`, no validation of what is there.
- Makefile recipes are **tab**-indented. Preserve the file's existing comment voice (explains *why*, not *what*) and its em-dash usage.
- Branch `feat/make-clone` already exists and carries the committed design doc. PR at the end; do **not** merge.
- There is no test suite — CI is lint-only and that is the gate. Every task ends with the repo's three documented checks.

---

### Task 1: `GIT_REMOTE` variable + the `clone` target

**Files:**
- Modify: `Makefile` (variables block after `DATA_PROFILE`, `.PHONY` line, `help` recipe, new `clone` target after `pull`)
- Modify: `federation.env.example` (add `GIT_REMOTE`)

**Interfaces:**
- Produces: `GIT_REMOTE` (make variable, account URL prefix) and the `.PHONY` target `clone`. Task 2 edits the same example file; Task 3 documents both.

- [ ] **Step 1: Write the failing test**

There is no test framework here; the red state is Make itself not knowing the target. Run:

```bash
make -n clone
```

- [ ] **Step 2: Run it to verify it fails**

Expected output (this is the RED state — confirm you see it before writing any code):

```
make: *** No rule to make target 'clone'.  Stop.
```

- [ ] **Step 3: Add the `GIT_REMOTE` variable**

In `Makefile`, immediately after the `DATA_PROFILE ?= cpu` line and before the `# Health-probe knobs…` comment block, add:

```makefile

# Account/namespace prefix the federation members are cloned from (see `clone`).
# Deliberately a PREFIX, not a full URL, so one edit switches transport for the
# whole federation: https://github.com/nos-tromo, git@github.com:nos-tromo, or an
# internal mirror. Each member's URL is $(GIT_REMOTE)/<dir>.git — repo name and
# directory name are identical for every member.
GIT_REMOTE   ?= https://github.com/nos-tromo
```

- [ ] **Step 4: Register the target as phony**

In `Makefile`, change the `.PHONY` line from:

```makefile
.PHONY: help setup up up-dev down ps logs pull bundle load
```

to:

```makefile
.PHONY: help setup up up-dev down ps logs clone pull bundle load
```

- [ ] **Step 5: Add the `clone` target**

In `Makefile`, insert this **immediately before** the `# Refresh every federation repo from GitHub:` comment that heads the `pull` target, so `clone` and `pull` read as the pair they are:

```makefile
# Populate a bare host: clone every federation member missing under INFRA_ROOT.
# Fills in gaps only — an existing directory is left untouched (refreshing is
# `pull`'s job), so the target is idempotent and safe to re-run after a partial
# network failure. A failed clone warns and the loop continues, exiting non-zero
# at the end. Never shallow: every member's `make bundle` builds the latest
# annotated tag reachable from HEAD, and a --depth clone carries no tags.
# (deploy itself is already on disk. infra-ui is a build-time pnpm git dependency
# and pr-notify is CI tooling — neither is a federation member, so neither is
# cloned.)
clone:
	@failed=""; cloned=0; skipped=0; \
	mkdir -p "$(INFRA_ROOT)"; \
	for d in $(VLLM_DIR) $(DATA_DIR) $(OBS_DIR) $(EDGE_DIR) $(APP_DIRS) $(OPENWEBUI_DIR); do \
	  if [ -d "$(INFRA_ROOT)/$$d" ]; then \
	    echo ">> $$d already present — skipping"; skipped=$$((skipped+1)); continue; \
	  fi; \
	  echo ">> $$d cloning"; \
	  git clone "$(GIT_REMOTE)/$$d.git" "$(INFRA_ROOT)/$$d" \
	    && cloned=$$((cloned+1)) \
	    || { echo "WARNING: $$d not cloned."; failed="$$failed $$d"; }; \
	done; \
	echo "cloned $$cloned, skipped $$skipped. run 'make pull' to refresh existing repos."; \
	[ -z "$$failed" ] || { echo "WARNING: not cloned:$$failed"; exit 1; }
```

Every line of the recipe body is **tab**-indented, and every line but the last ends with ` \`. The `$$` sequences become a single `$` in the shell — do not reduce them.

- [ ] **Step 6: Add the help line**

In the `help` recipe, insert a `clone` line **above** the existing `make setup` line (clone precedes setup in the onboarding path):

```makefile
	@echo "  make clone    clone every missing federation member repo under INFRA_ROOT"
```

- [ ] **Step 7: Add `GIT_REMOTE` to `federation.env.example`**

In `federation.env.example`, directly after the `INFRA_ROOT=..` block and before the `# Member directory names` comment, add:

```
# Where 'make clone' fetches the member repos from — an account/namespace
# PREFIX, not a full URL. Each member is cloned from $GIT_REMOTE/<dir>.git.
GIT_REMOTE=https://github.com/nos-tromo
# SSH instead:      GIT_REMOTE=git@github.com:nos-tromo
# Internal mirror:  GIT_REMOTE=https://git.example.internal/mirror/nos-tromo
```

- [ ] **Step 8: Run the test to verify it passes (GREEN)**

```bash
make -n clone
```

Expected: the full shell loop is printed, with `$(INFRA_ROOT)`, the nine member directory names, and `https://github.com/nos-tromo/$d.git` all expanded. No "No rule to make target" error.

- [ ] **Step 9: Verify the all-skipped path against this workspace**

Every member is already present here, so this exercises the skip branch end to end without touching the network:

```bash
make clone; echo "exit=$?"
```

Expected: nine `>> <name> already present — skipping` lines, then
`cloned 0, skipped 9. run 'make pull' to refresh existing repos.` and `exit=0`.
Confirm `git status --short` shows no new or modified files anywhere.

- [ ] **Step 10: Verify the cloning path into an empty INFRA_ROOT**

Point `INFRA_ROOT` at an empty scratch directory and trim the member list to one small repo, so this stays a single quick clone:

```bash
SCRATCH="$(mktemp -d)"
make clone INFRA_ROOT="$SCRATCH/fresh" \
  VLLM_DIR= DATA_DIR= OBS_DIR=obs-plane EDGE_DIR= APP_DIRS= OPENWEBUI_DIR=
echo "exit=$?"
ls "$SCRATCH/fresh"
git -C "$SCRATCH/fresh/obs-plane" tag | head -3
```

Expected: `>> obs-plane cloning`, git's clone output, `cloned 1, skipped 0.…`, `exit=0`, `ls` shows `obs-plane`, and `git tag` lists at least one `vX.Y.Z` tag — that last check is the one that proves the clone is not shallow. Note that `INFRA_ROOT` pointed at a **non-existent** directory and the `mkdir -p` created it.

- [ ] **Step 11: Verify the failure path warns, continues, and exits non-zero**

```bash
make clone INFRA_ROOT="$SCRATCH/broken" \
  VLLM_DIR=no-such-repo DATA_DIR= OBS_DIR=obs-plane EDGE_DIR= APP_DIRS= OPENWEBUI_DIR=
echo "exit=$?"
ls "$SCRATCH/broken"
```

Expected: git fails on `no-such-repo`, `WARNING: no-such-repo not cloned.` is printed, **the loop continues and clones `obs-plane` anyway**, then `cloned 1, skipped 0.…`, `WARNING: not cloned: no-such-repo`, and a non-zero `exit=` (make reports `2`). `ls` shows `obs-plane` present. Then clean up: `rm -rf "$SCRATCH"`.

- [ ] **Step 12: Run the repo's lint gate**

```bash
shellcheck scripts/*.sh
make help >/dev/null && make -n ps >/dev/null
yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
```

Expected: all three silent / exit 0. Also confirm `make help` now lists the `clone` line above `setup`.

- [ ] **Step 13: Commit**

```bash
git add Makefile federation.env.example
git commit -m "feat: add 'make clone' — clone missing federation member repos

Derives each member's clone URL as \$(GIT_REMOTE)/<dir>.git from the
directory names the Makefile already carries, so switching transport or
pointing at an internal mirror is a one-line federation.env edit.

Existing directories are skipped (refreshing stays 'make pull'), making
the target idempotent and re-runnable after a partial network failure.
A failed clone warns, the loop continues, and the target exits non-zero.
Clones are never shallow — members' 'make bundle' needs reachable tags."
```

---

### Task 2: Correct the stale `federation.env.example` content

Independent of Task 1's feature: this fixes drift that predates it. A reviewer could accept `clone` and still want the comment worded differently, so it commits separately.

**Files:**
- Modify: `federation.env.example` (the `APP_DIRS` block)

**Interfaces:**
- Consumes: nothing from Task 1 beyond both tasks editing the same file — do Task 1 first to avoid a conflicting edit.
- Produces: `OPENWEBUI_DIR` documented in the example, matching the Makefile.

- [ ] **Step 1: Confirm the drift is real (the failing check)**

```bash
grep -n OPENWEBUI_DIR Makefile federation.env.example
```

Expected: `Makefile` matches on several lines; `federation.env.example` produces **no match at all**. That absence is the bug — the example cannot produce a working profile for a host that wants to drop the chat UI.

- [ ] **Step 2: Replace the stale `APP_DIRS` block**

In `federation.env.example`, replace this block:

```
# Apps deployed on THIS host (space-separated dir names). Trim to the subset
# this host actually runs. NOTE: only common.mk apps belong here. open-webui-service
# has a bespoke interface and self-manages — bring it up separately
# (`make -C open-webui-service up`); don't add it to this list.
APP_DIRS=chorus docint Nextext translator
```

with:

```
# First-party (common.mk) apps deployed on THIS host, space-separated dir
# names. Trim to the subset this host actually runs. Only common.mk apps
# belong here — open-webui-service has its own variable below.
APP_DIRS=chorus docint Nextext translator

# The upstream chat UI (pulled image, bespoke Makefile). It is kept out of
# APP_DIRS because it is a different kind of member, NOT because it is
# excluded: it is a full lifecycle member, joining every app-tier loop
# (setup/up/down/ps/logs) and the bundle/load fan-out. Set empty to drop it
# from the federation entirely.
OPENWEBUI_DIR=open-webui-service
```

The correction that matters is the **reason**: the old text told the operator open-webui "self-manages — bring it up separately", which has been wrong since it became a full lifecycle member.

- [ ] **Step 3: Verify the example now covers every member variable the Makefile reads**

```bash
for v in INFRA_ROOT GIT_REMOTE VLLM_DIR DATA_DIR OBS_DIR EDGE_DIR APP_DIRS OPENWEBUI_DIR DATA_PROFILE; do
  printf '%-14s %s\n' "$v" "$(grep -c "^$v=" federation.env.example)"
done
```

Expected: every variable reports `1`. A `0` means the example is still incomplete.

- [ ] **Step 4: Verify the example is still a valid profile**

The example must survive being used as an actual `federation.env`:

```bash
make -f Makefile -n ps GIT_REMOTE=x >/dev/null && echo "parses OK"
sh -n federation.env.example && echo "shell-parses OK"
```

Expected: both print their OK line. (`federation.env` is `-include`d by make and is shell-shaped `KEY=value`, so `sh -n` catches a typo that would silently poison every target.)

- [ ] **Step 5: Commit**

```bash
git add federation.env.example
git commit -m "docs: fix stale federation.env.example — add OPENWEBUI_DIR

The example never gained OPENWEBUI_DIR after open-webui-service became a
full lifecycle member, and its APP_DIRS comment still told operators the
service 'self-manages — bring it up separately', which the Makefile has
contradicted since it joined every app-tier loop. Corrects the reason it
sits outside APP_DIRS: a different kind of member, not an excluded one."
```

---

### Task 3: Document `clone` in README and CLAUDE.md

**Files:**
- Modify: `README.md` (on-host layout, quick start, targets table)
- Modify: `CLAUDE.md` (design-split section, Configuration, Commands)

**Interfaces:**
- Consumes: `GIT_REMOTE` and the `clone` target from Task 1; `OPENWEBUI_DIR` in the example from Task 2.

- [ ] **Step 1: README — note how the layout gets populated**

In `README.md`, in the **On-host layout** section, after the closing ``` of the directory tree, add:

```markdown
On a bare host you do not have to create that layout by hand — clone `deploy`
first, then `make clone` fills in every member repo beside it (see **Quick
start**).
```

- [ ] **Step 2: README — put `clone` at the head of the quick start**

In the **Quick start** fenced block, insert `clone` as the first line so the onboarding sequence reads in order:

```bash
make clone     # bare host: clone every missing member repo under INFRA_ROOT
cp federation.env.example federation.env   # then edit (apps on this host, profile)
make setup     # one-time: external networks + volumes for every tier
```

Leave the remaining `up` / `up-dev` / `ps` / `down` lines untouched.

- [ ] **Step 3: README — add the targets-table row**

In the **Targets** table, insert this row directly **above** the `pull` row (clone precedes pull in the lifecycle, and they are siblings):

```markdown
| `clone` | Clones every federation member missing under `INFRA_ROOT`, from `$(GIT_REMOTE)/<dir>.git` (`GIT_REMOTE` defaults to the nos-tromo GitHub account; set it to an SSH prefix or an internal mirror in `federation.env`). Existing directories are skipped untouched — refreshing is `pull`'s job — so the target is idempotent. A failed clone warns and the loop continues, exiting non-zero at the end. Clones are never shallow, because members' `make bundle` needs reachable tags. `deploy` itself, `infra-ui`, and `pr-notify` are not cloned. |
```

- [ ] **Step 4: CLAUDE.md — extend the design-split section**

`CLAUDE.md`'s "central design split" describes delegated targets (1) and compose-driven targets (2), but never accounts for the git-level targets. In the section **The central design split (read before editing the Makefile)**, add a third numbered item after item 2:

```markdown
3. **`clone`/`pull`: drive `git` directly.** These two act on the member *repos*,
   not their services, so there is nothing to delegate — a member's Makefile
   cannot clone the repo that contains it. They share one contract: iterate the
   member list, run one git command per repo, warn-and-continue on refusal, and
   exit non-zero at the end if any repo was skipped. `clone` fills in what is
   missing (`$(GIT_REMOTE)/<dir>.git`, never shallow — members' `bundle` needs
   reachable tags); `pull` refreshes what is present. Keep them symmetrical.
```

Then, in the same section's closing **Rule of thumb** paragraph, replace:

```markdown
Rule of thumb: ordered/health-gated bring-up and every uniform target → delegate to the member's Make
target; only the aggregate read-only views (`ps`/`logs`) are driven directly.
```

with:

```markdown
Rule of thumb: ordered/health-gated bring-up and every uniform target → delegate to the member's Make
target; only the aggregate read-only views (`ps`/`logs`) and the repo-level git targets
(`clone`/`pull`) are driven directly.
```

- [ ] **Step 5: CLAUDE.md — document `GIT_REMOTE` in Configuration**

In the **Configuration** section, in the sentence listing the knobs, add `GIT_REMOTE` to the enumeration and append an explanatory sentence at the end of the paragraph:

```markdown
`GIT_REMOTE` is an account/namespace **prefix**, not a full URL — `make clone`
derives each member's URL as `$(GIT_REMOTE)/<dir>.git`, so one edit repoints the
whole federation at SSH or an internal mirror.
```

- [ ] **Step 6: CLAUDE.md — add `clone` to the Commands block**

In the **Commands** fenced block, insert as the first command line, above `make setup`:

```bash
make clone     # bare host: clone every missing member repo under INFRA_ROOT (never shallow)
```

- [ ] **Step 7: Verify the docs match the implementation**

Documentation drift is exactly what Task 2 was fixing, so check this one rather than trusting it:

```bash
make help
grep -n "clone" README.md CLAUDE.md Makefile federation.env.example
```

Expected: `make help` lists `clone`; every documented flag, default, and behavior (prefix form, skip-existing, never shallow, non-zero exit) matches the Task 1 recipe. Confirm no doc claims `clone` refreshes existing repos.

- [ ] **Step 8: Run the full lint gate one more time**

```bash
shellcheck scripts/*.sh
make help >/dev/null && make -n ps >/dev/null
yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
```

Expected: all silent / exit 0.

- [ ] **Step 9: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document 'make clone' in README and CLAUDE.md

Adds the targets-table row, puts clone at the head of the quick start,
and records the third arm of the Makefile's design split: clone/pull act
on the member repos rather than their services, so they drive git
directly instead of delegating."
```

- [ ] **Step 10: Open the PR (do not merge)**

```bash
git push -u origin feat/make-clone
gh pr create --base main --title "feat: add 'make clone' — clone the federation onto a bare host" --body "$(cat <<'EOF'
## Summary

Adds `make clone`, the missing first step of host onboarding: it clones every
federation member repo that is absent under `INFRA_ROOT`, making the path
`make clone && make setup && make up`. Previously the nine member checkouts had
to be created by hand, where a typo or a missed member surfaced much later as a
confusing failure during `setup` or mid-bring-up.

Design: `docs/2026-08-02-clone-target-design.md`.

## Design decisions

- **One new variable, `GIT_REMOTE`** (default `https://github.com/nos-tromo`) — an
  account *prefix*, not a full URL, so `git@github.com:nos-tromo` and internal
  mirrors work with a one-line `federation.env` edit. Each member's URL is
  `$(GIT_REMOTE)/<dir>.git`, reusing the directory names the Makefile already
  carries, so there is no second list to drift.
- **Skip-existing, never touch.** `clone` fills in gaps; refreshing stays `make
  pull`. Idempotent and safe to re-run after a partial network failure.
- **Warn and continue**, exiting non-zero at the end — the same contract `pull`
  already uses. One unreachable member should not cost you the other eight.
- **Never shallow.** Every member's `make bundle` builds the latest annotated tag
  reachable from HEAD; a `--depth` clone carries no tags and would break releases
  on any host provisioned this way.
- Scope is the nine members `pull` already iterates. `infra-ui` is a build-time
  pnpm git dependency that never needs to be on disk, and `pr-notify` is CI
  tooling — neither is cloned.

## Drive-by fix

`federation.env.example` had drifted: it was missing `OPENWEBUI_DIR` entirely, and
its `APP_DIRS` comment still told operators open-webui-service "self-manages —
bring it up separately", which the Makefile has contradicted since the service
became a full lifecycle member.

## Verification

No test suite (CI is lint-only, and that is the gate). Beyond the three documented
checks, the target was exercised on all three paths: all-skipped against this
workspace (exit 0, no files touched), a real clone into a non-existent
`INFRA_ROOT` (directory created, tags present — confirming a non-shallow clone),
and a failing member (warned, loop continued and cloned the rest, exited non-zero).
EOF
)"
```

Report the PR URL and stop. Do **not** merge — CI must pass and the human reviews.

---

## Self-Review

**Spec coverage** — every design decision maps to a step: (1) `GIT_REMOTE` derivation → T1 S3/S7; (2) nine-member scope → T1 S5 loop list, PR body; (3) skip-existing → T1 S5, verified T1 S9; (4) warn-and-continue + non-zero exit → T1 S5, verified T1 S11; (5) never shallow → T1 S5 comment + the `git tag` assertion in T1 S10; (6) inline, adjacent to `pull` → T1 S5 placement; (7) `mkdir -p INFRA_ROOT` → T1 S5, verified T1 S10; (8) empty vars skip free → inherent to the loop list. The `federation.env.example` corrections section → Task 2. The design's Verification section → the lint gate in T1 S12 / T3 S8.

**Placeholder scan** — no TBD/TODO, no "similar to Task N", no "add appropriate error handling". Every code step carries literal content.

**Naming consistency** — `GIT_REMOTE`, `INFRA_ROOT`, `OPENWEBUI_DIR`, and the target name `clone` are spelled identically in the Makefile, the example, README, CLAUDE.md, and the PR body. Output strings (`already present — skipping`, `WARNING: not cloned:`) match between the recipe in T1 S5 and the expectations in T1 S9/S11.
