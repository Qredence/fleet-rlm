import { createCliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";
import { App } from "./App";

const renderer = await createCliRenderer({
  exitOnCtrlC: false,  // Let the app handle Ctrl+C for cancel
});

createRoot(renderer).render(<App />);
