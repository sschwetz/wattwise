import { ReferenceArea } from "recharts";
import type { ZoomSelection } from "./useChartZoom";

export function ErrorAlert({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </p>
  );
}

export function ZoomSelectionArea({ selection, yAxisId }: { selection: ZoomSelection | null; yAxisId?: string }) {
  if (!selection) {
    return null;
  }
  return (
    <ReferenceArea
      yAxisId={yAxisId}
      x1={selection.x1}
      x2={selection.x2}
      fill="var(--color-solar)"
      fillOpacity={0.15}
      stroke="var(--color-solar)"
      strokeOpacity={0.5}
    />
  );
}

export function ResetZoomButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-line bg-surface px-3 py-1 text-xs text-ink hover:bg-line/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
    >
      Reset zoom
    </button>
  );
}
