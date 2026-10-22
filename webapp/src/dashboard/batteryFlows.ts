import { BUCKET_STEP_MS, bucketStartFromMs, bucketStartMs, enumerateBucketStarts } from "./historyChart";
import type { DateRange } from "./useHistoricalData";
import type { Reading } from "./useLiveReadings";

interface BatteryChartInputs {
  soc: Reading[];
  charge: Reading[];
  discharge: Reading[];
  production: Reading[];
  consumption: Reading[];
  gridExport: Reading[];
}

export interface BatteryChartPoint {
  ts: number;
  /** Change in battery charge over the bucket, in percentage points; null with no SOC data. */
  socChangePct: number | null;
  solarToBatteryKw: number;
  gridToBatteryKw: number;
  batteryToHouseKw: number;
  batteryToGridKw: number;
}

const BUCKET = "5min";
const MINUTES_PER_BUCKET = BUCKET_STEP_MS[BUCKET] / 60_000;

interface Flows {
  solarToBattery: number;
  gridToBattery: number;
  batteryToHouse: number;
  batteryToGrid: number;
}

const FLOW_KEYS = ["solarToBattery", "gridToBattery", "batteryToHouse", "batteryToGrid"] as const;

function byMinute(readings: Reading[]): Map<number, number> {
  return new Map(readings.map((r) => [new Date(r.ts).getTime(), r.value]));
}

/**
 * Splits one minute's battery charge and discharge by where the energy came
 * from or went. The collectors report totals, not sources, so this is an
 * estimate: charging takes solar surplus (production minus consumption) first
 * and the rest comes from the grid; discharging goes to the grid up to the
 * minute's export, and the rest to the house.
 */
export function splitBatteryFlows(
  chargeW: number,
  dischargeW: number,
  productionW: number,
  consumptionW: number,
  gridExportW: number,
): Flows {
  const solarToBattery = Math.min(chargeW, Math.max(productionW - consumptionW, 0));
  const batteryToGrid = Math.min(dischargeW, gridExportW);
  return {
    solarToBattery,
    gridToBattery: chargeW - solarToBattery,
    batteryToHouse: dischargeW - batteryToGrid,
    batteryToGrid,
  };
}

/** Last SOC value in each bucket, by bucket start. */
function lastSocByBucket(soc: Reading[]): Map<number, number> {
  const last = new Map<number, number>();
  for (const r of [...soc].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))) {
    last.set(bucketStartMs(r.ts, BUCKET), r.value);
  }
  return last;
}

/** SOC change per bucket: its last reading minus the last reading of the
 * nearest earlier bucket that has one. The first bucket with data has no
 * earlier reading, so it shows no change. */
function socChanges(soc: Reading[], starts: number[]): Map<number, number> {
  const last = lastSocByBucket(soc);
  const changes = new Map<number, number>();
  let previous: number | undefined;
  for (const ts of starts) {
    const value = last.get(ts);
    if (value === undefined) continue;
    if (previous !== undefined) changes.set(ts, value - previous);
    previous = value;
  }
  return changes;
}

type FlowKw = Pick<BatteryChartPoint, `${keyof Flows}Kw`>;

const zeroFlowKw = (): FlowKw => ({ solarToBatteryKw: 0, gridToBatteryKw: 0, batteryToHouseKw: 0, batteryToGridKw: 0 });

/** Adds each minute's estimated flows into its 5-minute bucket as average kW. */
function averageFlowsByBucket(inputs: BatteryChartInputs): Map<number, FlowKw> {
  const production = byMinute(inputs.production);
  const consumption = byMinute(inputs.consumption);
  const gridExport = byMinute(inputs.gridExport);
  const charge = byMinute(inputs.charge);
  const discharge = byMinute(inputs.discharge);
  const buckets = new Map<number, FlowKw>();
  for (const ts of new Set([...charge.keys(), ...discharge.keys()])) {
    const flows = splitBatteryFlows(
      charge.get(ts) ?? 0,
      discharge.get(ts) ?? 0,
      production.get(ts) ?? 0,
      consumption.get(ts) ?? 0,
      gridExport.get(ts) ?? 0,
    );
    const key = bucketStartFromMs(ts, BUCKET);
    let total = buckets.get(key);
    if (!total) {
      total = zeroFlowKw();
      buckets.set(key, total);
    }
    for (const k of FLOW_KEYS) {
      total[`${k}Kw`] += flows[k] / 1000 / MINUTES_PER_BUCKET;
    }
  }
  return buckets;
}

export function buildBatteryChartData(inputs: BatteryChartInputs, range: DateRange): BatteryChartPoint[] {
  const starts = enumerateBucketStarts(range, BUCKET);
  const changes = socChanges(inputs.soc, starts);
  const flows = averageFlowsByBucket(inputs);
  return starts.map((ts) => ({ ts, socChangePct: changes.get(ts) ?? null, ...(flows.get(ts) ?? zeroFlowKw()) }));
}
