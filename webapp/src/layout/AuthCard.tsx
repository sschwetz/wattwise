import type { ReactNode } from "react";

/** The quiet, single-card chrome shared by every auth/gatekeeping screen
 * (login, register, password reset, TOTP setup/verify...) -- deliberately
 * plainer than the authenticated app shell in SiteLayout. */
export function AuthCard({
  title,
  children,
  maxWidth = "max-w-sm",
  fill = false,
}: {
  title: string;
  children: ReactNode;
  maxWidth?: "max-w-sm" | "max-w-md";
  /** True for callers already nested under the protected header (e.g.
   * SitePickerPage): `min-h-screen` there would add the header's own height
   * on top of a full viewport, forcing the page taller than the screen.
   * False (default) for every public, header-less route this card also
   * serves (login, register, password reset, ...). */
  fill?: boolean;
}) {
  return (
    <div className={`flex items-center justify-center bg-bg px-4 py-12 ${fill ? "flex-1" : "min-h-screen"}`}>
      <div className={`w-full ${maxWidth} rounded-xl border border-line bg-surface p-8 shadow-sm`}>
        <p className="font-display text-lg font-semibold text-solar">WattWise</p>
        <h1 className="mt-4 font-display text-2xl font-semibold text-ink">{title}</h1>
        <div className="mt-6">{children}</div>
      </div>
    </div>
  );
}
