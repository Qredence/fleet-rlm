const ACCESS_TOKEN_KEY = "fleet-rlm:access-token";

function safeSessionStorage(): Storage | undefined {
  if (typeof sessionStorage === "undefined") return undefined;
  return sessionStorage;
}

function readTokenFromStorage(
  storage: Storage | undefined,
  key: string,
): string | null {
  if (!storage) return null;
  try {
    const token = storage.getItem(key);
    return token && token.trim().length > 0 ? token : null;
  } catch {
    return null;
  }
}

function removeTokenFromStorage(
  storage: Storage | undefined,
  key: string,
): void {
  if (!storage) return;
  try {
    storage.removeItem(key);
  } catch {
    // Best-effort cleanup only.
  }
}

function writeTokenToStorage(
  storage: Storage | undefined,
  key: string,
  token: string,
): void {
  if (!storage) return;
  try {
    storage.setItem(key, token);
  } catch {
    // Best-effort persistence only.
  }
}

function loadAccessTokenFromStorage(): string | null {
  const session = safeSessionStorage();
  return readTokenFromStorage(session, ACCESS_TOKEN_KEY);
}

let cachedAccessToken: string | null = loadAccessTokenFromStorage();

export function setAccessToken(token: string | null): void {
  const normalized = token && token.trim().length > 0 ? token : null;
  cachedAccessToken = normalized;

  const session = safeSessionStorage();

  if (normalized) {
    writeTokenToStorage(session, ACCESS_TOKEN_KEY, normalized);
  } else {
    removeTokenFromStorage(session, ACCESS_TOKEN_KEY);
  }
}

export function getAccessToken(): string | null {
  if (cachedAccessToken) return cachedAccessToken;

  cachedAccessToken = loadAccessTokenFromStorage();
  return cachedAccessToken;
}

export function clearAccessToken(): void {
  setAccessToken(null);
}

export function clearTokens(): void {
  clearAccessToken();
}

export { ACCESS_TOKEN_KEY };
