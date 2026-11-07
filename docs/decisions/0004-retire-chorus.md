# 0004 — Retire chorus and Neo4j from the federation

Status: accepted (2026-11-07)
Date: 2026-11-07

## Context

chorus, the GraphRAG app for social-network analysis, was never used in
production. docint covers the same use.

chorus was also Neo4j's only consumer: data-plane ran Neo4j for chorus and
Qdrant for docint. Neo4j Community, the edition the federation runs, has
neither multiple databases nor role-based access control (chorus ADR 0017):
all data sits in one database, and every client connects with the same
account, which may read and write everything. The database cannot keep one
user's or one case's data from another's. The audit trail (ADR 0003) names
the user behind each access, but with Neo4j nothing below the app limits what
that user reaches. Separating users would take one Neo4j instance per
project, as ADR 0017 proposed.

## Decision

**chorus and Neo4j leave the federation.**

- deploy runs `docint Nextext translator` as its `APP_DIRS`, gates the state
  tier on `qdrant:6333` only, and no longer ships chorus to the airgap host.
- data-plane runs Qdrant alone, without Neo4j's service, volumes, settings and
  targets.
- obs-plane no longer scrapes chorus, probes Neo4j or lists `chorus/backend`
  among the audit emitters.
- edge-plane no longer routes `/chorus/`, sends no `CHORUS_EDGE_SECRET` and
  shows no chorus tile in the portal.
- What chorus and Neo4j hold on a host is deleted, not migrated, once per host
  ([retire-chorus.md](../runbooks/retire-chorus.md)).
- The repository nos-tromo/chorus stays on GitHub as it is, with its pull
  requests; the open ones are closed unmerged.

## Alternatives considered

- **Keep chorus, unused.** Neo4j would keep running on every host, and every
  federation-wide change would keep touching an app no one uses.
- **One Neo4j instance per project** (chorus ADR 0017). It would separate
  projects, at one database instance each, for an app that is not in use.
- **Neo4j Enterprise**, which has multiple databases and role-based access
  control: a commercial licence for the same unused app.
- **Keep Neo4j in data-plane without chorus.** No other app uses it. A graph
  database can return with an app that needs one, as a decision of its own.

## Consequences

- data-plane runs only Qdrant. Its shell (`make qdrant-shell`), backups,
  restores and loopback admin port (`127.0.0.1:6333`) are Qdrant's, and its
  volumes are `qdrant-storage` and `qdrant-snapshots`. No backup stops a
  database any more, so backups need no maintenance window.
- The federation has three Python apps (docint, Nextext and translator) and
  eleven repositories.
- ADR 0003 continues to apply without chorus: chorus' backend is no longer an
  audit source, and docint and Nextext are the apps that require a stated
  purpose for queries and disclosures. chorus' events already in the trail
  stay there until the trail's retention period deletes them.
- ADRs 0001–0003 keep their text. Where they name chorus or Neo4j, they
  describe the federation at the time they were written.
- chorus' and Neo4j's data on the hosts is deleted, with no way back.
