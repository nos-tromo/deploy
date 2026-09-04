#!/usr/bin/env bash
set -euo pipefail

# Download a Hugging Face model into this machine's HF hub cache.
# The front half of the model-weights flow: fetch here, then pack-model.sh the
# resulting directory for transfer to an airgapped host.
# Usage: ./fetch-model.sh <model-id> [cache-dir]
# Example:
#   ./fetch-model.sh example-org/example-model-fp8
# Env: HF_TOKEN for gated or private repos.

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <model-id> [cache-dir]" >&2
    exit 1
fi

MODEL_ID="$1"

# `hf` since huggingface_hub 0.34, `huggingface-cli` on older hosts.
HF=$(command -v hf || command -v huggingface-cli) || {
    echo "error: hugging face CLI not found (pip install -U huggingface_hub)" >&2
    exit 1
}

# No cache-dir given: leave the CLI's own resolution alone, so a host that
# already sets HF_HUB_CACHE or HF_HOME keeps it.
if [[ -n "${2:-}" ]]; then
    export HF_HUB_CACHE="$2"
fi

CACHE_DIR="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
echo "Downloading $MODEL_ID -> $CACHE_DIR"

# The federation runs HF_HUB_OFFLINE=1 by default; inheriting that here would
# fail the one thing this script does.
SNAPSHOT=$(HF_HUB_OFFLINE=0 "$HF" download "$MODEL_ID")

# hf prints the snapshot path (<cache>/models--org--name/snapshots/<rev>) — bare
# on huggingface_hub 0.x, "path=" prefixed on 1.x. The models--... root above it
# is what pack-model.sh takes.
SNAPSHOT="${SNAPSHOT#path=}"
MODEL_DIR="${SNAPSHOT%%/snapshots/*}"
if [[ ! -d "$MODEL_DIR" ]]; then
    echo "error: could not derive the model directory from: $SNAPSHOT" >&2
    exit 1
fi

echo "Done:"
du -sh "$MODEL_DIR"
echo
echo "To pack for transfer: ./pack-model.sh $MODEL_DIR"
