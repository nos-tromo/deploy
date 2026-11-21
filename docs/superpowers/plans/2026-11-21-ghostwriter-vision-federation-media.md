# Ghostwriter vision, stage 3: the federation's media seam — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** deploy's env-sync shares Ghostwriter's Nextext service token as a second `entry=` of Nextext's `NEXTEXT_SERVICE_TOKENS`, and Nextext and vllm-service document the media seam and the vision limits. Three PRs; no code changes in Nextext or vllm-service.

**Architecture:** An `entry=` row of env-sync.conf stops owning the whole list: it owns its own `name=token` item, reads only that item, and fills by appending it (or replacing its placeholder where it stands), never touching the other entries. That lets two groups (`nextext-token`, `nextext-ghostwriter-token`) share one key. The conventions check names every partner of a key held by two groups. Nextext and vllm-service change only `.env.example` comments and `docs/configuration.md`.

**Tech Stack:** deploy: Python stdlib (3.9 floor, CI matrix 3.9 and 3.12), `unittest`. Nextext, vllm-service: Markdown and `.env.example`.

**Spec:** `deploy/docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§5.3, §7.5, §8 stage 3) and `deploy/docs/decisions/0009-ghostwriter-sees-images-and-media.md` (Consequences). Sibling plans: `2026-11-21-ghostwriter-vision-workbench-v0.9.md`, `2026-11-21-ghostwriter-vision-app-v0.6.md`.

Every path is relative to the repo its task names: `deploy/` (Tasks 1–4), `Nextext/` (Task 5), `vllm-service/` (Task 6).

## Decisions

The lead's calls (the owner may correct them):
- **E1, deploy.** Group `nextext-ghostwriter-token` = `ghostwriter NEXTEXT_API_KEY gen` + `Nextext NEXTEXT_SERVICE_TOKENS gen entry=ghostwriter`. Deliberate rule change, documented in the runbook: an `entry=` row owns only its entry in a `name=token` list. Uniqueness becomes `(member, key, entry)`. `_Pass.read` reads the row's own entry from a list that holds others. `fill` appends a missing entry to a list that already has other entries and never rewrites or drops them. A list whose own entry conflicts is still a conflict. `conventions.expected_shared` handles a key held by two groups (the tag names both partners). Python floor 3.9.
- **E2, Nextext (no code).** `.env.example` and `docs/configuration.md` gain the `ghostwriter=<token>` entry (tag `[shared: docint NEXTEXT_API_KEY, ghostwriter NEXTEXT_API_KEY]`) and a note on `NEXTEXT_JOB_CONCURRENCY=1`.
- **E3, vllm-service (docs only).** Four points: `CHAT_LIMIT_MM_PER_PROMPT={"image":8,"video":0}`, at least the larger of docint's `VISUAL_ANSWER_MAX_IMAGES` and `GHOSTWRITER_MAX_IMAGES`; `CHAT_MAX_MODEL_LEN` ≥ `GHOSTWRITER_CONTEXT_TOKENS`; the router's request body vs about 12 MiB of base64 per request.

Calls made in this plan:
- A list item that is neither `name=token` nor a placeholder (for example `NEXTEXT_SERVICE_TOKENS=SENTINEL-x`) makes the list unreadable for every entry row, as today: a conflict when the single key is set, `unset_everywhere` otherwise, never filled. The rule change covers only a missing or placeholder entry.
- A list whose every item is a placeholder counts as unset and is rewritten whole, as today. Dropping a placeholder item is not dropping an entry.
- In the table, one `(member, key)` may appear on several rows only when every one of them carries `entry=` with a different name.
- PR order: deploy first, then Nextext; vllm-service may go at any time. Until Nextext's PR merges, deploy's local sibling check reports T5 on Nextext's old tag. Until Ghostwriter 0.6.0, whose example has no `NEXTEXT_API_KEY`, the new group is skipped with a `table` warning.
- Versions: deploy `VERSION` 0.14.0, Nextext 1.20.1, vllm-service 0.12.2 (docs releases, as vllm-service v0.11.1 was).

## Global Constraints

- One branch and PR per repo, `feat/ghostwriter-media-seam` from a fresh `main`; GitHub Flow. Each PR bumps its version, and the tag is minted on merge, never by hand. Workflow `uses:` stay SHA-pinned (no workflow change is planned).
- deploy: stdlib only; Python 3.9 floor (no `match`, no runtime `X | Y`; `from __future__ import annotations` as the modules have it). `python3 -B -m unittest discover -s tests -p 'test_*.py'` must pass on 3.9 and 3.12 before push.
- env-sync never prints a value: events name keys and members only. Every test token is `SENTINEL-…` or a repeated letter, as today.
- No real data and no absolute local path in anything git sees, commit messages included.
- `.env.example` format: comment lines under 80 characters, tags in the order `[required] [secret] [shared: …] [dev]`. `python3 -B ../deploy/scripts/check_env_examples.py .` must be clean in Nextext and vllm-service.
- Nextext and vllm-service: no runtime change. `make verify` green before push.
- Commit messages conventional (`feat(envsync): …`, `docs(config): …`).

## Review Focus

1. **A Nextext `.env` that already holds docint's token, on a host that now has Ghostwriter.** Expect `docint=<d>,ghostwriter=<g>`: docint's item byte for byte, Ghostwriter's appended, and a second `make env` that changes nothing. Test in Task 2.
2. **A hand-edited list with a space or an odd item** (`docint=<d>, other=x`). Expect `unsafe` on Nextext with nothing written, never a half-rewritten list, and docint's group unaffected. Test in Task 2.
3. **Ghostwriter's entry holds a different token than Ghostwriter's `NEXTEXT_API_KEY`.** Expect the conflict `NEXTEXT_SERVICE_TOKENS: Nextext differs from ghostwriter`, nothing changed, and docint's group silent. Test in Task 2.
4. **A fresh host, `make env ENV_GENERATE=1`, nothing set anywhere.** Expect two different tokens, each single key its own, and the list `docint=<a>,ghostwriter=<b>`. Test in Task 2.
5. **An older Ghostwriter checkout whose example has no `NEXTEXT_API_KEY`.** Expect the new group skipped with the `table` warning `env-sync.conf: NEXTEXT_API_KEY is not in ghostwriter's example`, while docint's group fills the list as before. Test in Task 2.

---

### Task 1 (deploy): The table allows one entry per group on one key

**Files:**
- Modify: `scripts/envsync/shared.py:88-120` (`load_table`)
- Test: `tests/test_envsync_shared.py` (`TableTests`)

**Interfaces:**
- Produces: `load_table(text) -> List[Group]` keys its duplicate check by `(member, key)` as today. It lets a later row through when it and every earlier row of that `(member, key)` carry `entry=`, their names differ, and they stand in different groups. Anything else is the existing `TableError`: `"{member} {key} is already in group {g} (line {n})"`.

- [ ] **Step 0: Branch.** `git fetch origin && git switch main && git pull --ff-only && git switch -c feat/ghostwriter-media-seam`.
- [ ] **Step 1: Write the failing tests** (`TableTests`):
  - `test_table_errors` gains three cases, each failing at the line given:
    - `"a list key twice, one row without entry"`: `"g  alpha  A_KEY  gen\ng  beta  L_KEY  gen  entry=alpha\nh  gamma  C_KEY  gen\nh  beta  L_KEY  gen\n"`, line 4;
    - `"the same entry twice"`: as above, with the last row `entry=alpha`, line 4;
    - `"two entries of one list in one group"`: `"g  alpha  A_KEY  gen\ng  beta  L_KEY  gen  entry=alpha\ng  beta  L_KEY  gen  entry=gamma\n"`, line 3;
    - `"a key in two groups"`: unchanged, line 3.
  - `test_a_list_key_holds_one_entry_per_group`: the text above with the last row `entry=gamma` loads as groups `g` and `h`. The `beta L_KEY` rows carry entries `alpha` and `gamma`.
- [ ] **Step 2: Run** `python3 -B -m unittest discover -s tests -p 'test_envsync_shared.py' -k TableTests`. Expected: the new case FAILS (`TableError`).
- [ ] **Step 3: Implement.** Keep `seen` as a `Dict[Tuple[str, str], List[Row]]`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(envsync): one key may hold one list entry per group`

### Task 2 (deploy): An entry row owns its own item

**Files:**
- Modify: `scripts/envsync/shared.py:202-215` (`_list_unset`, `_entry_token`; new `_unreadable`, `_merged`), `:304-321` (`_Pass.read`), `:323-353` (`reconcile`, `nothing_set` pass the group's placeholders on), `:365-384` (`fill`)
- Test: `tests/test_envsync_shared.py` (`EntryRowTests`, new `TwoEntryTests`)

**Interfaces:**
- Produces:
  - `_unreadable(value: str, placeholders: Set[str]) -> bool`: some item has no `=` and is not a placeholder.
  - `_merged(current: Optional[str], name: str, token: str, placeholders: Set[str]) -> str`: the list with this row's item set (below).
  - `_Pass.fill(side, value, origin, source, placeholders)`: an entry row's target is `_merged(...)` of the plan's current value, read at fill time, so the second group of a run sees the first one's fill. The rest is unchanged: `kept_unset`, the `SAFE_VALUE` check of the whole target (`unsafe`), a comma in `value` (`unsafe`), `fill_line`, `_forget`, the note.

`read` for an entry row, and the merge (the order decides the outcomes the tests pin):

```python
# _Pass.read, for row.entry is not None
if not row.source and _list_unset(entry.value, placeholders):
    return _Side(row, shared, False, None)                  # every item a placeholder: unset, rewritten whole
if _unreadable(entry.value, placeholders):
    return _Side(row, shared, True, None)                   # as today: never filled; a set single key conflicts
token = _entry_token(entry.value, row.entry, placeholders)  # this row's real token, or None
return _Side(row, shared, token is not None, token)         # the other entries are not this row's

def _merged(current, name, token, placeholders):
    mine = f"{name}={token}"
    if current is None or _list_unset(current, placeholders):
        return mine
    items = current.split(",")                              # kept verbatim, spaces and order included
    for i, item in enumerate(items):
        if item.partition("=")[0].strip() == name:          # only a placeholder item can be ours here
            items[i] = mine
            return ",".join(items)
    return ",".join(items + [mine])
```

- [ ] **Step 1: Write the failing tests.**
  - `EntryRowTests`. `test_a_set_list_is_never_appended_to` is split in two:
    - `test_a_missing_entry_is_appended_and_the_others_kept`, with the docint key `SENTINEL-t`:
      - `other=SENTINEL-o` becomes `NEXTEXT_SERVICE_TOKENS=other=SENTINEL-o,docint=SENTINEL-t`;
      - `docint=<token>,other=SENTINEL-o` becomes `…=docint=SENTINEL-t,other=SENTINEL-o`.
      - Each case: no cross-member event; Nextext's `filled` items are `["NEXTEXT_SERVICE_TOKENS (from docint)"]`.
    - `test_a_list_with_an_item_that_is_not_name_token_is_left_alone`: `SENTINEL-x` gives the conflict `NEXTEXT_SERVICE_TOKENS: Nextext differs from docint`, and the snapshot is unchanged.
  - `test_a_list_without_the_entry_and_nothing_else_set`:
    - The case "no entry" (`other=SENTINEL-o`) still answers `unset_everywhere("NEXTEXT_API_KEY (docint, Nextext)")` with `generate=False`.
    - With `generate=True` and `Tokens("a" * 64)`, it now answers `[]`: docint's `NEXTEXT_API_KEY` is `"a" * 64` (origin `generated`), and the list is `other=SENTINEL-o,docint=aaaa…` (64 `a`).
    - The case "not name=token" is unchanged for both values of `generate`.
  - `TwoEntryTests(SharedCase)`. The table is `TOKENS + GW_TOKENS`, where `GW_TOKENS = "nextext-ghostwriter-token  ghostwriter  NEXTEXT_API_KEY  gen\nnextext-ghostwriter-token  Nextext  NEXTEXT_SERVICE_TOKENS  gen  entry=ghostwriter\n"`. Nextext's example is `# NEXTEXT_SERVICE_TOKENS=docint=<token>,ghostwriter=<token>\n`. Ghostwriter's example is `# NEXTEXT_API_KEY=\nOTHER=1\n`.
    - `test_each_caller_has_its_own_entry`: docint `SENTINEL-d`, ghostwriter `SENTINEL-g`, Nextext without the key gives the list `docint=SENTINEL-d,ghostwriter=SENTINEL-g`. Rendering every file and applying again over the rendered texts gives no event and no `filled` note (Review Focus 1).
    - `test_appending_keeps_the_other_entry_byte_for_byte`: Nextext `docint=SENTINEL-d`, ghostwriter `SENTINEL-g`, docint unset. The list becomes `docint=SENTINEL-d,ghostwriter=SENTINEL-g`; docint's `NEXTEXT_API_KEY` is filled `SENTINEL-d (from Nextext)`.
    - `test_an_unsafe_list_is_not_rewritten` (Review Focus 2): Nextext `docint=SENTINEL-d, other=x`, both single keys set to their tokens. `items(nextext, "unsafe") == ["NEXTEXT_SERVICE_TOKENS"]`, there is no cross-member event, the entry keeps origin `kept`, and docint's single key is unchanged.
    - `test_a_conflicting_entry_names_its_own_partner` (Review Focus 3): Nextext `docint=SENTINEL-d,ghostwriter=SENTINEL-x`, docint `SENTINEL-d`, ghostwriter `SENTINEL-g`. The events are exactly `[conflict("NEXTEXT_SERVICE_TOKENS: Nextext differs from ghostwriter")]`, and the snapshot is unchanged.
    - `test_both_entries_are_generated_in_one_run` (Review Focus 4): nothing set, `generate=True`, `Tokens("a" * 64, "b" * 64)`. docint gets `a…`, ghostwriter `b…`, and the list is `docint=a…,ghostwriter=b…`; the stub is empty afterwards.
    - `test_a_placeholder_list_is_rewritten_whole`: Ghostwriter not on the host, Nextext `docint=<token>,ghostwriter=<token>`, docint `SENTINEL-d`. The list becomes `docint=SENTINEL-d`.
    - `test_the_ghostwriter_group_waits_for_its_example` (Review Focus 5): Ghostwriter's example is `OTHER=1\n`. The events are exactly `[Event("table", ["env-sync.conf: NEXTEXT_API_KEY is not in ghostwriter's example"], "warn")]`, and docint's group fills the list `docint=SENTINEL-d` as before.
- [ ] **Step 2: Run** `python3 -B -m unittest discover -s tests -p 'test_envsync_shared.py' -k EntryRowTests -k TwoEntryTests`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** as in Interfaces. The comment above `reconcile`'s `value is None` branch now reads: "A list that cannot be read is set for every entry row but holds no token: nothing disagrees, and it is never filled."
- [ ] **Step 4: Run** `python3 -B -m unittest discover -s tests -p 'test_*.py'`, under `python3.9` when one is installed. Expected: PASS (`RoundTripTests` included).
- [ ] **Step 5: Commit** `feat(envsync): an entry row owns its own item; a missing one is appended`

### Task 3 (deploy): The conventions name every partner of a shared list

**Files:**
- Modify: `scripts/envsync/conventions.py:469-485` (`expected_shared`)
- Test: `tests/test_envsync_conventions.py`

**Interfaces:**
- Produces: `expected_shared(key) -> Optional[str]` reads every group that holds this member's key, in table order. Within each group the rule is today's: a non-source row names the source, and the source names every other row. The partners are joined by `", "`, each named once, as `member` (same key) or `member KEY`.

- [ ] **Step 1: Write the failing test** `test_a_list_key_in_two_groups_names_both_partners` (the table `TOKENS + GW_TOKENS`, as Task 2):
  - Nextext's example `# [secret] [shared: docint NEXTEXT_API_KEY, ghostwriter NEXTEXT_API_KEY]\n# NEXTEXT_SERVICE_TOKENS=\n` has no `T5`; with `[shared: docint NEXTEXT_API_KEY]` alone it has `("T5", "NEXTEXT_SERVICE_TOKENS")`.
  - Ghostwriter's `# [secret] [shared: Nextext NEXTEXT_SERVICE_TOKENS]\n# NEXTEXT_API_KEY=\n` has no `T5`.
- [ ] **Step 2: Run** `python3 -B -m unittest discover -s tests -p 'test_envsync_conventions.py'`. Expected: the new test FAILS.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(envsync): a key in two groups is tagged with both partners`

### Task 4 (deploy): The group, the runbooks and the release

**Files:**
- Modify:
  - `env-sync.conf`: two rows after `:33`; the header line `:12` becomes "`entry=NAME`  the key holds a `NAME=token,...` list; this row owns NAME's item".
  - `tests/test_envsync_shared.py`: `GROUP_NAMES` `:77`, `test_real_table_loads` `:185`, `test_shared_keys_for` `:214`, `test_the_ghostwriter_rows` `:258`.
  - `docs/runbooks/env-sync.md` (`:65`, `:194-196`), `docs/runbooks/bring-up.md` (`:130-135`; the Ghostwriter section), `docs/tech-stack.md` `:207`, `docs/tech-stack.de.md` (the same row), `VERSION` (`0.14.0`).

**Interfaces:**
- Consumes: Tasks 1–3.
- The rows, verbatim:
  ```
  nextext-ghostwriter-token  ghostwriter  NEXTEXT_API_KEY         gen
  nextext-ghostwriter-token  Nextext      NEXTEXT_SERVICE_TOKENS  gen entry=ghostwriter
  ```

- [ ] **Step 1: Write the failing tests.**
  - `GROUP_NAMES` gains `"nextext-ghostwriter-token"` after `"nextext-token"`.
  - `test_real_table_loads` keys `rows` by `(member, key, entry)`: 31 rows. The entries are `[("Nextext", "docint"), ("Nextext", "ghostwriter")]`.
  - `test_shared_keys_for`: ghostwriter's keys gain `NEXTEXT_API_KEY`.
  - `test_the_ghostwriter_rows` gains `("nextext-ghostwriter-token", [("ghostwriter", "NEXTEXT_API_KEY"), ("Nextext", "NEXTEXT_SERVICE_TOKENS")])`, all `gen` and no `source`.
- [ ] **Step 2: Run** `python3 -B -m unittest discover -s tests -p 'test_envsync_shared.py'`. Expected: FAIL.
- [ ] **Step 3: Add the rows.**
- [ ] **Step 4: Docs.**
  - `env-sync.md:65`, the `unset` (second form) row. Meaning: "… A list (`entry=`) without a real item for an entry counts as unset for that entry." What to do: "Set it, or run `make env ENV_GENERATE=1`, which appends a generated item; a set single key is appended too."
  - `env-sync.md:194-196`, replaced by: "`entry=NAME` rows share one `NAME=token` item of a list (Nextext's `NEXTEXT_SERVICE_TOKENS`, with the `docint` and `ghostwriter` entries, one group each). A row owns its own item only. A missing item is appended, a placeholder item is replaced where it stands, and the other items are never changed, reordered or dropped. A list whose every item is a placeholder is written anew. A list with an item that is not `name=token` is left alone: a conflict when the other side is set. Before deploy 0.14.0 a set list was never appended to."
  - `bring-up.md:130-135`: two service-to-service callers. docint → Nextext and Ghostwriter → Nextext each use a token: the same value in Nextext's `NEXTEXT_SERVICE_TOKENS` (`docint=<token>,ghostwriter=<token>`) and in the caller's `NEXTEXT_API_KEY` (groups `nextext-token` and `nextext-ghostwriter-token`).
  - `bring-up.md`, the Ghostwriter section: "Ghostwriter starts without Nextext. Video and audio are offered once its probe of Nextext's `/health` passes and workbench is 0.9.0 or later. Clips queue behind docint's ingest at `NEXTEXT_JOB_CONCURRENCY=1`."
  - `tech-stack.md:207` (and the German row): the backend also calls Nextext (`nextext-backend` on `inference-net`, service token `service:ghostwriter`) for video and audio.
- [ ] **Step 5: Version.** `VERSION` holds `0.14.0`.
- [ ] **Step 6: Gates.**
  - `python3 -B -m unittest discover -s tests -p 'test_*.py'`. Expected: PASS.
  - `make -n env >/dev/null && make -n env-check >/dev/null`. Expected: silent.
  - `python3 -B scripts/check_env_examples.py ../Nextext ../ghostwriter`. Expected before the Nextext PR merges: exactly `T5 NEXTEXT_SERVICE_TOKENS` on Nextext. Paste that line into the PR.
- [ ] **Step 7: Commit** `feat(envsync): Ghostwriter's Nextext token is shared; release v0.14.0`
- [ ] **Step 8: Push and PR** `git push -u origin feat/ghostwriter-media-seam`.
  - **Operators** line: "0.14.0 changes env-sync's list rule: an `entry=` row now appends its own item to a list that holds others, which it never did before. With Ghostwriter 0.6.0 on the host, `make env ENV_GENERATE=1` adds `ghostwriter=<token>` to Nextext's `NEXTEXT_SERVICE_TOKENS`."
  - Confirm CI ran on the last commit, both Python versions.

### Task 5 (Nextext): The Ghostwriter entry and the queue, documented

**Files:**
- Modify:
  - `.env.example`: the service-token block `:58-66` and the `NEXTEXT_JOB_CONCURRENCY` block `:189-191`.
  - `docs/configuration.md`: § Service tokens and § Rolling out `:405-432`; § Job concurrency `:112-119`.
  - `pyproject.toml` (`version = "1.20.1"`), `uv.lock`.

**Interfaces:**
- Consumes: deploy 0.14.0's conventions (Task 3) for the tag.

- [ ] **Step 0: Branch** as in Task 1, in `Nextext/`.
- [ ] **Step 1: `.env.example`.** The service-token comment names both callers: "docint's entry takes the value of NEXTEXT_API_KEY in docint's .env, Ghostwriter's that of NEXTEXT_API_KEY in Ghostwriter's .env; make env fills both in." The tag line is exactly `# [secret] [shared: docint NEXTEXT_API_KEY, ghostwriter NEXTEXT_API_KEY]`. Above `# NEXTEXT_JOB_CONCURRENCY=1` add: "At 1, Ghostwriter's clips wait behind docint's ingest, and the wait counts against Ghostwriter's NEXTEXT_TIMEOUT."
- [ ] **Step 2: `docs/configuration.md`.**
  - § Service tokens: the example `NEXTEXT_SERVICE_TOKENS=docint=<token>,ghostwriter=<token>`, then a paragraph. Ghostwriter sends its `NEXTEXT_API_KEY` as the bearer token, and its calls resolve to `service:ghostwriter`. It runs one job per attached clip, under the file name `media.<ext>`, with `keyframes` on, `visual_context` and `summarization` off and `keyframes_max` 19. It fetches `docint.jsonl` and `keyframes.zip` and deletes the job at once. At start and daily it deletes any job of its own that no clip of its owns.
  - § Rolling out: adding Ghostwriter's entry changes no existing principal, so it may go in at any time; recreate the backend while no job is in flight (jobs live in memory).
  - § Job concurrency: the queue note from Step 1.
- [ ] **Step 3: Check.**
  - `python3 -B ../deploy/scripts/check_env_examples.py .` against deploy 0.14.0 (its branch, or `main` after merge). Expected: clean.
  - `make verify`. Expected: green.
- [ ] **Step 4: Version.** `uv lock`. Commit `docs(config): Ghostwriter's service token and its clips in the queue; release v1.20.1`.
- [ ] **Step 5: Push and PR.** The PR notes that deploy 0.14.0 must be merged first. Confirm CI ran on the last commit.

### Task 6 (vllm-service): Vision limits, documented

**Files:**
- Modify:
  - `.env.example` `:203-215`: the optional-flags block, with `CHAT_LIMIT_MM_PER_PROMPT`.
  - `docs/configuration.md`: a new `## Vision` after `## Prefix caching` (`:95-111`).
  - `pyproject.toml` (`version = "0.12.2"`), `uv.lock`.

- [ ] **Step 0: Branch** as in Task 1, in `vllm-service/`.
- [ ] **Step 1: Confirm the router sets no body limit.** Run `grep -rn "max_request_size" docker/ src/ docs/`. Expected: only the enterprise note at `docs/configuration.md:238`. If a limit is set, quote it in Step 2 instead of "no limit".
- [ ] **Step 2: `docs/configuration.md` § Vision.** It covers four points:
  - `CHAT_LIMIT_MM_PER_PROMPT={"image":8,"video":0}` for a vision `TEXT_MODEL`. The image limit must be at least the larger of docint's `VISUAL_ANSWER_MAX_IMAGES` (6) and Ghostwriter's `GHOSTWRITER_MAX_IMAGES` (8): a request over it is refused with 400, which docint degrades to a caption-only answer and Ghostwriter retries once without images (its `images_dropped` notice). `video: 0`, because neither app sends video input: Ghostwriter sends a clip's frames as images.
  - `CHAT_MAX_MODEL_LEN` at least `GHOSTWRITER_CONTEXT_TOKENS` (Ghostwriter's default 40000; it refuses less than 22000, and keeps its answer reserve inside that number). The default 8192 is too small for Ghostwriter.
  - Body size: one Ghostwriter request carries at most 8 images under 1 MiB each, about 11 MiB of base64 with the text, so plan for 12 MiB. Neither the router (LiteLLM's `max_request_size_mb` needs an enterprise licence) nor vLLM limits a body. A proxy placed in front must pass at least 16 MiB; its 413 shows in Ghostwriter as `images_dropped`.
  - Images arrive as base64 data URLs only; no backend fetches an image by URL (airgap).
- [ ] **Step 3: `.env.example`.** The example line becomes `#   CHAT_LIMIT_MM_PER_PROMPT={"image":8,"video":0}`, with one comment line: "with vision, at least docint's and Ghostwriter's image caps (docs/configuration.md, Vision)".
- [ ] **Step 4: Check.** `python3 -B ../deploy/scripts/check_env_examples.py .` and `make verify`. Expected: clean, green.
- [ ] **Step 5: Version.** `uv lock`. Commit `docs(config): vision limits for docint and Ghostwriter; release v0.12.2`.
- [ ] **Step 6: Push and PR.** Confirm CI ran on the last commit.

---

## Self-review

- **Spec coverage.** Spec §7.5 asks for three things, covered by these tasks:
  - deploy's group and the table's two entries of one key: Tasks 1–4;
  - Nextext's token entry and concurrency note: Task 5;
  - vllm-service's `CHAT_LIMIT_MM_PER_PROMPT` and `CHAT_MAX_MODEL_LEN`: Task 6.
  - Bring-up "Ghostwriter starts without Nextext": Task 4 Step 4.
  - ADR 0009 already exists (stage 1).
- **Type consistency:** `_merged`, `_unreadable` and `fill(..., placeholders)` live in Task 2 alone; Task 4 consumes only the table.
- **Proportion:** code appears only for the read/merge rule (the append semantics the tests cannot fully determine) and the two table rows. Everything else is test assertions and doc text.
