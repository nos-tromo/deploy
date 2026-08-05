#!/usr/bin/env bash
# Collect everything the airgap host needs from the federation workspace into
# one destination directory (e.g. a mounted USB stick): every member's image
# tarballs (from `make bundle`), its compose files + Makefile (+ vendored
# make/ includes), and the config the containers mount straight from the repo.
# Secrets deliberately stay behind — see the edge-plane special case below.
#
# Runs from anywhere: member paths are derived from this script's location
# inside the deploy repo, assuming the infra/ workspace layout (members are
# siblings of deploy/). Override with INFRA_ROOT=/path/to/workspace.
#
# Usage: copy-bundles.sh <dest-dir>
# Example: copy-bundles.sh /media/transfer/builds

set -euo pipefail
shopt -s nullglob # *.tar.gz globs expand to nothing instead of a literal

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_ROOT="${INFRA_ROOT:-$(cd "$DEPLOY_SRC/.." && pwd)}"

if [ $# -ne 1 ]; then
    echo "usage: $0 <dest-dir>" >&2
    echo "example: $0 /media/transfer/builds" >&2
    exit 2
fi
DEST_BASE="$1"

echo -e "${BLUE}=== Copying airgap transfer set ===${NC}"
echo -e "Workspace:   $INFRA_ROOT"
echo -e "Destination: $DEST_BASE"

mkdir -p "$DEST_BASE"

copy_project() {
    local source_dir="$1"
    local dest_name="$2"
    shift 2

    local project_dest="$DEST_BASE/$dest_name"
    mkdir -p "$project_dest"

    echo -e "\n${BLUE}Processing member: [$dest_name]${NC}"

    # 1. Image tarballs. With nullglob active the loop simply doesn't run
    # when no tarball exists.
    local tarball_count=0
    for tarball in "$source_dir"/*.tar.gz; do
        cp -u "$tarball" "$project_dest/"
        echo -e "  ${GREEN}✓${NC} image copied: $(basename "$tarball")"
        tarball_count=$((tarball_count + 1))
    done

    if [ "$tarball_count" -eq 0 ]; then
        echo -e "  ${YELLOW}⚠ no .tar.gz files found in the source dir.${NC}"
    fi

    # 2. Compose files.
    mkdir -p "$project_dest/docker"
    if [ -f "$source_dir/docker/compose.yaml" ]; then
        cp "$source_dir"/docker/compose.yaml "$project_dest/docker/"
        # The dev overlay is optional (not every member has one).
        if [ -f "$source_dir/docker/compose.override.yaml" ]; then
            cp "$source_dir"/docker/compose.override.yaml "$project_dest/docker/"
        fi
        echo -e "  ${GREEN}✓${NC} docker compose files copied."
    else
        echo -e "  ${YELLOW}⚠ no compose.yaml found.${NC}"
    fi

    # 3. Makefile.
    if [ -f "$source_dir/Makefile" ]; then
        cp "$source_dir"/Makefile "$project_dest/"
        echo -e "  ${GREEN}✓${NC} Makefile copied."
        # Vendored make includes: the Makefile does `include make/common.mk`;
        # without make/ every make target fails on the airgap side.
        # (edge-plane/obs-plane/data-plane have standalone Makefiles without
        # make/ — then nothing is copied here.)
        if [ -d "$source_dir/make" ]; then
            cp -r "$source_dir/make" "$project_dest/"
            echo -e "  ${GREEN}✓${NC} make/ (vendored common.mk) copied."
        fi
    fi

    # 4. Optional extra files and directories (version files, .env.example,
    # mounted config dirs like caddy/ or grafana/ — dirs copied recursively).
    local extra
    for extra in "$@"; do
        if [ -e "$extra" ]; then
            cp -r "$extra" "$project_dest/"
            echo -e "  ${GREEN}✓${NC} copied: $(basename "$extra")"
        else
            echo -e "  ${YELLOW}⚠ not found — skipped: $extra${NC}"
        fi
    done
}

# --- Member list ---

copy_project "$INFRA_ROOT/docint" docint \
    "$INFRA_ROOT/docint/.docint-version" \
    "$INFRA_ROOT/docint/.env.example"

copy_project "$INFRA_ROOT/Nextext" Nextext \
    "$INFRA_ROOT/Nextext/.nextext-version" \
    "$INFRA_ROOT/Nextext/.env.example"

copy_project "$INFRA_ROOT/vllm-service" vllm-service \
    "$INFRA_ROOT/vllm-service/.vllm-service-version" \
    "$INFRA_ROOT/vllm-service/.env.example"

# Special case: the router config is mounted from the repo.
if [ -f "$INFRA_ROOT/vllm-service/docker/litellm.config.yaml" ]; then
    cp "$INFRA_ROOT/vllm-service/docker/litellm.config.yaml" "$DEST_BASE/vllm-service/docker/"
    echo -e "  ${GREEN}✓${NC} litellm.config.yaml copied."
fi

copy_project "$INFRA_ROOT/chorus" chorus \
    "$INFRA_ROOT/chorus/.chorus-version" \
    "$INFRA_ROOT/chorus/.env.example"

copy_project "$INFRA_ROOT/translator" translator \
    "$INFRA_ROOT/translator/.translator-version" \
    "$INFRA_ROOT/translator/.env.example"

copy_project "$INFRA_ROOT/open-webui-service" open-webui-service \
    "$INFRA_ROOT/open-webui-service/.env.example"

copy_project "$INFRA_ROOT/data-plane" data-plane \
    "$INFRA_ROOT/data-plane/.data-plane-version" \
    "$INFRA_ROOT/data-plane/.env.example"

# obs-plane: the containers mount their configuration straight from the repo —
# without these directories nothing starts on the airgap side.
copy_project "$INFRA_ROOT/obs-plane" obs-plane \
    "$INFRA_ROOT/obs-plane/VERSION" \
    "$INFRA_ROOT/obs-plane/.env.example" \
    "$INFRA_ROOT/obs-plane/prometheus" \
    "$INFRA_ROOT/obs-plane/grafana" \
    "$INFRA_ROOT/obs-plane/loki" \
    "$INFRA_ROOT/obs-plane/alloy" \
    "$INFRA_ROOT/obs-plane/blackbox"

# edge-plane: Caddy/portal configuration is likewise mounted from the repo.
# CAUTION: authelia/ and certs/ are deliberately NOT copied wholesale —
# authelia/users.yml (real users/hashes), certs/ (private keys) and .env must
# not leave the build machine; per the README they are provisioned fresh on
# the airgap side.
copy_project "$INFRA_ROOT/edge-plane" edge-plane \
    "$INFRA_ROOT/edge-plane/.edge-plane-version" \
    "$INFRA_ROOT/edge-plane/.env.example" \
    "$INFRA_ROOT/edge-plane/caddy" \
    "$INFRA_ROOT/edge-plane/landing" \
    "$INFRA_ROOT/edge-plane/authcode"

# edge-plane special case: authelia/ only template + configuration (without
# users.yml), certs/ only as an empty mountpoint (compose mounts it ro).
mkdir -p "$DEST_BASE/edge-plane/authelia" "$DEST_BASE/edge-plane/certs"
cp "$INFRA_ROOT/edge-plane/authelia/configuration.yml" \
   "$INFRA_ROOT/edge-plane/authelia/users.template.yml" \
   "$DEST_BASE/edge-plane/authelia/"
echo -e "  ${GREEN}✓${NC} edge-plane: authelia configuration (without users.yml) copied."

# edge-plane special case: client provisioning (hosts entry + CA trust) is
# needed on the airgap clients, not just on the gateway.
if [ -f "$INFRA_ROOT/edge-plane/scripts/client-setup.sh" ]; then
    mkdir -p "$DEST_BASE/edge-plane/scripts"
    cp "$INFRA_ROOT/edge-plane/scripts/client-setup.sh" \
       "$DEST_BASE/edge-plane/scripts/"
    echo -e "  ${GREEN}✓${NC} edge-plane: scripts/client-setup.sh copied."
else
    echo -e "  ${YELLOW}⚠ edge-plane: scripts/client-setup.sh not found — skipped.${NC}"
fi

# deploy (this repo): only what production needs — scripts/,
# federation.env.example, Makefile.
echo -e "\n${BLUE}Processing: [deploy]${NC}"
DEPLOY_DEST="$DEST_BASE/deploy"
mkdir -p "$DEPLOY_DEST"

if [ -d "$DEPLOY_SRC/scripts" ]; then
    cp -r "$DEPLOY_SRC/scripts" "$DEPLOY_DEST/"
    echo -e "  ${GREEN}✓${NC} deploy: scripts/ copied."
else
    echo -e "  ${YELLOW}⚠ deploy: scripts/ not found — skipped.${NC}"
fi

for deploy_file in federation.env.example Makefile; do
    if [ -f "$DEPLOY_SRC/$deploy_file" ]; then
        cp "$DEPLOY_SRC/$deploy_file" "$DEPLOY_DEST/"
        echo -e "  ${GREEN}✓${NC} deploy: $deploy_file copied."
    else
        echo -e "  ${YELLOW}⚠ deploy: $deploy_file not found — skipped.${NC}"
    fi
done

echo -e "\n${GREEN}=== Transfer set complete ===${NC}"
