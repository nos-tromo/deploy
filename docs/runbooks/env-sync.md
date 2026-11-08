# Runbook: keeping every `.env` in step with its example

Bring every member's `.env` (and `federation.env`) in step with its
`.env.example` after a release, without touching a value you set. The
[README](../../README.md#keeping-env-files-in-step) has the two commands; this
is the detail. The design is
[2026-11-07-env-sync-design.md](../superpowers/specs/2026-11-07-env-sync-design.md).

## Running it

```bash
make env-check   # preview: what `make env` would change; writes nothing
make env         # write: new keys, followed defaults, carried renames, shared values
```

`make up` and `make up-dev` run `env-check` first and stop before any tier
starts while a `.env` is behind its example or a check fails. `make env` is
never run for you: the write is always a deliberate step.

Both commands cover `federation.env` plus the `.env` of every member listed in
it. A member whose directory is missing on this host is reported as `missing`
and the others still sync.

What `make env` does to a file, in one paragraph: it rebuilds the file from the
current example, putting your lines in byte for byte. Keys the example added
arrive with their default. A default you never touched follows the example,
except for secrets, anything named `*RETENTION*`, shared keys and everything in
`federation.env`, which are only reported. Keys the example no longer knows
stay at the end of the file under a `# [env-sync]` heading; nothing is deleted.
Before a file is replaced, the old one is copied to the state directory.

Operator values are never printed. A value appears in the report only when it
comes from an example and the key is not secret-like.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Done; for `env-check`, nothing is behind. |
| 1 | A refused or missing file, a shared conflict, a failed state write after a `.env` was written, or (`env-check`) a file behind or never synced. |
| 2 | Usage, table, lock or state-directory error. Raised before any file is touched. |

`ENV_ALLOW_DRIFT=1 make up` overrides every `env-check` failure, one-shot, with
a loud warning; use it only when you accept starting on a `.env` that is
behind.

## The report

One block per file, `== <member>/.env  <status>`, then one line per kind of
event, `   <label> <items><note>`. A file's status is one of `written`,
`created`, `unchanged` (`make env`), `would write`, `would create`, `in sync`
(`make env-check`), `refused` or `missing`. When nothing is behind and there is
no error, the output is a single line, `env: N files in sync` (check) or
`env: N files unchanged` (write), followed by any warnings under their file
headers. A `.env` that exists but has no stored copy fails `env-check` with
`never synced: run 'make env' once`.

| Label | Meaning | What to do |
|---|---|---|
| `added` | Keys new in the example, written with the example's default. | Check any that need a real value. |
| `filled` | A shared key that was unset, copied from the member that holds the value. | Nothing. |
| `generated` | A shared secret generated because of `ENV_GENERATE=1`. | Nothing; it is written to every member of the group. |
| `followed` | A default you never touched moved with the example (new value). | Nothing. |
| `unset` (first form) | The example now only comments the key out; your inherited default no longer applies. | Nothing; set it yourself to keep a value. |
| `unset` (second form) | A shared key is unset in every member (shown as `KEY (members)`). A list (`entry=`) that is set without its entry counts as unset too. | Set it, or run `make env ENV_GENERATE=1`. A list you set is never generated into: add its entry by hand. |
| `removed` | The key is gone from the example and your line was its old default; the line is dropped. | Nothing. |
| `held` | The example's default changed, but the key is secret-like, a retention, a shared key or in `federation.env`. Yours is kept. | Compare with the example and decide. |
| `renamed` | The example renames a key. Three notes: the value is carried to the new name; the value is not carried (the old line stays at the end of the file with `# [env-sync] renamed to NEW in the example; value not carried`); or both names were set and the new name applies. | When not carried, copy the value by hand if you still want it, then delete the old line. |
| `unknown` | Keys the example does not have; kept at the end of the file. | Delete them once you are sure nothing reads them. |
| `kept unset` | A key you commented out on purpose stays commented. | Nothing. |
| `differs` | First run: keys whose value differs from the example's default. By name only. | Nothing; this is the baseline. |
| `lint` | A line that reads differently than it looks, for example `KEY=   # text`, whose value is `# text`; or one whose `${KEY}` now reads a line the sync added above it (`now reads ${KEY} from a line the sync added`). | Fix the line; for `now reads`, check the value it gets now. |
| `placeholder` | A key still holding the example's placeholder. | Set a real value. |
| `comments` | Your comment lines that were not carried (comments come from the example). | Recover them from the backup if needed. |
| `readable` | The `.env` is readable by every user (member `.env` files only). | `chmod 600` it. |
| `mode` | `make env` wrote a shared secret into this file and removed other users' access (group rights are kept). | Nothing, unless another account must read it. |
| `state` | The stored copy could not be read or saved. | Fix the cause (see [State directory](#keep-the-state-directory)); an unreadable copy counts as never synced. |
| `conflict` | A shared group holds two different set values. Nothing in the group changes and `env-check` fails. | See [Shared values](#shared-values). |
| `not copied` | A shared value with characters outside the safe set. | Set it by hand in each member. |
| `table` | `env-sync.conf` names a key the member's example does not know. | Fix `env-sync.conf` or the example. |

### When a rename carries its value

The example records a rename as `# renamed: OLD -> NEW`. For a file that still
has an OLD line:

- **Carried.** The stored copy of the last synced example still documents OLD
  (the rename is new to this host) and the OLD line is your own, not the old
  default left untouched. Only the key name changes; the rest of the line stays
  byte for byte.
- **Dropped.** An OLD line still at its old default is dropped and NEW takes the
  example's value (`old default dropped`). The exception is a held key (secret-like,
  `*RETENTION*`, a shared key, anything in `federation.env`, on either name):
  it carries its old value, so a retention is never lowered through a rename.
- **NEW already set.** When NEW holds your own value, NEW applies and OLD goes
  to the end of the file (`both set; the new name applies`). The same happens
  when a held OLD is still at its old default, and when two OLD keys name the
  same NEW. An untouched NEW, still at the example's default, is no conflict
  for your own OLD: OLD's value is carried over it, under the same conditions
  as **Carried**.
- **Not carried.** The rename predates the last sync, or there is no sync record
  yet: OLD stays at the end of the file with
  `# [env-sync] renamed to NEW in the example; value not carried`, so a value
  ignored for months does not start to apply. Move it by hand if you still want it.
- **Removed before the rename.** A setting you removed stays removed under its
  new name: NEW is written commented out (`kept unset`).

A `refused` file is left untouched with its line number and the reason (a
duplicate key, CRLF line ends, a byte-order mark, a multi-line value, a
symlink, ...). The other files still sync. Fix the file and run again.

`federation.env` is read as make reads it, and only these forms are accepted:
`KEY=value`, `KEY = value`, `# KEY=value`, other comments and blank lines. Forms
that make itself accepts are refused: `export`, `?=`, `:=`, `+=`, conditionals,
and a `KEY=` line (set or commented out) that ends in `\`, which continues it
onto the next line. A refused `federation.env` stops `make up` like any other
refused file. Such lines belong in the Makefile, not in `federation.env`.

## First run on a host

The first `make env` on a host has no stored copy of any example, so nothing
follows a changed default and the report only lists the `differs` keys. It
records each example it synced against, which is what lets the next release
follow untouched defaults and carry renames.

```bash
make env-check   # read the preview
make env         # write
```

An existing `.env` without a stored copy fails `env-check` until this first
`make env`. A missing `.env` is created from the example (mode 0600); an empty
or blank-only one is rebuilt from the example after a backup.

A write that adds a filled or generated shared secret to a `.env` readable by other users also removes their access to it (`mode & ~0o007`); every other write keeps the mode.

**Build host.** Run `make env` after `make pull`, not between `make bundle` and
`make pull`: `bundle` leaves each member on its release tag, and the stored copy
should be the `main` examples you will work from.

**Airgap host, adoption.** The members' examples arrive with the transfer set,
so adopt with two passes of `make env`, so the first upgrade already follows
defaults and carries renames:

1. Replace deploy's files first.
2. `make env`, against the still-old member examples. This records them.
3. Replace the member files.
4. `make env` again.

Then `make setup` and `make up` as in [airgap-transfer.md](airgap-transfer.md).

## Pinning a default

A default follows the example only while your line is byte-identical to the one
in the stored example. To keep a value that happens to equal today's default,
add a comment to the line:

```
SOME_SETTING=30  # pinned
```

Any edit, an added comment included, makes the value yours. Secret-like keys,
`*RETENTION*` keys, shared keys and everything in `federation.env` never follow
in any case.

Do not pin with a comment a key that a member's Makefile reads straight from
its `.env` (`grep -E '^KEY=' .env | cut -d= -f2`): the comment ends up in the
value. These are data-plane's `PROFILE`, `DATA_NET`, `BACKUP_RECIPIENTS`,
`BACKUP_RETENTION_DAYS` and `RETENTION_NOTICE_DAYS`, and the `*_NET` keys of
obs-plane (`INFERENCE_NET`, `DATA_NET`, `EDGE_NET`), edge-plane (`EDGE_NET`) and
open-webui-service (`INFERENCE_NET`, `EDGE_NET`). Their defaults rarely change,
and the `*RETENTION*` ones are held anyway.

## Shared values

Some values must be equal across members: the edge secrets, the Qdrant key, the
router key and so on. `env-sync.conf` lists them as groups, one row per member
and key. Per group:

- A row is **unset** when it has no active line, an empty value, a value
  starting with `#`, a value containing `<...>`, or an example value of any row
  of the group (current or previously seen, active or commented).
  Placeholders therefore never spread, even after an example changed its
  placeholder: the tool remembers every example value of the shared keys it has
  seen.
- A set value fills the unset rows (`filled`). A row marked `source` counts as
  set whenever it has an active line; the router key's authority is
  vllm-service, `EDGE_HOST`'s is edge-plane.
- Two different set values are a **conflict**: nothing in the group changes and
  `env-check` fails. Decide which value is right, put it in the other `.env` by
  hand, and run again.
- A group with fewer than two members on this host is skipped.
- Only values made of `[A-Za-z0-9._~:/@+=,-]` are copied; others are reported
  as `not copied`.
- `entry=NAME` rows hold a `NAME=token,...` list (Nextext's
  `NEXTEXT_SERVICE_TOKENS`); only the `docint` entry is shared. A list you set
  is never appended to.

The `router-key` group rests on an assumption that holds on these hosts: every
app reaches inference only through `vllm-router`, so vllm-service's
`OPENAI_API_KEY` (LiteLLM's master key) is the source for the apps. If a host
points an app at another provider, remove that app's `router-key` row from
`env-sync.conf`. Otherwise the tool reports a conflict for the app's own key,
or, while that key is unset or a placeholder, writes the router key into it.

`ENV_GENERATE=1 make env` generates a fresh 64-hex value for each group that is
unset in every present member, and only when all its rows can take it. Secrets
that belong to one member (Authelia's keys) are never generated; they show up
as `placeholder`.

A new or renamed shared key in a member's example needs an edit to
`env-sync.conf`; see [For member authors](#for-member-authors).

## Restoring a backup

Every write first copies the old file into the state directory, the last five
per file. To restore one, copy it back and delete the file's stored copy, from
deploy:

```bash
ls .env-sync/backup/<member>/
cp -p .env-sync/backup/<member>/<stamp>.env ../<member>/.env
rm .env-sync/base/<member>.example
```

The stored copy is the example the replaced file was in step with, and the
backup is older. Kept, it makes the next `make env` read every key the example
gained since the backup as one you commented out on purpose, and comment it
out (`kept unset`). Deleted, the next run treats the file as never synced:
missing keys come back with their defaults, keys whose value differs from the
example are listed as `differs`, and renames are reported rather than carried.
Until that `make env`, `make env-check` fails with `never synced`, so nothing
drifts unnoticed. Keep `base/<member>.placeholders`: it only remembers example
values, so that a placeholder never spreads.

The same applies to any `.env` you replace with an older copy by hand.

`<member>` is the directory name. `federation.env` is stored under `deploy`
(`backup/deploy/`, `base/deploy.example`), and its restore target is
`federation.env` in deploy. The stamp is UTC, `YYYYmmddTHHMMSSZ`. The report
names the backup in the file's header line.

## Keep the state directory

`.env-sync/` in deploy (mode 0700, gitignored, never shipped by
`copy-bundles.sh`) holds:

| Path | Content |
|---|---|
| `base/<member>.example` | The example each file was last synced against. |
| `base/<member>.placeholders` | Every example value of the shared keys the tool has seen. |
| `backup/<member>/<stamp>.env` | The previous `.env` before each write (0600, last 5 per file). |
| `lock` | Held while the command runs. |
| `tmp/` | Where a new `.env` is written before it replaces the old one atomically. |

Nothing is ever created in a member directory except its `.env`.

The backups are copies of your `.env` files and contain secrets: treat the
directory like the `.env` files themselves ([host-audit.md](host-audit.md)
watches it). Do not delete it: without the stored copies defaults stop
following and `env-check` fails until the next `make env`.

## For member authors

How a member's `.env.example` must be written for the sync to read it:

- Settings are commented as `# KEY=value`.
- Prose and alternatives never start with `KEY=` right after `# `.
- Never `KEY=   # comment`: compose reads the comment as the value. Put the
  comment on its own line.
- Record a rename as `# renamed: OLD -> NEW`, and keep the line forever (what
  it does to an operator's value: [When a rename carries its value](#when-a-rename-carries-its-value)). OLD
  must no longer be a key of the example, a chain must end in a key the example
  has, and no file may reference `${OLD}`.
- A new or renamed shared key means an edit to deploy's `env-sync.conf`.

The example's value for a key should equal the default the member's Makefile or
compose file applies; otherwise a followed default and the real behaviour
diverge.

## Knobs

| Setting | Effect |
|---|---|
| `ENV_GENERATE=1` | `make env` generates the shared secrets that are unset everywhere. |
| `ENV_ALLOW_DRIFT=1` | `env-check` (and so `make up`) continues past every failure, with a warning. |

Pass them one-shot (`make env ENV_GENERATE=1`) or set them in `federation.env`.
