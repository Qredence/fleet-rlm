import { useMediaQuery } from "@/hooks/use-media-query"

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  return !useMediaQuery(`(min-width: ${MOBILE_BREAKPOINT}px)`)
}
