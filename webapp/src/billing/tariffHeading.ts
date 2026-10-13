import type { Source } from "../sources/useSources";

const DEFAULT_HEADING = "Tariffs";

function isActivePricingSource(source: Source, now: Date): boolean {
  return (
    source.kind === "pricing" &&
    source.enabled &&
    (source.effective_to == null || new Date(source.effective_to) > now)
  );
}

/** The Billing section heading, named after whoever supplies the site's
 * prices. A site has at most one active pricing source. */
export function tariffHeading(sources: Source[] | undefined, now: Date = new Date()): string {
  const active = sources?.find((source) => isActivePricingSource(source, now));
  if (!active) return DEFAULT_HEADING;
  if (active.provider === "amber") return "Amber Tariffs";
  const name = active.name?.trim();
  return name ? `${name} Tariffs` : DEFAULT_HEADING;
}
