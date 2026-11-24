# @infra/ui v0.28.0 — the context meter and the tile's warning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `AttachmentTile`'s `warning`, the new `ContextMeter` and the `--color-warning` token they both draw in, released as `@infra/ui` v0.28.0, so Ghostwriter 0.7.0's limit notices (spec § 5.7, § 5.8) consume them from a pinned SHA.

**Architecture:** Two additions, each with its own tests and demo entry, then one task for the docs and the release. The tile's warning comes first and brings the warning colour token, AA-checked in both themes. On a row it is a third line under the detail; on an image's square it is the drawing alone in a corner. Either way it joins the face button's `aria-describedby`. `ContextMeter` is a new primitive: a `role="meter"` span around a drawn SVG ring. The arc's length is the rounded percent. The arc turns to the warning colour at `warnAt`. The tag is minted on merge.

**Tech Stack:** React 19, Tailwind v4 (semantic tokens from `src/theme.css`), class-variance-authority, tsup, vitest 5 + happy-dom + @testing-library/react + @testing-library/user-event 14, ESLint, Prettier, pnpm 9.

**Spec:** `docs/superpowers/specs/2026-11-24-ghostwriter-limit-notices-design.md` in deploy (§ 5.7, § 5.8, § 6 step 2, § 7 Gates). All paths below are relative to the infra-ui repo root unless they name deploy.

## Global Constraints

- **Branch** `feat/limit-notices`, from infra-ui's `origin/main` (v0.27.0, `0512d87`) after `git fetch origin`, in a separate worktree (superpowers:using-git-worktrees).
  - The main infra-ui checkout is on the stale `feat/chat-menu-polish` (v0.25.0, no `AttachmentTile`): do not switch it, commit to it or rebase it.
  - The one open PR, #89 (`docs/nextext-ochre`), touches only `docs/app-icons.md` and takes no version, so nothing waits on it.
  - If another PR merges first and takes `0.28.0`, rebase and take the next minor.
- **Version** `package.json` `"version": "0.28.0"`, set in Task 3 only.
  - The repo keeps no CHANGELOG: the release notes are the PR body.
  - The shared `release-tag` workflow mints `v0.28.0` on merge. Never tag by hand.
- **Committed `dist/`:** every commit that touches `src/` runs `pnpm build` and stages `dist/` with it (`index.js`, `index.d.ts`, `tokens.css`). CI (`node-lib-ci`, `check-dist: true`) runs lint, typecheck, test and build, and fails on any `dist/` drift.
- **Consumers pin a commit SHA:** `https://codeload.github.com/nos-tromo/infra-ui/tar.gz/<40-hex>`, never a tag (`docs/pinning.md`).
  - The SHA is the *peeled* tag, `refs/tags/v0.28.0^{}`, which is the merge commit on `main`.
  - Ghostwriter's current pin, `0512d87…`, is `v0.27.0^{}`. Its own plan moves the pin, not this one.
- **Semantic tokens only**, no raw palette colours. Light is the default; dark comes from the OS preference or the toggle through the same tokens. Class strings stay literal, so a consumer's `@source '…/node_modules/@infra/ui/dist'` sees them.
- **One new token, `--color-warning`:** a caution, where something needs attention and nothing has failed. `danger` stays for failure.
  - Light: `hsl(32 95% 32%)`, about 5.5:1 on `background` and 5.0:1 on `muted`.
  - Dark: `hsl(38 92% 55%)`, about 9.3:1 and 7.5:1. It goes in both dark blocks, value-identical.
  - If `src/contrast.test.ts` fails, change L minimally. Never rename the token.
- **edge-plane changes nothing here.** Its `landing/tokens.css` is a vendored copy, and `scripts/check-tokens-vendor.sh` checks only its header. The token reaches the portal at its next re-vendor, and nothing there breaks.
- **Icons are drawn, never typed.**
  - The tile reuses `WarningIcon`.
  - The meter's ring is drawn SVG local to `ContextMeter.tsx`. It has one call site, so `docs/icon-policy.md` keeps it out of `src/icons/`. It is `aria-hidden` and `currentColor`, and holds no text.
  - No `⚠` character anywhere, the demo included. The meter draws no digits and no `%`: its number is in `label` only.
- **No motion.** The meter has no transition and no animation, so `prefers-reduced-motion` has nothing to turn off.
- **No runtime fetches. Peer deps stay peer** (`react`, `react-dom`). No new dependency.
- **Data confidentiality:** invented names and copy only:
  - files: `field-survey.pdf`, `site-ledger.xlsx`, `harbour-sketch.png`;
  - copy: "Very long: read only in parts", "about 62 % of the context used".

  No absolute local paths in any committed file or commit message.
- **Gate before every commit:** `pnpm lint && pnpm typecheck && pnpm test && pnpm build`, then `pnpm exec prettier --check` on the files you touched. Run `prettier --write` only on those files, never `pnpm format` over the repo.

## Review Focus

1. **A warning while the tile is not ready.** The warning arrives after `ready`, but a tile can move on: taking a file back shows `processing`, a later failure `failed`, retention `expired`. The warning shows only while `status` is `ready`, and never beside a spinner, the failure's marker or the badge. Task 1 tests: `failed and expired keep their own marker…` and `a %s tile shows its spinner, not the warning yet`.
2. **A third line in a 56px row.** The `sm` row is `h-14`. Its border leaves 54px, and the name (20px), detail (16px) and warning (16px) take 52px. The row keeps `h-14 w-56`, the name and the detail stay, and the warning truncates with its whole text in `title`. Task 1 test: `a ready document shows its warning under the detail…`; Task 1 Step 10 checks it visually in both themes.
3. **Two warned tiles in one row.** Each warning id comes from `useId`, so each face button is described by its own warning, not its neighbour's. Task 1 test: `each face button is described by its own warning`.
4. **The percent and the level disagreeing at the threshold.** `0.799` reads as 80 but is below `0.8`. The level is decided on the value, so the ring agrees with an app that compares the same value with the same threshold, as Ghostwriter's "nearly full" note does. Task 2 test: `the level is decided on the value, not on the rounded percent`.
5. **An estimate over the budget, or no number.** The app divides an estimate that can exceed its budget, and a missing number becomes `NaN`.
   - `1.4` draws a full ring, says 100 and warns.
   - `NaN` says 0 and stays normal.
   - Anything that rounds to 0 draws no arc, not a round-capped dot.

   Task 2 tests: `a value of %s is clamped…` and `the arc draws the same whole percent…`.

---

### Task 1: `AttachmentTile`'s warning, and the warning token

**Files:**
- Modify: `src/theme.css:11`, `:66`, `:79`. Add `--color-warning` after `--color-danger` in `@theme` and in both dark blocks. In `@theme` it gets one comment line: "a caution: something needs attention and nothing has failed (`ContextMeter`'s warn level, `AttachmentTile`'s warning); `danger` stays for failure".
- Modify: `src/theme.test.ts:8-20` (the family token list), `src/contrast.test.ts:39-48` (`PAIRS`)
- Modify: `src/primitives/AttachmentTile.tsx`:
  - the props, `:12-35`;
  - the ids and the description, `:122-143`;
  - the square, `:165-211`;
  - the row's name and detail column, `:230-239`;
  - the docstring, `:76-108`.
- Modify: `src/primitives/AttachmentTile.test.tsx` (append)
- Modify: `demo/main.tsx`: `DemoTile` (`:134-142`), `INITIAL_TILES`, and the `sm` row's map (`:338-350`)
- Rebuild: `dist/`

**Interfaces:**
- Consumes: `WarningIcon` (`src/icons`), `cn`, and the tile's existing `useId` base `id`, `describedBy`, `row` and `face`.
- Produces:
  - `AttachmentTileProps.warning?: string`. Its doc comment: "A short caution about a ready file ("Very long: read only in parts"). Shown only while `status` is `ready`."
  - The token `--color-warning` and its utility `text-warning`. Task 2 draws the meter's warn level in it.

**What it renders** (the tests pin the parts marked as tested):
- **When:** `showWarning = Boolean(warning) && status === 'ready'`. Otherwise nothing is drawn and nothing joins the description (tested).
- **On a row** (every kind but `image`; an image is a row only when expired, so never with a warning):
  - A third line in the name/detail column, after the detail (tested). It is a `span` with `flex min-w-0 items-center gap-1 text-xs text-warning`.
  - It holds `WarningIcon className="h-3 w-3 shrink-0"`, then a `span` with `id={`${id}-warning`}`, `title={warning}` and `className="truncate"` holding the text (tested).
  - The row keeps its size (`h-14 w-56` / `h-16 w-64`). The face's border stays `border-border`, since a caution is not a failure (tested).
- **On a square** (a ready image, `sm` or `md`):
  - There is no room for a line, so the drawing sits alone in the bottom-left corner. It is a `span` with `absolute bottom-1 left-1 inline-flex rounded-full bg-background p-0.5 text-warning` and `title={warning}`, holding `WarningIcon className="h-3.5 w-3.5"` (tested).
  - Inside that span, an `sr-only` span holds the text and carries the warning id (tested).
  - The picture stays, unveiled (tested). The bottom-left corner keeps the drawing clear of the remove cross at the top right.
- **The description:** the button face's `aria-describedby` lists the ids of the detail, the marker and the warning that exist, in that order (tested). A `div` face keeps no description, as today. There the warning is text, read in order after the name and the detail.

- [ ] **Step 1: Make the worktree and check the baseline**

```bash
git -C <infra-ui checkout> fetch origin
git -C <infra-ui checkout> worktree add <worktree path> -b feat/limit-notices origin/main
cd <worktree path>
pnpm install --frozen-lockfile
pnpm test && pnpm build && git status --short
```
Expected: the tests pass, and `git status --short` prints nothing, because the build reproduces the committed `dist/`. If `dist/` differs after a clean build, stop and report it.

- [ ] **Step 2: Write the failing token tests**
  - `src/theme.test.ts`: add `'--color-warning'` after `'--color-danger'` in the family list.
  - `src/contrast.test.ts`: add `['--color-warning', '--color-background']` and `['--color-warning', '--color-muted']` to `PAIRS`.

- [ ] **Step 3: Run them and see them fail**

Run: `pnpm test src/theme.test.ts src/contrast.test.ts`
Expected: FAIL. `declares the fixed family tokens` fails, and the four new pairs (two per palette) fail on `expect(t[fg]).toBeDefined()`.

- [ ] **Step 4: Add the token** with the values in Global Constraints. Then run `pnpm build`, which regenerates `dist/tokens.css`.

- [ ] **Step 5: Run the token tests and see them pass**

Run: `pnpm test src/theme.test.ts src/contrast.test.ts src/tokens.test.ts`
Expected: PASS. `tokens.test.ts` passes only because Step 4 rebuilt `dist/tokens.css`.

- [ ] **Step 6: Write the failing tile tests.** Append them to `src/primitives/AttachmentTile.test.tsx`, reusing its `PICTURE`, `face` and `drawing`:

```tsx
const LONG = 'Very long: read only in parts'

test('a ready document shows its warning under the detail, drawn and said, without hiding either', () => {
  const { container } = render(
    <AttachmentTile name="field-survey.pdf" kind="document" status="ready" detail="18.6 MB" warning={LONG} />,
  )
  const text = screen.getByText(LONG)
  expect(text).toHaveClass('truncate')
  expect(text).toHaveAttribute('title', LONG)
  expect(text.parentElement).toHaveClass('text-warning')
  expect(text.parentElement!.querySelector('svg')!.innerHTML).toBe(drawing(<WarningIcon />))
  const detail = screen.getByText('18.6 MB')
  expect(detail.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy() // under it
  expect(screen.getByText('field-survey.pdf')).toHaveAttribute('title', 'field-survey.pdf')
  expect(face(container)).toHaveClass('h-14', 'w-56', 'border-border') // the row's size; a caution, not a failure
  expect(face(container).querySelector('svg')!.innerHTML).toBe(drawing(<FileIcon />)) // the kind still leads
})

test('each face button is described by its own warning', () => {
  render(
    <>
      <AttachmentTile name="field-survey.pdf" kind="document" status="ready" detail="18.6 MB" warning={LONG} onOpen={() => {}} />
      <AttachmentTile name="site-ledger.xlsx" kind="document" status="ready" detail="4.1 MB" warning="Sheets read in parts" onOpen={() => {}} />
    </>,
  )
  const first = screen.getByRole('button', { name: 'field-survey.pdf' })
  const text = screen.getByText(LONG)
  expect(text.id).not.toBe('')
  expect(first.getAttribute('aria-describedby')!.split(' ')).toContain(text.id)
  expect(first).toHaveAccessibleDescription(`18.6 MB ${LONG}`)
  expect(screen.getByRole('button', { name: 'site-ledger.xlsx' })).toHaveAccessibleDescription(
    '4.1 MB Sheets read in parts',
  )
})

test('failed and expired keep their own marker and drop the warning', () => {
  const props = { name: 'field-survey.pdf', kind: 'document', detail: '18.6 MB', warning: LONG, onOpen: () => {} } as const
  const { rerender } = render(<AttachmentTile {...props} status="failed" statusLabel="Could not be read" />)
  expect(screen.getByRole('img', { name: 'Could not be read' })).toBeInTheDocument()
  expect(screen.queryByText(LONG)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'field-survey.pdf' })).toHaveAccessibleDescription(
    '18.6 MB Could not be read',
  )
  rerender(<AttachmentTile {...props} status="expired" statusLabel="Expired" />)
  expect(screen.getByText('Expired')).toBeInTheDocument()
  expect(screen.queryByText(LONG)).not.toBeInTheDocument()
})

test.each(['uploading', 'processing'] as const)('a %s tile shows its spinner, not the warning yet', (status) => {
  render(<AttachmentTile name="field-survey.pdf" kind="document" status={status} detail="18.6 MB" warning={LONG} />)
  expect(screen.getByRole('status')).toBeInTheDocument()
  expect(screen.queryByText(LONG)).not.toBeInTheDocument()
})

test('without a warning, or with an empty one, the tile is as before', () => {
  const props = { name: 'field-survey.pdf', kind: 'document', status: 'ready', detail: '18.6 MB', onOpen: () => {} } as const
  const { container, rerender } = render(<AttachmentTile {...props} />)
  expect(face(container).querySelectorAll('svg')).toHaveLength(1) // the kind alone
  expect(screen.getByRole('button')).toHaveAccessibleDescription('18.6 MB')
  rerender(<AttachmentTile {...props} warning="" />)
  expect(face(container).querySelectorAll('svg')).toHaveLength(1)
  expect(screen.getByRole('button')).toHaveAccessibleDescription('18.6 MB')
})

test('on an image’s square the warning is the drawing in a corner, its text the tooltip and the description', () => {
  const { container } = render(
    <AttachmentTile name="harbour-sketch.png" kind="image" status="ready" thumbnailUrl={PICTURE} warning={LONG} onOpen={() => {}} />,
  )
  expect(face(container)).toHaveClass('size-14') // still the square
  expect(container.querySelector('img')).toHaveAttribute('src', PICTURE)
  expect(container.querySelector('[class*="bg-background/50"]')).toBeNull() // not veiled
  const corner = face(container).querySelector(`[title="${LONG}"]`)!
  expect(corner).toHaveClass('text-warning')
  expect(corner.querySelector('svg')!.innerHTML).toBe(drawing(<WarningIcon />))
  expect(screen.getByText(LONG)).toHaveClass('sr-only')
  expect(screen.getByRole('button', { name: 'harbour-sketch.png' })).toHaveAccessibleDescription(LONG)
})
```

- [ ] **Step 7: Run them and see them fail**

Run: `pnpm test src/primitives/AttachmentTile.test.tsx`
Expected: FAIL in the three tests that look for the warning (`a ready document…`, `each face button…`, `on an image’s square…`), which cannot find its text. The tests that check its absence pass already, and so do the tests from before.

- [ ] **Step 8: Add `warning` to `AttachmentTile`**, following "What it renders" above.
  - Build the id as `${id}-warning`, beside `markerId` and `detailId`.
  - Append `showWarning && warningId` to the `describedBy` list.
  - In the docstring, add one paragraph: what the warning is for; why it shows only while the tile is ready (a failed or expired tile has its own marker, and a busy one's warning waits); and why a square draws it alone in a corner.

- [ ] **Step 9: Run the tests and see them pass**

Run: `pnpm test src/primitives/AttachmentTile.test.tsx`
Expected: PASS, every test in the file.

- [ ] **Step 10: Add warned tiles to the demo, and look at them**
  - Add `warning?: string` to `DemoTile`, and pass `warning={tile.warning}` in the `sm` row's map.
  - Append two entries to `INITIAL_TILES`:
    - `{ id: 't11', name: 'field-survey.pdf', kind: 'document', status: 'ready', detail: '18.6 MB', warning: 'Very long: read only in parts' }`
    - `{ id: 't12', name: 'site-map.png', kind: 'image', status: 'ready', thumbnailUrl: PICTURE_FLOORPLAN, warning: 'Sent at a reduced size' }`
  - Run `pnpm demo` and check, in light and in dark:
    - the warned row's three lines fit inside `h-14`, and nothing is clipped;
    - the warning reads amber, apart from the failed tile's red;
    - hovering the cut warning shows its whole text;
    - the square's corner drawing sits clear of the cross.

- [ ] **Step 11: Run the gate and commit**

```bash
pnpm lint && pnpm typecheck && pnpm test && pnpm build
git add src/theme.css src/theme.test.ts src/contrast.test.ts src/primitives/AttachmentTile.tsx src/primitives/AttachmentTile.test.tsx demo/main.tsx dist/
git commit -m "feat(ui): AttachmentTile's warning, and the warning colour token"
```

---

### Task 2: `ContextMeter`

**Files:**
- Create: `src/primitives/ContextMeter.tsx`, `src/primitives/ContextMeter.test.tsx`
- Modify: `src/index.ts:105` (export after `ImageLightbox`)
- Modify: `demo/main.tsx` (the Chat section and its `ChatComposer`'s `end`)
- Rebuild: `dist/`

**Interfaces:**
- Consumes: `text-warning` (Task 1), `cn`.
- Produces, exported from `src/index.ts`:

```ts
export interface ContextMeterProps {
  /** How much is used, as a fraction from 0 to 1. Clamped to [0, 1]; NaN is 0. */
  value: number
  /** The accessible name and the tooltip, in the app's language: "about 62 % of the context used". */
  label: string
  /** The fraction from which the meter warns. Defaults to 0.8. */
  warnAt?: number
  /** Merged onto the root, last. */
  className?: string
}
export function ContextMeter(props: ContextMeterProps): ReactElement
```

**What it renders:**
- **The numbers:**
  - `fraction = Number.isNaN(value) ? 0 : Math.min(1, Math.max(0, value))`;
  - `percent = Math.round(fraction * 100)`;
  - `level = fraction >= warnAt ? 'warn' : 'normal'`. The level is decided on the fraction, never on the rounded percent.
- **The root:** a `span` with:
  - `role="meter"`, `aria-valuemin={0}`, `aria-valuemax={100}`, `aria-valuenow={percent}`;
  - `aria-label={label}` and `title={label}`;
  - `data-level={level}`;
  - `className={cn('inline-flex size-4.5 shrink-0', className)}`.

  It holds no text.
- **The SVG:** `viewBox="0 0 24 24"`, `fill="none"`, `aria-hidden`, `className="h-full w-full -rotate-90"`, so the arc starts at the top and runs clockwise.
- **The track:** `<circle data-part="track" cx={12} cy={12} r={9} stroke="currentColor" strokeWidth={3} className="text-border" />`.
- **The arc:** drawn only when `percent > 0`. It is the same circle with `data-part="arc"`, `strokeLinecap="round"`, `pathLength={100}` and `strokeDasharray={`${percent} 100`}`. Its class is `text-warning` at `warn` and `text-muted-foreground` otherwise.
- No transition and no animation. Set `ContextMeter.displayName`.

- [ ] **Step 1: Write the failing tests** in `src/primitives/ContextMeter.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { ContextMeter } from './ContextMeter'

const LABEL = 'about 62 % of the context used'
const arc = (c: HTMLElement) => c.querySelector('[data-part="arc"]')

test('a meter from 0 to 100, named and titled by its label, at the whole percent of its value', () => {
  render(<ContextMeter value={0.623} label={LABEL} />)
  const meter = screen.getByRole('meter', { name: LABEL })
  expect(meter).toHaveAttribute('aria-valuemin', '0')
  expect(meter).toHaveAttribute('aria-valuemax', '100')
  expect(meter).toHaveAttribute('aria-valuenow', '62')
  expect(meter).toHaveAttribute('title', LABEL)
  expect(meter.textContent).toBe('') // drawn, never typed
  expect(meter.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
})

test.each([
  [1.4, '100', 'warn'],
  [Infinity, '100', 'warn'],
  [-1, '0', 'normal'],
  [-Infinity, '0', 'normal'],
  [Number.NaN, '0', 'normal'],
])('a value of %s is clamped: aria-valuenow %s, level %s', (value, now, level) => {
  render(<ContextMeter value={value} label={LABEL} />)
  expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', now)
  expect(screen.getByRole('meter')).toHaveAttribute('data-level', level)
})

test('the arc draws the same whole percent the meter says, and nothing at zero', () => {
  const { container, rerender } = render(<ContextMeter value={0.623} label={LABEL} />)
  expect(arc(container)).toHaveAttribute('stroke-dasharray', '62 100')
  expect(arc(container)).toHaveAttribute('pathLength', '100')
  rerender(<ContextMeter value={1.4} label={LABEL} />)
  expect(arc(container)).toHaveAttribute('stroke-dasharray', '100 100')
  for (const value of [0, 0.004, Number.NaN]) {
    rerender(<ContextMeter value={value} label={LABEL} />)
    expect(arc(container)).toBeNull() // no round-capped dot
    expect(container.querySelector('[data-part="track"]')).toHaveClass('text-border')
  }
})

test('the level turns to warn at 0.8 by default, and the arc to the warning colour', () => {
  const { container, rerender } = render(<ContextMeter value={0.79} label={LABEL} />)
  expect(screen.getByRole('meter')).toHaveAttribute('data-level', 'normal')
  expect(arc(container)).toHaveClass('text-muted-foreground')
  expect(arc(container)).not.toHaveClass('text-warning')
  for (const value of [0.8, 0.95, 1]) {
    rerender(<ContextMeter value={value} label={LABEL} />)
    expect(screen.getByRole('meter')).toHaveAttribute('data-level', 'warn')
    expect(arc(container)).toHaveClass('text-warning')
  }
})

test('the level is decided on the value, not on the rounded percent', () => {
  render(<ContextMeter value={0.799} label="about 80 % of the context used" />)
  expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '80')
  expect(screen.getByRole('meter')).toHaveAttribute('data-level', 'normal') // as an app comparing 0.799 with 0.8 decides
})

test('warnAt moves the threshold', () => {
  const { rerender } = render(<ContextMeter value={0.5} warnAt={0.5} label={LABEL} />)
  expect(screen.getByRole('meter')).toHaveAttribute('data-level', 'warn')
  rerender(<ContextMeter value={0.49} warnAt={0.5} label={LABEL} />)
  expect(screen.getByRole('meter')).toHaveAttribute('data-level', 'normal')
  rerender(<ContextMeter value={0.85} warnAt={0.9} label={LABEL} />)
  expect(screen.getByRole('meter')).toHaveAttribute('data-level', 'normal')
})

test('className is merged onto the meter, last', () => {
  render(<ContextMeter value={0.623} label={LABEL} className="size-5 ml-1" />)
  const meter = screen.getByRole('meter')
  expect(meter).toHaveClass('inline-flex', 'size-5', 'ml-1')
  expect(meter).not.toHaveClass('size-4.5')
})
```

- [ ] **Step 2: Run them and see them fail**

Run: `pnpm test src/primitives/ContextMeter.test.tsx`
Expected: FAIL, because `./ContextMeter` cannot be resolved.

- [ ] **Step 3: Implement `ContextMeter(props: ContextMeterProps): ReactElement`** in `src/primitives/ContextMeter.tsx`, following "What it renders" above, with `warnAt = 0.8` as the default. The docstring says:
  - what it shows: how much of a budget is used, such as a chat's context;
  - why the number lives only in `label`: the app words it and rounds it in its own language, with its "about";
  - why the normal arc is muted and not the accent: it is a reading, not a control;
  - why the level is decided on the value and not on the rounded percent: so an app that shows a note from the same value and threshold agrees with the ring;
  - why there is no motion.

  Export the component and `ContextMeterProps` from `src/index.ts`, after `ImageLightbox`.

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm test src/primitives/ContextMeter.test.tsx`
Expected: PASS.

- [ ] **Step 5: Add meters to the demo, and look at them**
  - In `demo/main.tsx`'s Chat section, add a row of four meters at `0.12`, `0.62`, `0.8` and `1.4`. Label each "about N % of the context used", with N the rounded, capped percent.
  - Put one more in the `ChatComposer`'s `end`, before `SendButton`. It takes `value={chatDraft.length / 200}`, so typing fills it.
  - Run `pnpm demo` and check, in light and in dark:
    - the normal arc is muted and the warn arc is amber, on the page and on the pill's `bg-muted`;
    - the track is faint but visible;
    - the ring is centred beside Send;
    - hovering a ring shows its label.

- [ ] **Step 6: Run the gate and commit**

```bash
pnpm lint && pnpm typecheck && pnpm test && pnpm build
git add src/primitives/ContextMeter.tsx src/primitives/ContextMeter.test.tsx src/index.ts demo/main.tsx dist/
git commit -m "feat(ui): ContextMeter, how much of a budget is used, as a ring"
```

---

### Task 3: the docs, and release v0.28.0

**Files:**
- Modify: `docs/components.md`:
  - the opening list, `:3-9`;
  - a new `## Context meter` section between § Chat composer and user bubble and `:540`;
  - § AttachmentTile: the props table (`:611-621`), the status table's `ready` row (`:629`), the description paragraph (`:636-639`) and the remove-button sentence (`:642-644`).
- Modify: `docs/icon-policy.md` (a `v0.28.0` paragraph after the `v0.26.0` one)
- Modify: `README.md` § Primitives, `CLAUDE.md` (Primitive set), `docs/README.md` (the components.md row)
- Modify: `package.json` (`"version": "0.27.0"` → `"0.28.0"`)
- Rebuild: `dist/` (expected unchanged)

**Interfaces:**
- Consumes: Tasks 1 and 2 on the branch.
- Produces: the tag `v0.28.0` on merge, and the commit SHA from `git ls-remote https://github.com/nos-tromo/infra-ui 'refs/tags/v0.28.0^{}'`, which Ghostwriter's plan pins.

- [ ] **Step 1: Write the docs**
  - **`docs/components.md` § Context meter** has:
    - A usage example: the meter in `ChatComposer`'s `end`, before Send. It is rendered only when the app has a number, and its `warnAt` is the app's own constant, the same one that decides the app's note.
    - A props table: `value`, `label`, `warnAt` (default `0.8`) and `className`.
    - What it draws:
      - a ring, 18px;
      - the arc muted, then `text-warning` from `warnAt`, with `data-level` `normal` or `warn`;
      - no arc at 0;
      - no motion.
    - Its accessibility: `role="meter"`, `aria-valuemin`/`aria-valuemax` 0 and 100, `aria-valuenow` the whole percent, and `label` as both the name and the tooltip.
    - Three rules: the level is decided on the value and not the rounded percent; out-of-range values and `NaN` are clamped; an app with no number renders no meter rather than a 0.
  - **The opening list:** add "the context meter" after "the chat composer and user bubble".
  - **§ AttachmentTile:**
    - A `warning` row in the props table: `string`, none, "A caution about a ready file: a line under the detail on a row, the drawing alone in a square's corner with the text as its tooltip. Shown only while `status` is `ready`."
    - The `ready` row of the status table: Marker reads "none; a `warning`, if given".
    - A paragraph after the one on failures. It says that a warning (v0.28.0) is a caution, not a failure: `WarningIcon` in `text-warning` and the face's border unchanged. It is shown only while the tile is ready, its text truncates, and its whole text is in its tooltip.
    - `:636-639`: the button is "described by the detail, the marker and the warning".
    - `:642-644`: replace "and is always visible" with how v0.27.0 shows it: "shown on hover or keyboard focus where a pointer can hover, always on a touch screen". The sentence contradicts the paragraph at `:561-566` and `src/primitives/AttachmentTile.tsx:94-100`.
  - **`docs/icon-policy.md`, a `v0.28.0` paragraph:**
    - There is no new icon.
    - The tile's warning draws `WarningIcon` in the new warning colour. That is a caution, which is what its docstring has always said.
    - A failed tile keeps the same drawing in `danger` with a red border, and the two never show on one tile.
    - The meter's ring is a drawing of a value. It lives in `ContextMeter` and is not in the set.
  - **One-line entries:**
    - `README.md` § Primitives: `ContextMeter` (how much of a budget is used, as a small ring), after `ImageLightbox`.
    - `CLAUDE.md` Primitive set: `ContextMeter`, after `ImageLightbox`.
    - `docs/README.md`: add "the context meter" to the components.md row.

- [ ] **Step 2: Check the format and commit the docs**

```bash
pnpm exec prettier --check docs/components.md docs/icon-policy.md docs/README.md README.md CLAUDE.md
git add docs/components.md docs/icon-policy.md docs/README.md README.md CLAUDE.md
git commit -m "docs: ContextMeter and AttachmentTile's warning"
```
Expected: Prettier reports every file formatted.

- [ ] **Step 3: Confirm the version is still free**

Run: `git fetch origin && git show origin/main:package.json | grep '"version"'`
Expected: `"version": "0.27.0"`. If it is higher, rebase onto `origin/main` and use the next minor in Steps 4 and 7.

- [ ] **Step 4: Bump `package.json` to `0.28.0`**

- [ ] **Step 5: Run the full gate and check the exports**

```bash
pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build && git status --short
grep -cE 'ContextMeter|ContextMeterProps|warning\?: string' dist/index.d.ts
grep -c -- '--color-warning' dist/tokens.css
```
Expected:
- everything passes;
- `git status --short` shows only `package.json`;
- the first grep counts at least 3;
- the second prints `3`, one per palette block.

- [ ] **Step 6: Commit and push**

```bash
git add package.json
git commit -m "chore: release v0.28.0"
git push -u origin feat/limit-notices
```

- [ ] **Step 7: Open the PR**
  - Title: `feat(ui): ContextMeter, AttachmentTile's warning and the warning token (v0.28.0)`.
  - The body names spec § 5.7 and § 5.8 and the consumer, Ghostwriter 0.7.0. It also notes that edge-plane's portal gets `--color-warning` at its next re-vendor.
  - Confirm that CI ran on the latest push: `gh pr checks --watch`.
  - Merging is the owner's call.

- [ ] **Step 8: After the merge, resolve the pin for Ghostwriter**

Run: `git ls-remote https://github.com/nos-tromo/infra-ui 'refs/tags/v0.28.0^{}'`
Expected: one line, `<40-hex>	refs/tags/v0.28.0^{}`.

That commit SHA goes into Ghostwriter's `frontend/package.json` codeload URL. Do not use the tag object's SHA from `refs/tags/v0.28.0`. Ghostwriter's plan owns that bump.
