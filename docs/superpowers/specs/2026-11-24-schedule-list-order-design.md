# Reorderable schedule list with separators

## Intent

The Schedules list is ordered by creation (`ORDER BY ActorSchedule.id`). The owner wants to group related schedules, for example the kids' zone schedules apart from the rest, and to see the groups with a visual break between them. A site can have several actors of any provider, such as two air conditioners or a different device later, and one list holds all their schedules. A manager creates a separator, then drags schedules above and below it.

## Goals

- A manager can drag schedules into any order.
- A manager can add, rename and remove separators, and drag them like a row.
- Everyone with access to the site sees the saved order and the separators.
- A new schedule appears at the end of the list without any action.

## Non-goals

- Collapsing or hiding a group.
- A separate order for each user.
- A separate order for each actor or provider. The site has one list.
- Any effect on when or how schedules run. Order is display only.

## Anti-goals

- The actor must never see a separator. It polls the schedule list every 30 seconds and would try to run one.
- A failed save must not leave the list showing an order the server did not store.

## Approaches considered

- **A. `position` column on schedules plus a separators table.** Every move renumbers two tables, and the actor's schedule payload gains a field it does not use.
- **B. One saved layout per site (chosen).** A JSON column on `sites` holds the ordered list of schedules and separators. The schedules table and the actor payload do not change. One write saves the whole order.
- **C. A group label on each schedule.** Smallest build, but there is no free placement and a separator is not a thing the owner can drop where they like.

The owner chose B with drag and drop. A layout per actor was rejected because the site can have several actors and the list shows them together.

## Design

**Data.** `sites.schedule_layout`, nullable JSON. Null means "never arranged": the list is in id order, as now. Otherwise a list of items:

```json
[
  {"type": "schedule", "id": 3},
  {"type": "schedule", "id": 5},
  {"type": "separator", "key": "9f2c...", "label": "Kids"},
  {"type": "schedule", "id": 1}
]
```

A separator's `key` is a random string made by the webapp. It keeps the drag identity stable and is not shown. `label` is optional, up to 60 characters, empty for a plain line.

**API.**
- `GET /sites/{id}/schedules` keeps returning a plain list of schedules. The layout has its own site-access `GET` and a manager-only `PUT`, for example `/sites/{id}/schedule-layout`.
- `PUT` replaces the layout. It returns 422 for a schedule id that is not on an actor of that site, for a repeated schedule id or separator key, for a label over 60 characters, and for more than 200 items.
- A schedule the layout does not mention is not an error. A layout may also name a schedule that was since deleted. The webapp drops those ids when it builds the list, so schedule create and delete need no change.

**Webapp.**
- The list is one flat list across all actors. The per-actor headings go. Each row already shows its actor's name.
- A pure function `arrangeRows(schedules, layout)` returns the rows to render: layout order, unknown ids dropped, unlisted schedules appended in id order. Tested on its own.
- Managers see a drag handle on each schedule row and on each separator. Viewers see neither. A viewer's list shows the separators as non-interactive lines.
- An "Add separator" button sits beside "New schedule". It adds an unlabelled separator at the end of the list, and the owner drags it into place.
- A separator shows its label, or a plain line when empty. Clicking the label edits it. A remove button deletes the separator.
- A drop saves the new layout. The list updates at once, and returns to the stored order with an error line if the save fails.
- Drag and drop uses `@dnd-kit/core` and `@dnd-kit/sortable`. This is my call: it supports touch and keyboard moves, and the native HTML5 drag API supports neither on a phone.

**Docs.** `docs/data-model.md` (column), `docs/core-api.md` (endpoints), `docs/webapp.md` (list behaviour), `webapp/src/docs/08-schedules.md` (user chapter), plus the glossary entry for "separator". An Alembic migration adds the column.

## Left to the builder

- The exact route path, following the site routes in `routers/actors.py`.
- Component split and file names under `webapp/src/actors/`.
- Wording of the error line on a failed save.
- Tests: backend validation and permissions, `arrangeRows`, and a list test that a drop sends the expected layout.
