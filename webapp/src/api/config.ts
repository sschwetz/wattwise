interface AppConfig {
  apiBaseUrl: string;
}

declare global {
  interface Window {
    __APP_CONFIG__?: AppConfig;
  }
}

export function getApiBaseUrl(): string {
  return window.__APP_CONFIG__?.apiBaseUrl ?? "http://localhost:8000";
}

/** The API base URL with its scheme swapped to ws/wss, plus `path`. */
export function toWebSocketUrl(path: string): string {
  return `${getApiBaseUrl().replace(/^http/, "ws")}${path}`;
}
