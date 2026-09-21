/**
 * Tessera Auth utilities — re-exports from Amplify for use outside the provider.
 *
 * The main Amplify.configure() call lives in auth-provider.tsx (via amplify-config.ts).
 * This module provides typed helpers for components that need auth operations
 * without importing Amplify directly.
 */
import {
  signIn,
  signUp,
  signOut,
  confirmSignUp,
  fetchAuthSession,
  getCurrentUser,
  confirmSignIn,
} from "aws-amplify/auth";

export interface AuthUser {
  userId: string;
  email: string;
  groups: string[];
  idToken: string;
}

export async function getAuthUser(): Promise<AuthUser | null> {
  try {
    const session = await fetchAuthSession();
    const idToken = session.tokens?.idToken;
    if (!idToken) return null;
    const payload = idToken.payload;
    return {
      userId: (payload.sub as string) || "",
      email: (payload.email as string) || "",
      groups: (payload["cognito:groups"] as string[]) || [],
      idToken: idToken.toString(),
    };
  } catch {
    return null;
  }
}

export async function getIdToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    return session.tokens?.idToken?.toString() || null;
  } catch {
    return null;
  }
}

export async function refreshToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession({ forceRefresh: true });
    return session.tokens?.idToken?.toString() || null;
  } catch {
    return null;
  }
}

export { signIn, signUp, signOut, confirmSignUp, confirmSignIn, fetchAuthSession, getCurrentUser };
