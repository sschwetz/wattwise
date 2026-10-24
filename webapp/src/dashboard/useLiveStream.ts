import { useEffect, useState } from "react";
import { getAccessToken } from "../api/client";
import { toWebSocketUrl } from "../api/config";
import {
  applyLiveMessage,
  backoffMs,
  freshLiveReadings,
  LIVE_FRESH_MS,
  parseLiveMessage,
  type LiveValues,
} from "./liveStream";
import type { Reading } from "./useLiveReadings";

interface LiveState {
  energy: LiveValues;
  weather: LiveValues;
}

const EMPTY: LiveState = { energy: new Map(), weather: new Map() };
const CLOSE_POLICY_VIOLATION = 1008;

function earliestExpiry(state: LiveState): number | null {
  let earliest: number | null = null;
  for (const values of [state.energy, state.weather]) {
    for (const v of values.values()) {
      const at = v.receivedAt + LIVE_FRESH_MS;
      if (earliest === null || at < earliest) earliest = at;
    }
  }
  return earliest;
}

/** Opens the site's live WebSocket and reconnects with backoff until unmounted or rejected. */
function connectLive(siteId: number, onMessage: (data: unknown) => void): () => void {
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  let stopped = false;

  const open = () => {
    const token = getAccessToken();
    if (!token) return;
    const ws = new WebSocket(toWebSocketUrl(`/api/v1/sites/${siteId}/live`));
    socket = ws;
    ws.onopen = () => ws.send(JSON.stringify({ token }));
    ws.onmessage = (event) => {
      attempt = 0;
      try {
        onMessage(JSON.parse(String(event.data)));
      } catch {
        // A frame that is not JSON is dropped.
      }
    };
    ws.onclose = (event) => {
      if (stopped || event.code === CLOSE_POLICY_VIOLATION) return;
      timer = setTimeout(open, backoffMs(attempt));
      attempt += 1;
    };
  };

  open();
  return () => {
    stopped = true;
    clearTimeout(timer);
    socket?.close();
  };
}

export function useLiveStream(siteId: number): { energy: Reading[]; weather: Reading[] } {
  const [state, setState] = useState<LiveState>(EMPTY);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    setState(EMPTY);
    return connectLive(siteId, (data) => {
      const message = parseLiveMessage(data);
      if (!message) return;
      const receivedAt = Date.now();
      setState((s) => ({ ...s, [message.kind]: applyLiveMessage(s[message.kind], message, receivedAt) }));
      setNow(receivedAt);
    });
  }, [siteId]);

  useEffect(() => {
    const expiry = earliestExpiry(state);
    if (expiry === null) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(expiry - Date.now(), 0));
    return () => clearTimeout(timer);
  }, [state, now]);

  return { energy: freshLiveReadings(state.energy, now), weather: freshLiveReadings(state.weather, now) };
}
