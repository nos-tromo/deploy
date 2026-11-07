#!/usr/bin/env bash
set -euo pipefail

# Unpack a model tarball (created by pack-model.sh) into a Docker volume's
# huggingface cache directory.
# Usage: sudo ./unpack-model.sh <tarball.tar.zst> [dest-dir]
# Example:
#   sudo ./unpack-model.sh example-model-fp8.tar.zst

DEFAULT_DEST="/var/lib/docker/volumes/huggingface-cache/_data"

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <tarball.tar.zst> [dest-dir]" >&2
    exit 1
fi

TARBALL="$1"
DEST="${2:-$DEFAULT_DEST}"

if [[ ! -f "$TARBALL" ]]; then
    echo "error: tarball not found: $TARBALL" >&2
    exit 1
fi

if [[ ! -d "$DEST" ]]; then
    echo "error: destination not found: $DEST" >&2
    echo "hint: reading /var/lib/docker usually requires root — try running with sudo" >&2
    echo "      if the 'huggingface-cache' volume doesn't exist on this machine," >&2
    echo "      create it with: docker volume create huggingface-cache" >&2
    exit 1
fi

command -v zstd >/dev/null || { echo "error: zstd not installed (apt install zstd)" >&2; exit 1; }

# Verify checksum if the .sha256 file sits next to the tarball.
if [[ -f "$TARBALL.sha256" ]]; then
    echo "Verifying checksum..."
    ( cd "$(dirname "$TARBALL")" && sha256sum -c "$(basename "$TARBALL").sha256" )
else
    echo "warning: $TARBALL.sha256 not found, skipping checksum verification" >&2
fi

# The archive root is the models--... directory; warn if it already exists
# (extraction proceeds and overwrites matching files in place).
# `|| true`: head exits after one line, so zstd/tar die of SIGPIPE and
# pipefail would otherwise kill the script here on any non-tiny tarball.
MODEL_ROOT=$(zstd -dc "$TARBALL" 2>/dev/null | tar -tf - 2>/dev/null | head -1 | cut -d/ -f1) || true
if [[ -z "$MODEL_ROOT" ]]; then
    echo "error: could not read archive listing from $TARBALL" >&2
    exit 1
fi
if [[ -e "$DEST/$MODEL_ROOT" ]]; then
    echo "warning: $DEST/$MODEL_ROOT already exists, extracting over it" >&2
fi

# Free-space check: model weights barely compress, so the tarball size is a
# close estimate of the extracted size; add 10% headroom.
NEEDED_KB=$(( $(stat -c%s "$TARBALL") * 11 / 10 / 1024 ))
AVAIL_KB=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
if (( AVAIL_KB < NEEDED_KB )); then
    echo "error: not enough space in $DEST (need ~${NEEDED_KB}K, have ${AVAIL_KB}K)" >&2
    exit 1
fi

echo "Extracting $TARBALL -> $DEST/$MODEL_ROOT"
zstd -dc "$TARBALL" | tar -xf - -C "$DEST"

# Every link must now point at a file. An archive that an older pack-model.sh
# made from a huggingface_hub >= 2 cache holds links into that cache's shared
# blob store (<cache>/blobs/), which the archive does not contain: it arrives
# without the weights, and the service would fail at startup.
DANGLING=$(find "$DEST/$MODEL_ROOT" -type l ! -exec test -e {} \; -print)
if [[ -n "$DANGLING" ]]; then
    echo "error: the archive holds links to files it does not contain (the model's weights are missing):" >&2
    printf '%s\n' "$DANGLING" | head -n 5 >&2 || true
    echo "hint: re-pack the model on the source machine with this repo's scripts/pack-model.sh," >&2
    echo "      which stores those files themselves, then unpack the new archive here." >&2
    exit 1
fi

# Make the extracted files owned by whoever owns the cache dir (i.e. the UID
# the container runs as). Override with e.g. CHOWN=1000:1000 if needed.
OWNER="${CHOWN:-$(stat -c '%u:%g' "$DEST")}"
echo "Setting ownership to $OWNER..."
chown -R "$OWNER" "$DEST/$MODEL_ROOT"

echo "Done:"
du -sh "$DEST/$MODEL_ROOT"

# The revision(s) this host now serves, read from the extracted archive itself
# rather than from the .manifest beside it, which no checksum covers.
if [[ -d "$DEST/$MODEL_ROOT/refs" ]]; then
    find "$DEST/$MODEL_ROOT/refs" -type f | sort | while read -r ref; do
        echo "ref ${ref#"$DEST/$MODEL_ROOT/refs/"}: $(cat "$ref")"
    done
fi
for snap in "$DEST/$MODEL_ROOT"/snapshots/*/; do
    if [[ -d "$snap" ]]; then
        echo "snapshot: $(basename "$snap")"
    fi
done
