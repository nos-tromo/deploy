# Hardening migration: huggingface-cache ownership (ADR 0001)

One-time, per host. As of vllm-service 0.4.0 every first-party container runs
as the non-root `app` user (uid `10001`) — but on hosts deployed before the
hardening wave, the `huggingface-cache` volume was populated by root-running
containers, so its contents are root-owned and unreadable/unwritable to the
hardened stack. The volume must be handed to uid `10001` **before the first
hardened start**.

The migration is ownership metadata only — no data moves, so it completes in
seconds-to-minutes even for tens of GB of weights. The same uid is shared with
docint's mounts of this volume, so one chown serves both stacks.

## Symptom of skipping it

The failure is **not** a clear ownership error. vLLM starts, then spams

```
Ignoring corrupted tree cache file ... Permission denied
```

and the pooling backends (embed, embed-sparse, rerank) later die during
EngineCore startup. If you see that log line on a hardened host, this
migration was skipped or left incomplete.

## Procedure

1. Stop **every** stack that mounts the volume — vllm-service *and* docint:

   ```sh
   make stop          # in each repo's checkout
   ```

2. From the vllm-service checkout, run the migration target:

   ```sh
   make migrate-cache
   ```

   It snapshots the volume to `./huggingface-cache-pre-hardening.tar` (plain
   tar — weights don't compress; it refuses to run without enough free space
   in the current directory), then `chown -R 10001:10001`s the volume
   mountpoint, then verifies nothing is left wrongly owned. On airgap hosts
   the snapshot is mandatory — the weights are expensive to re-obtain. On
   hosts where the cache is trivially re-downloadable, `SNAPSHOT=no` skips it.

3. Start the hardened stack and watch the first backend's logs for the
   *absence* of the "Ignoring corrupted tree cache" line.

4. Once healthy, delete the snapshot tar.

## Manual fallback (no vllm-service checkout / no make)

The volume is a host directory; the target's steps by hand:

```sh
HUB=$(docker volume inspect huggingface-cache -f '{{.Mountpoint}}')
sudo tar -C "$HUB" -cf ./huggingface-cache-pre-hardening.tar .   # snapshot
sudo chown -R 10001:10001 "$HUB"
sudo find "$HUB" \( ! -uid 10001 -o ! -gid 10001 \) | head -5    # must print nothing
```

If the mountpoint isn't directly reachable (rootless Docker), reuse any
shipped first-party image with the baked-in user overridden — no extra image
import needed:

```sh
docker run --rm --user 0 --entrypoint sh \
  -v huggingface-cache:/hub \
  vllm-service-vad:<version> -c 'chown -R 10001:10001 /hub'
```

## After migration

Nothing recurs: `unpack-model.sh` chowns extracted models to the owner of the
destination directory, so once the volume root is `10001`, every future model
transfer lands correctly owned.
