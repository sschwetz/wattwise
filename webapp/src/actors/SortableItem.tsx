import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { ReactNode } from "react";

export interface SortableItemProps {
  id: string;
  canEdit: boolean;
  children: ReactNode;
}

/** One list item. A manager gets a handle to drag it; only the handle starts a drag, so the row itself still selects. */
export function SortableItem({ id, canEdit, children }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id,
    disabled: !canEdit,
  });
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-1 ${isDragging ? "relative z-10 rounded-md bg-surface shadow-md" : ""}`}
    >
      {canEdit && (
        <button
          type="button"
          ref={setActivatorNodeRef}
          aria-label="Drag to reorder"
          className="shrink-0 cursor-grab touch-none rounded-md px-1.5 py-2 text-ink-muted hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          {...attributes}
          {...listeners}
        >
          <span aria-hidden="true">⠿</span>
        </button>
      )}
      <div className="min-w-0 flex-1">{children}</div>
    </li>
  );
}
