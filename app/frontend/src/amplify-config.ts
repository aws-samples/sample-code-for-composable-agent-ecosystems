// Cognito authentication configuration for Amplify.
// SECURITY: User pool IDs and client IDs are NOT secrets — they are public identifiers
// required by the browser to initiate auth flows. No client secrets are exposed here.
//
// Single-eco export: the eco's Cognito pool/client are created fresh PER DEPLOY, so their ids
// aren't known at build time. They're injected at RUNTIME into window.__TESSERA_SOLO__ (rendered
// by the exporter into a blocking <script src="/tessera-config.js"> in <head>). We MUST read that
// global when Amplify is actually configured, NOT at module-evaluation time — a statically
// evaluated config can run before the injected script in a Next static export, silently falling
// back to the build-time (platform) pool and 401-ing every request against the eco's own API.
//
// Therefore this module exports a FACTORY, buildAmplifyConfig(), called from the auth provider
// during client render (guaranteed to run after the config script). The default export is kept
// for backwards compatibility but should not be relied on for the /solo build.

function buildAmplifyConfig() {
  const federatedEnabled = process.env.NEXT_PUBLIC_FEDERATED_LOGIN_ENABLED === "true";

  const solo: Record<string, string> =
    (typeof window !== "undefined" && (window as unknown as { __TESSERA_SOLO__?: Record<string, string> }).__TESSERA_SOLO__) || {};
  const cognitoDomain = solo.cognitoDomain || process.env.NEXT_PUBLIC_COGNITO_DOMAIN || "";

  // redirect targets resolve to the current origin at runtime (client-only).
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  const loginWith: Record<string, unknown> = { email: true };
  if (federatedEnabled && cognitoDomain) {
    loginWith.oauth = {
      domain: cognitoDomain,
      scopes: ["phone", "email", "openid", "profile"],
      redirectSignIn: [`${origin}/`],
      redirectSignOut: [`${origin}/`],
      responseType: "code",
    };
  }

  return {
    Auth: {
      Cognito: {
        userPoolId: solo.userPoolId || process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID || "",
        userPoolClientId: solo.clientId || process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || "",
        loginWith,
      },
    },
  };
}

export { buildAmplifyConfig };

// Backwards-compatible default (computed at import). Prefer buildAmplifyConfig() at runtime.
const amplifyConfig = buildAmplifyConfig();
export default amplifyConfig;
