import type { AlertSeverity } from "./types";

/** A small lamp. Failures and partials pulse while open; notices stay lit; cleared bulbs go grey inside. */
export function Bulb({ severity, cleared }: { severity: AlertSeverity; cleared: boolean }) {
  const cls = `bulb bulb-${severity}${cleared ? " bulb-cleared" : ""}`;
  return <span aria-hidden="true" className={cls} />;
}
