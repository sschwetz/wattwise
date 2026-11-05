import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Link, MemoryRouter, RouterProvider, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useLeaveGuard } from "./useLeaveGuard";

function Guarded({ dirty }: { dirty: boolean }) {
  const guard = useLeaveGuard(dirty);
  return (
    <div>
      <p>Editor</p>
      <Link to="/other">Elsewhere</Link>
      {guard.blocked && (
        <>
          <button type="button" onClick={guard.reset}>Stay</button>
          <button type="button" onClick={guard.proceed}>Go</button>
        </>
      )}
    </div>
  );
}

function routes(dirty: boolean) {
  return (
    <Routes>
      <Route path="/edit" element={<Guarded dirty={dirty} />} />
      <Route path="/other" element={<p>Other page</p>} />
    </Routes>
  );
}

function dataRouter(dirty: boolean) {
  const router = createMemoryRouter([{ path: "*", element: routes(dirty) }], { initialEntries: ["/start", "/edit"], initialIndex: 1 });
  render(<RouterProvider router={router} />);
  return router;
}

/** True when the page asked the browser to confirm leaving. */
function leaveIsQuestioned(): boolean {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

describe("useLeaveGuard", () => {
  it("works without a data router and only guards the tab", async () => {
    const { rerender } = render(<MemoryRouter initialEntries={["/edit"]}>{routes(true)}</MemoryRouter>);
    expect(leaveIsQuestioned()).toBe(true);
    rerender(<MemoryRouter initialEntries={["/edit"]}>{routes(false)}</MemoryRouter>);
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("questions closing the tab only while dirty, and stops once unmounted", () => {
    const { unmount } = render(<MemoryRouter initialEntries={["/edit"]}>{routes(true)}</MemoryRouter>);
    expect(leaveIsQuestioned()).toBe(true);
    unmount();
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("lets a link through at once while clean", async () => {
    dataRouter(false);
    await userEvent.click(screen.getByRole("link", { name: "Elsewhere" }));
    expect(screen.getByText("Other page")).toBeInTheDocument();
  });

  it("holds a link while dirty until the owner decides", async () => {
    const router = dataRouter(true);
    await userEvent.click(screen.getByRole("link", { name: "Elsewhere" }));
    expect(screen.getByText("Editor")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/edit");
    await userEvent.click(screen.getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("button", { name: "Stay" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Elsewhere" }));
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(await screen.findByText("Other page")).toBeInTheDocument();
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("holds browser back while dirty and leaves the URL as it was", async () => {
    const router = dataRouter(true);
    await act(() => router.navigate(-1));
    expect(router.state.location.pathname).toBe("/edit");
    expect(screen.getByText("Editor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument();
  });
});
