# Runbook: host-level auditing (auditd)

The federation's audit trail records what its services do. Some actions
happen outside every service, on the host itself, and only the kernel's
audit subsystem can name the person behind them:

- **Hand edits of edge-plane's `authelia/users.yml`**, the only way to create,
  remove or regroup accounts. edge-plane's `auth-audit` records *what*
  changed, but a file edit carries no operator identity.
- **Edits of a member's `.env`**, which holds the secrets and switches such
  as `AUDIT_REQUIRE_PURPOSE`.
- **`docker` commands run by hand**, for example a `docker exec` into a
  database container that bypasses data-plane's audited `make qdrant-shell`.
- **Reads of data-plane's backup files.** `make backup` is recorded in the
  trail, but copying a backup off the host is not.

For each of these the kernel records the **login user** (`auid`), which stays
the same through `sudo` and `su`. These records live in the host's audit log,
not in the hash-chained trail; the [audit-trail runbook](audit.md) covers
the trail itself.

## Setup (once per host, as root)

1. **Install and start auditd.** Debian and Ubuntu: `apt install auditd`; the
   RHEL family: `dnf install audit`. Then `systemctl enable --now auditd`.
2. **Write the rules.** auditd needs absolute paths. Run this from
   `deploy/`, with `root` set to the directory that holds `deploy/` and the
   members (`INFRA_ROOT` in `federation.env`):

   ```bash
   root=$(cd .. && pwd)   # INFRA_ROOT; use its absolute path if federation.env sets another
   docker_cli=$(readlink -f "$(command -v docker)")
   sudo tee /etc/audit/rules.d/60-federation.rules >/dev/null <<EOF
   ## Federation: host-level records (deploy docs/runbooks/host-audit.md)
   # The account file, watched through its directory: an editor that saves by
   # writing a new file and renaming it over the old one replaces the file.
   -w $root/edge-plane/authelia -p wa -k fed-accounts
   # Every member's .env: secrets and switches such as AUDIT_REQUIRE_PURPOSE.
   -w $root/docint/.env -p wa -k fed-config
   -w $root/Nextext/.env -p wa -k fed-config
   -w $root/translator/.env -p wa -k fed-config
   -w $root/vllm-service/.env -p wa -k fed-config
   -w $root/data-plane/.env -p wa -k fed-config
   -w $root/obs-plane/.env -p wa -k fed-config
   -w $root/edge-plane/.env -p wa -k fed-config
   -w $root/open-webui-service/.env -p wa -k fed-config
   -w $root/deploy/federation.env -p wa -k fed-config
   # make env's state directory: its backups are .env copies, so a read
   # counts; the stored examples are read at every make up, so writes only.
   -w $root/deploy/.env-sync/backup -p rwa -k fed-config
   -w $root/deploy/.env-sync -p wa -k fed-config
   # data-plane's backups (its BACKUP_DIR, if set elsewhere): copying one
   # off the host reads the whole database.
   -w $root/data-plane/backup/snapshots -p rwa -k fed-backup
   # Every docker CLI run, with its arguments (exec, run, cp, compose ...).
   -w $docker_cli -p x -k fed-docker
   # Docker's own configuration.
   -w /etc/docker -p wa -k fed-docker-config
   EOF
   sudo augenrules --load
   sudo auditctl -l | grep -- '-k fed-'
   ```

   A watch on a file survives the file being replaced, and one on a missing
   file starts when the file is created; the parent directory must exist. A
   directory watch covers everything below it. Whether a path is watched as a
   directory or as a file is decided when the rules load, so the two
   `.env-sync` directories must exist by then. The first `make env` creates
   them: on a host that has not run it yet, load the rules again after it
   (`sudo augenrules --load`), or reboot once they are locked (step 3).
3. **Lock the rules (recommended).** Add `-e 2` as the very last rule, e.g.
   `echo '-e 2' | sudo tee /etc/audit/rules.d/99-finalize.rules`, and load
   again. From then on no rule can be removed or changed until the next
   reboot, and a reboot shows in the records. Changing the rules later means
   editing the files and rebooting.
4. **Keep the records.** In `/etc/audit/auditd.conf` set
   `max_log_file_action = keep_logs`, so auditd rotates its log but never
   deletes a rotated file. Move the rotated files (`/var/log/audit/audit.log.*`)
   to the offline medium with the daily anchors, and delete them after the
   same period as the trail's `AUDIT_RETENTION`.
5. **Synchronize the clock.** Every record of the trail and of auditd carries
   the host's time. Point chrony or systemd-timesyncd at the site's time source
   (on an airgapped host, an internal NTP server), and check
   `timedatectl show -p NTPSynchronized --value` answers `yes`.

`make env` replaces each `.env` it changes with a new file, which shows as
`fed-config` records under the operator's login user, as does a read of a
backup in `deploy/.env-sync/backup`.

A host that ran this setup before the two `.env-sync` lines existed adds them
to `/etc/audit/rules.d/60-federation.rules` after its first `make env`, then
loads the rules again (`sudo augenrules --load`). With the rules locked
(`-e 2`, step 3), the lines apply after the next reboot.

## Reading the records

```bash
sudo ausearch -k fed-accounts -i --start yesterday   # who wrote to the account file
sudo ausearch -k fed-config -i --start today          # who changed a .env
sudo ausearch -k fed-docker -i --start today          # docker commands; the EXECVE record holds the arguments
sudo ausearch -k fed-backup -i --start this-week      # who read, wrote or deleted a backup file
sudo aureport -k --summary                            # record counts per key
```

`-i` prints the `auid` as a login name. To name who made an account change,
take the `account.*` event of edge-plane's `auth-audit` from the trail and the
`fed-accounts` record closest before it in time.

## Limits

- **root can switch auditd off** unless the rules are locked (`-e 2`), and can
  edit the local log until it has left the host. Lock the rules and move the
  logs off daily.
- **The Docker API socket used directly is not recorded.** A client that
  talks to the socket without the CLI (an SDK, `curl --unix-socket`) leaves
  no `fed-docker` record. Anyone in the `docker` group can do that and is
  root-equivalent: keep the group to the administrators, and run Docker
  through `sudo docker` so every command is a recorded CLI run.
- **A watch names the writer, not the content.** The records show who wrote
  to a file and when; what changed comes from the trail (`auth-audit`'s
  account events) or from the file's history.
