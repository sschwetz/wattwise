const PALETTE: { bg: string; text: string }[] = [
  { bg: "bg-solar", text: "text-[#1a1203]" },
  { bg: "bg-flow", text: "text-white" },
  { bg: "bg-grid", text: "text-white" },
  { bg: "bg-danger", text: "text-white" },
];

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function initialsFromName(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]!.toUpperCase())
    .join("");
}

function initialsFromEmail(email: string): string {
  const localPart = email.split("@")[0] ?? "";
  return localPart.length > 0 ? localPart[0]!.toUpperCase() : "?";
}

export interface AvatarProps {
  name: string | null;
  email: string;
}

/** Initials-only avatar -- there is no uploaded-image support yet (see
 * sub-project 4 in docs/superpowers/specs/2026-10-12-header-account-menu-design.md). */
export function Avatar({ name, email }: AvatarProps) {
  const trimmedName = name?.trim();
  const hasName = !!trimmedName;
  const initials = hasName ? initialsFromName(trimmedName) : initialsFromEmail(email);
  const colorKey = hasName ? trimmedName : email;
  const palette = PALETTE[hashString(colorKey) % PALETTE.length]!;

  return (
    <span
      aria-hidden="true"
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-display text-sm font-semibold ${palette.bg} ${palette.text}`}
    >
      {initials}
    </span>
  );
}
