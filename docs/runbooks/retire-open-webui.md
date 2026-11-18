# Runbook: retiring Open WebUI from a host

A one-time procedure for every federation host that has open-webui-service,
in the maintenance window of the upgrade to deploy v0.13.0, edge-plane v0.18.0,
workbench v0.4.0, vllm-service v0.12.0 and obs-plane v0.16.0. Why:
[ADR 0008](../decisions/0008-retire-open-webui.md). What Open WebUI holds is
deleted, not migrated. The transfer itself follows the
[README](../../README.md#airgap-flow) and
[airgap-transfer.md](airgap-transfer.md); this runbook adds what the removal
needs on top.

Commands run from `deploy/`. `..` stands for `INFRA_ROOT`; use its path if
`federation.env` sets another.

Do step 1 before obs-plane v0.16.0 rolls out: from then on its verifier lists
no Open WebUI source, and a sidecar still running is reported as an
`unlisted_source`. Do step 5 before workbench v0.4.0 starts.

1. **Stop Open WebUI and delete its volume**, in its own checkout, before the
   new deploy files come in: from v0.13.0 on `make down` no longer reaches it.

   ```bash
   make -C ../open-webui-service down
   make -C ../open-webui-service nuke
   ```

   `nuke` asks for a confirm, then removes `open-webui-data`: every account,
   chat and upload. While the old deploy files are in place,
   `OPENWEBUI_DIR=` in `federation.env` keeps their targets off the stopped
   member.
2. **Remove the image**, but not with `make prune`: after `down` no container
   uses any federation image, so it would take them all, and Open WebUI is no
   longer a member afterwards. `PRUNE_UPSTREAM` would also take the
   `nginx-unprivileged` and `python` images that other members use. The loop
   takes every image of the `open-webui` repository that no container uses.
   Tags go first; a digest reference that went with them is skipped.

   ```bash
   docker image ls --digests ghcr.io/open-webui/open-webui
   used=$(docker ps -aq --no-trunc | xargs -r docker container inspect --format '{{.Image}}')
   for id in $(docker image ls -q --no-trunc ghcr.io/open-webui/open-webui | sort -u); do
     if grep -qxF -- "$id" <<<"$used"; then echo "in use, kept: $id"; continue; fi
     docker image inspect --format '{{range .RepoTags}}{{println .}}{{end}}{{range .RepoDigests}}{{println .}}{{end}}' "$id" |
       grep -E '^ghcr\.io/open-webui/open-webui[:@]' |
       while read -r ref; do
         if docker image inspect "$ref" >/dev/null 2>&1; then docker rmi "$ref"; fi
       done
   done
   ```
3. **Take `OPENWEBUI_DIR` out of `federation.env`**, if it is set. deploy
   v0.13.0 ignores it, so a leftover does no harm.
4. **Delete the checkout**, with its `.env` and its `*.tar.gz` tarballs, and
   its auditd watch where [host-audit.md](host-audit.md)'s rules are set up:

   ```bash
   rm -rf ../open-webui-service
   sudo sed -i '\#/open-webui-service/\.env #d' /etc/audit/rules.d/60-federation.rules
   sudo augenrules --load
   ```

   With the rules locked (`-e 2`, host-audit.md step 3), the change applies at
   the next reboot instead. A transfer set made before v0.13.0 still holds the
   checkout: build the next one into an empty `<dest>`.
5. **Give workbench a key that remains.** workbench v0.4.0 starts only with the
   key of a remaining caller, `DOCINT_WORKBENCH_KEY` or
   `GHOSTWRITER_WORKBENCH_KEY`. Where workbench's `.env` holds only
   `OPEN_WEBUI_WORKBENCH_KEY`, run `make env ENV_GENERATE=1`: it generates
   `GHOSTWRITER_WORKBENCH_KEY` and writes it into Ghostwriter's `.env` as well
   ([env-sync.md](env-sync.md#shared-values)). Ghostwriter's checkout must be
   on the host.
6. **Clean up the settings** no release reads any more:

   ```bash
   sed -i '/^OPEN_WEBUI_EDGE_SECRET=/d' ../edge-plane/.env
   sed -i '/^AUDIT_OPENWEBUI_JWT_SECRET=/d' ../vllm-service/.env
   sed -i '/^OPEN_WEBUI_WORKBENCH_KEY=/d' ../workbench/.env
   ```

   By hand: take `open-webui-service/retention` out of `AUDIT_EMITTERS` in
   obs-plane's `.env`, where it is set. A leftover does no harm, as
   `make env-check` lists it as not in the example. Then this prints nothing:

   ```bash
   grep -s -n -i -E '^[^#]*open[-_]?webui' ../edge-plane/.env ../vllm-service/.env ../workbench/.env ../obs-plane/.env federation.env
   ```

## Checks

Each prints nothing:

```bash
docker ps -a --format '{{.Names}}' | grep -i open-webui
docker volume ls -q | grep -x open-webui-data
docker image ls -q ghcr.io/open-webui/open-webui
ls ../open-webui-service 2>/dev/null
sudo auditctl -l | grep -F /open-webui-service/   # with locked rules, only after the reboot
```

An image step 2 kept for a container still shows: after `make down`, that
container is not the federation's.
