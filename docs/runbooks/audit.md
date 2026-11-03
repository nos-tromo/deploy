# Runbook: the audit trail

How to set up, watch and use the federation's audit trail. The design and its
limits are in [ADR 0003](../decisions/0003-federation-audit-trail.md). The
store's own reference is obs-plane's `docs/audit.md`.

## Setup (once per host)

1. **Volumes and secrets.** obs-plane's `make volumes` creates the audit
   volumes along with the others (`make setup` here runs it). Generate
   `AUDIT_EDGE_SECRET` with edge-plane's `make secret` and put the same value
   into edge-plane's and obs-plane's `.env`; until both carry it, `/audit/`
   answers 403. If the applicable rules prescribe a period other than one
   year, set `AUDIT_RETENTION` in obs-plane's `.env` (Loki duration syntax,
   e.g. `8760h`). Choose the period before the trail holds data that must be
   kept.
2. **Who may read it.** In edge-plane's `authelia/users.yml`, add the group
   `audit` to the people who review data access, and only to them. `admins`
   does not grant it. Reading the trail is itself recorded.
3. **Signing key for anchors and exports.** Create an SSH signing key that
   never sits on the host for longer than signing takes:
   `ssh-keygen -t ed25519 -f audit-signing -C audit`. Keep the private key
   offline. Put its public key, with the signer identity you will use, into
   an `allowed_signers` file that travels with the anchors. Set
   `AUDIT_SIGNING_KEY` to the private key's path only while signing.
4. **Prerequisites from phase 1.** Every app's edge secret is set and logins
   use a second factor (edge-plane `docs/identity-contract.md`,
   `docs/user-accounts.md`). Without that, the "who" in the trail could be
   forged.

`make up` brings the audit store up with the obs tier; nothing else is
sequenced differently.

## Daily: anchor the previous day

The chains make altered or removed lines visible. The anchor is what makes
lines added after the fact visible, so take it every day:

```bash
cd obs-plane
make audit-anchor DAY=$(date -u -d yesterday +%F)   # writes audit-exports/anchor-<day>.json
# with AUDIT_SIGNING_KEY set, the target signs it (anchor-<day>.json.sig)
```

Move the anchor and its signature to the offline medium the same day. To check
an anchor later:

```bash
ssh-keygen -Y verify -f allowed_signers -I <signer identity> -n audit \
  -s anchor-<day>.json.sig < anchor-<day>.json
```

## Watch: alerts

Prometheus (obs-plane) evaluates the verifier's metrics:

| Alert | Meaning | What to do |
|---|---|---|
| `AuditChainProblem` | A chain defect was found: altered or removed lines, a gap, a repeat, a malformed line, an event claiming another service's name or continuing a chain from another container, lines of one chain from several containers when its start was not seen (`ambiguous_source`), or a chain that stopped without a clean shutdown. obs-plane's `docs/audit.md` lists every kind. | Run `make audit-verify FROM= TO=` over the window to see which service, instance and sequence numbers are affected (never event content). A single `unterminated` after a crash or a forced restart is expected. Anything else needs an explanation. Compare against the signed anchors to tell a transport loss from tampering. |
| `AuditVerifierStale` | The verifier has not completed a run for several intervals. | Check the `audit-verifier` and `loki-audit` containers (`make ps`, `make logs S=audit-verifier`). |
| `AuditVerifierDown` | Prometheus cannot reach the verifier. | Same. |

## On request: export for a review

```bash
cd obs-plane
make audit-export FROM=2026-01-01T00:00:00Z TO=2026-02-01T00:00:00Z            # everything in range
make audit-export FROM=… TO=… SERVICE=docint                                     # one service
make audit-export FROM=… TO=… REF=<reference>                                    # lines mentioning a reference
```

The export is JSONL with a `.sha256`, signed like the anchors when
`AUDIT_SIGNING_KEY` is set. Hand over the file, its checksum and signature, and
the matching anchors. An export contains personal data: give it only to whoever
is entitled to it, and record that hand-over through your own procedure.

## Retention

The audit store deletes lines older than `AUDIT_RETENTION` by itself (Loki's
compactor). There is no API to delete individual lines. Changing the period
takes effect from the next compaction. Lowering it deletes older lines for
good, so decide first.
