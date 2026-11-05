import { isStale, formatRunTime } from "./runFormat";
import { useNow } from "../dashboard/useNow";
import { cardClass } from "./styles";
import type { ActorInstance, DeviceStatePayload } from "./types";

const TICK_MS = 30_000;
const STALE_TITLE = "The actor has not reported for more than 2 minutes.";

const temp = (c: number) => `${Number(c.toFixed(1))}°C`;
const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  );
}

function rows(state: DeviceStatePayload): Array<[string, string]> {
  const out: Array<[string, string]> = [
    ["Power", state.is_on ? "On" : "Off"],
    ["Mode", titleCase(state.mode)],
    ["Cool setpoint", temp(state.cool_setpoint_c)],
  ];
  if (state.mode.toUpperCase() === "HEAT") out.push(["Heat setpoint", temp(state.heat_setpoint_c)]);
  if (state.indoor_temp_c !== null) out.push(["Indoor", temp(state.indoor_temp_c)]);
  if (state.outdoor_temp_c !== null) out.push(["Outdoor", temp(state.outdoor_temp_c)]);
  if (state.humidity_pct !== null) out.push(["Humidity", `${Math.round(state.humidity_pct)}%`]);
  return out;
}

function Zones({ state }: { state: DeviceStatePayload }) {
  const zones = Object.entries(state.enabled_zones ?? {});
  if (zones.length === 0) return null;
  return (
    <ul className="space-y-1 text-sm text-ink">
      {zones.map(([name, on]) => {
        const t = state.zone_temps_c?.[name];
        return <li key={name}>{`${name}: ${on ? "On" : "Off"}${t === undefined ? "" : `, ${temp(t)}`}`}</li>;
      })}
    </ul>
  );
}

function StateBody({ state, timezone, now }: { state: DeviceStatePayload; timezone: string; now: Date }) {
  return (
    <>
      <dl className="space-y-1">
        {rows(state).map(([label, value]) => <Row key={label} label={label} value={value} />)}
      </dl>
      <Zones state={state} />
      <p className="text-xs text-ink-muted">
        Updated <time dateTime={state.updated_at}>{formatRunTime(state.updated_at, timezone, now)}</time>
      </p>
    </>
  );
}

/** Read-only view of what the actor last reported about the unit. */
export function UnitStatePanel({ instance, timezone }: { instance: ActorInstance; timezone: string }) {
  const now = useNow(TICK_MS);
  const state = instance.latest_readings;
  return (
    <section className={`${cardClass} space-y-3`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-lg font-medium text-ink">Air conditioner</h2>
        {isStale(instance.last_seen_at, now) && <span title={STALE_TITLE} className="text-xs text-ink-muted">Not updating</span>}
      </div>
      {state ? <StateBody state={state} timezone={timezone} now={now} /> : <p className="text-sm text-ink-muted">No device state yet.</p>}
    </section>
  );
}
