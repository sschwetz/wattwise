import GithubSlugger from "github-slugger";
import { describe, expect, it } from "vitest";
import { chapters, getImageUrl } from "./content";

// Guards the guide against link rot: every link, anchor and image in
// src/docs must point at something that exists.

const LINK = /(!?)\[[^\]]*\]\(([^)\s]+)\)/g;

/** Heading text as rehype-slug sees it: Markdown syntax stripped. */
function plainText(heading: string): string {
  return heading
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[`*_]/g, "")
    .trim();
}

function headingSlugs(markdown: string): Set<string> {
  const slugger = new GithubSlugger();
  const slugs = new Set<string>();
  for (const match of markdown.matchAll(/^#{1,6}\s+(.+?)\s*#*$/gm)) slugs.add(slugger.slug(plainText(match[1])));
  return slugs;
}

const slugsByChapter = Object.fromEntries(Object.entries(chapters).map(([slug, md]) => [slug, headingSlugs(md)]));

describe("help docs", () => {
  it("loads the index and all nine chapters", () => {
    expect(Object.keys(chapters)).toHaveLength(10);
  });

  for (const [slug, markdown] of Object.entries(chapters)) {
    describe(slug, () => {
      const links = [...markdown.matchAll(LINK)].map(([, bang, target]) => ({ isImage: bang === "!", target }));

      it("has no broken chapter links or anchors", () => {
        const problems: string[] = [];
        for (const { isImage, target } of links) {
          if (isImage || /^(https?:|mailto:)/.test(target)) continue;
          const [file, anchor] = target.split("#");
          const targetSlug = file === "" ? slug : file.replace(/\.md$/, "");
          if (file !== "" && !file.endsWith(".md")) problems.push(`${target}: not a .md link`);
          else if (!(targetSlug in chapters)) problems.push(`${target}: no such file`);
          else if (anchor !== undefined && !slugsByChapter[targetSlug].has(anchor)) problems.push(`${target}: no such heading`);
        }
        expect(problems).toEqual([]);
      });

      it("references only images that exist", () => {
        const missing = links.filter((l) => l.isImage && getImageUrl(l.target) === undefined).map((l) => l.target);
        expect(missing).toEqual([]);
      });
    });
  }
});
