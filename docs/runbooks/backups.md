# Runbook: scheduled, encrypted backups

data-plane's `make backup` snapshots every Qdrant collection, encrypting each
file with age on its way out of the store; no plaintext copy rests on the
host. Each run is recorded in the audit trail (`disclose` /
`backup.<store>`). This runbook schedules it on the host. How the backup and
the restores work is data-plane's `backup/README.md`.

## Once per host

1. **Install age** from the distribution: `apt install age` (Debian 12+,
   Ubuntu 22.04+), `dnf install age` (Fedora; the RHEL family from EPEL). On an
   airgapped host, install the downloaded, signed package. Install it on every
   host a restore may run on too.
2. **Create the key pair offline**, on a machine that is not the server:
   `age-keygen -o backup-identity.txt` prints the public key (`age1…`). Keep
   the identity file offline, the way the audit signing key is kept
   ([audit.md](audit.md)). Add a second recipient, held by someone else, if a
   single lost identity must not lose every backup.
3. **Give the server the public key only:** put the `age1…` line or lines into
   `backup/recipients.txt` in the data-plane checkout (ignored by git), or set
   `BACKUP_RECIPIENTS` in data-plane's `.env` to another path. Whoever can
   change the file chooses who reads the next backup. So the file and every
   directory above it, up to the data-plane checkout (or up to `/` for a file
   outside it), must belong to root or to the backup account (step 4), and
   be writable by their owner only (a directory with the sticky bit, such as
   `/tmp`, excepted). The backup account must be able to read the file.
   Without such a file, every backup refuses to run, naming the path at fault
   (data-plane 0.6.1 and later; `backup/README.md` § The recipients file).
4. **A dedicated account for the unit**, for example `fed-backup`, with
   Docker access (the `docker` group is root-equivalent; see
   [host-audit.md](host-audit.md)). The trail records the account that ran
   each backup, so a dedicated one tells scheduled runs apart from an
   operator's.

## The timer

```ini
# /etc/systemd/system/federation-backup.service
[Unit]
Description=Encrypted data-plane backup (deploy docs/runbooks/backups.md)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
User=fed-backup
WorkingDirectory=<INFRA_ROOT>/data-plane
ExecStart=/usr/bin/make backup "REASON=scheduled backup"
```

```ini
# /etc/systemd/system/federation-backup.timer
[Unit]
Description=Nightly encrypted data-plane backup

[Timer]
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now federation-backup.timer
systemctl list-timers federation-backup.timer
```

- **`REASON=` is required.** Under the default `AUDIT_REQUIRE_PURPOSE`, a
  backup is a disclosure that needs a stated purpose; without one the run is
  recorded as denied and exits non-zero.
- **data-plane must be up.** `admin-audit` records the run; when it cannot,
  nothing is done.
- **Watch the exit status.** It is 0 only when every file was written and
  recorded. Wire `OnFailure=` to the site's alerting.
- **The output**, warnings about plaintext files from earlier releases
  included, goes to the journal: `journalctl -u federation-backup`.

## After each run

The encrypted files land in data-plane's `backup/snapshots/`. Backups are
short-lived restore points, not an archive: the stores delete data after the
configured retention period, and a backup kept longer would keep what they
already deleted.

- **On the host, the timer's runs prune.** After every backup that succeeded,
  data-plane deletes that store's backups older than `BACKUP_RETENTION_DAYS`
  (data-plane `.env`, default `30`), and records each pruning (`delete` /
  `backup.prune.<store>`). A period new on the host deletes nothing for
  `RETENTION_NOTICE_DAYS` (default `30`); a changed one never deletes sooner
  than announced — a longer one applies at once, a shorter one gives the
  notice to what it makes due sooner. Details: data-plane `backup/README.md`
  § Retention.
- **Off the host, the operator prunes.** Copying them to the offline medium
  with the daily audit anchors is not part of the targets, and neither is
  pruning there: delete the copies at the same age, `BACKUP_RETENTION_DAYS`.
- The host's auditd rules record who reads, writes or deletes a backup file
  ([host-audit.md](host-audit.md)).

A restore takes the identity for that run only:
`make restore-qdrant COLLECTION=<name> SNAPSHOT=<file>.age IDENTITY=<file>`
(data-plane `backup/README.md`).

**After a restore,** the restored data still carries its own deadlines:
docint's daily retention sweep, which starts a few minutes after docint's
backend starts, deletes what fell due while it was away. Let it run before
users work on restored collections.
