import { motion } from "motion/react";
import { RefreshCw } from "lucide-react";
import { springs } from "../../config/motion-config";
import { MAX_PULL, PULL_THRESHOLD } from "../../../lib/skills/library";

export function PullToRefreshIndicator({
  pullDistance,
  isPullingActive,
  isRefreshing,
  prefersReduced,
}: {
  pullDistance: number;
  isPullingActive: boolean;
  isRefreshing: boolean;
  prefersReduced: boolean | null;
}) {
  if (pullDistance <= 0) {
    return null;
  }

  return (
    <div
      className="flex items-center justify-center shrink-0 overflow-hidden"
      style={{
        height: pullDistance,
        transition: isPullingActive
          ? "none"
          : "height 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
      }}
    >
      <motion.div
        animate={{
          rotate: isRefreshing ? 360 : (pullDistance / (MAX_PULL * 0.45)) * 270,
        }}
        transition={
          isRefreshing
            ? {
                repeat: Infinity,
                duration: prefersReduced ? 0.01 : 0.8,
                ease: "linear",
              }
            : prefersReduced
              ? springs.instant
              : springs.default
        }
      >
        <RefreshCw
          className="w-5 h-5 text-muted-foreground"
          style={{
            opacity: Math.min(pullDistance / (PULL_THRESHOLD * 0.45), 1),
          }}
        />
      </motion.div>
    </div>
  );
}
