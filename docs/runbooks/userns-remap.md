# Runbook: enabling `userns-remap` on a federation host

The compensating control prescribed by ADR 0001
(`docs/decisions/0001-container-engine-docker.md`) for the root-daemon
finding: container-root maps to an unprivileged host UID range, so a
container escape lands as a nobody-equivalent user, not host root. The
daemon itself stays root; membership in the `docker` group remains an
administrative privilege and must be treated as such in the host's role
model.

**Rehearse this entire runbook — together with
[volume-reown.md](volume-reown.md) — on a scratch host before any
production or staging host. Record timings; the `huggingface-cache`
re-own is the long pole.**

## Effect on UIDs (read first)

With remap enabled, a file that a container sees as owned by uid `N` is
owned on the host by `subuid_base + N`. The federation convention is
container-uid `10001` (`app`) for first-party images, plus a handful of
image-internal users (see the table in volume-reown.md). All
`chown` commands executed **inside a container** (the pattern the
runbooks use) keep working unchanged, because they speak container UIDs;
host-side `chown`/`ls -n` against `/var/lib/docker/…` must add the base
offset. This is why every re-own step runs via `docker run`, not via
host `chown`.

Note: `/var/lib/docker` is per-daemon-config — enabling remap switches
the storage root to `/var/lib/docker/<base>.<base>/`, so **all images,
volumes, and networks appear empty afterwards** until re-loaded/re-created.
Plan for a full `make load` + `make setup` + model unpack on the new root
(or migrate the volume data explicitly). This is the main reason the
change is scheduled together with the volume re-own in one maintenance
window.

## Procedure

1. **Stop the federation:** `make down` in `deploy/` (reverse-order,
   volume-safe).
2. **Create the remap user and subordinate ranges** (Ubuntu 24.04):

   ```bash
   sudo adduser --system --no-create-home --group dockremap || true
   grep dockremap /etc/subuid || echo "dockremap:300000:65536" | sudo tee -a /etc/subuid
   grep dockremap /etc/subgid || echo "dockremap:300000:65536" | sudo tee -a /etc/subgid
   ```

   The range must cover every container UID in use; 65536 covers the
   federation's highest (10001) many times over.
3. **Enable remap in the daemon config** (`/etc/docker/daemon.json`):

   ```json
   { "userns-remap": "dockremap" }
   ```

   Merge with existing keys — do not clobber the file.
4. **Restart the daemon:** `sudo systemctl restart docker`.
5. **Re-provision on the new storage root:** `make setup` (networks +
   volumes), `make load` (airgap) or `make pull`+builds (online), model
   weights via `unpack-model.sh` (its destination path now lives under
   the remapped root — check `docker volume inspect huggingface-cache`
   for the real mountpoint rather than assuming the default).
6. **Re-own the volumes** per [volume-reown.md](volume-reown.md) — run
   its container-based chowns now, under the remapped daemon, so the
   offsets land correctly.
7. **Bring the federation up:** `make up`; the tier health gates verify
   the order. Then spot-check one service per tier writes successfully
   (see the verification list in volume-reown.md).

## Rollback

Remove the `userns-remap` key, restart the daemon — the old
`/var/lib/docker` content reappears untouched (the remapped root is a
sibling directory). Data written while remapped stays under the remapped
root.

## Known interactions

- `wait-healthy.sh`'s busybox probe container is unaffected (no volumes).
- `docker load` must be re-run after enabling remap (separate image
  store).
- Host-path bind mounts (edge-plane's `authelia/users.yml`, config
  dirs mounted `:ro`) keep host ownership; ro mounts are unaffected,
  and Authelia's users.yml writeback works because its container keeps
  the caps to manage its own files — verify the password self-service
  cycle after enabling remap (edge-plane `scripts/smoke.sh`).
