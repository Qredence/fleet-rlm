const ACCESS_TOKEN_KEY = "fleet-rlm:access-token";
const LEGACY_LOCAL_STORAGE_KEYS = ["fleet_access_token"] as const;

function safeSessionStorage(): Storage | undefined {
  if (typeof sessionStorage === "undefined") return undefined;
  return sessionStorage;
}

function safeLocalStorage(): Storage | undefined {
  if (typeof localStorage === "undefined") return undefined;
  return localStorage;
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
  const local = safeLocalStorage();

  const fromSession = readTokenFromStorage(session, ACCESS_TOKEN_KEY);
  if (fromSession) return fromSession;

  const fromCanonicalLocal = readTokenFromStorage(local, ACCESS_TOKEN_KEY);
  if (fromCanonicalLocal) {
    writeTokenToStorage(session, ACCESS_TOKEN_KEY, fromCanonicalLocal);
    removeTokenFromStorage(local, ACCESS_TOKEN_KEY);
    for (const legacyKey of LEGACY_LOCAL_STORAGE_KEYS) {
      removeTokenFromStorage(local, legacyKey);
    }
    return fromCanonicalLocal;
  }

  for (const legacyKey of LEGACY_LOCAL_STORAGE_KEYS) {
    const legacyToken = readTokenFromStorage(local, legacyKey);
    if (legacyToken) {
      writeTokenToStorage(session, ACCESS_TOKEN_KEY, legacyToken);
      removeTokenFromStorage(local, legacyKey);
      return legacyToken;
    }
  }

  return null;
}

let cachedAccessToken: string | null = loadAccessTokenFromStorage();

export function setAccessToken(token: string | null): void {
  const normalized = token && token.trim().length > 0 ? token : null;
  cachedAccessToken = normalized;

  const session = safeSessionStorage();
  const local = safeLocalStorage();

  if (normalized) {
    writeTokenToStorage(session, ACCESS_TOKEN_KEY, normalized);
    removeTokenFromStorage(local, ACCESS_TOKEN_KEY);
  } else {
    removeTokenFromStorage(session, ACCESS_TOKEN_KEY);
    removeTokenFromStorage(local, ACCESS_TOKEN_KEY);
  }

  for (const legacyKey of LEGACY_LOCAL_STORAGE_KEYS) {
    removeTokenFromStorage(local, legacyKey);
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

export { ACCESS_TOKEN_KEY };
