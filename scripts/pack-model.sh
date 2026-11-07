#!/usr/bin/env bash
set -euo pipefail

# Pack a Hugging Face model dir (e.g. from a Docker volume) into a compressed tarball.
# Usage: sudo ./pack-model.sh <model-dir> [output-dir]
# Example:
#   sudo ./pack-model.sh /var/lib/docker/volumes/huggingface-cache/_data/models--example-org--example-model-fp8

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <model-dir> [output-dir]" >&2
    exit 1
fi

MODEL_DIR="${1%/}"
OUT_DIR="${2:-$PWD}"

# models--example-org--example-model -> example-model (fall back to the raw dir name)
BASE=$(basename "$MODEL_DIR")
NAME="${BASE##*--}"
OUT_FILE="$OUT_DIR/${NAME:-$BASE}.tar.zst"

if [[ ! -d "$MODEL_DIR" ]]; then
    echo "error: model directory not found: $MODEL_DIR" >&2
    echo "hint: reading /var/lib/docker usually requires root — try running with sudo" >&2
    exit 1
fi

command -v zstd >/dev/null || { echo "error: zstd not installed (apt install zstd)" >&2; exit 1; }

# Print the physical path a link finally points to; fail when it points nowhere.
resolve() {
    local path="$1" target dir hops=0
    while [[ -L "$path" ]]; do
        ((hops++ < 40)) || return 1
        target=$(readlink "$path")
        [[ "$target" == /* ]] || target="$(dirname "$path")/$target"
        path="$target"
    done
    [[ -e "$path" ]] || return 1
    dir=$(cd "$(dirname "$path")" && pwd -P) || return 1
    printf '%s/%s\n' "$dir" "$(basename "$path")"
}

# Print the physical path a link points to directly (one hop), without
# following further links; fail when its directory does not exist.
first_hop() {
    local path="$1" target dir
    target=$(readlink "$path")
    [[ "$target" == /* ]] || target="$(dirname "$path")/$target"
    dir=$(cd "$(dirname "$target")" 2>/dev/null && pwd -P) || return 1
    printf '%s/%s\n' "$dir" "$(basename "$target")"
}

# huggingface_hub >= 2 keeps large files in a blob store shared by every repo,
# at the cache root (<cache>/blobs/xx/<sha256>): the repo's blobs/<sha> is then
# a link out of the model directory. Archived as is, that link would arrive
# without the file it points to — an archive without the weights. Find such
# links, and refuse a link that points nowhere (an incomplete cache).
# Only links into that store are followed: the script runs as root on a volume
# that containers can write to, so a link to anywhere else (another repo, a
# host file) is refused rather than packed.
PARENT_DIR=$(dirname "$MODEL_DIR")
MODEL_REAL=$(cd "$MODEL_DIR" && pwd -P)
BLOB_STORE="$(cd "$PARENT_DIR" && pwd -P)/blobs"
OUTSIDE=0
while IFS= read -r -d '' link; do
    if ! real=$(resolve "$link"); then
        echo "error: $link points nowhere — the cache is incomplete; fetch the model again" >&2
        exit 1
    fi
    hop=$(first_hop "$link")
    [[ "$hop" == "$MODEL_REAL"/* ]] && continue
    if [[ "$real" != "$BLOB_STORE"/* || ! -f "$real" || -L "$real" ]]; then
        echo "error: $link points outside the cache's blob store ($BLOB_STORE); refusing to pack it" >&2
        exit 1
    fi
    OUTSIDE=$((OUTSIDE + 1))
done < <(find "$MODEL_DIR" -type l -print0)

# With links out of the model directory, pack a staged mirror of it instead, in
# which those links are the files they point to: hard links next to the model
# directory, so no data is copied (a copy only where a hard link is refused).
# Every other link (snapshots/ -> blobs/) stays a link. The cache itself is left
# as it is, and the mirror is removed when the script ends.
PACK_PARENT="$PARENT_DIR"
if ((OUTSIDE > 0)); then
    STAGE=$(mktemp -d "$PARENT_DIR/.pack-stage.XXXXXX")
    trap 'rm -rf "$STAGE"' EXIT
    echo "Resolving $OUTSIDE link(s) into the cache's shared blob store..."
    while IFS= read -r -d '' rel; do
        src="$PARENT_DIR/$rel"
        dst="$STAGE/$rel"
        if [[ -L "$src" ]]; then
            if [[ "$(first_hop "$src")" == "$MODEL_REAL"/* ]]; then
                ln -s "$(readlink "$src")" "$dst"
            else
                real=$(resolve "$src")
                ln "$real" "$dst" 2>/dev/null || cp -p "$real" "$dst"
            fi
        elif [[ -d "$src" ]]; then
            mkdir -p "$dst"
        else
            ln "$src" "$dst" 2>/dev/null || cp -p "$src" "$dst"
        fi
    done < <(cd "$PARENT_DIR" && find "$BASE" -print0)
    PACK_PARENT="$STAGE"
fi

# Measured on the tree that is packed, so files that live in the shared blob
# store count, and each of them once.
SIZE=$(du -sh "$PACK_PARENT/$BASE" | cut -f1)
echo "Packing $MODEL_DIR ($SIZE) -> $OUT_FILE"

# Free-space sanity check: need roughly the model size at the destination.
NEEDED_KB=$(du -sk "$PACK_PARENT/$BASE" | cut -f1)
AVAIL_KB=$(df -Pk "$OUT_DIR" | awk 'NR==2 {print $4}')
if (( AVAIL_KB < NEEDED_KB )); then
    echo "error: not enough space in $OUT_DIR (need ~${NEEDED_KB}K, have ${AVAIL_KB}K)" >&2
    exit 1
fi

# -C parent so the archive root is the models--... dir itself.
# Symlinks (snapshots/ -> blobs/) are preserved by default — do NOT dereference,
# or every file would be stored twice.
# Model weights are near-incompressible, so zstd -3 with all cores is the sweet spot.
tar -C "$PACK_PARENT" -cf - "$BASE" \
    | zstd -3 -T0 -o "$OUT_FILE"

# Checksum with only the filename in the .sha256 so it verifies anywhere
# the two files sit side by side.
echo "Generating checksum..."
( cd "$OUT_DIR" && sha256sum "$(basename "$OUT_FILE")" > "$(basename "$OUT_FILE").sha256" )

# Record the revision(s) the archive carries beside it, like the checksum:
# refs/<name> holds the commit a branch resolved to at download time, and
# snapshots/<commit>/ the files of each commit downloaded (a cache dir can
# hold several). unpack-model.sh reads the same from the archive itself.
REPO_ID="${BASE#models--}"
REPO_ID="${REPO_ID/--//}"
{
    echo "model: $REPO_ID"
    if [[ -d "$MODEL_DIR/refs" ]]; then
        find "$MODEL_DIR/refs" -type f | sort | while read -r ref; do
            echo "ref ${ref#"$MODEL_DIR/refs/"}: $(cat "$ref")"
        done
    fi
    for snap in "$MODEL_DIR"/snapshots/*/; do
        if [[ -d "$snap" ]]; then
            echo "snapshot: $(basename "$snap")"
        fi
    done
} > "$OUT_FILE.manifest"

echo "Done:"
ls -lh "$OUT_FILE" "$OUT_FILE.sha256" "$OUT_FILE.manifest"
cat "$OUT_FILE.manifest"
echo
echo "To extract: zstd -dc $(basename "$OUT_FILE") | tar -xf -"
