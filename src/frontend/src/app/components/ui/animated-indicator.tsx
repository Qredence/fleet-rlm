import { motion } from "motion/react";
import { useSpring } from "../config/motion-config";

interface AnimatedIndicatorProps {
  layoutId: string;
  className?: string;
}

export function AnimatedIndicator({
  layoutId,
  className = "",
}: AnimatedIndicatorProps) {
  const spring = useSpring("indicator");

  return (
    <motion.div
      layoutId={layoutId}
      className={`absolute inset-0 -z-0 rounded-lg bg-secondary ${className}`}
      transition={spring}
    />
  );
}
