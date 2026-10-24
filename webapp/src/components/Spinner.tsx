/** A small spinning ring in the current text color. Decorative: pair it with
 * visible or screen-reader text that says what is in progress. */
export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" fill="none" className={`animate-spin ${className}`}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity={0.25} strokeWidth={3} />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth={3} strokeLinecap="round" />
    </svg>
  );
}
