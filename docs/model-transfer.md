# Model transfer scripts

Three scripts for getting a Hugging Face model onto a machine and moving it to
another when it has to live in a Docker volume (e.g. the `huggingface-cache`
volume used by the vLLM service): `scripts/fetch-model.sh` downloads a model
by its hub id into the online machine's own HF cache, `scripts/pack-model.sh`
creates a compressed tarball from any model directory, and
`scripts/unpack-model.sh` restores that tarball into the volume on the target
machine. They are the model-weights half of the airgap flow — `make bundle` /
`make load` move the *images*, these move the *weights*.

```
fetch-model.sh   ->  pack-model.sh  --copy-->  unpack-model.sh
(online host)        (tarball)                 (airgap host volume)
```

`pack-model.sh` and `unpack-model.sh` need root to access `/var/lib/docker`,
and both require `zstd` (`apt install zstd`). `fetch-model.sh` needs neither —
it writes to the invoking user's cache and only requires the Hugging Face CLI
(`pip install -U huggingface_hub`).

All model names below are placeholders — substitute the `models--<org>--<name>`
directory that actually sits in your cache.

## Fetching (online machine)

```sh
./fetch-model.sh <model-id> [cache-dir]

# example
./fetch-model.sh example-org/example-model-fp8
```

- `<model-id>` is the Hugging Face repo id, `<org>/<name>` — the same string
  you would put in `TEXT_MODEL` (or another `*_MODEL` var) in vllm-service's
  `.env`.
- `[cache-dir]` overrides where the download lands. Omitted, the CLI's own
  resolution applies: `HF_HUB_CACHE`, else `$HF_HOME/hub`, else
  `~/.cache/huggingface/hub`.

The script downloads into **the machine's own HF hub cache**, not into the
`huggingface-cache` Docker volume — so it needs no root and no `sudo`. On
success it prints the `models--<org>--<name>` directory, which is exactly the
argument `pack-model.sh` takes next.

Notes:

- Gated or private repos: accept the conditions on the Hub, then export
  `HF_TOKEN` before running. No `sudo` is involved, so the variable is
  inherited normally.
- The federation ships `HF_HUB_OFFLINE=1` by default (see
  [tech-stack.md](tech-stack.md)); the script forces it off for its own
  download so a shell that has it exported does not silently defeat the fetch.
- Downloads resume — re-running after an interruption picks up where it
  stopped, and an already-complete model is a no-op.
- This replaces the older "flip `HF_HUB_OFFLINE=0` in `.env`, start the
  service once so it pulls, flip it back" procedure. Nothing needs to run.

## Packing (source machine)

```sh
sudo ./pack-model.sh <model-dir> [output-dir]

# example
sudo ./pack-model.sh \
  /var/lib/docker/volumes/huggingface-cache/_data/models--example-org--example-model-fp8 \
  /data/transfer
```

- `<model-dir>` is the model's directory inside the HF cache (the
  `models--<org>--<name>` directory). Find candidates with
  `sudo ls /var/lib/docker/volumes/huggingface-cache/_data`.
- `[output-dir]` defaults to the current directory.

Output: `<name>.tar.zst` (e.g. `example-model-fp8.tar.zst`) plus a matching
`.sha256` checksum file. Copy **both** files to the target machine, e.g.:

```sh
scp example-model-fp8.tar.zst* user@target:
```

Notes:

- Compression is zstd level 3 on all cores — fast, since model weights are
  nearly incompressible anyway.
- The HF cache's internal symlinks (`snapshots/` → `blobs/`) are preserved,
  which keeps the archive at single-copy size and the cache layout intact.
- The script checks that the output directory has roughly the model's size in
  free space before starting.

## Unpacking (target machine)

```sh
sudo ./unpack-model.sh <tarball.tar.zst> [dest-dir]

# example — extracts into the huggingface-cache volume
sudo ./unpack-model.sh example-model-fp8.tar.zst
```

- `[dest-dir]` defaults to
  `/var/lib/docker/volumes/huggingface-cache/_data`. The volume must already
  exist (`docker volume create huggingface-cache` if not).

What it does, in order:

1. Verifies the tarball against the `.sha256` file if it sits next to it
   (warns and continues if the checksum file is missing).
2. Warns if the model directory already exists in the volume, then extracts
   over it. Overwriting replaces files with the same paths but does **not**
   delete files that only exist in the old copy — remove the directory first
   if you need a guaranteed-clean replacement.
3. Checks free space (tarball size + 10% headroom).
4. Extracts, then `chown -R`s the model directory to match the owner of the
   destination directory, so files get the UID the container runs as.
   Override with `sudo CHOWN=1000:1000 ./unpack-model.sh ...` if needed.

> **Hardened releases (ADR 0001):** the vllm-service containers run as uid
> `10001`, so the volume itself must already be owned `10001:10001` — see
> [runbooks/volume-reown.md](runbooks/volume-reown.md). The chown in step 4
> then inherits the right owner automatically. Under `userns-remap` the
> on-host path differs; use `docker volume inspect huggingface-cache` to find
> the real mountpoint.

## Manual extraction

The tarball is a plain zstd-compressed tar, so without the script:

```sh
zstd -dc example-model-fp8.tar.zst | tar -xf - -C <dest-dir>
```
