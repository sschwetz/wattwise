import type { DragEndEvent } from "@dnd-kit/core";
import { arrangeRows, layoutFromRows, moveRow, newSeparator, type ListRow } from "./scheduleLayout";
import type { ActorSchedule } from "./types";
import { useSaveScheduleLayout, useScheduleLayout } from "./useActors";

/** The rows of the Schedules list in their saved order, and the edits a manager can make to that order. */
export function useArrangement(siteId: number, schedules: ActorSchedule[]) {
  const layout = useScheduleLayout(siteId);
  const save = useSaveScheduleLayout(siteId);
  const rows = arrangeRows(schedules, layout.data?.items ?? null);
  const persist = (next: ListRow[]) => save.mutate(layoutFromRows(next));
  return {
    rows,
    saveFailed: save.isError,
    addSeparator: () => persist([...rows, newSeparator()]),
    removeSeparator: (key: string) => persist(rows.filter((r) => r.kind !== "separator" || r.key !== key)),
    renameSeparator: (key: string, label: string) =>
      persist(rows.map((r) => (r.kind === "separator" && r.key === key ? { ...r, label } : r))),
    drop: ({ active, over }: DragEndEvent) => {
      const next = over ? moveRow(rows, String(active.id), String(over.id)) : rows;
      if (next !== rows) persist(next);
    },
  };
}
