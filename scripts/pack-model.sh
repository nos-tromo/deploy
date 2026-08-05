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

SIZE=$(du -sh "$MODEL_DIR" | cut -f1)
echo "Packing $MODEL_DIR ($SIZE) -> $OUT_FILE"

# Free-space sanity check: need roughly the model size at the destination.
NEEDED_KB=$(du -sk "$MODEL_DIR" | cut -f1)
AVAIL_KB=$(df -Pk "$OUT_DIR" | awk 'NR==2 {print $4}')
if (( AVAIL_KB < NEEDED_KB )); then
    echo "error: not enough space in $OUT_DIR (need ~${NEEDED_KB}K, have ${AVAIL_KB}K)" >&2
    exit 1
fi

# -C parent so the archive root is the models--... dir itself.
# Symlinks (snapshots/ -> blobs/) are preserved by default — do NOT dereference,
# or every file would be stored twice.
# Model weights are near-incompressible, so zstd -3 with all cores is the sweet spot.
tar -C "$(dirname "$MODEL_DIR")" -cf - "$BASE" \
    | zstd -3 -T0 -o "$OUT_FILE"

# Checksum with only the filename in the .sha256 so it verifies anywhere
# the two files sit side by side.
echo "Generating checksum..."
( cd "$OUT_DIR" && sha256sum "$(basename "$OUT_FILE")" > "$(basename "$OUT_FILE").sha256" )

echo "Done:"
ls -lh "$OUT_FILE" "$OUT_FILE.sha256"
echo
echo "To extract: zstd -dc $(basename "$OUT_FILE") | tar -xf -"
