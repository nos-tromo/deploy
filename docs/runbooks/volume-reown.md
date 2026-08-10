# Runbook: one-time volume re-ownership for the hardening releases

The ADR 0001 hardening releases run every first-party container as the
non-root `app` user (uid `10001`) instead of root. External volumes on
existing hosts still hold root-owned data from the previous releases, so
each volume needs a **one-time** `chown` before the first hardened start
— otherwise the services crashloop on `permission denied`.

**Rehearse on a scratch host first** (ideally together with
[userns-remap.md](userns-remap.md) in the same window — one re-own pass
instead of two). The `huggingface-cache` chown is the long pole: time it.

## Rules

1. **Stop the stack first** (`make down` in `deploy/`).
2. **Snapshot before chown** — mandatory for the volumes marked ⚠ (they
   hold data that is expensive or impossible to regenerate on an
   airgapped host):

   ```bash
   docker run --rm -v <vol>:/v:ro -v /srv/backups:/b \
     docker.io/library/alpine tar czf /b/<vol>.tgz -C /v .
   ```

   (Use any locally-present small image on airgap hosts; the tar is the
   point, not the image.)
3. **Chown from a container, never from the host** — container-side UIDs
   stay correct with or without `userns-remap`:

   ```bash
   docker run --rm -v <vol>:/v docker.io/library/alpine chown -R <uid>:<gid> /v
   ```

4. **If enabling `userns-remap` in the same window: enable it first**,
   restart the daemon, then chown — the remapped daemon has its own
   storage root, and a chown performed under the old config does not
   carry over.
5. **Fresh-volume trap (verified):** a brand-new empty volume gets
   populated from the image's directory content — root-owned — on its
   *first mount*. If you pre-chown an empty volume and then start the
   container, the copy re-clobbers the ownership. For fresh volumes:
   start once, let it fail, chown, start again (open-webui is the known
   case; the app repos' volumes have no image content and don't hit
   this).
6. Delete the snapshots only after the post-upgrade soak.

## Per-repo volume table

| Repo | Volume | chown to | Notes |
|---|---|---|---|
| chorus | `chorus-state` | `10001:10001` | ⚠ audit DB + raw store |
| docint | `docling-cache` | `10001:10001` | cache — regenerable |
| docint | `sessions-storage` | `10001:10001` | ⚠ chat sessions |
| docint | `source-preview-cache` | `10001:10001` | ⚠ uploaded sources |
| docint | `pipeline-storage` | `10001:10001` | new in docint ≥1.4.0 — `make volumes` creates it; chown right after creation |
| docint + vllm-service | `huggingface-cache` | `10001:10001` | ⚠ transferred model weights on airgap hosts — **snapshot first, time the chown**; shared volume, one chown serves both repos |
| Nextext | `nltk-cache`, `spacy-cache` | `10001:10001` | pre-seeded language resources |
| open-webui-service | `open-webui-data` | `10001:10001` | ⚠ user chats; fresh-volume trap applies (rule 5) |
| obs-plane | `alloy-data` | `473:473` | handled by `make volumes` (re-run once); positions only |
| obs-plane | `prometheus-data`, `loki-data`, `grafana-data` | — | no change — services keep their image-default users |
| data-plane | all six | — | no change this wave — `user:` deliberately deferred (see data-plane README) |
| edge-plane | `edge-state`, `edge-ca` | — | no change — caddy stays root-in-container (remap covers it), authelia manages its own files |

Model weights arriving later via `unpack-model.sh` are safe: the script
chowns what it extracts to the cache directory's current owner, so once
the volume is `10001:10001` new models inherit it.

## Post-chown verification

Bring the stack up (`make up` in `deploy/`) and confirm, per tier:

- every health gate passes (the bring-up enforces this);
- one write per re-owned volume actually lands: a chorus tool call
  (audit log row), a docint upload, a Nextext job, an open-webui chat,
  a model load in vllm-service (`/health` on each backend);
- `docker compose exec <svc> id` reports uid `10001` (or the documented
  image-internal user) — not `0`.
