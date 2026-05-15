import { lazy, type ComponentType } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { LottieRefCurrentProps } from "lottie-react";
import { cn } from "./utils/cn";
import { spiralFastData, spiralSlowData } from "./spiral-loader-data";

type LottieModuleShape = {
  default?: unknown;
  "module.exports"?: unknown;
};

function isComponent(value: unknown): value is ComponentType<any> {
  return typeof value === "function";
}

function unwrapComponent(value: unknown): ComponentType<any> | null {
  if (isComponent(value)) return value;
  if (value && typeof value === "object" && "default" in value) {
    const nestedDefault = (value as { default?: unknown }).default;
    if (isComponent(nestedDefault)) return nestedDefault;
  }
  return null;
}

function getModuleExport(module: LottieModuleShape, exportName: keyof LottieModuleShape): unknown {
  try {
    return module[exportName];
  } catch {
    return undefined;
  }
}

function resolveLottieComponent(module: LottieModuleShape): ComponentType<any> {
  const defaultExport = getModuleExport(module, "default");
  const moduleExports = getModuleExport(module, "module.exports");
  const candidates = [
    defaultExport,
    unwrapComponent(defaultExport),
    moduleExports,
    unwrapComponent(moduleExports),
  ];
  const component = candidates.find(isComponent);
  if (!component) {
    throw new Error("Unable to resolve lottie-react component export.");
  }
  return component;
}

const Lottie = lazy(() =>
  import("lottie-react").then((module) => ({
    default: resolveLottieComponent(module as LottieModuleShape),
  })),
);

const FAST_REPEATS = 4;
const SLOW_REPEATS = 2;

export type SpiralLoaderProps = {
  size?: number;
  className?: string;
};

export function SpiralLoader({ size = 16, className }: SpiralLoaderProps) {
  const [isMounted, setIsMounted] = useState(false);
  const [phase, setPhase] = useState<"fast" | "slow">("fast");
  const repeatCountRef = useRef(0);
  const fastRef = useRef<LottieRefCurrentProps | null>(null);
  const slowRef = useRef<LottieRefCurrentProps | null>(null);
  const resolvedTheme =
    typeof document !== "undefined" && document.documentElement.classList.contains("dark")
      ? "dark"
      : "light";

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const startFastPhase = useCallback(() => {
    repeatCountRef.current = 0;
    setPhase("fast");
    slowRef.current?.stop();
    fastRef.current?.goToAndPlay(0, true);
  }, []);

  const startSlowPhase = useCallback(() => {
    repeatCountRef.current = 0;
    setPhase("slow");
    fastRef.current?.stop();
    slowRef.current?.goToAndPlay(0, true);
  }, []);

  const handleFastComplete = useCallback(() => {
    repeatCountRef.current += 1;
    if (repeatCountRef.current < FAST_REPEATS) {
      fastRef.current?.goToAndPlay(0, true);
    } else {
      startSlowPhase();
    }
  }, [startSlowPhase]);

  const handleSlowComplete = useCallback(() => {
    repeatCountRef.current += 1;
    if (repeatCountRef.current < SLOW_REPEATS) {
      slowRef.current?.goToAndPlay(0, true);
    } else {
      startFastPhase();
    }
  }, [startFastPhase]);

  if (!isMounted) return null;
  const needsInvert = resolvedTheme !== "dark";

  return (
    <div className={cn("relative shrink-0", className)} style={{ width: size, height: size }}>
      <div
        className={cn(
          "absolute inset-0 transition-opacity duration-75",
          needsInvert && "invert",
          phase === "fast" ? "opacity-100" : "opacity-0",
        )}
      >
        <Lottie
          lottieRef={fastRef}
          animationData={spiralFastData}
          loop={false}
          autoplay={true}
          onComplete={handleFastComplete}
          style={{ width: "100%", height: "100%" }}
        />
      </div>
      <div
        className={cn(
          "absolute inset-0 transition-opacity duration-75",
          needsInvert && "invert",
          phase === "slow" ? "opacity-100" : "opacity-0",
        )}
      >
        <Lottie
          lottieRef={slowRef}
          animationData={spiralSlowData}
          loop={false}
          autoplay={false}
          onComplete={handleSlowComplete}
          style={{ width: "100%", height: "100%" }}
        />
      </div>
    </div>
  );
}
