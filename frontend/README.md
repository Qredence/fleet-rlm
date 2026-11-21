# Fleet RLM frontend

Run the Phase 3 FastAPI server on port 8000, then start the frontend from this directory.

```sh
pnpm install
pnpm dev
```

Vite forwards API requests to FastAPI in development. If the server requires a bearer token, enter it in **API access** in the frontend. The token is kept in the current browser tab's session storage.

Use `pnpm typecheck` to check the application and Vite configuration, and `pnpm build` to produce the static frontend in `dist/`.
