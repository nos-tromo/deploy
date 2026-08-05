# Model transfer scripts

Two scripts for moving a Hugging Face model between machines when the model
lives in a Docker volume (e.g. the `huggingface-cache` volume used by the vLLM
service): `scripts/pack-model.sh` creates a compressed tarball on the source
machine, `scripts/unpack-model.sh` restores it into the volume on the target
machine. They are the model-weights half of the airgap flow — `make bundle` /
`make load` move the *images*, these move the *weights*.

Both scripts need root to access `/var/lib/docker`, and both require `zstd`
(`apt install zstd`).

All model names below are placeholders — substitute the `models--<org>--<name>`
directory that actually sits in your cache.

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

## Manual extraction

The tarball is a plain zstd-compressed tar, so without the script:

```sh
zstd -dc example-model-fp8.tar.zst | tar -xf - -C <dest-dir>
```
