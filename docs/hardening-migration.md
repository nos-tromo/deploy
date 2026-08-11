# Hardening volume ownership: self-healing at `make up` (ADR 0001)

As of the hardening wave every first-party container runs as the non-root
`app` user (uid `10001`) — but external volumes keep whatever ownership they
were created with: root, on a fresh `docker volume create`, or on hosts
populated before the wave. Image-build chowns cannot help, since the volume
mount shadows the image path at runtime.

**There is no manual migration step.** Every member whose uid-10001
containers mount volumes read-write ships a `volume-permissions` one-shot in
its compose file: a root container reusing the member's own image (no extra
airgap import) that runs

```sh
find <mountpoints> -xdev ! -user 10001 -exec chown -h 10001:10001 {} +
```

before the mounting services start (`depends_on:
service_completed_successfully`). The find form touches only wrong-owner
entries, so a boot over an already-owned multi-GB volume writes nothing, and
a volume deleted and re-created later (root-owned again) heals on the next
`up` instead of regressing. The canonical implementation is
`docint/docker/compose.yaml`; copy it verbatim (mounts adjusted) for any
future member.

## Coverage

| Member | Volumes self-healed |
| --- | --- |
| vllm-service | huggingface-cache (main stack and every `*-only` shape) |
| docint | docling-cache, huggingface-cache (shared), sessions-storage, source-preview-cache, pipeline-storage |
| chorus | chorus-state |
| Nextext | nltk-cache, spacy-cache, tmp-jobs |
| open-webui-service | open-webui-data |

Out of scope — **never chown these to 10001**: data-plane, obs-plane, and
edge-plane volumes. Their third-party images manage their own uids, and
their `user:`/`read_only:` hardening is deferred. docint's `ollama-cache`
is excluded for the same reason.

## Symptom of a missing one-shot

The failure is **not** a clear ownership error. vLLM starts, then spams

```
Ignoring corrupted tree cache file ... Permission denied
```

and the pooling backends (embed, embed-sparse, rerank) later die during
EngineCore startup. If you see that log line on a hardened host, the shape
you started is missing its `volume-permissions` service (or the one-shot
failed — check `docker compose ps -a` for its exit code).

## Optional snapshot before the first hardened boot

The chown is ownership metadata only — no data moves. Cautious airgap
operators can still snapshot the model weights first (they are expensive to
re-obtain; plain tar — weights don't compress):

```sh
HUB=$(docker volume inspect huggingface-cache -f '{{.Mountpoint}}')
sudo tar -C "$HUB" -cf ./huggingface-cache-pre-hardening.tar .
```

Delete the tar once the stack is healthy. `make migrate-cache` (the earlier,
manual form of this migration) has been removed; the one-shot supersedes it.

## After the first boot

Nothing recurs and nothing needs verifying by hand — but to check:

```sh
HUB=$(docker volume inspect huggingface-cache -f '{{.Mountpoint}}')
sudo find "$HUB" \( ! -uid 10001 -o ! -gid 10001 \) | head -5   # must print nothing
```

`unpack-model.sh` chowns extracted models to the owner of the destination
directory, so model transfers land correctly owned either way.
