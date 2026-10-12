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
