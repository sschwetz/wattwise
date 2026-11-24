# Reorderable Schedule List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manager drags schedules into any order and adds named separators between groups, and everyone on the site sees that order.

**Architecture:** One JSON column `sites.schedule_layout` holds an ordered list of schedule and separator items. Core API gets a `GET` and a manager-only `PUT` for it under the existing actors router. The webapp merges the layout with the schedule list in a pure function and renders one flat list with `@dnd-kit` drag and drop.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic (SQLite), pytest; React 19, TanStack Query, Vitest, `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`.

**Spec:** `docs/superpowers/specs/2026-11-24-schedule-list-order-design.md`

## Global Constraints

- The layout is per site, not per actor. One flat list across all actors, no per-actor headings.
- Layout items: `{"type": "schedule", "id": int}` and `{"type": "separator", "key": str, "label": str}`. `key` is 1 to 64 characters. `label` is 0 to 60 characters, empty for a plain line.
- At most 200 items. A repeated schedule id or separator key, or a schedule id not on an actor of the site, returns 422.
- `sites.schedule_layout` is nullable. Null means never arranged: id order.
- A schedule missing from the layout goes at the end in id order. A layout id with no schedule is dropped when the list is built. Schedule create and delete do not touch the layout.
- `GET /sites/{id}/actors/schedules` keeps returning a plain list of schedules. The actor never sees separators.
- Read needs site access. Write needs a manager (`require_site_manager`).
- A failed save returns the list to the stored order and shows an error line.
- Drag and drop uses `@dnd-kit` so it works on touch and keyboard.
- Prose in code comments and docs follows `.claude/rules/avoid-ai-writing.md`.
- The working tree already holds uncommitted work from other tasks. Commit only the paths each task lists, never `git add -A`.

## Review Focus

- A layout that names a deleted schedule: the list still renders and no error shows. Pinned in Task 2 by `arrangeRows` dropping unknown ids.
- A schedule created after the layout was saved: it shows at the end, below the last separator. Pinned in Task 2.
- A layout holding only separators, or an empty `items` list: every schedule shows in id order. Pinned in Task 2.
- A second site's schedule id in a `PUT`: 422, and nothing stored. Pinned in Task 1.
- A viewer opening the list: separators and order show, with no handles and no "Add separator". Pinned in Task 3.

---

### Task 1: Core API stores and serves the layout

**Files:**
- Modify: `core-api/core_api/models/identity.py` (the `Site` class, after `longitude`)
- Create: `core-api/alembic/versions/c3e5a7b9d1f2_add_site_schedule_layout.py`
- Modify: `core-api/core_api/actor_schemas.py`
- Modify: `core-api/core_api/routers/actors.py`
- Test: `core-api/tests/test_actors_router.py`
- Modify: `docs/data-model.md`, `docs/core-api.md`

**Interfaces:**
- Produces, in `actor_schemas.py`:
  - `class ScheduleLayoutItem` is a discriminated union (`type`) of `LayoutSchedule(type: Literal["schedule"], id: int)` and `LayoutSeparator(type: Literal["separator"], key: str, label: str = "")`, both built on `_Strict`.
  - `class ScheduleLayoutBody(_Strict)` with `items: list[ScheduleLayoutItem]` (max 200).
  - `class ScheduleLayoutView(BaseModel)` with `items: list[ScheduleLayoutItem] | None`.
- Produces, in `routers/actors.py`: `GET /sites/{site_id}/actors/schedule-layout` returns `ScheduleLayoutView`. `PUT` of the same path takes `ScheduleLayoutBody` and returns `ScheduleLayoutView`.
- Produces: `Site.schedule_layout: Mapped[list | None]`.

- [ ] **Step 1: Write the failing tests** in `test_actors_router.py`, using the existing `http`, `db`, `site`, `viewer` fixtures and `_instance` helper:
  - `test_layout_is_null_until_saved`: `GET {_base(site)}/schedule-layout` returns 200 and `{"items": None}`.
  - `test_manager_saves_and_reads_layout`: create an instance and two schedules (use the helper style of the existing schedule tests), `PUT` items `[schedule a, separator {"key": "k1", "label": "Kids"}, schedule b]`, expect 200 and the same items back, then `GET` returns them.
  - `test_layout_rejects_schedule_of_another_site`: a schedule on a second site's instance, `PUT` it, expect 422 and `GET` still `{"items": None}`.
  - `test_layout_rejects_repeats_and_long_labels`: a repeated schedule id, a repeated separator key, a 61-character label and 201 items each return 422.
  - `test_viewer_reads_layout_but_cannot_save_it`: viewer `GET` is 200, viewer `PUT` is 403.
  - `test_empty_layout_is_stored_as_empty_list`: `PUT {"items": []}` then `GET` returns `{"items": []}`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd core-api && uv run pytest tests/test_actors_router.py -k layout -q`
Expected: FAIL with 404 on the new path.

- [ ] **Step 3: Add the column and migration.** `Site.schedule_layout: Mapped[list | None] = mapped_column(JSON, nullable=True)` with a comment saying null means never arranged. The migration `c3e5a7b9d1f2` has `down_revision = 'b2d4f6a8c0e1'`, adds the column with `op.add_column` and drops it in `downgrade`. Import `JSON` in `identity.py` if it is not already imported.

- [ ] **Step 4: Add the schemas** named in Interfaces to `actor_schemas.py`. `label` has `max_length=60`, `key` has `min_length=1, max_length=64`. Use `Field(discriminator="type")` for the union.

- [ ] **Step 5: Add the two routes** to `routers/actors.py`. `PUT` rejects, with `AppError(422, "invalid_layout", <message>)`, a repeated schedule id, a repeated separator key, and any schedule id not among the ids returned by the same join as `list_schedules`. It stores `[item.model_dump() for item in body.items]` on `site.schedule_layout`, commits and returns the view. `GET` returns the stored value.

- [ ] **Step 6: Run the tests and the migration**

Run: `cd core-api && uv run pytest tests/test_actors_router.py -q && uv run alembic upgrade head && uv run alembic heads`
Expected: tests PASS, one head `c3e5a7b9d1f2`.

- [ ] **Step 7: Update the docs.** `docs/data-model.md`: a row or sentence for `sites.schedule_layout` and its migration. `docs/core-api.md`: the two routes in the actor route table (next to `GET/POST /schedules`), their 422 `invalid_layout` and the manager rule.

- [ ] **Step 8: Commit**

```bash
git add core-api/core_api/models/identity.py core-api/alembic/versions/c3e5a7b9d1f2_add_site_schedule_layout.py core-api/core_api/actor_schemas.py core-api/core_api/routers/actors.py core-api/tests/test_actors_router.py docs/data-model.md docs/core-api.md
git commit -m "feat(core-api): store a site-wide schedule list layout"
```

---

### Task 2: Webapp layout model and hooks

**Files:**
- Modify: `webapp/src/actors/types.ts`
- Create: `webapp/src/actors/scheduleLayout.ts`
- Test: `webapp/src/actors/scheduleLayout.test.ts`
- Modify: `webapp/src/actors/useActors.ts`
- Test: `webapp/src/actors/useActors.test.tsx`

**Interfaces:**
- Produces, in `types.ts`:
  - `type ScheduleLayoutItem = { type: "schedule"; id: number } | { type: "separator"; key: string; label: string }`
  - `interface ScheduleLayout { items: ScheduleLayoutItem[] | null }`
- Produces, in `scheduleLayout.ts`:
  - `type ListRow = { kind: "schedule"; schedule: ActorSchedule } | { kind: "separator"; key: string; label: string }`
  - `arrangeRows(schedules: ActorSchedule[], items: ScheduleLayoutItem[] | null): ListRow[]`
  - `rowId(row: ListRow): string`, which is `"s:<id>"` or `"p:<key>"`.
  - `layoutFromRows(rows: ListRow[]): ScheduleLayoutItem[]`
  - `moveRow(rows: ListRow[], activeId: string, overId: string): ListRow[]`, which returns `rows` unchanged when either id is missing or they are equal.
  - `newSeparator(): ListRow`, a separator with `key` from `crypto.randomUUID()` and an empty label.
- Produces, in `useActors.ts`: `actorKeys.layout(siteId)`, `useScheduleLayout(siteId)` (a query of `GET .../schedule-layout`), `useSaveScheduleLayout(siteId)` (a mutation taking `ScheduleLayoutItem[]`, `PUT`s `{items}`).

- [ ] **Step 1: Write the failing tests** in `scheduleLayout.test.ts`, with schedules of ids 1, 2, 3, 4:
  - `arrangeRows(schedules, null)` gives the four schedule rows in id order.
  - A layout `[s3, separator k1, s1]` gives `[s3, k1, s1, s2, s4]`: layout order first, then the unlisted schedules in id order.
  - A layout naming id 99 (no such schedule) gives no row for 99.
  - A layout that names id 2 twice shows it once.
  - A layout of only `[separator k1]` gives `[k1, s1, s2, s3, s4]`.
  - `layoutFromRows(arrangeRows(...))` round-trips to a list holding every schedule once.
  - `moveRow(rows, "s3", "s1")` puts s3 where s1 was. `moveRow` with an unknown id returns the same array.

- [ ] **Step 2: Run to verify they fail**

Run: `cd webapp && npx vitest run src/actors/scheduleLayout.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `scheduleLayout.ts` and the types.** `moveRow` uses `arrayMove` semantics (move the active row to the over row's index). It is plain code with no `@dnd-kit` import, so the tests need no new package.

- [ ] **Step 4: Write the failing hook tests** in `useActors.test.tsx`, following the file's existing style with a mocked `apiFetch`:
  - `useSaveScheduleLayout` calls `apiFetch` with `/api/v1/sites/7/actors/schedule-layout`, `method: "PUT"` and a body of `{"items": [...]}`.
  - After a save that rejects, the layout query holds the value it had before the save (the optimistic update rolls back).
  - After a successful save, the layout query is refetched.

- [ ] **Step 5: Implement the hooks.** `useSaveScheduleLayout` writes the new items into the `actorKeys.layout(siteId)` cache in `onMutate`, restores the previous value in `onError`, and invalidates the key in `onSettled`.

- [ ] **Step 6: Run the tests**

Run: `cd webapp && npx vitest run src/actors/scheduleLayout.test.ts src/actors/useActors.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webapp/src/actors/types.ts webapp/src/actors/scheduleLayout.ts webapp/src/actors/scheduleLayout.test.ts webapp/src/actors/useActors.ts webapp/src/actors/useActors.test.tsx
git commit -m "feat(webapp): schedule layout model and save hook"
```

---

### Task 3: Drag-and-drop list with separators

**Files:**
- Modify: `webapp/package.json`, `webapp/package-lock.json` (via npm)
- Modify: `webapp/src/actors/ScheduleList.tsx`
- Create: `webapp/src/actors/SortableItem.tsx`
- Create: `webapp/src/actors/SeparatorRow.tsx`
- Test: `webapp/src/actors/ScheduleList.test.tsx`
- Modify: `docs/webapp.md`, `webapp/src/docs/08-schedules.md`, `webapp/src/docs/11-glossary.md`

**Interfaces:**
- Consumes: `arrangeRows`, `rowId`, `layoutFromRows`, `moveRow`, `newSeparator`, `ListRow` from `scheduleLayout.ts`; `useScheduleLayout`, `useSaveScheduleLayout` from `useActors.ts`.
- `ScheduleList`'s props do not change (`site`, `data`, `view`).
- Produces: `SortableItem({ id, canEdit, children })`, a wrapper around `useSortable` that renders a handle button with `aria-label="Drag to reorder"` when `canEdit`. `SeparatorRow({ row, canEdit, onRename, onRemove })`, where `row` is the separator `ListRow` and `onRename(label: string)` and `onRemove()` take nothing else.

- [ ] **Step 1: Install the packages**

Run: `cd webapp && npm install --save-exact @dnd-kit/core@6.3.1 @dnd-kit/sortable@10.0.0 @dnd-kit/utilities@3.2.2`
Expected: `package.json` lists the three packages; `npm ls @dnd-kit/core` shows 6.3.1.

- [ ] **Step 2: Write the failing tests** in `ScheduleList.test.tsx`. The file's existing mock returns runs for every `apiFetch`, so route the mock by URL: the layout URL returns `{items: [...]}` and the runs URL returns the run list. Tests:
  - Rows render in layout order with a separator between them: layout `[s2, separator "Kids", s1, s3]` gives the names in that order and the text "Kids" between "Old" and "Peak off".
  - The per-actor heading is gone: with two instances, no `h3` with the instance name renders, and each row still shows its instance name.
  - A schedule not in the layout renders after the last item.
  - "Add separator" (manager) calls `PUT .../schedule-layout` with the current items plus a final `{type: "separator", label: ""}` item whose `key` is a non-empty string.
  - Renaming: editing a separator's label input and pressing Enter `PUT`s the layout with that label.
  - "Remove separator" `PUT`s the layout without it.
  - A viewer (`canEdit: false`) sees the separator and its label, no "Add separator" button, no handle with the name "Drag to reorder" and no remove button.
  - A failed `PUT` shows an element with `role="alert"` reading "Could not save the order."
  - An unnamed instance shows "Device" in the row, not "Air conditioner".

- [ ] **Step 3: Run to verify they fail**

Run: `cd webapp && npx vitest run src/actors/ScheduleList.test.tsx`
Expected: FAIL on the new tests; the existing tests still PASS.

- [ ] **Step 4: Implement.**
  - `ScheduleList.tsx`: read the layout with `useScheduleLayout(site.id)`, build `rows = arrangeRows(schedules, layout.data?.items ?? null)`, and render one `ul`. Delete `InstanceGroup`. Look up each row's instance with `instances.find`. Wrap the list in `DndContext` with `PointerSensor` (`activationConstraint: { distance: 6 }` so a click still selects a row) and `KeyboardSensor` using `sortableKeyboardCoordinates`, and in `SortableContext` with `verticalListSortingStrategy` and `rowId` ids. `onDragEnd` calls `save.mutate(layoutFromRows(moveRow(rows, String(active.id), String(over.id))))`. Rendering handles only when `canEdit`.
  - "Add separator" sits beside "New schedule" for managers and saves `[...layoutFromRows(rows), separator]`.
  - Show `<p role="alert">Could not save the order.</p>` while `save.isError`.
  - Change the `?? "Air conditioner"` fallback at `ScheduleList.tsx:51` to `?? "Device"`.
  - `SeparatorRow`: a labelled line. With `canEdit`, the label is a text input (`maxLength={60}`, placeholder "Separator") that saves on Enter or blur when changed, and a "Remove separator" button. Without `canEdit`, it is the label text and a line.
  - `SortableItem`: `useSortable({ id })`, applies `transform` and `transition` from `CSS.Transform`, and puts the drag attributes and listeners on the handle button only, so clicking a row still selects it.

- [ ] **Step 5: Run the full webapp suite and the type check**

Run: `cd webapp && npx vitest run && npx tsc -b`
Expected: all tests PASS, no type errors.

- [ ] **Step 6: Check the drag in a browser.** Use the `run` skill to start the app. With two or more schedules, add a separator, drag a schedule across it with the mouse, reload and confirm the order stays. Move one with the keyboard (focus a handle, Space, an arrow key, Space). Report what happened. A drag that does not move a row is a failure.

- [ ] **Step 7: Update the docs.** `docs/webapp.md`: the list is one flat list in the saved order, how the layout is read and saved, and the new components. `webapp/src/docs/08-schedules.md`: a short section on arranging schedules and adding separators, written for a user, and rewrite the sentence that describes the list order. `webapp/src/docs/11-glossary.md`: an entry for "Separator". Link to the section instead of repeating it.

- [ ] **Step 8: Commit**

```bash
git add webapp/package.json webapp/package-lock.json webapp/src/actors/ScheduleList.tsx webapp/src/actors/SortableItem.tsx webapp/src/actors/SeparatorRow.tsx webapp/src/actors/ScheduleList.test.tsx docs/webapp.md webapp/src/docs/08-schedules.md webapp/src/docs/11-glossary.md
git commit -m "feat(webapp): drag schedules into order and add separators"
```

---

### Task 4: Review and docs check

**Files:** none new.

- [ ] **Step 1:** Run the `documentation` agent over the change and apply what it reports.
- [ ] **Step 2:** Run `python3 scripts/check_docs.py`. Expected: no broken link or anchor.
- [ ] **Step 3:** Run `/simplify` on the changed files, then rerun `cd core-api && uv run pytest -q` and `cd webapp && npx vitest run`. Expected: both PASS.
- [ ] **Step 4:** Commit any fixes with explicit paths.

## Self-review

- **Spec coverage:** per-site column and migration, GET and PUT, validation rules, flat list without per-actor headings, `arrangeRows` behaviour, drag with `@dnd-kit` (touch and keyboard), add, rename and remove separators, viewer view, rollback on a failed save, and every doc in the spec are in Tasks 1 to 4. The `Device` fallback is in Task 3 and is not in the spec: it came from the review of air-conditioner wording.
- **Names:** `arrangeRows`, `rowId`, `layoutFromRows`, `moveRow`, `newSeparator`, `ListRow`, `ScheduleLayoutItem`, `useScheduleLayout` and `useSaveScheduleLayout` are the same in every task.
- **Not unit-tested:** the dnd-kit pointer and keyboard wiring, because jsdom has no layout. `moveRow` carries the logic and is tested. Step 6 of Task 3 checks the wiring by hand.
