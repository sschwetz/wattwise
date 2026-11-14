import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Bulb } from "./Bulb";

describe("Bulb", () => {
  it("renders the severity class", () => {
    const { container } = render(<Bulb severity="partial" cleared={false} />);
    expect(container.firstElementChild).toHaveClass("bulb", "bulb-partial");
    expect(container.firstElementChild).not.toHaveClass("bulb-cleared");
  });

  it("adds bulb-cleared when cleared", () => {
    const { container } = render(<Bulb severity="failure" cleared />);
    expect(container.firstElementChild).toHaveClass("bulb-failure", "bulb-cleared");
  });

  it("is hidden from assistive tech", () => {
    const { container } = render(<Bulb severity="notice" cleared={false} />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });
});
