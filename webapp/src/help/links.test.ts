import { describe, expect, it } from "vitest";
import { chapterPath, classifyLink } from "./links";

describe("classifyLink", () => {
  it("treats http(s) links as external", () => {
    expect(classifyLink("https://example.com/x", "index")).toEqual({ kind: "external", href: "https://example.com/x" });
  });

  it("maps chapter files and keeps the hash", () => {
    expect(classifyLink("06-billing.md#tariffs", "index")).toEqual({ kind: "chapter", slug: "06-billing", hash: "#tariffs" });
    expect(classifyLink("index.md", "03-sites")).toEqual({ kind: "chapter", slug: "index", hash: "" });
  });

  it("resolves a bare anchor against the current chapter", () => {
    expect(classifyLink("#demand-charges", "06-billing")).toEqual({ kind: "chapter", slug: "06-billing", hash: "#demand-charges" });
  });

  it("flags links to chapters that do not exist", () => {
    expect(classifyLink("99-nope.md", "index")).toEqual({ kind: "unknown" });
    expect(classifyLink("../secret.txt", "index")).toEqual({ kind: "unknown" });
  });
});

describe("chapterPath", () => {
  it("puts the index at the base", () => {
    expect(chapterPath("/help", "index")).toBe("/help");
    expect(chapterPath("/sites/2/help", "09-glossary")).toBe("/sites/2/help/09-glossary");
  });
});
