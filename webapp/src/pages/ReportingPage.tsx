import { useMemo, useState } from "react";
import DatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { useParams, useSearchParams } from "react-router-dom";
import { ErrorAlert } from "../dashboard/chartParts";
import { type TotalsRow, useBillingPeriodBounds, useTotals } from "../dashboard/useHistoricalData";
import { BackLink } from "../layout/BackLink";
import {
  bucketOf,
  dayDate,
  monthDate,
  monthKey,
  parentView,
  parseView,
  rangeForView,
  type ReportingView,
  viewSearch,
} from "./reportingView";
import { TotalsChart } from "./TotalsChart";
import { TotalsTable } from "./TotalsTable";
import { DEFAULT_SORT, type SortState } from "./totalsSort";

function MethodRadio({
  method,
  selected,
  onSelect,
}: {
  method: "net" | "gross";
  selected: boolean;
  onSelect: () => void;
}) {
  const label = method === "net" ? "Net" : "Gross";
  return (
    <label className="flex items-center gap-1 text-sm text-ink-muted">
      <input type="radio" name="method" checked={selected} onChange={onSelect} aria-label={label} />
      {label}
    </label>
  );
}

const DATE_PICKER_INPUT_CLASS =
  "rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";

const pad = (n: number) => String(n).padStart(2, "0");

/** The heading of a drilled-in view: the day, and the hour when there is one. */
function viewTitle(day: string, hour: number | null): string {
  const date = dayDate(day).toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  return hour === null ? date : `${date}, ${pad(hour)}:00–${pad((hour + 1) % 24)}:00`;
}

function ViewHeader({ view, onChange }: { view: ReportingView; onChange: (view: ReportingView) => void }) {
  if (view.day === null) {
    return (
      <DatePicker
        selected={view.month === null ? null : monthDate(view.month)}
        onChange={(date: Date | null) => onChange({ month: date && monthKey(date), day: null, hour: null })}
        showMonthYearPicker
        dateFormat="MMMM yyyy"
        placeholderText="Current billing period"
        isClearable
        aria-label="Month"
        className={DATE_PICKER_INPUT_CLASS}
      />
    );
  }
  return (
    <div className="flex items-center gap-2">
      <BackLink to={`?${viewSearch(parentView(view))}`} />
      <h2 className="font-display text-lg font-semibold text-ink">{viewTitle(view.day, view.hour)}</h2>
    </div>
  );
}

function HistoryAndCost({ siteId }: { siteId: number }) {
  const [params, setParams] = useSearchParams();
  const view = parseView(params);
  const bucket = bucketOf(view);
  const [method, setMethod] = useState<"net" | "gross">("net");
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const { data: billingRange, isPending: billingPending } = useBillingPeriodBounds(siteId);
  const waitingForBilling = view.month === null && view.day === null && billingPending;
  const { month, day, hour } = view;
  const range = useMemo(
    () => (waitingForBilling ? undefined : rangeForView({ month, day, hour }, billingRange ?? null, new Date())),
    [waitingForBilling, month, day, hour, billingRange],
  );
  const { data: rows, isError } = useTotals(siteId, range, method, bucket);

  const show = (next: ReportingView) => setParams(viewSearch(next));
  const drillInto = (row: TotalsRow) =>
    show(bucket === "day" ? { ...view, day: row.date } : { ...view, hour: new Date(row.start).getHours() });

  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center gap-4">
        <ViewHeader view={view} onChange={show} />
        <MethodRadio method="net" selected={method === "net"} onSelect={() => setMethod("net")} />
        <MethodRadio method="gross" selected={method === "gross"} onSelect={() => setMethod("gross")} />
      </div>
      <div className="mt-4 space-y-4" data-testid="history-cost">
        {isError && <ErrorAlert message="Failed to load totals." />}
        {!rows && !isError && <p className="text-sm text-ink-muted">Loading&hellip;</p>}
        {rows && (
          <>
            <TotalsChart rows={rows} bucket={bucket} />
            <TotalsTable view={{ bucket, rows }} sorting={{ sort, onSort: setSort }} onSelectRow={drillInto} />
          </>
        )}
      </div>
    </div>
  );
}

export function ReportingPage() {
  const { siteId } = useParams<{ siteId: string }>();

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <h1 className="font-display text-2xl font-semibold text-ink">Reporting</h1>
      <HistoryAndCost siteId={Number(siteId)} />
    </div>
  );
}
