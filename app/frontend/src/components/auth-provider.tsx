"use client";
// Authentication via Cognito user pools

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { Authenticator, useAuthenticator } from "@aws-amplify/ui-react";
import { Amplify } from "aws-amplify";
import { fetchAuthSession, signInWithRedirect } from "aws-amplify/auth";
import { buildAmplifyConfig } from "@/amplify-config";
import "@aws-amplify/ui-react/styles.css";

// Configure Amplify from the RUNTIME config. buildAmplifyConfig() reads
// window.__TESSERA_SOLO__ each call; invoking it here (module load in the browser) plus again
// on mount below guarantees the eco's injected Cognito pool wins over the build-time fallback,
// even if this module happens to evaluate before the injected config script. Without this the
// exported /solo build can authenticate against the wrong (build-time/platform) pool → 401s.
if (typeof window !== "undefined") {
  Amplify.configure(buildAmplifyConfig());
}

// Federated Login (OIDC SSO) — button shown on the sign-in screen only when enabled.
// The provider NAME is the Cognito OIDC identity-provider identifier ("FederatedLogin");
// the visible LABEL is "Federated Login". (Never "Midway".)
const FEDERATED_ENABLED = process.env.NEXT_PUBLIC_FEDERATED_LOGIN_ENABLED === "true";
const FEDERATED_PROVIDER = process.env.NEXT_PUBLIC_FEDERATED_PROVIDER_NAME || "AmazonFederate";

// Self-registration — when enabled the Amplify Authenticator shows the "Create Account"
// tab so users can sign themselves up. Off by default (admin-created accounts only).
const ALLOW_SELF_SIGNUP = process.env.NEXT_PUBLIC_ALLOW_SELF_SIGNUP === "true";

function FederatedLoginButton() {
  if (!FEDERATED_ENABLED) return null;
  return (
    <div className="px-8 pb-6 pt-2">
      <div className="relative my-3 text-center">
        <span className="relative z-10 bg-white px-3 text-xs uppercase tracking-wide text-gray-400">or</span>
        <div className="absolute inset-x-0 top-1/2 h-px bg-gray-200" />
      </div>
      <button
        type="button"
        onClick={() => signInWithRedirect({ provider: { custom: FEDERATED_PROVIDER } })}
        className="w-full rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        Federated Login
      </button>
    </div>
  );
}

export interface TesseraUser {
  userId: string;
  email: string;
  groups: string[];
}

interface AuthContextValue {
  user: TesseraUser | null;
  idToken: string | null;
  isAdmin: boolean;
  signOut: () => void;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  idToken: null,
  isAdmin: false,
  signOut: () => {},
  authFetch: (url, init) => fetch(url, init),
});

export function useAuth() {
  return useContext(AuthContext);
}

function AuthInner({ children }: { children: React.ReactNode }) {
  const { user: amplifyUser, signOut } = useAuthenticator();
  const [tesseraUser, setTesseraUser] = useState<TesseraUser | null>(null);
  const [idToken, setIdToken] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const loadSession = async () => {
      try {
        const session = await fetchAuthSession();
        const token = session.tokens?.idToken;
        if (!token || !mounted) return;
        const payload = token.payload;
        setTesseraUser({
          userId: (payload.sub as string) || "",
          email: (payload.email as string) || "",
          groups: (payload["cognito:groups"] as string[]) || [],
        });
        setIdToken(token.toString());
      } catch {
        setTesseraUser(null);
        setIdToken(null);
      }
    };
    loadSession();
    // Refresh token every 45 minutes
    const interval = setInterval(loadSession, 45 * 60 * 1000);
    return () => { mounted = false; clearInterval(interval); };
  }, [amplifyUser]);

  const authFetch = useCallback(async (url: string, init?: RequestInit): Promise<Response> => {
    // Always get fresh token
    let token = idToken;
    try {
      const session = await fetchAuthSession({ forceRefresh: false });
      token = session.tokens?.idToken?.toString() || token;
      if (token !== idToken) setIdToken(token);
    } catch { /* use cached */ }

    const headers = new Headers(init?.headers);
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    return fetch(url, { ...init, headers });
  }, [idToken]);

  const isAdmin = tesseraUser?.groups?.includes("admin") || false;

  return (
    <AuthContext.Provider value={{ user: tesseraUser, idToken, isAdmin, signOut, authFetch }}>
      {children}
    </AuthContext.Provider>
  );
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Race-proof: reconfigure Amplify from the runtime config during the FIRST client render —
  // synchronously, before <Authenticator> mounts and runs its initial auth check. A useState
  // initializer runs at render time (not in a post-render effect, which would be too late), and
  // by first render in the browser the blocking tessera-config.js has set window.__TESSERA_SOLO__.
  // This is the definitive fix for the exported /solo build authenticating against the wrong pool.
  useState(() => {
    if (typeof window !== "undefined") {
      Amplify.configure(buildAmplifyConfig());
    }
    return null;
  });

  // Federated Login returns to the app via an OAuth redirect. When the account is
  // disabled (new user awaiting admin approval), Cognito bounces back to "/" with
  // ?error=invalid_request&error_description=User+is+not+enabled instead of a token.
  // Native sign-in surfaces this via handleSignIn; here we surface the redirect error
  // as a friendly banner so the user isn't dumped on a blank login screen.
  const [oauthNotice, setOauthNotice] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const err = params.get("error");
    if (!err) return;
    const desc = (params.get("error_description") || "").toLowerCase();
    const pendingApproval = desc.includes("not enabled") || desc.includes("disabled");
    setOauthNotice(
      pendingApproval
        ? "Your account is pending administrator approval. You'll be able to sign in with Federated Login once an admin enables it."
        : "Federated sign-in didn't complete. Please try again, or contact an administrator if it persists."
    );
    // Strip the error params so a refresh doesn't keep showing the banner.
    window.history.replaceState({}, document.title, window.location.origin + window.location.pathname);
  }, []);

  const SignInHeader = () =>
    oauthNotice ? (
      <div
        role="status"
        className="mx-8 mt-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800"
      >
        {oauthNotice}
      </div>
    ) : null;

  return (
    <Authenticator
      variation="modal"
      hideSignUp={!ALLOW_SELF_SIGNUP}
      components={{
        Header: () => (
          <div className="text-center py-4">
            <h2 className="text-xl font-bold tessera-gradient">⬡ Tessera</h2>
            {/* bedrock attribution */}
            <p className="text-[11px] text-gray-500 mt-1">Powered by Amazon Bedrock</p>
          </div>
        ),
        Footer: () =>
          ALLOW_SELF_SIGNUP ? (
            <div className="text-center text-xs text-gray-500 py-2">
              New accounts can sign in immediately unless admin approval is enabled.
            </div>
          ) : (
            <div className="text-center text-xs text-gray-500 py-2">
              Account pending admin approval after sign-up
            </div>
          ),
        // Federated Login button + the OAuth-redirect notice on the sign-in screen.
        SignIn: {
          Header: SignInHeader,
          Footer: FederatedLoginButton,
        },
      }}
      services={{
        handleSignIn: async (input) => {
          // Default Amplify behavior — errors bubble up to the UI
          const { signIn } = await import("aws-amplify/auth");
          try {
            return await signIn(input);
          } catch (e: any) {
            if (e?.name === "NotAuthorizedException" && e?.message?.includes("disabled")) {
              throw new Error("Account pending admin approval. Please wait for an administrator to enable your account.");
            }
            throw e;
          }
        },
      }}
    >
      <AuthInner>{children}</AuthInner>
    </Authenticator>
  );
}
