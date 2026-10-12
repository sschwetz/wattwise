const PALETTE: { bg: string; text: string }[] = [
  { bg: "bg-solar", text: "text-[#1a1203]" },
  { bg: "bg-flow", text: "text-[#06251c]" },
  { bg: "bg-grid", text: "text-[#0b1a33]" },
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
  imageUrl?: string | null;
}

/** Initials-only fallback when no image has been uploaded yet; see
 * useAvatarImage for how imageUrl is produced. */
export function Avatar({ name, email, imageUrl }: AvatarProps) {
  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        aria-hidden="true"
        className="h-9 w-9 shrink-0 rounded-full object-cover"
      />
    );
  }

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
