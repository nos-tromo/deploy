#!/usr/bin/env bash
# Wait until one or more services are reachable on a shared Docker network, by
# probing <service>:<port> from a throwaway container on that network. This
# generalizes chorus's scripts/check_dataplane_health.sh to the whole
# federation — it checks cross-project reachability via network alias, which is
# exactly how the app backends find inference + state at runtime.
#
# Usage: wait-healthy.sh <network> <service:port> [<service:port> ...]
# Env:   WAIT_TIMEOUT (seconds per target, default 180)
#        WAIT_PROBE_IMAGE (default busybox:1.37; must be loaded on airgap hosts)

set -euo pipefail

NETWORK="${1:?usage: wait-healthy.sh <network> <service:port>...}"
shift
TIMEOUT="${WAIT_TIMEOUT:-180}"
PROBE_IMAGE="${WAIT_PROBE_IMAGE:-busybox:1.37}"

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "network '$NETWORK' not found — bring up its owning stack first." >&2
  exit 1
fi

for target in "$@"; do
  svc="${target%:*}"
  port="${target##*:}"
  echo "waiting up to ${TIMEOUT}s for ${svc}:${port} on ${NETWORK}..."
  start=$(date +%s)
  until docker run --rm --network "$NETWORK" "$PROBE_IMAGE" \
      sh -c "nc -z -w 2 ${svc} ${port}" >/dev/null 2>&1; do
    if (( $(date +%s) - start > TIMEOUT )); then
      echo "timed out waiting for ${svc}:${port} on ${NETWORK}" >&2
      # Surface WHY on timeout: distinguishes a DNS-resolution failure ("bad
      # address" -> service not attached to this network / wrong alias) from a
      # TCP failure ("refused" / no route -> nothing listening on that port yet).
      echo "  last probe (nslookup + verbose nc):" >&2
      docker run --rm --network "$NETWORK" "$PROBE_IMAGE" sh -c \
        "nslookup ${svc} 2>&1 | tail -n +3; echo '---'; nc -v -w 2 ${svc} ${port}" >&2 || true
      exit 1
    fi
    sleep 2
  done
  echo "  ${svc}:${port} reachable."
done
