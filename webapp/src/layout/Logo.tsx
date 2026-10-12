import { useId } from "react";

/** The WattWise brand mark: bolt-in-a-chip icon plus wordmark, matching the
 * source branding artwork's icon geometry and gradient. */
export function Logo({ className = "" }: { className?: string }) {
  const gradientId = useId();

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <svg width="28" height="28" viewBox="0 0 120 120" aria-hidden="true">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#ffd54a" />
            <stop offset="1" stopColor="#ff9f1c" />
          </linearGradient>
        </defs>
        <rect width="120" height="120" rx="28" fill="#16304f" stroke="#2a4a72" strokeWidth="2" />
        <path d="M68 14 L30 68 H58 L48 106 L92 46 H62 Z" fill={`url(#${gradientId})`} />
      </svg>
      <span className="font-display text-lg font-semibold text-solar">WattWise</span>
    </div>
  );
}
