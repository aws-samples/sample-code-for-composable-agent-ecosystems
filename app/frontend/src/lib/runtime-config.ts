"use client";
// Runtime endpoint resolution for the exported single-eco build.
//
// The platform build bakes NEXT_PUBLIC_* at build time. The exported /solo build does NOT
// (the eco's endpoints/Cognito are only known at deploy), so components must resolve the API
// base at RUNTIME from window.__TESSERA_SOLO__ (rendered by the eco-lite Terraform module into
// tessera-config.js at deploy), falling back to the build-time env for the platform build.
// Read inside components/hooks (client-side) so window is available.
export interface SoloConfig {
  ecoId?: string;
  apiUrl?: string;
  wsUrl?: string;
  region?: string;
  userPoolId?: string;
  clientId?: string;
  cognitoDomain?: string;
}

export function soloConfig(): SoloConfig {
  if (typeof window === "undefined") return {};
  return (window as unknown as { __TESSERA_SOLO__?: SoloConfig }).__TESSERA_SOLO__ || {};
}

/** REST API base URL — runtime (window.__TESSERA_SOLO__) first, then build-time env. */
export function getApiUrl(): string {
  return soloConfig().apiUrl || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
}

/** WebSocket URL — runtime first, then env, else derived from the API base. */
export function getWsUrl(): string {
  return soloConfig().wsUrl || process.env.NEXT_PUBLIC_WS_URL || getApiUrl().replace(/^http/, "ws");
}

/** True when running as an exported single-eco build (config injected at deploy). */
export function isSolo(): boolean {
  return !!soloConfig().ecoId;
}
