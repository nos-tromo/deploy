# @infra/ui v0.26.0 — attachment tiles, image lightbox, kind icons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `AttachmentTile`, `ImageLightbox` and the drawn icons for the four attachment kinds in `@infra/ui`, released as v0.26.0, so Ghostwriter's vision UI (spec § 7.4) consumes them from a pinned SHA.

**Architecture:** Three additions to the existing primitive set, each with its own test file, docs entry and rebuilt `dist/`. First the icons (`FileIcon`, `VideoIcon`, `AudioIcon`; the existing `ImageIcon` is reused). Then `AttachmentTile`, which draws a picture or a kind icon, a status marker and a separate `RemoveButton`. Then `ImageLightbox`, a native modal `<dialog>` portalled to `<body>` on `PurposeBar`'s pattern, with its own Escape, focus trap and focus return because happy-dom's `showModal()` only sets `open`. The release task bumps `package.json`. The tag is minted on merge.

**Tech Stack:** React 19, Tailwind v4 (semantic tokens from `src/theme.css`), class-variance-authority, tsup, vitest 5 + happy-dom + @testing-library/react + @testing-library/user-event 14, ESLint, pnpm 9.

**Spec:** `docs/superpowers/specs/2026-11-20-ghostwriter-vision-design.md` (§ 7.1, § 7.4, § 8 step 1), in deploy. All paths below are relative to the infra-ui repo root unless they name deploy.

## Global Constraints

- **Branch** `feat/attachment-tile-lightbox`, from infra-ui's `origin/main` after `git fetch origin`, in a separate worktree (superpowers:using-git-worktrees). Local `main` is stale (v0.23.0), so do not branch from it. The main infra-ui checkout is on `feat/chat-menu-polish`: do not switch it, commit to it or rebase it. That branch's PR #85 is already merged (v0.25.0, `a49ce7a`) and has no diff against `origin/main`, so this branch waits on nothing. If any other infra-ui PR merges before this one and takes `0.26.0`, rebase and take the next minor.
- **Version** `package.json` `"version": "0.26.0"`, set in Task 4 only. Never tag by hand. The shared `release-tag` workflow mints `v0.26.0` on merge.
- **Committed `dist/`:** every commit that touches `src/` runs `pnpm build` and stages `dist/` with it. CI (`node-lib-ci`, `check-dist: true`) runs lint, typecheck, test, build and fails on any `dist/` drift.
- **Consumers pin a commit SHA**: `https://codeload.github.com/nos-tromo/infra-ui/tar.gz/<40-hex>`, never a tag. The SHA is the *peeled* tag (`refs/tags/v0.26.0^{}`), which is the merge commit on `main`. Ghostwriter's current pin `a49ce7a…` is `v0.25.0^{}`.
- **React 19 + Tailwind v4 semantic tokens only** (`bg-background`, `bg-muted`, `text-muted-foreground`, `border-border`, `text-primary`, `bg-primary/15`, `text-danger`, `border-danger`, `bg-danger/15`). No raw palette colours. Light is the default; dark comes from the OS preference or the toggle through the same tokens. An app re-themes the components with `--app-accent`. Class strings stay literal, so the consumer's `@source '…/node_modules/@infra/ui/dist'` sees them.
- **Icons are drawn, never typed:** inline SVG on the shared `base` (24-unit viewBox, 2px round stroke, `currentColor`, `aria-hidden`), with at least one `<path>` and no text. No `×`, `▶` or `⚠` characters anywhere, including the demo.
- **No runtime fetches.** The components take URLs the app made (object or data URLs) and fetch nothing. The demo's pictures are inline `data:` SVGs.
- **Peer deps stay peer** (`react`, `react-dom`). Add no runtime dependency.
- **Data confidentiality:** synthetic names only (`harbour-sketch.png`, `quarterly-plan.pdf`, `site-walk.mp4`, `standup-notes.m4a`). No absolute local paths in any committed file or commit message.
- **Gate before every commit:** `pnpm lint && pnpm typecheck && pnpm test && pnpm build`, then `pnpm exec prettier --check` on the files you touched. Run `prettier --write` only on those files, never `pnpm format` over the repo.

## Review Focus

1. **A thumbnail that fails to load.** Causes: an object URL revoked by an earlier render, or a picture gone after retention. The tile shows the drawn `ImageIcon`, never the browser's broken-image glyph, and tries again when given a new `thumbnailUrl` (Task 2, test `a picture that fails to load…`).
2. **A long name with no spaces** (120 characters). The tile keeps its fixed width and truncates the name with an ellipsis. The whole name is in `title` and in the accessible name (Task 2, test `a long name keeps the tile's width…`).
3. **Events inside the lightbox reaching the opener's React ancestors.** A portal bubbles through the component tree. Escape closes one layer only. A click inside never reaches an ancestor's `onClick`, so it cannot re-fire a tile's `onOpen` (Task 3, tests `Escape closes it once…` and `a click beside the picture…`).
4. **The opener is gone when the lightbox closes** (the tile re-keyed, the chat switched). Closing does not throw and forces focus nowhere (Task 3, test `closing after the opener is gone…`).
5. **The lightbox unmounted while open** (the chat deleted under it). The dialog leaves the DOM, the page is not left inert, and focus returns to the opener (Task 3, test `unmounting while open…`).

---

### Task 1: the attachment kind icons

**Files:**
- Modify: `src/icons/index.tsx`. Add `FileIcon`, `VideoIcon` and `AudioIcon` after `ImageIcon`. In `ReportIcon`'s docstring, replace "It is the only page in the set, so nothing else in a row can be mistaken for it." with a sentence saying it is the only page *with lines*, and that `FileIcon` is the same sheet left blank.
- Modify: `src/icons/icons.test.tsx`
- Modify: `src/index.ts` (icon export block)
- Modify: `docs/components.md` (§ The icon set inventory), `docs/icon-policy.md` (a `v0.26.0` paragraph after the `v0.25.0` one)
- Rebuild: `dist/`

**Interfaces:**
- Consumes: `base`, `IconProps` and `ReportIcon`'s first two paths, all in `src/icons/index.tsx`.
- Produces: `FileIcon`, `VideoIcon`, `AudioIcon`, each `({ className = 'h-4 w-4', ...props }: IconProps) => ReactElement`, shaped exactly like `ImageIcon`. Exported from `src/index.ts`. Task 2 maps `image → ImageIcon`, `document → FileIcon`, `video → VideoIcon`, `audio → AudioIcon`.

- [ ] **Step 1: Make the worktree and check the baseline**

```bash
git -C <infra-ui checkout> fetch origin
git -C <infra-ui checkout> worktree add <worktree path> -b feat/attachment-tile-lightbox origin/main
cd <worktree path>
pnpm install --frozen-lockfile
pnpm test && pnpm build && git status --short
```
Expected: the tests pass, and `git status --short` prints nothing (the build reproduces the committed `dist/`). If `dist/` differs after a clean build, stop and report it: CI's `check-dist` would then fail on every commit.

- [ ] **Step 2: Write the failing tests.** Add `AudioIcon`, `FileIcon` and `VideoIcon` to the import and to `ICONS`, so the existing geometry, `currentColor` and `aria-hidden` tests cover them. Then append:

```tsx
const ds = (c: HTMLElement) => [...c.querySelectorAll('path')].map((p) => p.getAttribute('d'))

test('the file is the report’s sheet with nothing written on it', () => {
  // A file of any content: the same page and fold, no lines. The lines make it a report.
  expect(ds(render(<FileIcon />).container)).toEqual(ds(render(<ReportIcon />).container).slice(0, 2))
})

test('the film strip keeps its rails, so a clip reads as neither a picture nor play', () => {
  const { container } = render(<VideoIcon />)
  expect(container.querySelectorAll('path').length).toBeGreaterThanOrEqual(3)
  expect(container.querySelector('circle')).toBeNull() // ImageIcon's sun
  expect(ds(container)).not.toContain(ds(render(<PlayIcon />).container)[0])
})

test('the waveform is open bars of different heights, not the microphone', () => {
  const d = ds(render(<AudioIcon />).container).join(' ')
  expect(d).not.toMatch(/[zZaA]/) // no capsule, no arc
  const bars = [...d.matchAll(/v(\d+)/g)].map((m) => Number(m[1]))
  expect(bars.length).toBeGreaterThanOrEqual(4)
  expect(new Set(bars).size).toBeGreaterThanOrEqual(3)
})

test('the four attachment kinds are four different drawings', () => {
  const drawing = (icon: React.ReactElement) => render(icon).container.querySelector('svg')!.innerHTML
  expect(new Set([<ImageIcon />, <FileIcon />, <VideoIcon />, <AudioIcon />].map(drawing)).size).toBe(4)
})
```

- [ ] **Step 3: Run them and see them fail**

Run: `pnpm test src/icons/icons.test.tsx`
Expected: FAIL, because `FileIcon`, `VideoIcon` and `AudioIcon` are not exported.

- [ ] **Step 4: Draw the three icons.** Each gets a docstring in the set's voice saying what it means and what it must not be mistaken for. The path data is fixed:
  - `FileIcon` ("one file, whatever is in it"): `M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z` and `M14 3v5h5`.
  - `VideoIcon` ("a clip": a strip of frames, not `PlayIcon`, which is the action of opening a recording): `ImageIcon`'s frame `M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z`, the rails `M7 4v16M17 4v16`, and the perforations `M3 8h4M3 12h4M3 16h4M17 8h4M17 12h4M17 16h4`.
  - `AudioIcon` ("a recording": a waveform, not `MicIcon`, which starts dictation): `M4 10v4M8 6v12M12 3v18M16 8v8M20 11v2`.

  Export all three from `src/index.ts`, next to `ImageIcon`.

- [ ] **Step 5: Run the tests and see them pass**

Run: `pnpm test src/icons/icons.test.tsx`
Expected: PASS.

- [ ] **Step 6: Update the docs.**
  - `docs/components.md` § The icon set: add `FileIcon`, `VideoIcon`, `AudioIcon` after `ImageIcon`.
  - `docs/icon-policy.md`: add a `v0.26.0` paragraph covering these points:
    - the four attachment kinds are a *value set* drawn by `AttachmentTile`;
    - `ImageIcon` (v0.20.0) is reused because it already names a kind of source;
    - `FileIcon` is `ReportIcon`'s sheet left blank, because a report holds lines someone wrote and a file may hold anything;
    - `VideoIcon` is a film strip, kept apart from `PlayIcon`'s action;
    - `AudioIcon` is a waveform, kept apart from `MicIcon`'s action.

- [ ] **Step 7: Run the gate and commit**

```bash
pnpm lint && pnpm typecheck && pnpm test && pnpm build
git add src/icons/index.tsx src/icons/icons.test.tsx src/index.ts docs/components.md docs/icon-policy.md dist/
git commit -m "feat(ui): FileIcon, VideoIcon and AudioIcon, the attachment kinds"
```

---

### Task 2: `AttachmentTile`

**Files:**
- Create: `src/primitives/AttachmentTile.tsx`, `src/primitives/AttachmentTile.test.tsx`
- Modify: `src/index.ts`, `demo/main.tsx` (the Chat section), `docs/components.md`, `README.md` (§ Primitives), `CLAUDE.md` (Primitive set), `docs/README.md` (the components.md row)
- Rebuild: `dist/`

**Interfaces:**
- Consumes: `ImageIcon`, `FileIcon`, `VideoIcon`, `AudioIcon`, `WarningIcon` (Task 1, `src/icons`); `Spinner` (`label`, `className`); `Badge`; `RemoveButton` (`label`, `onClick`, `className`); `cn`.
- Produces, exported from `src/index.ts`:

```ts
export type AttachmentKind = 'image' | 'document' | 'video' | 'audio'
export type AttachmentTileStatus = 'uploading' | 'processing' | 'ready' | 'failed' | 'expired'
export interface AttachmentTileProps {
  name: string
  kind: AttachmentKind
  status: AttachmentTileStatus
  thumbnailUrl?: string // drawn only when kind === 'image' and status !== 'expired'
  detail?: string       // "2.4 MB", "24 slides", "01:12"; shown on non-image tiles
  statusLabel?: string  // default: Uploading | Processing | Failed | Expired
  onOpen?: () => void   // the face is a <button> only when set
  onRemove?: () => void // renders a RemoveButton
  removeLabel?: string  // the RemoveButton's label; default `Remove ${name}`
  size?: 'sm' | 'md'    // default 'sm'
  className?: string    // merged onto the root
}
export function AttachmentTile(props: AttachmentTileProps): ReactElement
```

**What it renders** (the tests pin the parts marked as tested):
- **Root:** a `div`, `relative`, with `data-kind`, `data-status` and `data-size` (tested), and `className` merged last.
- **Face:** `data-part="face"` (tested). It is a `<button type="button">` when `onOpen` is set, otherwise a `<div>`. The button has `aria-label={name}`, `aria-describedby` set to the ids of the visible detail and of the status marker when they exist, and the button recipe's ring (`focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary`). The face's surface is opaque (`border border-border bg-background`), so it reads on the page and on `UserBubble`'s accent alike.
- **Image kind:** a square, `size-14` at `sm` and `size-28` at `md` (tested), `rounded-xl overflow-hidden`. It holds an `<img class="h-full w-full object-cover">` (tested). The `<img>` has `alt={name}` when the face is a div, and `alt=""` inside the button, which carries the name. It has `title={name}`. With no URL, a URL that failed to load, or `status === 'expired'`, the square shows `ImageIcon` (`h-6 w-6 text-muted-foreground`) instead.
- **Other kinds:** a row `w-56` at `sm` and `w-64` at `md` (tested), `h-14`/`h-16`. It holds a `size-10 rounded-lg bg-primary/15 text-primary` square with the kind icon (`h-5 w-5`), then the name (`truncate`, `title={name}`, tested) over the detail (`text-xs text-muted-foreground`). A `thumbnailUrl` is ignored (tested).
- **Status marker:**
  - `uploading` and `processing` show `Spinner` labelled `statusLabel`. On an image it is centred over the picture on a `bg-background/50` veil. On other kinds it takes the icon's place.
  - `failed` shows `WarningIcon text-danger` in a `role="img"` span with `aria-label`/`title` = `statusLabel`, and the face gets `border-danger` (tested). It is not `XIcon`: the corner `×` is the remove action, and two crosses on one tile would mean two things.
  - `expired` shows a `Badge` whose text is `statusLabel` (`max-w-full truncate`), and the icon square goes `bg-muted text-muted-foreground`.
  - `ready` shows no marker.
  - The marker's id (the one the face's `aria-describedby` points to) goes on a wrapper `span`, because `Spinner` takes no `id`. The marker sits inside the face.
- **Remove:** `RemoveButton` as a sibling of the face, never inside it. It sits at `absolute -right-2 -top-2 h-6 w-6 rounded-full border border-border bg-background shadow-sm` and is always visible. It comes after the face in tab order.
- **Load failure:** the image's error state resets whenever `thumbnailUrl` changes.

- [ ] **Step 1: Write the failing tests** in `src/primitives/AttachmentTile.test.tsx`. Use `const PICTURE = 'data:image/png;base64,iVBORw0KGgo='`, `const OTHER = 'data:image/png;base64,AAAA'`, `const face = (c: HTMLElement) => c.querySelector('[data-part="face"]')!` and the `drawing(icon)` helper from `PurposeBar.test.tsx`.

```tsx
test('an image shows its picture as a square cover, named by the file', () => {
  const { container, rerender } = render(<AttachmentTile name="harbour-sketch.png" kind="image" status="ready" thumbnailUrl={PICTURE} />)
  const img = screen.getByRole('img', { name: 'harbour-sketch.png' })
  expect(img).toHaveAttribute('src', PICTURE)
  expect(img).toHaveClass('object-cover')
  expect(face(container)).toHaveClass('size-14')
  expect(container.firstElementChild).toHaveAttribute('data-kind', 'image')
  expect(container.firstElementChild).toHaveAttribute('data-status', 'ready')
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  rerender(<AttachmentTile name="harbour-sketch.png" kind="image" status="ready" thumbnailUrl={PICTURE} size="md" />)
  expect(face(container)).toHaveClass('size-28')
})

test('with onOpen the face is one button named by the file; Enter, Space and a click open it', async () => {
  const onOpen = vi.fn()
  const user = userEvent.setup()
  render(<AttachmentTile name="harbour-sketch.png" kind="image" status="ready" thumbnailUrl={PICTURE} onOpen={onOpen} />)
  const button = screen.getByRole('button', { name: 'harbour-sketch.png' })
  expect(button.querySelector('img')).toHaveAttribute('alt', '') // the button carries the name
  button.focus()
  await user.keyboard('{Enter}')
  await user.keyboard(' ')
  await user.click(button)
  expect(onOpen).toHaveBeenCalledTimes(3)
})

test('the remove button is its own control, after the face, and never opens', async () => {
  const [onOpen, onRemove] = [vi.fn(), vi.fn()]
  const user = userEvent.setup()
  render(<AttachmentTile name="quarterly-plan.pdf" kind="document" status="ready" onOpen={onOpen} onRemove={onRemove} removeLabel="Remove quarterly-plan.pdf" />)
  await user.tab()
  expect(screen.getByRole('button', { name: 'quarterly-plan.pdf' })).toHaveFocus()
  await user.tab()
  expect(screen.getByRole('button', { name: 'Remove quarterly-plan.pdf' })).toHaveFocus()
  await user.keyboard('{Enter}')
  await user.click(screen.getByRole('button', { name: 'Remove quarterly-plan.pdf' }))
  expect(onRemove).toHaveBeenCalledTimes(2)
  expect(onOpen).not.toHaveBeenCalled()
})

test('without a removeLabel the remove button is still named after the file', () => {
  render(<AttachmentTile name="quarterly-plan.pdf" kind="document" status="ready" onRemove={() => {}} />)
  expect(screen.getByRole('button', { name: 'Remove quarterly-plan.pdf' })).toBeInTheDocument()
})

test('uploading and processing veil the picture with a spinner named by the status', () => {
  const { rerender } = render(<AttachmentTile name="harbour-sketch.png" kind="image" status="uploading" thumbnailUrl={PICTURE} statusLabel="Attaching harbour-sketch.png" onOpen={() => {}} />)
  expect(screen.getByRole('status', { name: 'Attaching harbour-sketch.png' })).toBeInTheDocument()
  expect(screen.getByRole('button')).toHaveAccessibleDescription('Attaching harbour-sketch.png')
  expect(screen.getByRole('button').querySelector('img')).toHaveAttribute('src', PICTURE) // the local preview stays
  rerender(<AttachmentTile name="harbour-sketch.png" kind="image" status="processing" thumbnailUrl={PICTURE} />)
  expect(screen.getByRole('status', { name: 'Processing' })).toBeInTheDocument()
})

test('a failed tile is marked in danger with the warning drawing, not a cross', () => {
  const { container } = render(<AttachmentTile name="harbour-sketch.png" kind="image" status="failed" thumbnailUrl={PICTURE} statusLabel="Could not be read" />)
  expect(face(container)).toHaveClass('border-danger')
  const marker = screen.getByRole('img', { name: 'Could not be read' })
  expect(marker.querySelector('svg')!.innerHTML).toBe(drawing(<WarningIcon />))
})

test('an expired tile shows no picture, even when given one, and says so', () => {
  const { container } = render(<AttachmentTile name="harbour-sketch.png" kind="image" status="expired" thumbnailUrl={PICTURE} size="md" />)
  expect(container.querySelector('img')).toBeNull()
  expect(face(container).querySelector('svg')!.innerHTML).toBe(drawing(<ImageIcon />))
  expect(screen.getByText('Expired')).toBeInTheDocument()
})

test.each([
  ['document', 'quarterly-plan.pdf', '24 slides', FileIcon],
  ['video', 'site-walk.mp4', '01:12', VideoIcon],
  ['audio', 'standup-notes.m4a', '04:30', AudioIcon],
] as const)('a %s tile shows its drawn kind, the name and the detail', (kind, name, detail, Icon) => {
  const { container } = render(<AttachmentTile name={name} kind={kind} status="ready" detail={detail} thumbnailUrl={PICTURE} />)
  expect(container.querySelector('img')).toBeNull() // a thumbnail is for images only
  expect(face(container).querySelector('svg')!.innerHTML).toBe(drawing(<Icon />))
  expect(screen.getByText(name)).toHaveAttribute('title', name)
  expect(screen.getByText(detail)).toBeInTheDocument()
})

test('a picture that fails to load falls back to the drawing, and a new one is tried', () => {
  const { container, rerender } = render(<AttachmentTile name="harbour-sketch.png" kind="image" status="ready" thumbnailUrl={PICTURE} />)
  fireEvent.error(container.querySelector('img')!)
  expect(container.querySelector('img')).toBeNull()
  expect(face(container).querySelector('svg')!.innerHTML).toBe(drawing(<ImageIcon />))
  rerender(<AttachmentTile name="harbour-sketch.png" kind="image" status="ready" thumbnailUrl={OTHER} />)
  expect(container.querySelector('img')).toHaveAttribute('src', OTHER)
})

test('a long name keeps the tile’s width: truncated, whole in the tooltip and the name', () => {
  const name = `${'minutes-of-the-planning-meeting-'.repeat(4)}final.pdf` // 137 characters, no spaces
  const { container } = render(<AttachmentTile name={name} kind="document" status="ready" onOpen={() => {}} />)
  expect(face(container)).toHaveClass('w-56')
  expect(screen.getByText(name)).toHaveClass('truncate')
  expect(screen.getByText(name)).toHaveAttribute('title', name)
  expect(screen.getByRole('button', { name })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run them and see them fail**

Run: `pnpm test src/primitives/AttachmentTile.test.tsx`
Expected: FAIL, because `./AttachmentTile` cannot be resolved.

- [ ] **Step 3: Implement `AttachmentTile(props: AttachmentTileProps): ReactElement`** in `src/primitives/AttachmentTile.tsx`, following "What it renders" above.
  - Use a `KIND_ICONS: Record<AttachmentKind, (p: IconProps) => ReactElement>` map and a `DEFAULT_STATUS_LABELS` record (`Uploading`, `Processing`, `Failed`, `Expired`).
  - Build the marker and detail ids from `useId()`.
  - Track the load failure as state holding the URL that failed (`failedUrl === thumbnailUrl`), so a new URL resets it with no effect.
  - Use `cva` for the size and kind variants.
  - Set `AttachmentTile.displayName`.
  - The docstring says what the tile is for, why the face and the remove button are siblings, and why a failure is drawn with the warning and not the cross.

  Export the component and its three types from `src/index.ts`, after `UserBubble`.

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm test src/primitives/AttachmentTile.test.tsx`
Expected: PASS.

- [ ] **Step 5: Add tiles to the demo and document them.**
  - **Demo:** in `demo/main.tsx`'s Chat section, add a row of `sm` tiles above the `ChatComposer`. Cover each status and each kind, with `onRemove` taking a tile out of a demo `useState` list. Inside the `UserBubble`, add a `flex flex-wrap gap-2` grid of `md` image tiles. The pictures are inline `data:image/svg+xml,…` drawings defined in the file.
  - **Visual check:** run `pnpm demo` and look at both themes and an `--app-accent` override.
  - **`docs/components.md`:** add `## Attachment tiles and the image lightbox` after § Chat composer and user bubble, with a `### AttachmentTile` subsection, and add both names to the file's opening list. The subsection has:
    - a usage example: the composer row and the bubble grid;
    - a props table with every prop above and its default;
    - the status table: marker, visible text, accessible text;
    - a note that the app makes, owns and revokes the thumbnail object URLs, and the tile fetches nothing;
    - a note that tiles sit *above* `ChatComposer`'s pill, not inside it. The pill's `[&_button]:rounded-full` would round the face;
    - the keyboard map, verbatim: "Keyboard: `Tab` reaches the face (only when it opens) and then the remove button · `Enter`/`Space` on the face open · `Enter`/`Space` on the remove button remove, and never open. A tile without `onOpen` is not focusable."
  - **One-line entries:**
    - `README.md` § Primitives: `AttachmentTile` (an attached file: its picture or drawn kind, its state, remove).
    - `CLAUDE.md` Primitive set: `AttachmentTile`.
    - `docs/README.md` components.md row: add "attachment tiles".

- [ ] **Step 6: Run the gate and commit**

```bash
pnpm lint && pnpm typecheck && pnpm test && pnpm build
git add src/primitives/AttachmentTile.tsx src/primitives/AttachmentTile.test.tsx src/index.ts demo/main.tsx docs/components.md docs/README.md README.md CLAUDE.md dist/
git commit -m "feat(ui): AttachmentTile, an attached file as a picture or its drawn kind"
```

---

### Task 3: `ImageLightbox`

**Files:**
- Create: `src/primitives/ImageLightbox.tsx`, `src/primitives/ImageLightbox.test.tsx`
- Modify: `src/index.ts`, `demo/main.tsx`, `docs/components.md`, `README.md`, `CLAUDE.md`, `docs/README.md`
- Rebuild: `dist/`

**Interfaces:**
- Consumes: `IconButton` (`icon`, `label`, `onClick`, forwarded ref), `XIcon`, `DownloadLink` (`href`, `label`, `download`), `DownloadButton` (`label`, `onClick`), `cn`, `createPortal`. The pattern is `PurposeBar`'s `show`/`finish`/`onKeyDown`/`onCancel` (`src/purpose/PurposeBar.tsx`).
- Produces, exported from `src/index.ts`:

```ts
export interface ImageLightboxProps {
  open: boolean
  src: string
  alt: string            // the img's alt and the dialog's aria-label
  onClose: () => void
  closeLabel: string
  downloadHref?: string  // renders a DownloadLink
  downloadName?: string  // the link's download attribute (an object URL would save under a random name)
  downloadLabel?: string // default 'Download'
  onDownload?: () => void // renders a DownloadButton when downloadHref is not set
}
export function ImageLightbox(props: ImageLightboxProps): ReactElement | null
```

**What it renders:**
- **Closed:** while `open` is false, nothing.
- **Open:** a `<dialog aria-modal="true" aria-label={alt}>` portalled to `document.body`, shown with `showModal()` in a layout effect, falling back to `setAttribute('open', '')` as `PurposeBar` does. It fills the viewport: `fixed inset-0 m-0 h-dvh max-h-none w-dvw max-w-none border-0 bg-background/95 p-0 text-foreground`, so the light and dark themes follow the token.
- **Stage:** a `data-part="stage"` div, `flex h-full w-full items-center justify-center p-4 pt-14`, holding `<img src alt class="max-h-full max-w-full rounded-lg object-contain shadow-lg">`.
- **Toolbar:** at `absolute right-3 top-3 flex gap-1`. The download control comes first: a `DownloadLink`, or a `DownloadButton`, or none. Pass `download={downloadName}` to the link only when `downloadName` is set: `download={undefined}` would cancel the `download` that `DownloadLink` sets itself. Close comes last: `IconButton icon={<XIcon />}`, not `RemoveButton`, because closing a view takes nothing away, so it has no danger tint. Both are `rounded-full bg-muted/80`.
- **Focus:** on open, the element that held focus (if not `body`) is recorded and focus goes to Close. On close or unmount, the dialog is closed natively first if still open, then the recorded element is focused if it `isConnected`.
- **Keys:** Escape (not while `isComposing`) runs `preventDefault`, `stopPropagation` and `onClose()`. Tab on the last control and Shift+Tab on the first wrap around, with `preventDefault`. The native `cancel` event runs `preventDefault` and `onClose()`.
- **Clicks:** a click whose target is the dialog or the stage calls `onClose()`. Every click and keydown has its propagation stopped at the dialog.

- [ ] **Step 1: Write the failing tests** in `src/primitives/ImageLightbox.test.tsx`, with `PICTURE` as in Task 2 and this harness:

```tsx
function Harness({ withOpener = true, mounted = true, ...extra }: Partial<ImageLightboxProps> & { withOpener?: boolean; mounted?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div onClick={outerClick} onKeyDown={outerKey}>
      {withOpener && <button type="button" onClick={() => setOpen(true)}>Open harbour-sketch.png</button>}
      {mounted && <ImageLightbox open={open} src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={() => setOpen(false)} {...extra} />}
    </div>
  )
}
// outerClick / outerKey: vi.fn() spies, reset in afterEach
```

```tsx
test('renders nothing while closed', () => {
  render(<ImageLightbox open={false} src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={() => {}} />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('opens as a modal dialog named by the picture, on the body, with focus on Close', async () => {
  const showModal = vi.spyOn(HTMLDialogElement.prototype, 'showModal')
  render(<Harness />)
  await userEvent.setup().click(screen.getByRole('button', { name: 'Open harbour-sketch.png' }))
  const dialog = screen.getByRole('dialog', { name: 'harbour-sketch.png' })
  expect(showModal).toHaveBeenCalledTimes(1)
  expect(dialog).toHaveAttribute('aria-modal', 'true')
  expect(dialog.parentElement).toBe(document.body)
  expect(within(dialog).getByRole('img', { name: 'harbour-sketch.png' })).toHaveClass('object-contain', 'max-h-full', 'max-w-full')
  expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus()
})

test('Escape closes it once, goes no further, and hands focus back to the opener', async () => {
  const user = userEvent.setup()
  render(<Harness />)
  await user.click(screen.getByRole('button', { name: 'Open harbour-sketch.png' }))
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(outerKey).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Open harbour-sketch.png' })).toHaveFocus()
})

test('Close closes it and hands focus back', async () => { /* click Close → no dialog, opener focused */ })

test('a click beside the picture closes it; a click on the picture does not, and neither reaches the page', async () => {
  const onClose = vi.fn()
  render(<div onClick={outerClick}><ImageLightbox open src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={onClose} /></div>)
  fireEvent.click(screen.getByRole('img', { name: 'harbour-sketch.png' }))
  expect(onClose).not.toHaveBeenCalled()
  fireEvent.click(document.querySelector('[data-part="stage"]')!)
  fireEvent.click(screen.getByRole('dialog'))
  expect(onClose).toHaveBeenCalledTimes(2)
  expect(outerClick).not.toHaveBeenCalled()
})

test('the browser’s own close request is a close', () => {
  const onClose = vi.fn()
  render(<ImageLightbox open src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={onClose} />)
  const cancel = new Event('cancel', { cancelable: true })
  fireEvent(screen.getByRole('dialog'), cancel)
  expect(cancel.defaultPrevented).toBe(true)
  expect(onClose).toHaveBeenCalledTimes(1)
})

test('Tab and Shift+Tab stay inside', async () => {
  const user = userEvent.setup()
  render(<Harness downloadHref={PICTURE} downloadName="harbour-sketch.png" />)
  await user.click(screen.getByRole('button', { name: 'Open harbour-sketch.png' }))
  const [download, close] = [screen.getByRole('link', { name: 'Download' }), screen.getByRole('button', { name: 'Close' })]
  await user.tab()
  expect(download).toHaveFocus()
  await user.tab()
  expect(close).toHaveFocus()
  await user.tab({ shift: true })
  expect(download).toHaveFocus()
  await user.tab({ shift: true })
  expect(close).toHaveFocus()
})

test('with Close alone, Tab keeps focus on Close', async () => { /* open without download; tab twice → Close focused */ })

test('offers a download link, a download button, or neither', async () => {
  const onDownload = vi.fn()
  const { rerender } = render(<ImageLightbox open src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={() => {}} downloadHref={PICTURE} downloadName="harbour-sketch.png" downloadLabel="Save picture" />)
  expect(screen.getByRole('link', { name: 'Save picture' })).toHaveAttribute('download', 'harbour-sketch.png')
  rerender(<ImageLightbox open src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={() => {}} onDownload={onDownload} />)
  await userEvent.setup().click(screen.getByRole('button', { name: 'Download' }))
  expect(onDownload).toHaveBeenCalledTimes(1)
  rerender(<ImageLightbox open src={PICTURE} alt="harbour-sketch.png" closeLabel="Close" onClose={() => {}} />)
  expect(within(screen.getByRole('dialog')).getAllByRole('button')).toHaveLength(1)
})

test('closing after the opener is gone leaves focus on the body without an error', async () => {
  // Open from the opener, rerender <Harness withOpener={false} />, press Escape:
  // no throw, no dialog, document.activeElement === document.body.
})

test('unmounting while open removes the dialog and hands focus back', async () => {
  // Open, rerender <Harness mounted={false} />: no dialog in the document, opener focused.
})
```
The three tests whose bodies are comments get the code their comments spell out, with the same calls as the tests above.

- [ ] **Step 2: Run them and see them fail**

Run: `pnpm test src/primitives/ImageLightbox.test.tsx`
Expected: FAIL, because `./ImageLightbox` cannot be resolved.

- [ ] **Step 3: Implement `ImageLightbox(props: ImageLightboxProps): ReactElement | null`** in `src/primitives/ImageLightbox.tsx`, following "What it renders" above.
  - The focusable set for the trap is `dialog.querySelectorAll('a[href], button:not([disabled])')`.
  - Keep a ref to the Close button for the initial focus.
  - The docstring says why the dialog is native (inert page, top layer), why Escape, focus and the trap are handled by hand (happy-dom, and the browser's Tab leaving to its own chrome), and why events stop at the dialog (portal bubbling).

  Export the component and `ImageLightboxProps` from `src/index.ts`, after `AttachmentTile`.

- [ ] **Step 4: Run the tests and see them pass**

Run: `pnpm test src/primitives/ImageLightbox.test.tsx`
Expected: PASS.

- [ ] **Step 5: Wire the demo and document it.**
  - **Demo:** in `demo/main.tsx`, give the ready image tiles from Task 2 an `onOpen` that opens one `ImageLightbox` with that tile's data URL and a `downloadHref`.
  - **Visual check:** run `pnpm demo`. In both themes and at phone width, the picture fits the viewport, Escape and a click beside it close, and focus returns to the tile.
  - **`docs/components.md`:** add `### ImageLightbox` under the section from Task 2. It has:
    - a usage example: a tile's `onOpen` sets state, and the lightbox's `onClose` clears it;
    - a props table;
    - the note that `src` is an object URL the app owns and revokes;
    - the keyboard map, verbatim: "Keyboard: focus starts on Close · `Tab`/`Shift+Tab` cycle Download and Close and never leave · `Enter`/`Space` run the focused control · `Esc` closes and returns focus to the control that opened it. A click beside the picture closes; a click on it does not. Escape and clicks stop at the dialog, so one press closes one layer, as in `Menu` and `PurposeBar`."
  - **`docs/icon-policy.md`:** extend the `v0.26.0` paragraph with one sentence: the lightbox's Close is `XIcon` on a plain `IconButton`, not `RemoveButton`, because closing a view removes nothing.
  - **One-line entries:**
    - `README.md` § Primitives: `ImageLightbox` (one picture at full view, in a modal dialog).
    - `CLAUDE.md` Primitive set: `ImageLightbox`.
    - `docs/README.md`: add "the image lightbox".

- [ ] **Step 6: Run the gate and commit**

```bash
pnpm lint && pnpm typecheck && pnpm test && pnpm build
git add src/primitives/ImageLightbox.tsx src/primitives/ImageLightbox.test.tsx src/index.ts demo/main.tsx docs/ README.md CLAUDE.md dist/
git commit -m "feat(ui): ImageLightbox, one picture in a modal dialog"
```

---

### Task 4: release v0.26.0

**Files:**
- Modify: `package.json` (`"version": "0.25.0"` → `"0.26.0"`)
- Rebuild: `dist/` (expected unchanged)

**Interfaces:**
- Consumes: Tasks 1–3 on the branch.
- Produces: the tag `v0.26.0` on merge, and the commit SHA `git ls-remote https://github.com/nos-tromo/infra-ui 'refs/tags/v0.26.0^{}'` that the Ghostwriter plan pins.

- [ ] **Step 1: Confirm the version is still free**

Run: `git fetch origin && git show origin/main:package.json | grep '"version"'`
Expected: `"version": "0.25.0"`. If it is higher, rebase onto `origin/main` and use the next minor in Steps 2 and 5.

- [ ] **Step 2: Bump `package.json` to `0.26.0`**

- [ ] **Step 3: Run the full gate and check the exports**

```bash
pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build && git status --short
grep -cE 'AttachmentTile|ImageLightbox|FileIcon|VideoIcon|AudioIcon|AttachmentKind|AttachmentTileStatus' dist/index.d.ts
```
Expected: everything passes, `git status --short` shows only `package.json`, and the grep count is at least 7.

- [ ] **Step 4: Commit and push**

```bash
git add package.json
git commit -m "chore: release v0.26.0"
git push -u origin feat/attachment-tile-lightbox
```

- [ ] **Step 5: Open the PR**

Title: `feat(ui): AttachmentTile, ImageLightbox and the attachment kind icons (v0.26.0)`. The body names spec § 7.4 and the consumer (Ghostwriter). Then confirm CI ran on the latest push: `gh pr checks --watch`. Merging is the owner's call.

- [ ] **Step 6: After the merge, resolve the pin for Ghostwriter**

Run: `git ls-remote https://github.com/nos-tromo/infra-ui 'refs/tags/v0.26.0^{}'`
Expected: one line `<40-hex>	refs/tags/v0.26.0^{}`. That commit SHA, not the tag object's SHA from `refs/tags/v0.26.0`, goes into Ghostwriter's `frontend/package.json` codeload URL. The Ghostwriter plan owns that bump.
