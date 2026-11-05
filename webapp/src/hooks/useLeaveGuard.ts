import { useContext, useEffect } from "react";
import { type BlockerFunction, UNSAFE_DataRouterContext, useBlocker } from "react-router-dom";

export interface LeaveGuard {
  /** An in-app navigation is on hold until proceed or reset is called. */
  blocked: boolean;
  proceed: () => void;
  reset: () => void;
}

const OPEN: LeaveGuard = { blocked: false, proceed: () => {}, reset: () => {} };

/** Ask the browser to confirm closing or reloading the tab while `dirty`. */
function useBeforeUnloadWhile(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}

function useRouteBlocker(dirty: boolean): LeaveGuard {
  const shouldBlock: BlockerFunction = ({ currentLocation, nextLocation }) =>
    dirty && (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search);
  const blocker = useBlocker(shouldBlock);
  if (blocker.state !== "blocked") return OPEN;
  return { blocked: true, proceed: blocker.proceed, reset: blocker.reset };
}

/** Holds in-app navigation (links and browser back or forward) while `dirty`, and
 * guards closing the tab. Navigation is held only under a data router; with a plain
 * router, such as MemoryRouter in tests, only the tab is guarded. */
export function useLeaveGuard(dirty: boolean): LeaveGuard {
  const dataRouter = useContext(UNSAFE_DataRouterContext) !== null;
  useBeforeUnloadWhile(dirty);
  // Whether a data router is present does not change while the component is mounted.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return dataRouter ? useRouteBlocker(dirty) : OPEN;
}
