import { clearAccessToken, setAccessToken } from "@/lib/auth/token-store";
import { trimOrEmpty } from "@/lib/utils/env";

function parseCsv(value: string | undefined): string[] {
  return trimOrEmpty(value)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

const clientId = trimOrEmpty(import.meta.env.VITE_ENTRA_CLIENT_ID);
const authority =
  trimOrEmpty(import.meta.env.VITE_ENTRA_AUTHORITY) ||
  "https://login.microsoftonline.com/organizations";
const scopes = parseCsv(import.meta.env.VITE_ENTRA_SCOPES);
const redirectPath = trimOrEmpty(import.meta.env.VITE_ENTRA_REDIRECT_PATH) || "/login";

export const entraAuthConfig = {
  clientId,
  authority,
  scopes,
  redirectPath,
} as const;

export function isEntraAuthConfigured(): boolean {
  return !!entraAuthConfig.clientId && entraAuthConfig.scopes.length > 0;
}

// MSAL types only — no runtime import until getMsalClient() is called.
type PublicClientApplication = import("@azure/msal-browser").PublicClientApplication;
type AuthenticationResult = import("@azure/msal-browser").AuthenticationResult;
type AccountInfo = import("@azure/msal-browser").AccountInfo;

let msalClient: PublicClientApplication | null = null;
let initPromise: Promise<PublicClientApplication> | null = null;

function getRedirectUri(): string {
  if (typeof window === "undefined") return entraAuthConfig.redirectPath;
  return new URL(entraAuthConfig.redirectPath, window.location.origin).toString();
}

// Loads @azure/msal-browser on first call — splits it from the main bundle.
// Non-Entra deployments never download this 26 MB dependency.
async function getMsalClient(): Promise<PublicClientApplication> {
  if (!isEntraAuthConfigured()) {
    throw new Error(
      "Entra auth is not configured. Set VITE_ENTRA_CLIENT_ID and VITE_ENTRA_SCOPES.",
    );
  }

  if (msalClient) {
    return msalClient;
  }

  if (!initPromise) {
    const { PublicClientApplication, BrowserCacheLocation } = await import("@azure/msal-browser");
    const client = new PublicClientApplication({
      auth: {
        clientId: entraAuthConfig.clientId,
        authority: entraAuthConfig.authority,
        redirectUri: getRedirectUri(),
      },
      cache: {
        cacheLocation: BrowserCacheLocation.SessionStorage,
      },
    });

    initPromise = client.initialize().then(async () => {
      await client.handleRedirectPromise();
      msalClient = client;
      return client;
    });
  }

  return initPromise;
}

async function acquireAccessTokenForAccount(account: AccountInfo): Promise<AuthenticationResult> {
  const client = await getMsalClient();
  const result = await client.acquireTokenSilent({
    account,
    scopes: [...entraAuthConfig.scopes],
  });
  if (!result.accessToken) {
    throw new Error("Entra login succeeded, but no API access token was returned.");
  }
  client.setActiveAccount(result.account);
  setAccessToken(result.accessToken);
  return result;
}

export async function initializeEntraSession(): Promise<string | null> {
  if (!isEntraAuthConfigured()) {
    return null;
  }
  const client = await getMsalClient();
  const account = client.getActiveAccount() ?? client.getAllAccounts()[0] ?? null;
  if (!account) {
    clearAccessToken();
    return null;
  }
  const result = await acquireAccessTokenForAccount(account);
  return result.accessToken;
}

export async function loginWithEntra(): Promise<string> {
  const client = await getMsalClient();
  const result = await client.loginPopup({
    scopes: [...entraAuthConfig.scopes],
    prompt: "select_account",
  });
  const account = result.account ?? client.getActiveAccount();
  if (!account) {
    throw new Error("Microsoft sign-in completed without an active account.");
  }
  const silent = await acquireAccessTokenForAccount(account);
  return silent.accessToken;
}

export async function logoutWithEntra(): Promise<void> {
  if (!isEntraAuthConfigured()) {
    clearAccessToken();
    return;
  }

  const client = await getMsalClient();
  const account = client.getActiveAccount() ?? client.getAllAccounts()[0] ?? undefined;
  clearAccessToken();
  await client.logoutPopup({
    account,
    postLogoutRedirectUri: getRedirectUri(),
  });
}
