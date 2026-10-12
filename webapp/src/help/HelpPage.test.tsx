import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HelpPage } from "./HelpPage";

function Where() {
  const { pathname, hash } = useLocation();
  return <p data-testid="where">{pathname + hash}</p>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/help/:slug?" element={<HelpPage />} />
        <Route path="/sites/:siteId/help/:slug?" element={<HelpPage />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

describe("HelpPage", () => {
  const scrollIntoView = vi.fn();
  const scrollTo = vi.fn();

  beforeEach(() => {
    scrollIntoView.mockReset();
    scrollTo.mockReset();
    Element.prototype.scrollIntoView = scrollIntoView;
    window.scrollTo = scrollTo as typeof window.scrollTo;
  });

  it("renders the index inside an article with the chapter h1", () => {
    renderAt("/help");
    const article = screen.getByRole("article");
    expect(article).toContainElement(screen.getByRole("heading", { level: 1, name: "WattWise User Guide" }));
  });

  it("shows the contents table inside a scrolling wrapper", () => {
    renderAt("/help");
    const table = screen.getByRole("table");
    expect(table.parentElement?.className).toContain("overflow-x-auto");
    expect(screen.getByRole("link", { name: "3. Sites" })).toHaveAttribute("href", "/help/03-sites");
  });

  it("builds links under the site route when there is a site", () => {
    renderAt("/sites/7/help");
    expect(screen.getByRole("link", { name: "3. Sites" })).toHaveAttribute("href", "/sites/7/help/03-sites");
  });

  it("navigates to a chapter by clicking an internal link and scrolls to the top", async () => {
    renderAt("/help");
    await userEvent.click(screen.getByRole("link", { name: "3. Sites" }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/sites/i);
    expect(screen.getByTestId("where")).toHaveTextContent("/help/03-sites");
    expect(scrollTo).toHaveBeenCalledWith(0, 0);
  });

  it("keeps the hash and scrolls to the heading it names", async () => {
    renderAt("/help");
    await userEvent.click(screen.getByRole("link", { name: "first-day checklist" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/help/01-getting-started#first-day-checklist");
    expect(scrollIntoView).toHaveBeenCalled();
    expect(scrollIntoView.mock.contexts.at(-1)).toHaveProperty("id", "first-day-checklist");
  });

  it("scrolls to a same-file anchor without leaving the chapter", async () => {
    renderAt("/help/06-billing");
    const link = screen.getAllByRole("link").find((a) => a.getAttribute("href")?.startsWith("/help/06-billing#"));
    expect(link).toBeDefined();
    await userEvent.click(link!);
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/help\/06-billing#/);
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("resolves image sources through the asset map and lazy-loads them", () => {
    renderAt("/help/02-signing-in");
    const img = screen.getByAltText(/login card/i);
    expect(img.getAttribute("src")).toMatch(/login\.png$/);
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img.className).toContain("max-w-full");
  });

  it("shows a not-found page with a link back to the contents", async () => {
    renderAt("/sites/7/help/does-not-exist");
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Back to the help contents" }));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/sites\/7\/help$/);
    expect(screen.getByRole("heading", { level: 1, name: "WattWise User Guide" })).toBeInTheDocument();
  });
});
