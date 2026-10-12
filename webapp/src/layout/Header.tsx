import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { useMe } from "../account/useAccount";
import { useAvatarImage } from "../account/useAvatarImage";
import { useAuth } from "../auth/AuthContext";
import { Avatar } from "./Avatar";
import { Logo } from "./Logo";

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Header() {
  const { data: me } = useMe();
  const avatarUrl = useAvatarImage(me?.has_avatar ?? false);
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
    <header className="flex items-center justify-between gap-4 border-b border-line bg-surface px-4 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <Logo />
        <span className="hidden truncate text-sm text-ink-muted sm:inline">
          - Built by someone who got <span className="font-bold text-solar">sick</span> of being{" "}
          <span className="font-bold text-solar">mugged</span> by a{" "}
          <span className="font-bold text-white">power bill</span>.
        </span>
      </div>
      <div ref={containerRef} className="relative shrink-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={label}
          className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1 text-ink hover:bg-surface-2"
        >
          <Avatar name={displayName} email={email} imageUrl={avatarUrl} />
          <span className="max-w-[12rem] truncate text-sm font-medium sm:max-w-xs">{label}</span>
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
