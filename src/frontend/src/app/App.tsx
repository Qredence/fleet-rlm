import { RouterProvider } from '@tanstack/react-router'
import { router } from '@/router'

/**
 * Root application component.
 *
 * Renders the TanStack Router provider which handles all routing,
 * layout composition, and file-based route delivery.
 */
export default function App() {
  return (
    <>
      <RouterProvider router={router} />
    </>
  )
}
