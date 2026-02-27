import { RouterProvider } from "react-router";
import { router } from "@/app/routes";

/**
 * Root application component.
 *
 * Renders the React Router provider which handles all routing,
 * layout composition, and lazy route-module delivery with retry-aware
 * chunk loading.
 */
export default function App() {
  return (
    <>
      <RouterProvider router={router} />
    </>
  );
}
