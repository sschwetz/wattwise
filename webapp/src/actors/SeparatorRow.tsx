import type { ListRow } from "./scheduleLayout";
import { compactInputClass } from "./styles";

type Separator = Extract<ListRow, { kind: "separator" }>;

export interface SeparatorRowProps {
  row: Separator;
  /** Null for a viewer, who sees the separator but cannot change it. */
  edit: { onRename: (label: string) => void; onRemove: () => void } | null;
}

const lineClass = "h-px flex-1 bg-line";

export function SeparatorRow({ row, edit }: SeparatorRowProps) {
  if (!edit) {
    return (
      <div role="separator" className="flex items-center gap-2 px-3 py-2 text-xs font-medium uppercase text-ink-muted">
        {row.label && <span>{row.label}</span>}
        <span className={lineClass} />
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 py-1">
      <input
        // Remounts when the saved label changes, so the box shows what was stored.
        key={row.label}
        defaultValue={row.label}
        aria-label="Separator label"
        placeholder="Separator"
        maxLength={60}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        onBlur={(e) => e.currentTarget.value !== row.label && edit.onRename(e.currentTarget.value)}
        className={`${compactInputClass} w-40 text-xs uppercase`}
      />
      <span className={lineClass} />
      <button
        type="button"
        aria-label="Remove separator"
        onClick={edit.onRemove}
        className="shrink-0 rounded-md px-2 py-1 text-ink-muted hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}
