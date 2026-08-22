# Runbook: airgap transfer

Moving a release from the online build host to an offline federation host.
The [README](../../README.md#airgap-flow) has the four-step shape; this is
what each step actually does, and the two guards that stop a broken transfer
set from leaving the build host.

```
build host (online)                 airgap host (offline)
──────────────────                  ─────────────────────
make bundle  ──▶ *.tar.gz  ──copy──▶  make load   (docker load all tarballs)
                                      make setup
                                      make up
```

## `bundle` / `load` — the images

Each member repo already produces its own versioned tarballs (`make bundle`,
sharing `scripts/bundle-lib.sh`); `make bundle` here just fans that out, and
`make load` loads them on the offline side. obs-plane's
`obs-plane-pulled-<version>.tar.gz` and edge-plane's
`edge-plane-pulled-<version>.tar.gz` are included in the fan-out.
The fan-out skips members already bundled at their current release tag (see
the `bundle` row in [bring-up.md](bring-up.md#targets)). Two caveats: the
version record carries no profile, so after switching `DATA_PROFILE` force a
data-plane rebuild with `BUNDLE_FORCE=1`; setting a `*VERSION_OVERRIDE`
disables the skip automatically for that run.
`wait-healthy.sh` uses a throwaway `busybox` probe container; `make bundle`
saves that image too (`wait-probe-image.tar.gz` in this repo, pulled by the
digest in `WAIT_PROBE_PIN` and tagged `WAIT_PROBE_IMAGE`), and `make load`
restores it — so the health gates work offline without any extra step. If you
swap the probe image, override `WAIT_PROBE_IMAGE` and `WAIT_PROBE_PIN`
together in `federation.env`.

## The copy step — `scripts/copy-bundles.sh`

The **copy** step is `scripts/copy-bundles.sh <dest-dir>`: it collects, per
member, the image tarballs plus everything the airgap host needs beside them —
compose files, Makefile (+ vendored `make/`), version files, `.env.example`,
and the config directories the containers mount from the repo — into one
destination (e.g. a mounted USB stick). Member paths are derived from the
script's location (siblings of `deploy/`; override with `INFRA_ROOT=...`).
Secrets stay behind by design: edge-plane's `authelia/users.yml`, `certs/`,
and every real `.env` are never copied — they are provisioned fresh on the
airgap side.

## The version-skew guard

The script also refuses to assemble a skewed transfer set. Member bundles are
built from the latest release tag, while the repo files copied beside them
come from the working tree — so a member that has moved past its last release
(new commits since the tag, or uncommitted changes) would hand the airgap
host compose files referencing images its tarball doesn't contain, and the
first `make up` there tries to pull from the internet. Each member's
`.<slug>-version` file records what its bundle was built from (release tag or
`<date>-<sha>`); on mismatch the run lists the skewed members and exits
non-zero. Re-run that member's `make bundle` (tagging a new release first if
the changes should ship), or force with `COPY_BUNDLES_ALLOW_SKEW=1`.

## What `bundle`/`load` do not move

- **Model weights** — `scripts/pack-model.sh` / `scripts/unpack-model.sh`;
  see [model-transfer.md](../model-transfer.md).
- **Volume ownership** — self-healing at every `up`; see
  [hardening-migration.md](../hardening-migration.md).
- **Secrets** — provisioned fresh on the airgap side (above).

## See also

- [releasing.md](releasing.md) — how the artifact you are transferring got its tag.
- [bring-up.md](bring-up.md) — `make setup` / `make up` on the offline host.
