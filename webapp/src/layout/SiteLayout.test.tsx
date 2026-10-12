import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { SiteLayout } from "./SiteLayout";

function renderLayout(initialPath = "/sites/7") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/sites" element={<p>Site picker</p>} />
        <Route path="/sites/:siteId" element={<SiteLayout />}>
          <Route index element={<p>Dashboard content</p>} />
          <Route path="sources" element={<p>Sources content</p>} />
          <Route path="billing" element={<p>Billing content</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("SiteLayout", () => {
  it("renders the matched child route inside its chrome", () => {
    renderLayout();
    expect(screen.getByText("Dashboard content")).toBeInTheDocument();
  });

  it("links to each site-scoped page for the current site id", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/sites/7");
    expect(screen.getByRole("link", { name: "Sources" })).toHaveAttribute("href", "/sites/7/sources");
    expect(screen.getByRole("link", { name: "Billing" })).toHaveAttribute("href", "/sites/7/billing");
    expect(screen.getByRole("link", { name: "Switch site" })).toHaveAttribute("href", "/sites");
  });

  it("navigates to Sources when its link is clicked", async () => {
    renderLayout();
    await userEvent.click(screen.getByRole("link", { name: "Sources" }));
    expect(await screen.findByText("Sources content")).toBeInTheDocument();
    // The nav stays mounted across child-route changes.
    expect(screen.getByRole("link", { name: "Billing" })).toBeInTheDocument();
  });

  it("navigates to Billing when its link is clicked", async () => {
    renderLayout();
    await userEvent.click(screen.getByRole("link", { name: "Billing" }));
    expect(await screen.findByText("Billing content")).toBeInTheDocument();
  });

  it("navigates back to the dashboard from a sibling page", async () => {
    renderLayout("/sites/7/billing");
    expect(screen.getByText("Billing content")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Dashboard" }));
    expect(await screen.findByText("Dashboard content")).toBeInTheDocument();
  });

  it("navigates to the site switcher", async () => {
    renderLayout();
    await userEvent.click(screen.getByRole("link", { name: "Switch site" }));
    expect(await screen.findByText("Site picker")).toBeInTheDocument();
  });
});
