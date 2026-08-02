# Federation `make clone` — design

Status: approved design, pre-implementation
Date: 2026-08-02
Scope: deploy repo only. Adds a `clone` target that populates a bare host
with the federation member repos, plus one new configuration variable
(`GIT_REMOTE`). Also corrects stale content in `federation.env.example`.

## Context

Every existing target in this repo assumes the member repos are *already*
on disk under `INFRA_ROOT`: `setup`, `up`, `down`, `ps`, `logs`, `bundle`,
and `load` all address `$(INFRA_ROOT)/<dir>/`, and `pull` refreshes repos
that exist. Nothing puts them there. Bootstrapping a new host is currently
an undocumented sequence of nine hand-typed `git clone` commands, where a
typo or a missed member surfaces much later as a confusing failure in
`setup` or mid-bring-up.

`clone` closes that gap: it is the step *before* `setup`, making the
onboarding path `make clone && make setup && make up`.

## Decisions

1. **Clone sources are derived, not enumerated.** One new variable,
   `GIT_REMOTE ?= https://github.com/nos-tromo`, supplies the account
   prefix; each member's URL is `$(GIT_REMOTE)/<dir>.git`, reusing the
   directory names the Makefile already carries (`VLLM_DIR`, `DATA_DIR`,
   `OBS_DIR`, `EDGE_DIR`, `APP_DIRS`, `OPENWEBUI_DIR`). Repo name equals
   directory name for all nine members today, so no second list is needed
   and the two cannot drift apart.

   The form is deliberately prefix-shaped rather than URL-shaped, so both
   `https://github.com/nos-tromo` and `git@github.com:nos-tromo` work
   unchanged, as does an internal mirror
   (`https://git.example.internal/mirror/nos-tromo`). Switching the whole
   federation between transports is a one-line edit in `federation.env`.

   No per-member override is introduced. A member that lives elsewhere is
   a real event that should be handled by cloning it by hand; adding an
   escape hatch for a case that has never occurred is speculative.

2. **Scope is the nine federation members** — exactly the set `pull`
   iterates, minus `deploy` itself (you are standing in it when you run
   the target). `infra-ui` is a build-time pnpm git dependency that pnpm
   fetches from the remote and never needs on disk, and `pr-notify` is CI
   tooling rather than a runtime member; neither is cloned.

3. **Existing directories are skipped, not touched.** A member whose
   directory is already present prints a skip line and the loop continues.
   `clone` fills in what is *missing*; refreshing what is present is
   `pull`'s job, and the two compose as `make clone && make pull`. This
   makes the target idempotent and safely re-runnable after a partial
   network failure. A directory is judged present by existence alone — no
   git-repo validation — keeping the check as simple as the failure it
   guards against.

4. **Failures warn and continue, collecting a failed list**, exiting
   non-zero at the end if any clone failed — the same contract `pull`
   already uses. One unreachable member should not deprive the operator of
   the other eight.

5. **Full clones. Never `--depth`.** Every image-bearing member's
   `make bundle` builds *the latest annotated tag reachable from HEAD*. A
   shallow clone carries no tags, so `bundle` would refuse (or, worse,
   silently degrade the release identity) on a host provisioned this way.
   The cost of a full clone is paid once per host; the failure mode of a
   shallow one is paid at every release.

6. **Implemented inline in the Makefile, adjacent to `pull`.** The two are
   the same shape — a loop over the member list running one git command
   per repo, with a warn-and-continue failure list — and reading them as a
   pair is worth more than symmetry with `scripts/wait-healthy.sh`. That
   script is a script because it does real work (probe containers,
   polling, timeouts); this is not.

7. **`INFRA_ROOT` is created if absent** (`mkdir -p`), so an absolute
   `INFRA_ROOT` pointing at a not-yet-existing directory works on a bare
   host. The default `..` always exists.

8. **Empty member variables skip for free.** `OBS_DIR=` / `EDGE_DIR=`
   expand to nothing in the iteration list, exactly as they do in `pull`
   and `bundle`. A host configured without observability or the gateway
   clones neither.

## `federation.env.example` corrections

The example file has drifted from the Makefile and is fixed in the same
change:

- **`OPENWEBUI_DIR` is missing entirely.** The Makefile has carried it
  since open-webui-service became a full lifecycle member; the example
  never gained it.
- **The `APP_DIRS` comment is wrong.** It instructs the reader that
  open-webui-service "has a bespoke interface and self-manages — bring it
  up separately (`make -C open-webui-service up`); don't add it to this
  list." The first half of that (keep it out of `APP_DIRS`) is still
  correct, but the reason given is not: it participates in every app-tier
  loop via `OPENWEBUI_DIR`. The comment is rewritten to say it lives in
  its own variable *because* it is a pulled-image member with a bespoke
  Makefile, while still being a full lifecycle member.
- **`GIT_REMOTE` is added** with the transport alternatives shown as
  comments.

## Out of scope

- Refreshing or resetting existing checkouts — that is `make pull`.
- Cloning at a pinned tag or branch. Clones land on the remote's default
  branch (`main`), matching what `pull` maintains. Release-pinning a host
  is a bundle/artifact concern, not a checkout concern.
- Submodule handling. No federation member uses submodules.
- Any change to `INFRA_ROOT` semantics or the member contract.
- `infra-ui` and `pr-notify`, per decision 2.

## Verification

No test suite exists; CI is lint-only and that is the gate. The change is
verified with the repo's documented three checks plus a dry run:

```bash
shellcheck scripts/*.sh
make help >/dev/null && make -n ps >/dev/null
yamllint -d "{extends: relaxed, rules: {line-length: disable, document-start: disable}}" .github/
make -n clone            # inspect the expanded loop without cloning
```

Because every member repo is already present in this workspace, a live
`make clone` here exercises the all-skipped path end to end; the cloning
path is verified by pointing `INFRA_ROOT` at an empty scratch directory.
