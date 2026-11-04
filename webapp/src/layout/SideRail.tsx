import { NavLink } from "react-router-dom";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `shrink-0 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive ? "bg-surface-2 text-ink" : "text-ink-muted hover:bg-surface-2 hover:text-ink"
  }`;

export interface SideRailItem {
  to: string;
  label: string;
  end?: boolean;
}

/** A row of links on narrow screens, a left-hand column from `sm` up. */
export function SideRail({ label, items }: { label: string; items: SideRailItem[] }) {
  return (
    <nav
      aria-label={label}
      className="flex shrink-0 flex-row gap-1 overflow-x-auto sm:w-44 sm:flex-col sm:overflow-visible"
    >
      {items.map(({ to, label: text, end }) => (
        <NavLink key={to} to={to} end={end} className={linkClass}>
          {text}
        </NavLink>
      ))}
    </nav>
  );
}
