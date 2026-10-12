import { render, screen } from "@testing-library/react";
import Markdown from "react-markdown";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMarkdownComponents } from "./markdownComponents";

function renderMarkdown(source: string) {
  return render(
    <MemoryRouter>
      <Markdown components={createMarkdownComponents({ base: "/help", currentSlug: "index" })}>{source}</Markdown>
    </MemoryRouter>,
  );
}

describe("markdown link and image renderers", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens http(s) links in a new tab with noopener noreferrer", () => {
    renderMarkdown("[Amber](https://example.com/amber)");
    const link = screen.getByRole("link", { name: "Amber" });
    expect(link).toHaveAttribute("href", "https://example.com/amber");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders a link to an unknown chapter as plain text and warns once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderMarkdown("[a](99-gone.md) and [b](99-gone.md)");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText(/a and b/)).toBeInTheDocument();
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("does not render raw HTML", () => {
    renderMarkdown("<script>alert(1)</script><b>bold</b>");
    expect(document.querySelector("script, b")).toBeNull();
  });

  it("falls back to the alt text when an image is not bundled", () => {
    renderMarkdown("![Missing shot](images/nope.png)");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("Missing shot")).toBeInTheDocument();
  });
});
