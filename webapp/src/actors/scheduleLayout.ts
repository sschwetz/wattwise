import type { ActorSchedule, ScheduleLayoutItem } from "./types";

export type ListRow =
  | { kind: "schedule"; schedule: ActorSchedule }
  | { kind: "separator"; key: string; label: string };

/** The id dnd-kit tracks a row by: "s:<schedule id>" or "p:<separator key>". */
export function rowId(row: ListRow): string {
  return row.kind === "schedule" ? `s:${row.schedule.id}` : `p:${row.key}`;
}

/**
 * The rows to show: the saved order first, then any schedule it does not name in id order.
 * A saved id with no schedule is dropped, and a schedule named twice shows once.
 */
export function arrangeRows(schedules: ActorSchedule[], items: ScheduleLayoutItem[] | null): ListRow[] {
  const byId = new Map(schedules.map((s) => [s.id, s]));
  const rows: ListRow[] = [];
  const placed = new Set<number>();
  for (const item of items ?? []) {
    if (item.type === "separator") {
      rows.push({ kind: "separator", key: item.key, label: item.label });
      continue;
    }
    const schedule = byId.get(item.id);
    if (schedule && !placed.has(item.id)) {
      placed.add(item.id);
      rows.push({ kind: "schedule", schedule });
    }
  }
  const unlisted = schedules.filter((s) => !placed.has(s.id)).sort((a, b) => a.id - b.id);
  return [...rows, ...unlisted.map((schedule): ListRow => ({ kind: "schedule", schedule }))];
}

export function layoutFromRows(rows: ListRow[]): ScheduleLayoutItem[] {
  return rows.map((row) =>
    row.kind === "schedule"
      ? { type: "schedule", id: row.schedule.id }
      : { type: "separator", key: row.key, label: row.label },
  );
}

/** Puts the dragged row where the target row was. Returns `rows` itself when there is nothing to move. */
export function moveRow(rows: ListRow[], activeId: string, overId: string): ListRow[] {
  const from = rows.findIndex((r) => rowId(r) === activeId);
  const to = rows.findIndex((r) => rowId(r) === overId);
  if (from < 0 || to < 0 || from === to) return rows;
  const moved = [...rows];
  const [row] = moved.splice(from, 1);
  moved.splice(to, 0, row);
  return moved;
}

export function newSeparator(): ListRow {
  return { kind: "separator", key: crypto.randomUUID(), label: "" };
}
