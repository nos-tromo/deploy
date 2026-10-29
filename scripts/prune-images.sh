#!/usr/bin/env bash
# Remove federation images that no container uses — deploy's `make prune`.
#
# Usage: prune-images.sh <member-dir>...
#
# Two matching rules, both read from each member's docker/compose.yaml:
#
#   built     (always) every image a member's compose project built. Compose
#             stamps com.docker.compose.project=<name> on each image it builds,
#             so a label-filtered `docker image prune` takes every old release
#             tag, superseded dev build and untagged rebuild leftover. <name> is
#             the compose file's `name:`, resolved without .env.
#   upstream  (PRUNE_UPSTREAM=<non-empty>) every version of the images the
#             members pull (neo4j, qdrant, grafana, ...). They carry no
#             federation label, so they match by repository — the members'
#             image lists, read with each member's .env: for each image no
#             container uses, the tag and digest references in a member's
#             repository are removed. A reference outside the federation keeps
#             the image alive under that name.
#
# Never touched: images used by any container (running or stopped) — not even
# one of their references — the health-probe image, other projects' images,
# containers, networks, volumes. No `docker system prune` and no unfiltered
# `image prune`: the former drops unused networks (the external seams while
# the stack is down).
#
# A member whose compose file cannot be read, or a removal that fails, is
# reported and skipped; the script carries on and exits 1 at the end (the
# clone/pull contract).

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <member-dir>..." >&2
    exit 2
fi

docker version --format '{{.Server.Version}}' >/dev/null || {
    echo "ERROR: docker daemon not reachable — nothing pruned." >&2
    exit 1
}

upstream="${PRUNE_UPSTREAM:-}"
failed=""
repos=""

# Repository of an image reference, in the familiar form `docker image` prints:
# drop @digest, then :tag (searched in the last path segment only, so a
# registry port survives), then Docker Hub's implicit docker.io/ + library/.
repo_of() {
    local ref="${1%%@*}" last
    last="${ref##*/}"
    case "$last" in *:*) ref="${ref%"$last"}${last%%:*}" ;; esac
    ref="${ref#docker.io/}"
    ref="${ref#index.docker.io/}"
    ref="${ref#library/}"
    printf '%s\n' "$ref"
}

for m in "$@"; do
    file="$m/docker/compose.yaml"
    if ! name="$(docker compose -f "$file" config --no-interpolate 2>/dev/null | sed -n '1s/^name: //p')" \
        || [ -z "$name" ]; then
        echo "WARNING: $m: compose project not resolved — its images are left alone."
        failed="$failed $m"
        continue
    fi
    echo ">> $m (project $name)"
    docker image prune -af --filter "label=com.docker.compose.project=$name" || failed="$failed $m"

    if [ -n "$upstream" ]; then
        # Interpolated with the member's .env (the pair `make ps` uses): typed
        # fields such as `mem_limit: ${X:-7g}` only decode once resolved.
        # --profile '*': every service's image, whichever profile gates it.
        if refs="$(docker compose --env-file "$m/.env" -f "$file" --profile '*' config --images 2>/dev/null)"; then
            while read -r ref; do
                if [ -n "$ref" ]; then repos="$repos$(repo_of "$ref")"$'\n'; fi
            done <<<"$refs"
        else
            echo "WARNING: $m: image list not resolved (missing .env?) — its upstream images are left alone."
            failed="$failed $m"
        fi
    fi
done

if [ -n "$upstream" ] && [ -n "$repos" ]; then
    echo ">> upstream images (PRUNE_UPSTREAM)"

    # Images any container uses, by ID. Their references stay untouched: a
    # running neo4j that lost its @digest reference would try to pull on the
    # next `up` — fatal on an airgap host. Fail closed: if either lookup
    # fails, set -e stops here, before anything is removed.
    cids_raw="$(docker ps -aq --no-trunc)"
    cids=()
    while read -r c; do
        if [ -n "$c" ]; then cids+=("$c"); fi
    done <<<"$cids_raw"
    used=""
    if [ "${#cids[@]}" -gt 0 ]; then
        used="$(docker container inspect --format '{{.Image}}' "${cids[@]}")"
    fi

    while read -r id; do
        if grep -qxF -- "$id" <<<"$used"; then continue; fi
        # Tags first, then digests; a reference can vanish with an earlier one.
        while read -r ref; do
            [ -n "$ref" ] || continue
            grep -qxF -- "$(repo_of "$ref")" <<<"$repos" || continue
            docker image inspect "$ref" >/dev/null 2>&1 || continue
            docker rmi "$ref" || failed="$failed $ref"
        done < <(docker image inspect --format \
            '{{range .RepoTags}}{{println .}}{{end}}{{range .RepoDigests}}{{println .}}{{end}}' \
            "$id" 2>/dev/null)
    done < <(docker image ls -q --no-trunc | sort -u)
fi

if [ -n "$failed" ]; then
    echo "WARNING: not pruned:$failed"
    exit 1
fi
echo ">> pruned. Removed images return with 'make load' (or 'make bundle BUNDLE_FORCE=1') before 'make up'."
