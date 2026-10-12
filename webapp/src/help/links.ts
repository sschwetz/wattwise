import { getChapter, INDEX_SLUG } from "./content";

export type HelpLink =
  | { kind: "external"; href: string }
  | { kind: "chapter"; slug: string; hash: string }
  | { kind: "unknown" };

const EXTERNAL = /^(https?:|mailto:)/i;
const CHAPTER_FILE = /^([\w-]+)\.md$/;

/** Classifies a link from a Markdown chapter. `currentSlug` resolves same-file anchors. */
export function classifyLink(href: string, currentSlug: string): HelpLink {
  if (EXTERNAL.test(href)) return { kind: "external", href };
  const hashAt = href.indexOf("#");
  const file = hashAt === -1 ? href : href.slice(0, hashAt);
  const hash = hashAt === -1 ? "" : href.slice(hashAt);
  if (file === "") return { kind: "chapter", slug: currentSlug, hash };
  const slug = CHAPTER_FILE.exec(file)?.[1];
  if (slug === undefined || getChapter(slug) === undefined) return { kind: "unknown" };
  return { kind: "chapter", slug, hash };
}

/** `base` is "/help" or "/sites/:id/help". The index chapter lives at the base itself. */
export function chapterPath(base: string, slug: string): string {
  return slug === INDEX_SLUG ? base : `${base}/${slug}`;
}
