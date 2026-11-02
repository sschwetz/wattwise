import type { TotalsBucket, TotalsRow } from "../dashboard/useHistoricalData";
import { formatRowTime } from "./totalsFormat";
import { summarizeTotals, type TotalsSummary } from "./totalsSummary";
import { DEFAULT_SORT, nextSort, type SortKey, type SortState, sortTotals } from "./totalsSort";

interface Column {
  key: SortKey;
  label: string;
}

const TIME_LABELS: Record<TotalsBucket, string> = { day: "Date", hour: "Hour", minute: "Time" };

const ENERGY_COLUMNS: Column[] = [
  { key: "production_kwh", label: "Total production" },
  { key: "consumption_kwh", label: "Total consumption" },
  { key: "solar_to_battery_kwh", label: "Solar to battery" },
  { key: "grid_to_battery_kwh", label: "Grid to battery" },
];

function columnsFor(bucket: TotalsBucket): Column[] {
  const first: Column[] = [
    { key: "start", label: TIME_LABELS[bucket] },
    { key: "cost_c", label: "Cost" },
  ];
  if (bucket === "day") {
    return [...first, ...ENERGY_COLUMNS, { key: "demand_charge_c", label: "Total demand charge" }];
  }
  return [
    ...first,
    { key: "avg_import_price_c", label: "Avg import price" },
    { key: "avg_export_price_c", label: "Avg export price" },
    ...ENERGY_COLUMNS,
  ];
}

/** Dollars with the sign in front (-$1.00). Rounds to whole cents first so a fraction of a cent is "$0.00", not "-$0.00". */
function formatDollars(cents: number): string {
  const whole = Math.round(cents);
  return `${whole < 0 ? "-" : ""}$${(Math.abs(whole) / 100).toFixed(2)}`;
}
const formatKwh = (kwh: number) => `${kwh.toFixed(2)} kWh`;
const formatPrice = (cents: number | null) => (cents === null ? "–" : `${cents.toFixed(1)} c/kWh`);

function EstimateIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      className="mr-1 inline-block h-3.5 w-3.5 align-text-bottom text-solar"
      role="img"
      aria-label="Estimated"
    >
      <title>Estimated: the billing period's peak demand can still change</title>
      <path d="M8 1.5 15 14H1z" fill="currentColor" />
    </svg>
  );
}

function DemandChargeCell({ row }: { row: Pick<TotalsRow, "demand_charge_c" | "demand_charge_estimated"> }) {
  if (row.demand_charge_c === null) {
    return <>&ndash;</>;
  }
  return (
    <>
      {row.demand_charge_estimated && <EstimateIcon />}
      {formatDollars(row.demand_charge_c)}
    </>
  );
}

function TotalsFooter({ columns, summary }: { columns: Column[]; summary: TotalsSummary }) {
  return (
    <tfoot>
      <tr className="border-t-2 border-line font-medium text-ink">
        {columns.map((column) => (
          <td key={column.key} className="px-2 py-2">
            <SummaryCell summary={summary} column={column} />
          </td>
        ))}
      </tr>
    </tfoot>
  );
}

function SummaryCell({ summary, column }: { summary: TotalsSummary; column: Column }) {
  switch (column.key) {
    case "start":
      return <>Total</>;
    case "cost_c":
      return <>{formatDollars(summary.cost_c)}</>;
    case "avg_import_price_c":
      return <>{formatPrice(summary.avg_import_price_c)}</>;
    case "avg_export_price_c":
      return <>{formatPrice(summary.avg_export_price_c)}</>;
    case "demand_charge_c":
      return <DemandChargeCell row={summary} />;
    default:
      return <>{formatKwh(summary[column.key])}</>;
  }
}

function Cell({ row, column, bucket }: { row: TotalsRow; column: Column; bucket: TotalsBucket }) {
  switch (column.key) {
    case "start":
      return <>{formatRowTime(row, bucket)}</>;
    case "cost_c":
      return <>{formatDollars(row.cost_c)}</>;
    case "avg_import_price_c":
      return <>{formatPrice(row.avg_import_price_c)}</>;
    case "avg_export_price_c":
      return <>{formatPrice(row.avg_export_price_c)}</>;
    case "demand_charge_c":
      return <DemandChargeCell row={row} />;
    default:
      return <>{formatKwh(row[column.key])}</>;
  }
}

/**
 * One row per day, hour or minute. Clicking a day or an hour row drills into
 * it through `onSelectRow`; minute rows do nothing. The sort lives with the
 * caller so it carries over from one level to the next. A sorted column the
 * level does not have (such as demand charge on an hourly view) falls back to
 * the default sort.
 */
export function TotalsTable({
  view,
  sorting,
  onSelectRow,
}: {
  view: { bucket: TotalsBucket; rows: TotalsRow[] };
  sorting: { sort: SortState; onSort: (sort: SortState) => void };
  onSelectRow: (row: TotalsRow) => void;
}) {
  const { bucket, rows } = view;
  const columns = columnsFor(bucket);
  const sort = columns.some((column) => column.key === sorting.sort.key) ? sorting.sort : DEFAULT_SORT;
  const drillable = bucket !== "minute";

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="totals-table">
        <thead>
          <tr className="border-b border-line text-left text-ink-muted">
            {columns.map(({ key, label }) => (
              <th
                key={key}
                scope="col"
                aria-sort={sort.key === key ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
                className="px-2 py-2 font-medium"
              >
                <button type="button" onClick={() => sorting.onSort(nextSort(sort, key))} className="hover:text-ink">
                  {label}
                  {sort.key === key && (sort.direction === "asc" ? " ▲" : " ▼")}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortTotals(rows, sort).map((row) => (
            <tr
              key={row.start}
              onClick={drillable ? () => onSelectRow(row) : undefined}
              onKeyDown={drillable ? (e) => e.key === "Enter" && onSelectRow(row) : undefined}
              tabIndex={drillable ? 0 : undefined}
              className={`border-b border-line/60 text-ink ${drillable ? "cursor-pointer hover:bg-surface-2" : ""}`}
            >
              {columns.map((column) => (
                <td key={column.key} className="px-2 py-1.5">
                  <Cell row={row} column={column} bucket={bucket} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {rows.length > 0 && <TotalsFooter columns={columns} summary={summarizeTotals(rows)} />}
      </table>
    </div>
  );
}
