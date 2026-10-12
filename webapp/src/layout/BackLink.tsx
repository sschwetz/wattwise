import { Link } from "react-router-dom";

function BackArrowIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M9.5 3.5 4 9l5.5 5.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** A left-pointing arrow back to a fixed parent screen (not browser
 * history) -- shown on every protected page except the Dashboard, which has
 * no parent to return to. */
export function BackLink({ to }: { to: string }) {
  return (
    <Link
      to={to}
      aria-label="Back"
      className="inline-flex items-center justify-center rounded-md p-1.5 text-ink-muted hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
    >
      <BackArrowIcon />
    </Link>
  );
}
