import { type FormEvent, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { BackLink } from "../layout/BackLink";
import { ConditionalCreditForm, ConditionalCreditList } from "../billing/ConditionalCredits";
import { CheckboxGroup, DAY_OPTIONS, MONTH_OPTIONS } from "../billing/CheckboxGroup";
import { tariffHeading } from "../billing/tariffHeading";
import { useSources } from "../sources/useSources";
import {
  useBillingCycles,
  useCreateBillingCycle,
  useCreateDemandWindow,
  useCreateExportAdjustmentWindow,
  useCreateMonthlyFeeDiscountWindow,
  useCreateTariff,
  useDemandCharges,
  useDemandWindows,
  useEndBillingCycle,
  useEndTariff,
  useExportAdjustmentWindows,
  useMonthlyFeeDiscountWindows,
  useTariffs,
} from "../billing/useBilling";
import {
  errorClass,
  formatCents,
  byLabel,
  formatDaysOfWeek,
  formatMonths,
  formatTime,
  inputClass,
    primaryButtonClass,
} from "../billing/billingUi";

const endLinkClass = "text-sm text-ink-muted underline hover:text-ink";
// Applied to anything shown but no longer active (e.g. an expired fee
// discount window) -- kept visible rather than removed, but visually
// struck through and muted.
const disabledClass = "text-ink-muted line-through";

function isActive(entity: { effective_to: string | null }): boolean {
  return !entity.effective_to;
}

function isExpired(endsAt: string): boolean {
  return new Date(endsAt).getTime() <= Date.now();
}

function formatSignedCents(value: string): string {
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
}

// The monthly fee and its discount windows are entered/displayed in dollars
// (unlike every other tariff field, which is cents) since they're a large
// human-facing amount -- $25.00 reads better than 2500c. Stored/sent as
// cents regardless, same as everything else cost.py sums in cents.
function formatCentsAsDollars(value: string): string {
  return `$${(Number(value) / 100).toFixed(2)}`;
}

function dollarsToCents(value: string): number {
  return Math.round(Number(value) * 100);
}

function DemandWindowsList({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const { data: windows } = useDemandWindows(siteId, tariffId);

  return (
    <ul className="space-y-2">
      {byLabel(windows).map((w) => (
        <li key={w.id} className="space-y-0.5 rounded-md border border-line/60 px-3 py-1.5">
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-ink">{w.label}</span>
            <span className="font-display tabular-nums text-ink">{formatCents(w.rate_c_per_kw_per_day)}c/kW/day</span>
          </div>
          <p className="text-xs text-ink-muted">
            {formatTime(w.start_time)}–{formatTime(w.end_time)} · {formatDaysOfWeek(w.days_of_week)} ·{" "}
            {formatMonths(w.months)} · every {w.measurement_interval_minutes} min
          </p>
        </li>
      ))}
    </ul>
  );
}

function DemandWindowForm({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const [label, setLabel] = useState("");
  const [months, setMonths] = useState<number[]>([]);
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>([]);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [rate, setRate] = useState("");
  const createWindow = useCreateDemandWindow(siteId, tariffId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      label,
      months,
      days_of_week: daysOfWeek,
      start_time: startTime,
      end_time: endTime,
      rate_c_per_kw_per_day: Number(rate),
    });
  }

  return (
    <div className="ml-2 space-y-2 border-l-2 border-line pl-4">
      {createWindow.isError && (
        <p role="alert" className={errorClass}>
          {createWindow.error instanceof Error
            ? createWindow.error.message
            : "Failed to create demand window. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Window label"
          aria-label="Window label"
          className={inputClass}
          required
        />
        <CheckboxGroup legend="Months" options={MONTH_OPTIONS} selection={{ selected: months, onChange: setMonths }} />
        <CheckboxGroup legend="Days of week" options={DAY_OPTIONS} selection={{ selected: daysOfWeek, onChange: setDaysOfWeek }} />
        <input
          value={startTime}
          onChange={(e) => setStartTime(e.target.value)}
          placeholder="Start time (HH:MM)"
          aria-label="Start time"
          className={inputClass}
          required
        />
        <input
          value={endTime}
          onChange={(e) => setEndTime(e.target.value)}
          placeholder="End time (HH:MM)"
          aria-label="End time"
          className={inputClass}
          required
        />
        <input
          value={rate}
          onChange={(e) => setRate(e.target.value)}
          placeholder="Rate (cents/kW/day)"
          aria-label="Rate"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <button
          type="submit"
          disabled={createWindow.isPending || months.length === 0 || daysOfWeek.length === 0}
          className={primaryButtonClass}
        >
          Add demand window
        </button>
      </form>
    </div>
  );
}

function ExportAdjustmentWindowsList({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const { data: windows } = useExportAdjustmentWindows(siteId, tariffId);

  return (
    <ul className="space-y-2">
      {byLabel(windows).map((w) => (
        <li key={w.id} className="space-y-0.5 rounded-md border border-line/60 px-3 py-1.5">
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-ink">{w.label}</span>
            <span className="font-display tabular-nums text-ink">{formatSignedCents(w.adjustment_c_per_kwh)}c/kWh</span>
          </div>
          <p className="text-xs text-ink-muted">
            {formatTime(w.start_time)}–{formatTime(w.end_time)} · {formatDaysOfWeek(w.days_of_week)} ·{" "}
            {formatMonths(w.months)}
          </p>
        </li>
      ))}
    </ul>
  );
}

function ExportAdjustmentWindowForm({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const [label, setLabel] = useState("");
  const [months, setMonths] = useState<number[]>([]);
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>([]);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [adjustment, setAdjustment] = useState("");
  const createWindow = useCreateExportAdjustmentWindow(siteId, tariffId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      label,
      months,
      days_of_week: daysOfWeek,
      start_time: startTime,
      end_time: endTime,
      adjustment_c_per_kwh: Number(adjustment),
    });
  }

  return (
    <div className="ml-2 space-y-2 border-l-2 border-line pl-4">
      {createWindow.isError && (
        <p role="alert" className={errorClass}>
          {createWindow.error instanceof Error
            ? createWindow.error.message
            : "Failed to create export adjustment window. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Adjustment label"
          aria-label="Adjustment label"
          className={inputClass}
          required
        />
        <CheckboxGroup legend="Export months" options={MONTH_OPTIONS} selection={{ selected: months, onChange: setMonths }} />
        <CheckboxGroup legend="Export days of week" options={DAY_OPTIONS} selection={{ selected: daysOfWeek, onChange: setDaysOfWeek }} />
        <input
          value={startTime}
          onChange={(e) => setStartTime(e.target.value)}
          placeholder="Export start time (HH:MM)"
          aria-label="Export start time"
          className={inputClass}
          required
        />
        <input
          value={endTime}
          onChange={(e) => setEndTime(e.target.value)}
          placeholder="Export end time (HH:MM)"
          aria-label="Export end time"
          className={inputClass}
          required
        />
        <input
          value={adjustment}
          onChange={(e) => setAdjustment(e.target.value)}
          placeholder="Adjustment (+incentive / -disincentive, cents/kWh)"
          aria-label="Adjustment"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <button
          type="submit"
          disabled={createWindow.isPending || months.length === 0 || daysOfWeek.length === 0}
          className={primaryButtonClass}
        >
          Add export adjustment
        </button>
      </form>
    </div>
  );
}

function MonthlyFeeDiscountWindowsList({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const { data: windows } = useMonthlyFeeDiscountWindows(siteId, tariffId);

  return (
    <ul className="space-y-2">
      {byLabel(windows).map((w) => (
        <li
          key={w.id}
          className={`space-y-0.5 rounded-md border border-line/60 px-3 py-1.5 ${isExpired(w.ends_at) ? disabledClass : ""}`}
        >
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className={isExpired(w.ends_at) ? "line-through" : "text-ink"}>{w.label}</span>
            <span
              className={`font-display tabular-nums ${isExpired(w.ends_at) ? "line-through" : "text-ink"}`}
            >
              -{formatCentsAsDollars(w.discounted_fee_c)}/month
            </span>
          </div>
          <p className={isExpired(w.ends_at) ? "text-xs" : "text-xs text-ink-muted"}>
            {new Date(w.starts_at).toLocaleDateString()} – {new Date(w.ends_at).toLocaleDateString()}
          </p>
        </li>
      ))}
    </ul>
  );
}

function MonthlyFeeDiscountWindowForm({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const [label, setLabel] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [discountedFee, setDiscountedFee] = useState("");
  const createWindow = useCreateMonthlyFeeDiscountWindow(siteId, tariffId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      label,
      starts_at: `${startsAt}T00:00:00Z`,
      ends_at: `${endsAt}T00:00:00Z`,
      discounted_fee_c: dollarsToCents(discountedFee),
    });
  }

  return (
    <div className="ml-2 space-y-2 border-l-2 border-line pl-4">
      {createWindow.isError && (
        <p role="alert" className={errorClass}>
          {createWindow.error instanceof Error
            ? createWindow.error.message
            : "Failed to create fee discount. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Discount label"
          aria-label="Discount label"
          className={inputClass}
          required
        />
        <input
          value={startsAt}
          onChange={(e) => setStartsAt(e.target.value)}
          placeholder="Discount starts (YYYY-MM-DD)"
          aria-label="Discount starts (YYYY-MM-DD)"
          className={inputClass}
          required
        />
        <input
          value={endsAt}
          onChange={(e) => setEndsAt(e.target.value)}
          placeholder="Discount ends (YYYY-MM-DD)"
          aria-label="Discount ends (YYYY-MM-DD)"
          className={inputClass}
          required
        />
        <input
          value={discountedFee}
          onChange={(e) => setDiscountedFee(e.target.value)}
          placeholder="Discounted fee ($/month)"
          aria-label="Discounted fee"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <div className="basis-full" />
        <button type="submit" disabled={createWindow.isPending} className={primaryButtonClass}>
          Add fee discount
        </button>
      </form>
    </div>
  );
}

function TariffSection({ siteId }: { siteId: number }) {
  const { data: tariffs } = useTariffs(siteId);
  const { data: sources } = useSources(siteId);
  const [serviceAvailability, setServiceAvailability] = useState("");
  const [monthlyFee, setMonthlyFee] = useState("");
  const [expandedTariffId, setExpandedTariffId] = useState<number | null>(null);
  const createTariff = useCreateTariff(siteId);
  const endTariff = useEndTariff(siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createTariff.mutate({
      effective_from: new Date().toISOString(),
      service_availability_c_per_day: Number(serviceAvailability),
      ...(monthlyFee.trim() !== "" ? { monthly_fee_c: dollarsToCents(monthlyFee) } : {}),
    });
  }

  const sortedTariffs = useMemo(
    () =>
      [...(tariffs ?? [])].sort((a, b) => {
        if (isActive(a) !== isActive(b)) return isActive(a) ? -1 : 1;
        return new Date(b.effective_from).getTime() - new Date(a.effective_from).getTime();
      }),
    [tariffs],
  );

  return (
    <section className="space-y-4 rounded-xl border border-line bg-surface p-5">
      <h2 className="font-display text-lg font-semibold text-ink">{tariffHeading(sources)}</h2>
      {endTariff.isError && (
        <p role="alert" className={errorClass}>
          {endTariff.error instanceof Error ? endTariff.error.message : "Failed to end the tariff. Please try again."}
        </p>
      )}
      {tariffs && tariffs.length === 0 && (
        <>
          <p className="text-ink">No tariffs yet.</p>
          <p className="text-sm text-ink-muted">
            Add a tariff first — demand windows (the time-of-day/month periods and their cents/kW/day rate) are
            configured per-tariff, and the form for them appears once a tariff exists.
          </p>
        </>
      )}
      <ul className="space-y-2">
        {sortedTariffs.map((tariff) => {
          const hasMonthlyFee = tariff.monthly_fee_c !== null;
          return isActive(tariff) ? (
            <li key={tariff.id} className="space-y-2 rounded-md border border-line p-3">
              <div className="flex items-center justify-between">
                <span className="text-ink">
                  <span className="font-display tabular-nums">{formatCents(tariff.service_availability_c_per_day)}c/day</span>
                  {hasMonthlyFee && (
                    <>
                      {" · "}
                      <span className="font-display tabular-nums">{formatCentsAsDollars(tariff.monthly_fee_c as string)}/month</span>
                    </>
                  )}{" "}
                  from {new Date(tariff.effective_from).toLocaleDateString()}
                </span>
                <button onClick={() => endTariff.mutate(tariff.id)} className={endLinkClass}>
                  End this tariff
                </button>
              </div>
              <div className="space-y-2">
                <DemandWindowsList siteId={siteId} tariffId={tariff.id} />
                <ExportAdjustmentWindowsList siteId={siteId} tariffId={tariff.id} />
                <ConditionalCreditList siteId={siteId} tariffId={tariff.id} />
                {hasMonthlyFee && <MonthlyFeeDiscountWindowsList siteId={siteId} tariffId={tariff.id} />}
              </div>
              <div className="space-y-4">
                <DemandWindowForm siteId={siteId} tariffId={tariff.id} />
                <ExportAdjustmentWindowForm siteId={siteId} tariffId={tariff.id} />
                <ConditionalCreditForm siteId={siteId} tariffId={tariff.id} />
                {hasMonthlyFee && <MonthlyFeeDiscountWindowForm siteId={siteId} tariffId={tariff.id} />}
              </div>
            </li>
          ) : (
            <li key={tariff.id} className={`rounded-md border border-line/40 text-sm ${disabledClass}`}>
              <button
                type="button"
                onClick={() => setExpandedTariffId(expandedTariffId === tariff.id ? null : tariff.id)}
                aria-expanded={expandedTariffId === tariff.id}
                className="w-full px-3 py-1.5 text-left"
              >
                <span className="font-display tabular-nums line-through">
                  {formatCents(tariff.service_availability_c_per_day)}c/day
                </span>{" "}
                from {new Date(tariff.effective_from).toLocaleDateString()} until{" "}
                {tariff.effective_to && new Date(tariff.effective_to).toLocaleDateString()}
              </button>
              {expandedTariffId === tariff.id && (
                <div className="border-t border-line/40 p-3 space-y-2">
                  <DemandWindowsList siteId={siteId} tariffId={tariff.id} />
                  <ExportAdjustmentWindowsList siteId={siteId} tariffId={tariff.id} />
                  <ConditionalCreditList siteId={siteId} tariffId={tariff.id} />
                  {hasMonthlyFee && <MonthlyFeeDiscountWindowsList siteId={siteId} tariffId={tariff.id} />}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {createTariff.isError && (
        <p role="alert" className={errorClass}>
          {createTariff.error instanceof Error ? createTariff.error.message : "Failed to create tariff. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <input
          value={serviceAvailability}
          onChange={(e) => setServiceAvailability(e.target.value)}
          placeholder="Service availability (cents/day)"
          aria-label="Service availability"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <input
          value={monthlyFee}
          onChange={(e) => setMonthlyFee(e.target.value)}
          placeholder="Monthly fee ($/month)"
          aria-label="Monthly fee"
          inputMode="decimal"
          className={inputClass}
        />
        <button type="submit" disabled={createTariff.isPending} className={primaryButtonClass}>
          Add tariff
        </button>
      </form>
    </section>
  );
}

function BillingCycleSection({ siteId }: { siteId: number }) {
  const { data: cycles } = useBillingCycles(siteId);
  const [cycleMonths, setCycleMonths] = useState("");
  const [anchorDay, setAnchorDay] = useState("");
  const createCycle = useCreateBillingCycle(siteId);
  const endCycle = useEndBillingCycle(siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createCycle.mutate({
      effective_from: new Date().toISOString(),
      cycle_months: Number(cycleMonths),
      anchor_day: Number(anchorDay),
    });
  }

  return (
    <section className="space-y-4 rounded-xl border border-line bg-surface p-5">
      <h2 className="font-display text-lg font-semibold text-ink">Billing cycle</h2>
      {endCycle.isError && (
        <p role="alert" className={errorClass}>
          {endCycle.error instanceof Error ? endCycle.error.message : "Failed to end the billing cycle. Please try again."}
        </p>
      )}
      {cycles && cycles.length === 0 && <p className="text-ink">No billing cycles yet.</p>}
      <ul className="space-y-2">
        {(cycles ?? []).map((cycle) => (
          <li key={cycle.id} className="flex items-center justify-between rounded-md border border-line p-3">
            <span className="text-ink">
              Every {cycle.cycle_months} month(s), anchored on day {cycle.anchor_day}, from{" "}
              {new Date(cycle.effective_from).toLocaleDateString()}
              {cycle.effective_to && ` until ${new Date(cycle.effective_to).toLocaleDateString()}`}
            </span>
            {isActive(cycle) && (
              <button onClick={() => endCycle.mutate(cycle.id)} className={endLinkClass}>
                End this cycle
              </button>
            )}
          </li>
        ))}
      </ul>
      {createCycle.isError && (
        <p role="alert" className={errorClass}>
          {createCycle.error instanceof Error
            ? createCycle.error.message
            : "Failed to create billing cycle. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <input
          value={cycleMonths}
          onChange={(e) => setCycleMonths(e.target.value)}
          placeholder="Cycle length (months)"
          aria-label="Cycle length"
          inputMode="numeric"
          className={inputClass}
          required
        />
        <input
          value={anchorDay}
          onChange={(e) => setAnchorDay(e.target.value)}
          placeholder="Anchor day (1-31)"
          aria-label="Anchor day"
          inputMode="numeric"
          className={inputClass}
          required
        />
        <button type="submit" disabled={createCycle.isPending} className={primaryButtonClass}>
          Add billing cycle
        </button>
      </form>
    </section>
  );
}

function DemandChargesSection({ siteId }: { siteId: number }) {
  const { data: demandCharges } = useDemandCharges(siteId);

  return (
    <section className="space-y-4 rounded-xl border border-line bg-surface p-5">
      <h2 className="font-display text-lg font-semibold text-ink">Demand charges</h2>
      {demandCharges && demandCharges.length === 0 && (
        <p className="text-sm text-ink-muted">
          No demand charges yet — these are calculated once enough billing-period data has been collected.
        </p>
      )}
      <ul className="space-y-2">
        {(demandCharges ?? []).map((charge) => (
          <li key={charge.id} className="rounded-md border border-line p-3 text-ink">
            <span className="font-display tabular-nums">${(Number(charge.charge_amount) / 100).toFixed(2)}</span> for{" "}
            {new Date(charge.billing_period_start).toLocaleDateString()} –{" "}
            {new Date(charge.billing_period_end).toLocaleDateString()}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function BillingPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-2">
        <BackLink to={`/sites/${siteId}`} />
        <h1 className="font-display text-2xl font-semibold text-ink">Billing</h1>
      </div>
      <TariffSection siteId={parsedSiteId} />
      <BillingCycleSection siteId={parsedSiteId} />
      <DemandChargesSection siteId={parsedSiteId} />
    </div>
  );
}
