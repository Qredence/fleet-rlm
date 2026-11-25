# Fleet RLM frontend

Use Node.js `26.13.1` from the repository’s [`.node-version`](../.node-version) and pnpm `12.23.0` to match CircleCI. Check your active versions with `node --version` and `pnpm --version`.

Run `make dev-backend` from the repository root to start FastAPI on port 8000. Then run these commands from `frontend/`.

```sh
pnpm install --frozen-lockfile
pnpm dev
```

Vite forwards API requests to FastAPI in development. If the server requires a bearer token, enter it in **Settings → General → API Bearer Token**. The token is kept in the current browser tab's session storage.

Use `pnpm typecheck` to check the application and Vite configuration, and `pnpm build` to produce the static frontend in `dist/`.
