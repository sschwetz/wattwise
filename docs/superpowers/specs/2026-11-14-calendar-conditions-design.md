# Calendar and demand window conditions for schedules

## Intent

Step 3 of the schedule editor ("Only if") only tests readings: a reading is above or below a value. People need to say "only in summer, when it is over 30 outside and over 25 inside, and no demand window is running". A second use is a household where the children are home on alternate weeks, so a schedule should run only on odd weeks or only on even ones.

This adds four kinds of row to the existing "Only if" list. It is not a new step. The same rows also work in the restore conditions (`restore.only_if` and `restore.early_if`), which share the editor and the evaluator.

## Goals

- A **Month** row: month is / is not [one or more months].
- A **Day** row: day of the week is / is not [one or more days].
- A **Week** row: the week is odd / even (see "Odd and even weeks").
- A **Demand window** row: a demand window is / is not active.
- Rows are ticked on or off and must all pass, as reading rows do now.
- Existing schedules load, save and evaluate exactly as before.

## Non-goals

- No new editor step and no second list. The rows go in the existing "Only if" list.
- No anchor date for the user to set.
- No week that starts on a day other than Monday, so a handover on a Friday or Sunday is not covered.
- No time-of-day row. The trigger already has a time.

## Anti-goals

- A saved schedule changing behavior because the stored shape changed.
- A calendar row failing for a data reason. Month, day and week come from the clock, so they are always known.

## Approaches

- **A. One list, with a row kind (chosen).** Each condition gets a `kind`. A row with no `kind` is a reading row, so no data migration is needed. Restore conditions get the rows for free.
- **B. A separate "calendar" section under step 3.** Simpler to build, but it splits the rules over two places and the restore conditions would need a second copy.

For odd and even weeks, the options were ISO week parity or a fixed anchor. ISO parity has two odd weeks in a row when a year has 53 weeks, which would break an alternating pattern once a year. The chosen anchor is the Unix epoch, so the user sets nothing.

## Odd and even weeks

Weeks run Monday to Sunday in the site's timezone. The week containing 1970-01-01 (Mon 1969-12-29 to Sun 1970-01-04) is week 0.

```
days  = local date - 1970-01-01, in days
week  = floor((days + 3) / 7)
odd   = week % 2 == 1
```

Week 0 is even, week 1 (from Mon 1970-01-05) is odd, and the weeks alternate from there with no year-end slip. Check: Sat 2026-11-14 is 20771 days after the epoch, which is week 2967, so it is an odd week.

When someone adds a Week row, or changes a row to the Week kind, the odd/even select starts on the parity of the current week in the site timezone. For today that is "odd".

## Stored shape (proposed; the builder may adjust names)

`kind` is optional. Absent means `"reading"`, and reading rows keep `reading`, `op`, `value`, `target`, `enabled`.

| Kind | Fields |
|------|--------|
| `months` | `op`: `in` or `not_in`; `values`: months 1-12, at least one |
| `days` | `op`: `in` or `not_in`; `values`: days 0-6 with Monday as 0 (as the trigger's `days_of_week`), at least one |
| `week` | `parity`: `odd` or `even` |
| `demand_window` | `op`: `active` or `not_active`; `window_id`: a window id, or null for any window |

All kinds carry `enabled`.

## Evaluation

- Month, day and week use `context.now` and `context.timezone`.
- A demand window is active when an occurrence in `context.windows` (the one with `window_id`, or any when null) has `start <= now < end`. The context already holds occurrences from 24 hours back, so a window that began earlier today is included.
- A failed row gives a reason in the run log, like the reading rows do. Example: `month Jul is not in Dec, Jan, Feb`.
- Calendar and window rows are always evaluable, so the restore gate evaluates them normally. Only reading rows keep the "missing reading passes" rule there.

## Parts that change

- **actron-neo-actor:** `Condition` in `models.py` and `schedule_eval.py`.
- **core-api:** `ConditionSpec` in `actor_schemas.py`, the validation in `actor_capabilities.py` and `actor_schedule_validation.py`. `validate_conditions_against` skips non-reading kinds. A `window_id` must belong to the site's current tariff.
- **contracts/actor-api:** the condition examples and schemas.
- **webapp (`src/actors`):** `ConditionsEditor.tsx`, `scheduleForm.ts`, `scheduleValidation.ts`, `scheduleDirty.ts`, and the tests beside them.
- **Docs:** any user docs that describe the "Only if" step.

## Editor behavior

The row's first select (today "Reading") also lists Month, Day of week, Week and Demand window. Picking one swaps the controls after it:

```
[x] [Month        v] [is v]      [Dec][Jan][Feb] ... (toggle buttons for 12 months)
[x] [Day of week  v] [is not v]  [Sat][Sun] ... (7 toggle buttons)
[x] [Week         v] [is v]      [odd v]
[x] [Demand window v] [is not v] active  [Any demand window v]
```

The window select lists the current tariff's windows (`useCurrentDemandWindows`), with "Any demand window" first.

## Decided by the builder's brief (my calls)

- The window select defaults to "Any demand window".
- A specific window that has since ended or been removed is never active. "Is not active" then passes and "is active" fails. Save rejects an id that is not the site's.
- Months and days start with none selected, and save is blocked until at least one is chosen.

## Left to the builder

- Exact JSON key names and the actor's `Condition` layout.
- Whether the toggle buttons reuse an existing component.
- How the webapp gets the site timezone for the week default.
