import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { BackLink } from "./BackLink";

describe("BackLink", () => {
  it("links back to the given destination", () => {
    render(
      <MemoryRouter>
        <BackLink to="/sites/7" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/sites/7");
  });
});
