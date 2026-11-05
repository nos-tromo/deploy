# Retention deadline: deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The federation's design record and runbooks state the retention period, the 30-day backups and the operator's part in it.

**Architecture:** Docs only, in the `deploy` repo; no version bump (docs-only changes never bump).

**Tech Stack:** Markdown; the repo's CI (yamllint, shellcheck, Makefile checks).

**Spec:** `docs/2026-11-05-retention-deadline-design.md` (section "deploy").

## Global Constraints

- Wording: "the configured retention period"; never a reason or legal basis.
- Merged together with, or after, data-plane's release that prunes backups (the runbook describes that release's behaviour).

## Review Focus

- The runbook's off-host instruction contradicts data-plane's `backup/README.md`: both say 30 days after this change.

---

### Task 1: Runbooks and index

**Files:**
- Add: `docs/2026-11-05-retention-deadline-design.md` and the five plans under `docs/superpowers/plans/2026-11-05-retention-deadline-*.md` (already written)
- Modify: `docs/runbooks/backups.md` (backups are restore points kept `BACKUP_RETENTION_DAYS`, default 30; the timer's runs prune; off-host copies pruned at the same age by the operator; after a restore, let docint's sweep run before users work on restored data), `docs/README.md` (link the design), `docs/runbooks/bring-up.md` if it lists `.env` settings (`DOCUMENT_RETENTION_DAYS`, `OPEN_WEBUI_CHAT_RETENTION_DAYS`, `NEXTEXT_CLI_RETENTION_DAYS`, `BACKUP_RETENTION_DAYS`, `RETENTION_NOTICE_DAYS` with their defaults)

- [ ] **Step 1: Edit** the files; check each variable name and default against the merged member repos.
- [ ] **Step 2: Gate:** yamllint, `shellcheck scripts/*.sh`, `make help >/dev/null && make -n ps >/dev/null`. Expected: pass.
- [ ] **Step 3: Commit** `docs: the retention period across the federation, and 30-day backups`.
