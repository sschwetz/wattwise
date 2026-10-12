/** Help guide content, bundled at build time from src/docs. */

export const INDEX_SLUG = "index";

const chapterSources = import.meta.glob<string>("../docs/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});

const imageSources = import.meta.glob<string>("../docs/images/*", {
  query: "?url",
  import: "default",
  eager: true,
});

function keyedBy(sources: Record<string, string>, strip: RegExp): Record<string, string> {
  return Object.fromEntries(Object.entries(sources).map(([path, value]) => [path.replace(strip, ""), value]));
}

/** Markdown by chapter slug: "index", "01-getting-started", ... */
export const chapters: Record<string, string> = keyedBy(chapterSources, /^.*\/|\.md$/g);

/** Hashed asset URLs by the path Markdown uses, e.g. "images/login.png". */
export const images: Record<string, string> = keyedBy(imageSources, /^.*\/docs\//);

export function getChapter(slug: string): string | undefined {
  return Object.hasOwn(chapters, slug) ? chapters[slug] : undefined;
}

export function getImageUrl(src: string): string | undefined {
  return Object.hasOwn(images, src) ? images[src] : undefined;
}
