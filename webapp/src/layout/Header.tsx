import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { useMe } from "../account/useAccount";
import { useAuth } from "../auth/AuthContext";
import { Avatar } from "./Avatar";

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Header() {
  const { data: me } = useMe();
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const displayName = me?.display_name ?? null;
  const email = me?.email ?? "";
  const label = displayName ?? email;

  return (
    <header className="flex items-center justify-end border-b border-line bg-surface px-4 py-2">
      <div ref={containerRef} className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={label}
          className="flex items-center gap-2 rounded-md px-2 py-1 text-ink hover:bg-surface-2"
        >
          <Avatar name={displayName} email={email} />
          <span className="text-sm font-medium">{label}</span>
          <ChevronIcon />
        </button>
        {open && (
          <div role="menu" className="absolute right-0 top-full z-10 mt-1 w-48 rounded-md border border-line bg-surface py-1 shadow-lg">
            <NavLink
              to="/account"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="block px-3 py-2 text-sm text-ink hover:bg-surface-2"
            >
              Account settings
            </NavLink>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                logout();
              }}
              className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-surface-2"
            >
              Log off
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
