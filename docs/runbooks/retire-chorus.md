# Runbook: retiring chorus and Neo4j from a host

A one-time procedure for every federation host, in the same maintenance window
as the upgrade to deploy v0.6.0, data-plane v0.9.0, obs-plane v0.12.0 and
edge-plane v0.12.0. Why: [ADR 0004](../decisions/0004-retire-chorus.md).
data-plane ran Neo4j on every host, so the procedure applies where chorus did
not run as well; skip what concerns chorus there. What chorus and Neo4j hold
is deleted, not migrated. The transfer itself follows the
[README](../../README.md#airgap-flow) and
[airgap-transfer.md](airgap-transfer.md); this runbook adds what the removal
needs on top. A host still on the releases from before the audit trail fits
these steps into [upgrade-to-audit-trail.md](upgrade-to-audit-trail.md), as
that runbook says.

Commands run from `deploy/`. `..` stands for `INFRA_ROOT`; use its path if
`federation.env` sets another.

## A. On the build host

1. If `federation.env` sets `APP_DIRS`, take `chorus` out of it. Then
   `make pull` and `make bundle`.
2. Delete data-plane's tarballs from before v0.9.0. They still hold the neo4j
   image, and `scripts/copy-bundles.sh` copies every tarball it finds:

   ```bash
   ver=$(cat ../data-plane/.data-plane-version) && [ -n "$ver" ] &&
     find ../data-plane -maxdepth 1 -name '*.tar.gz' ! -name "*-$ver.tar.gz" -print -delete
   ```

3. `scripts/copy-bundles.sh <dest>` into an empty `<dest>`. The script adds to
   what is already there, so an earlier transfer set would bring chorus and the
   old tarballs along.

The build host's own chorus checkout and images, if it has them, go as in B2
and B5.

## B. On the host, in the window

Step 1 needs the old files: the new ones no longer list chorus or Neo4j, so
their `make down` would leave both running. Steps 2 to 6 work with either, as
long as chorus' checkout is still there for step 2. Step 7 brings the new
files.

1. **Stop the federation with the old files:** `make down`. chorus, still in
   `APP_DIRS`, stops with the rest, and data-plane's old compose file still
   holds the neo4j service.
2. **Remove the images**, but not with `make prune`: after `down` no container
   uses any federation image, so it would remove them all, and chorus is no
   longer a member afterwards.
   - chorus' built images, matched by their compose project; an image a
     container still uses stays:

     ```bash
     scripts/prune-images.sh ../chorus
     ```

   - The neo4j image, by its tag and digest references. `PRUNE_UPSTREAM` does
     not fit: run over the old data-plane it would also take the unused Qdrant
     image, and the new data-plane no longer lists neo4j. The loop takes every
     image of the `neo4j` repository that no container uses, so check the list
     first for one another project on the host still needs. Tags go first; a
     digest reference that went with them is skipped.

     ```bash
     docker image ls --digests neo4j
     used=$(docker ps -aq --no-trunc | xargs -r docker container inspect --format '{{.Image}}')
     for id in $(docker image ls -q --no-trunc neo4j | sort -u); do
       if grep -qxF -- "$id" <<<"$used"; then echo "in use, kept: $id"; continue; fi
       docker image inspect --format '{{range .RepoTags}}{{println .}}{{end}}{{range .RepoDigests}}{{println .}}{{end}}' "$id" |
         grep -E '^(docker\.io/)?(library/)?neo4j[:@]' |
         while read -r ref; do
           if docker image inspect "$ref" >/dev/null 2>&1; then docker rmi "$ref"; fi
         done
     done
     ```

3. **Delete the volumes.** data-plane's `make nuke` no longer knows Neo4j's:

   ```bash
   docker volume rm chorus-state neo4j-data neo4j-logs neo4j-import neo4j-plugins
   ```

   A volume the host never had is reported missing; the others go regardless.
4. **Delete Neo4j's backups**, on the host and every copy off it. data-plane
   now prunes only Qdrant's, so these would stay:

   ```bash
   sudo find ../data-plane/backup/snapshots -maxdepth 1 -name 'neo4j-*.dump*' -print -delete
   ```

   The pattern also takes plaintext dumps from earlier releases and unfinished
   `.part` files. Where `BACKUP_DIR` points elsewhere, use that directory.
5. **Remove chorus' checkout**, whose `.env` holds secrets, and its auditd
   watch where [host-audit.md](host-audit.md)'s rules are set up:

   ```bash
   rm -rf ../chorus
   sudo sed -i '\#/chorus/\.env #d' /etc/audit/rules.d/60-federation.rules
   sudo augenrules --load
   ```

   With the rules locked (`-e 2`, host-audit.md step 3), the change applies at
   the next reboot instead.
6. **Clean up the settings** no release reads any more:

   ```bash
   sed -i '/^CHORUS_EDGE_SECRET=/d' ../edge-plane/.env
   sed -i '/^NEO4J_/d' ../data-plane/.env
   ```

   By hand: take `chorus/backend` out of `AUDIT_EMITTERS` in obs-plane's
   `.env`, and `chorus` out of `APP_DIRS` in `federation.env`, where they are
   set. Then this prints nothing:

   ```bash
   grep -s -n -E '^[^#]*(chorus|NEO4J_)' ../edge-plane/.env ../data-plane/.env ../obs-plane/.env federation.env
   ```

7. **Bring in the new releases.**
   - Replace the member files with the transfer set. Leave every `.env`,
     edge-plane's `authelia/users.yml` and `certs/`, and data-plane's
     `backup/` as they are.
   - Delete data-plane's tarballs from before v0.9.0, as on the build host:
     `make load` loads every tarball it finds, and those still hold the neo4j
     image.

     ```bash
     ver=$(cat ../data-plane/.data-plane-version) && [ -n "$ver" ] &&
       find ../data-plane -maxdepth 1 -name '*.tar.gz' ! -name "*-$ver.tar.gz" -print -delete
     ```

   - `make load`, `make setup`, `make up`. The state tier now waits on
     `qdrant:6333` only.

## C. Checks

Each prints nothing:

```bash
docker ps -a --format '{{.Names}}' | grep -i -E 'chorus|neo4j'
docker image ls -q neo4j
docker image ls -q --filter label=com.docker.compose.project=chorus
docker volume ls -q | grep -x -E 'chorus-state|neo4j-(data|logs|import|plugins)'
sudo auditctl -l | grep -F /chorus/   # with locked rules, only after the reboot
```

An image step 2 kept for a container still shows: after `make down`, that
container is not the federation's. The portal no longer shows chorus.
