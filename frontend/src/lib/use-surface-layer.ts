import { surfaceClasses } from './surface-classes'
import { useSurface } from './surface-context'

export function useSurfaceLayer(offset: number, shadowLevel?: number) {
  const level = Math.min(useSurface() + offset, 8)
  return { level, surfaceClass: surfaceClasses(level, shadowLevel) }
}
