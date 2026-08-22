# Runbook: releasing a federation member

The full release ritual for any image-bundling member repo (`chorus`,
`docint`, `Nextext`, `translator`, `vllm-service`, `data-plane`, `obs-plane`,
`open-webui-service`, `edge-plane`). The one-paragraph summary lives in the
[README](../../README.md#releasing); this is the step-by-step.

`deploy` itself is versioned the same way: a one-line `VERSION` file read by the
same `release-tag` workflow, minting the tag on merge to `main`.

## The ritual

1. In a `release/vX.Y.Z` branch, bump the member's declared version — `pyproject.toml`
   `[project].version` (the Python apps + `vllm-service`) or the one-line `VERSION`
   file (`data-plane`, `open-webui-service`) — and, for the Python repos, run
   `uv lock` to sync the lockfile. PR → CI → merge to `main`.
2. On merge, the shared `release-tag` workflow (`nos-tromo/.github@v3`) reads the
   declared version and mints the annotated `vX.Y.Z` tag **automatically** — no
   manual `git tag`. It is idempotent (an unchanged version is a no-op) and refuses
   a version that decreased. Bumping the version in the release PR is the whole
   release action.
3. Bundle the tag: `make bundle` — each member builds from the latest annotated
   tag reachable from HEAD (it checks the tag out and restores your branch after),
   stamping its image `vX.Y.Z`. It refuses on a dirty tree or with no reachable
   tag, so a release artifact is always tag-versioned, never a dev `date+sha`. For
   pre-tag soak iteration, per-member `make bundle-dev` bundles the current working
   tree instead (never promoted). Re-running `make bundle` is idempotent per
   member: one already bundled at its current tag (per its `.<slug>-version`
   file, the same record `copy-bundles.sh` checks for skew) is skipped, so a
   partially failed fan-out can be re-run without rebuilding the members that
   succeeded. `BUNDLE_FORCE=1` rebuilds everything.
4. Bring the tagged artifact up on a staging environment isolated from other
   workloads and exercise it end to end.
5. On success, promote the **same** artifact onward (see
   [airgap-transfer.md](airgap-transfer.md)). On failure, fix forward on `main`,
   tag the next patch (`vX.Y.Z+1`), and repeat — the failed candidate is never
   promoted.

## See also

- [airgap-transfer.md](airgap-transfer.md) — moving the artifact to an offline host.
- [bring-up.md](bring-up.md) — what `make up` does with it once it is there.
