#!/usr/bin/env bash
set -euo pipefail

# Regression test for scripts/pack-model.sh and scripts/unpack-model.sh.
#
# Builds synthetic Hugging Face caches in a temporary directory — no network,
# no real model — and checks that a packed archive carries the weights for
# both cache layouts:
#
#   per-repo blobs  (huggingface_hub < 2): models--org--name/blobs/<sha> is the file
#   shared blobs    (huggingface_hub >= 2): models--org--name/blobs/<sha> is a link
#                   into <cache>/blobs/xx/<sha256>, outside the model directory
#
# and that a dangling link stops the pack, and that unpack-model.sh refuses an
# archive whose links point at files it does not contain.
#
# Usage: bash tests/model-transfer.test.sh
# Needs zstd and sha256sum. The unpack half needs GNU stat (Linux); elsewhere it
# is skipped with a notice.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACK="$ROOT/scripts/pack-model.sh"
UNPACK="$ROOT/scripts/unpack-model.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

REV="0123456789abcdef0123456789abcdef01234567"
MODEL="models--example-org--example-model"
FAILURES=0

fail() {
    echo "FAIL: $*" >&2
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "ok: $*"
}

# Write a cache with one file, model.bin, in the per-repo layout.
make_per_repo_cache() {
    local cache="$1" content="$2"
    mkdir -p "$cache/$MODEL/blobs" "$cache/$MODEL/snapshots/$REV" "$cache/$MODEL/refs"
    printf '%s' "$content" > "$cache/$MODEL/blobs/1111111111111111111111111111111111111111"
    ln -s ../../blobs/1111111111111111111111111111111111111111 "$cache/$MODEL/snapshots/$REV/model.bin"
    printf '%s' "$REV" > "$cache/$MODEL/refs/main"
}

# Write a cache with one file, model.bin, in the shared-blob layout. With
# "dangling", the shared blob itself is left out.
make_shared_cache() {
    local cache="$1" content="$2" mode="${3:-}"
    mkdir -p "$cache/blobs/ab" "$cache/$MODEL/blobs" "$cache/$MODEL/snapshots/$REV" "$cache/$MODEL/refs"
    if [[ "$mode" != "dangling" ]]; then
        printf '%s' "$content" > "$cache/blobs/ab/abababababababababababababababababababababababababababababababab"
    fi
    ln -s ../../blobs/ab/abababababababababababababababababababababababababababababababab \
        "$cache/$MODEL/blobs/2222222222222222222222222222222222222222"
    ln -s ../../blobs/2222222222222222222222222222222222222222 "$cache/$MODEL/snapshots/$REV/model.bin"
    printf '%s' "$REV" > "$cache/$MODEL/refs/main"
}

# Extract an archive into a fresh directory and print model.bin through its snapshot link.
read_back() {
    local archive="$1" dest="$2"
    mkdir -p "$dest"
    zstd -dc "$archive" | tar -xf - -C "$dest"
    cat "$dest/$MODEL/snapshots/$REV/model.bin"
}

# The type character (d, l, -) of an archive member, from a verbose listing.
member_type() {
    local archive="$1" member="$2"
    zstd -dc "$archive" | tar -tvf - | awk -v m="$member" '{ for (i = 1; i <= NF; i++) if ($i == m) { print substr($1, 1, 1); exit } }'
}

check_pack() {
    local label="$1" cache="$2" content="$3" blob="$4"
    local out="$WORK/out-$label"
    mkdir -p "$out"
    if ! bash "$PACK" "$cache/$MODEL" "$out" > "$WORK/pack-$label.log" 2>&1; then
        fail "$label: pack-model.sh failed"
        cat "$WORK/pack-$label.log" >&2
        return
    fi
    local archive="$out/example-model.tar.zst"
    local got
    got=$(read_back "$archive" "$WORK/extract-$label" 2>/dev/null || true)
    if [[ "$got" == "$content" ]]; then
        pass "$label: the archive carries the weights"
    else
        fail "$label: the extracted snapshot file reads '$got', expected '$content'"
    fi
    if [[ "$(member_type "$archive" "$MODEL/blobs/$blob")" == "-" ]]; then
        pass "$label: the blob is stored as a regular file"
    else
        fail "$label: $MODEL/blobs/$blob is not a regular file in the archive"
    fi
    if [[ "$(member_type "$archive" "$MODEL/snapshots/$REV/model.bin")" == "l" ]]; then
        pass "$label: the snapshot entry stays a link"
    else
        fail "$label: the snapshot entry is not a link in the archive"
    fi
    if compgen -G "$cache/.pack-stage.*" > /dev/null; then
        fail "$label: a staging directory was left in the cache"
    else
        pass "$label: no staging directory left behind"
    fi
}

# --- pack ---------------------------------------------------------------------

make_per_repo_cache "$WORK/cache-per-repo" "weights-per-repo"
check_pack per-repo "$WORK/cache-per-repo" "weights-per-repo" 1111111111111111111111111111111111111111

make_shared_cache "$WORK/cache-shared" "weights-shared"
check_pack shared "$WORK/cache-shared" "weights-shared" 2222222222222222222222222222222222222222
if [[ -L "$WORK/cache-shared/$MODEL/blobs/2222222222222222222222222222222222222222" ]]; then
    pass "shared: the source cache is left as it was"
else
    fail "shared: the source cache's blob link was replaced"
fi

make_shared_cache "$WORK/cache-dangling" "unused" dangling
mkdir -p "$WORK/out-dangling"
if bash "$PACK" "$WORK/cache-dangling/$MODEL" "$WORK/out-dangling" > "$WORK/pack-dangling.log" 2>&1; then
    fail "dangling: pack-model.sh packed a cache whose blob is missing"
elif grep -q "points nowhere" "$WORK/pack-dangling.log"; then
    pass "dangling: pack-model.sh refuses and names the problem"
else
    fail "dangling: pack-model.sh failed without naming the dangling link"
    cat "$WORK/pack-dangling.log" >&2
fi

# A link out of the model directory that does not land in the cache's shared
# blob store (here: a file elsewhere on the host) must never be packed.
make_per_repo_cache "$WORK/cache-escape" "weights-escape"
printf 'host-secret' > "$WORK/host-file"
ln -s "$WORK/host-file" "$WORK/cache-escape/$MODEL/snapshots/$REV/extra.txt"
mkdir -p "$WORK/out-escape"
if bash "$PACK" "$WORK/cache-escape/$MODEL" "$WORK/out-escape" > "$WORK/pack-escape.log" 2>&1; then
    fail "escape: pack-model.sh packed a link to a file outside the cache's blob store"
elif grep -q "outside the cache's blob store" "$WORK/pack-escape.log"; then
    pass "escape: a link out of the cache's blob store is refused"
else
    fail "escape: pack-model.sh failed without naming the escaping link"
    cat "$WORK/pack-escape.log" >&2
fi

# --- unpack -------------------------------------------------------------------

if stat -c '%u' / > /dev/null 2>&1; then
    OWNER="$(id -u):$(id -g)"

    mkdir -p "$WORK/dest-good"
    if CHOWN="$OWNER" bash "$UNPACK" "$WORK/out-shared/example-model.tar.zst" "$WORK/dest-good" \
        > "$WORK/unpack-good.log" 2>&1 \
        && [[ "$(cat "$WORK/dest-good/$MODEL/snapshots/$REV/model.bin")" == "weights-shared" ]]; then
        pass "unpack: a fixed archive unpacks with its weights"
    else
        fail "unpack: the fixed archive did not unpack with its weights"
        cat "$WORK/unpack-good.log" >&2
    fi

    # What an older pack-model.sh produced from a shared-blob cache: the links, no weights.
    mkdir -p "$WORK/out-old"
    tar -C "$WORK/cache-shared" -cf - "$MODEL" | zstd -q -o "$WORK/out-old/example-model.tar.zst"
    mkdir -p "$WORK/dest-old"
    if CHOWN="$OWNER" bash "$UNPACK" "$WORK/out-old/example-model.tar.zst" "$WORK/dest-old" \
        > "$WORK/unpack-old.log" 2>&1; then
        fail "unpack: an archive without its weights was accepted"
    elif grep -q "re-pack" "$WORK/unpack-old.log"; then
        pass "unpack: an archive without its weights is refused with a re-pack hint"
    else
        fail "unpack: the refusal does not say to re-pack"
        cat "$WORK/unpack-old.log" >&2
    fi
else
    echo "skip: unpack checks need GNU stat (Linux)"
fi

if ((FAILURES > 0)); then
    echo "$FAILURES check(s) failed" >&2
    exit 1
fi
echo "all model-transfer checks passed"
