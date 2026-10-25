import type { ReactNode } from "react";
import { Spinner } from "../components/Spinner";
import { ReferenceArea } from "recharts";
import { useChartColors } from "./chartStyle";
import type { ZoomSelection } from "./useChartZoom";

export function ErrorAlert({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </p>
  );
}

export function ZoomSelectionArea({ selection, yAxisId }: { selection: ZoomSelection | null; yAxisId?: string }) {
  const { cost } = useChartColors();
  if (!selection) {
    return null;
  }
  return (
    <ReferenceArea
      yAxisId={yAxisId}
      x1={selection.x1}
      x2={selection.x2}
      fill={cost}
      fillOpacity={0.15}
      stroke={cost}
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

const SWATCH_PATTERNS = {
  dashed: (color: string) => `repeating-linear-gradient(to right, ${color} 0 4px, transparent 4px 7px)`,
  dotted: (color: string) => `repeating-linear-gradient(to right, ${color} 0 1px, transparent 1px 4px)`,
};

const SWATCH_SHAPES = { dot: "h-2 w-2 rounded-full", box: "h-2.5 w-2.5 rounded-sm", line: "h-0.5 w-4" } as const;

/** A legend/tooltip entry: a colored dot, box or line (optionally dashed or
 * dotted) and a label. The label renders in the theme's ink-muted token, never
 * a series color, so it reads in both light and dark mode. */
export function LegendSwatch({
  color,
  label,
  shape = "dot",
  pattern,
  textColor,
}: {
  color: string;
  label: string;
  shape?: keyof typeof SWATCH_SHAPES;
  pattern?: keyof typeof SWATCH_PATTERNS;
  /** Inline text color, e.g. for a live reading whose sign the text itself
   * should convey (red/green). Defaults to the neutral ink-muted token. */
  textColor?: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className={SWATCH_SHAPES[shape]}
        style={pattern ? { backgroundImage: SWATCH_PATTERNS[pattern](color) } : { backgroundColor: color }}
      />
      <span className={textColor ? undefined : "text-ink-muted"} style={textColor ? { color: textColor } : undefined}>
        {label}
      </span>
    </span>
  );
}

/** Wraps a chart so that, while its data loads, the chart dims and a spinner
 * with a label shows over it. Without this a slow response looks like a
 * broken, empty chart. */
export function ChartFrame({ loading, children }: { loading: boolean; children: ReactNode }) {
  return (
    <div className="relative" aria-busy={loading}>
      <div className={loading ? "opacity-30 transition-opacity" : "transition-opacity"}>{children}</div>
      {loading && (
        <div role="status" className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-ink-muted">
          <Spinner className="h-5 w-5" />
          Loading chart…
        </div>
      )}
    </div>
  );
}
