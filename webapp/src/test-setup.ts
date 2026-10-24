import "@testing-library/jest-dom/vitest";

// A WebSocket that never opens, so page tests do not open real sockets or
// schedule reconnects. Tests that need a working socket replace it locally.
class InertWebSocket {
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send(): void {}
  close(): void {}
}
globalThis.WebSocket = InertWebSocket as unknown as typeof WebSocket;
