#!/usr/bin/env bash
# Decide whether a member repo already holds a bundle for its current release
# version, so deploy's `make bundle` can skip re-invoking the member's own
# `make bundle` (see the bundle target in the Makefile).
#
# Usage: bundle-exists.sh <member-dir>
# Exit:  0  -> a bundle for the member's expected release version already
#              exists; the member is switched to that tag (detached, unless
#              HEAD already is the tag's commit) — exactly where its own
#              `make bundle` would leave it — and the ">> ... skipping
#              rebuild" line is printed. The caller skips.
#        !=0 -> no matching bundle, cannot determine, or the switch failed.
#              Silent. The caller
#              delegates to the member's `make bundle`, which raises the real
#              errors (dirty tree, no reachable tag) itself.
#
# The check is deliberately conservative — ANY doubt means "delegate". The
# member's `.<slug>-version` file is the portable signal for what its last
# bundle was built from (deploy must not assume tarball filename conventions,
# per CLAUDE.md); the expected version of a would-be release bundle is the
# latest annotated tag reachable from HEAD on a clean tree — exactly what the
# member's `make bundle` would build. Every image-bearing member writes that
# record from the vendored bundle-lib.sh (`bundle_version` -> `.<slug>-version`,
# gitignored), open-webui-service (`.open-webui-version`) and obs-plane
# (`.obs-plane-version`) included — obs-plane's tracked plain `VERSION` file is
# a source version, not the bundle record, and is not what is read here.
#
# Switching on the skip path keeps the repo files that copy-bundles.sh ships
# beside the tarballs on the same tag as the images, without a rebuild. It is
# this script's only write; `make pull` returns every repo to main.
#
# BUNDLE_FORCE=1 disables the skip entirely (always delegate).
# BUNDLE_EXISTS_DRY_RUN=1 (set by the Makefile under `make -n`, which still
# executes recipe lines that mention $(MAKE)) reports the switch instead of
# doing it.

set -euo pipefail
shopt -s nullglob # both globs below expand to nothing instead of a literal

if [ $# -ne 1 ]; then
    echo "usage: $0 <member-dir>" >&2
    exit 2
fi
dir="$1"

# Escape hatch: force the full fan-out.
[ -z "${BUNDLE_FORCE:-}" ] || exit 1

# A *VERSION_OVERRIDE in the environment (data-plane / obs-plane knob) changes
# what version those members' bundles would produce, so the tag comparison
# below would skip wrongly — never skip while one is set. (No -q: grep must
# read env to EOF, or pipefail can turn env's SIGPIPE into a false negative.)
if env | grep -E '^[A-Za-z_]*VERSION_OVERRIDE=.+' >/dev/null; then
    exit 1
fi

# Missing dir / not a repo: delegate — the member's make fails loudly there,
# same as before this check existed.
[ -d "$dir" ] || exit 1
git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || exit 1

# Dirty tree: the member's bundle would refuse anyway; let it say so.
dirty="$(git -C "$dir" status --porcelain -uno 2>/dev/null)" || exit 1
[ -z "$dirty" ] || exit 1

# Expected release version: latest annotated tag reachable from HEAD. No
# --tags: annotated tags only, the same rule bundle-lib.sh's `make bundle` uses.
tag="$(git -C "$dir" describe --abbrev=0 HEAD 2>/dev/null)" || exit 1
[ -n "$tag" ] || exit 1

# The bundle record: at least one .<slug>-version file, and every one of them
# (stale extras included) must record exactly the expected tag. A dev-bundle
# record (<date>-<sha>) never equals a tag, so a dev bundle never suppresses
# the release build.
vfiles=("$dir"/.*-version)
[ "${#vfiles[@]}" -ge 1 ] || exit 1
for vf in "${vfiles[@]}"; do
    [ -f "$vf" ] || exit 1
    [ "$(head -n 1 "$vf")" = "$tag" ] || exit 1
done

# The artifacts must actually still be there (a member may emit several
# tarballs; presence-only check — filenames are the member's business).
tarballs=("$dir"/*.tar.gz)
[ "${#tarballs[@]}" -ge 1 ] || exit 1

name="$(basename "$dir")"
hint="(set BUNDLE_FORCE=1 to rebuild)"

# Already at the tag's commit: the clean tree is the tagged tree; keep the branch.
if [ "$(git -C "$dir" rev-parse HEAD)" = "$(git -C "$dir" rev-parse "${tag}^{commit}")" ]; then
    echo ">> $name: bundle $tag already present — skipping rebuild $hint"
    exit 0
fi

if [ -n "${BUNDLE_EXISTS_DRY_RUN:-}" ]; then
    echo ">> $name: bundle $tag already present — would switch to $tag, skipping rebuild (dry run)"
    exit 0
fi

# Put the member where its own `make bundle` would leave it. A failed switch
# (e.g. an untracked file in the way) means delegate; the member's bundle hits
# the same switch and reports the error.
git -C "$dir" switch --quiet --detach "$tag" 2>/dev/null || exit 1
echo ">> $name: bundle $tag already present — switched to $tag, skipping rebuild $hint"
