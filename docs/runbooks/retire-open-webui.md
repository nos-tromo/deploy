# Runbook: retiring Open WebUI from a host

A one-time procedure for every federation host that has open-webui-service,
in the maintenance window of the upgrade to deploy v0.13.0, edge-plane v0.18.0,
workbench v0.5.0, vllm-service v0.12.0 and obs-plane v0.16.0. Why:
[ADR 0008](../decisions/0008-retire-open-webui.md). What Open WebUI holds is
deleted, not migrated. The transfer itself follows the
[README](../../README.md#airgap-flow) and
[airgap-transfer.md](airgap-transfer.md); this runbook adds what the removal
needs on top.

Commands run from `deploy/`. `..` stands for `INFRA_ROOT`; use its path if
`federation.env` sets another.

Do step 1 before obs-plane v0.16.0 rolls out. From then on its verifier lists
no Open WebUI source and its lane no longer sends Open WebUI's audit lines to
the audit store: a sidecar still running is reported as `unlisted_source`, and
its lines would go to the ops Loki, which admins can read. Even with step 1
first, the daily anchor of the day Open WebUI stops, and `make audit-verify`
over any range before it, list the sidecar's earlier lines as
`unlisted_source`, as the check uses the new emitter list. That is expected,
and harmless.

Do step 7 before workbench v0.5.0 starts, that is before `make up` in step 9.

1. **Stop Open WebUI and delete its volume**, in its own checkout; this works
   with whichever deploy files are on disk.

   ```bash
   make -C ../open-webui-service down
   make -C ../open-webui-service nuke
   ```

   `nuke` asks for a confirm, then removes `open-webui-data`: every account,
   chat and upload. Until the new deploy files are in (step 9), put
   `OPENWEBUI_DIR=` in `federation.env`, so the old files' `make down`,
   `make up` and the rest skip the stopped member.
2. **Delete every copy of `open-webui-data`**, on the host and every copy off
   it: the snapshot [volume-reown.md](volume-reown.md) rule 2 writes as
   `/srv/backups/<vol>.tgz`, and the copy kept under
   [upgrade-to-audit-trail.md](upgrade-to-audit-trail.md) B1 before v0.13.0,
   wherever they went. The snapshot is root's:

   ```bash
   sudo rm -f /srv/backups/open-webui-data.tgz
   ```
3. **Remove the images**, but not with `make prune`: Open WebUI is no longer a
   member once the new deploy files are in, so it cannot reach them. The loop
   takes the images of the `open-webui` repository and the retention
   sidecar's `python:3.15.0-alpine3.24`, which no other member uses (they pin
   `python:3.12.16-alpine3.24`), unless a container uses them. Its
   `nginx-unprivileged` image is obs-plane's too and stays. Tags go first; a
   digest reference that went with them is skipped.

   ```bash
   imgs="ghcr.io/open-webui/open-webui python:3.15.0-alpine3.24"
   for img in $imgs; do docker image ls --digests "$img"; done
   used=$(docker ps -aq --no-trunc | xargs -r docker container inspect --format '{{.Image}}')
   for img in $imgs; do
     for id in $(docker image ls -q --no-trunc "$img" | sort -u); do
       if grep -qxF -- "$id" <<<"$used"; then echo "in use, kept: $id"; continue; fi
       docker image inspect --format '{{range .RepoTags}}{{println .}}{{end}}{{range .RepoDigests}}{{println .}}{{end}}' "$id" |
         grep -E "^(docker\.io/)?(library/)?${img%%:*}[:@]" |
         while read -r ref; do
           if docker image inspect "$ref" >/dev/null 2>&1; then docker rmi "$ref"; fi
         done
     done
   done
   ```
4. **Close `:8443`** in the host's firewall and in any network ACL where it was
   opened for Open WebUI (edge-plane's ADR 0002 asked for it). From edge-plane
   v0.18.0 nothing is published on it.
5. **Delete the checkout**, with its `.env` and its `*.tar.gz` tarballs, and
   its auditd watch where [host-audit.md](host-audit.md)'s rules are set up:

   ```bash
   rm -rf ../open-webui-service
   sudo sed -i '\#/open-webui-service/\.env #d' /etc/audit/rules.d/60-federation.rules
   sudo augenrules --load
   ```

   With the rules locked (`-e 2`, host-audit.md step 3), the change applies at
   the next reboot instead. A transfer set made before v0.13.0 still holds the
   checkout: build the next one into an empty `<dest>`.
6. **Edit the portal pages**: take the Open WebUI rows and the `:8443` address
   out of edge-plane's `info/privacy.md`, `info/ai.md`, `info/help.md` and
   `announcements/`, where the host has them. They are the host's own copies;
   the templates in v0.18.0 no longer carry those rows.
7. **Give workbench a key that remains.** workbench v0.5.0 starts only with the
   key of a remaining caller, `DOCINT_WORKBENCH_KEY` or
   `GHOSTWRITER_WORKBENCH_KEY`. Where workbench's `.env` holds only
   `OPEN_WEBUI_WORKBENCH_KEY`, run `make env ENV_GENERATE=1`: it generates
   `GHOSTWRITER_WORKBENCH_KEY` and writes it into Ghostwriter's `.env` as well
   ([env-sync.md](env-sync.md#shared-values)). That needs `ghostwriter` in
   `APP_DIRS`: with one member present the group is skipped, silently. Where
   neither docint nor Ghostwriter runs, set `WORKBENCH_DIR=` in
   `federation.env`: workbench v0.5.0 would not start, and `make up` would stall
   at its gate.
8. **Clean up the settings** no release reads any more:

   ```bash
   sed -i '/^OPEN_WEBUI_EDGE_SECRET=/d' ../edge-plane/.env
   sed -i '/^AUDIT_OPENWEBUI_JWT_SECRET=/d' ../vllm-service/.env
   sed -i '/^OPEN_WEBUI_WORKBENCH_KEY=/d' ../workbench/.env
   ```

   A leftover does no harm: `make env-check` lists it as not in the example.
   By hand: take `open-webui-service/retention` out of `AUDIT_EMITTERS` in
   obs-plane's `.env`, where it is set. Then this prints nothing:

   ```bash
   grep -s -n -i -E '^[^#]*open[-_ ]?webui' ../edge-plane/.env ../vllm-service/.env ../workbench/.env ../obs-plane/.env
   ```
9. **Bring in the new releases.**
   - Replace deploy's and the members' files with the transfer set. Leave every
     `.env`, deploy's `federation.env` and `.env-sync/`, edge-plane's
     `authelia/users.yml` and `certs/`, the texts step 6 wrote into its
     `info/` (not the `*.example.md` templates) and its `announcements/`, and
     data-plane's `backup/` as they are.
   - Take `OPENWEBUI_DIR` out of `federation.env`, where it is set. deploy
     v0.13.0 ignores it, so a leftover does no harm.
   - `make load`, `make env`, `make setup`, `make up`. Steps 2 to 8 work before
     or after the files come in; step 7 only before `make up`.
   - On a host that both builds and runs the federation there is no transfer
     set and no `make load`: `make pull` brings the files, then `make env`,
     `make bundle` builds the images, then `make setup` and `make up`.

## Checks

Each prints nothing:

```bash
docker ps -a --format '{{.Names}}' | grep -i open-webui
docker volume ls -q | grep -x open-webui-data
sudo ls /srv/backups/open-webui-data.tgz 2>/dev/null   # and any copy off the host
docker image ls -q ghcr.io/open-webui/open-webui
docker image ls -q python:3.15.0-alpine3.24
docker ps --format '{{.Ports}}' | grep 8443   # after step 9
ss -ltn | grep -E ':8443\s'
ls ../open-webui-service 2>/dev/null
grep -s -l -i -E 'open[-_ ]?webui|8443' ../edge-plane/info/*.md ../edge-plane/announcements/*.md
sudo auditctl -l | grep -F /open-webui-service/   # with locked rules, only after the reboot
```

Where workbench runs, this prints 1 or more:

```bash
grep -c -E '^(DOCINT|GHOSTWRITER)_WORKBENCH_KEY=.+' ../workbench/.env
```

Where step 3 kept an image because a container uses it, `docker image ls`
still lists it. That container is not Open WebUI's: step 1 removed those.
