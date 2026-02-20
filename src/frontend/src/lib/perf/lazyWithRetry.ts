import { lazy, type ComponentType, type LazyExoticComponent } from "react";

type ModuleLoader<T extends ComponentType<unknown>> = () => Promise<{
  default: T;
}>;

const modulePromises = new Map<
  string,
  Promise<{ default: ComponentType<unknown> }>
>();

function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const message = error.message.toLowerCase();

  return (
    message.includes("failed to fetch dynamically imported module") ||
    message.includes("loading chunk") ||
    message.includes("importing a module script failed") ||
    message.includes("chunkloaderror")
  );
}

async function loadModule<T extends ComponentType<unknown>>(
  key: string,
  loader: ModuleLoader<T>,
  retries = 1,
): Promise<{ default: T }> {
  const cached = modulePromises.get(key) as Promise<{ default: T }> | undefined;
  if (cached) return cached;

  const run = (async () => {
    try {
      return await loader();
    } catch (error) {
      // Remove failed attempts from cache so retries can re-import.
      modulePromises.delete(key);

      if (retries > 0 && isChunkLoadError(error)) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        return loadModule(key, loader, retries - 1);
      }

      throw error;
    }
  })();

  modulePromises.set(key, run as Promise<{ default: ComponentType<unknown> }>);
  return run;
}

export function lazyWithRetry<T extends ComponentType<unknown>>(
  key: string,
  loader: ModuleLoader<T>,
): LazyExoticComponent<T> {
  return lazy(() => loadModule(key, loader));
}

export function preloadModule<T extends ComponentType<unknown>>(
  key: string,
  loader: ModuleLoader<T>,
): Promise<void> {
  return loadModule(key, loader).then(() => undefined);
}

export function clearLazyModuleCache(key?: string): void {
  if (key) {
    modulePromises.delete(key);
    return;
  }
  modulePromises.clear();
}
