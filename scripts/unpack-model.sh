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

# Make the extracted files owned by whoever owns the cache dir (i.e. the UID
# the container runs as). Override with e.g. CHOWN=1000:1000 if needed.
OWNER="${CHOWN:-$(stat -c '%u:%g' "$DEST")}"
echo "Setting ownership to $OWNER..."
chown -R "$OWNER" "$DEST/$MODEL_ROOT"

echo "Done:"
du -sh "$DEST/$MODEL_ROOT"
