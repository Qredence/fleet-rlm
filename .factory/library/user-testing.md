# User Testing

Testing surface: tools, URLs, setup steps, known quirks for the Frontend Architecture Optimization mission.

**What belongs here:** Testing tools, URLs, setup instructions, known issues.

## Testing Tools

- **Vitest** - Unit tests (`bun run test:unit`)
- **Playwright** - E2E tests (`bun run test:e2e`)
- **agent-browser** - Browser automation for manual verification

## URLs

| Page | URL | Notes |
|------|-----|-------|
| Login | http://localhost:5173/login | Microsoft Entra auth |
| Signup | http://localhost:5173/signup | Email/password signup |
| Workspace | http://localhost:5173/app/workspace | Main chat interface |
| Settings | http://localhost:5173/settings | Settings panels |
| Volumes | http://localhost:5173/app/volumes | Modal Volume browser |

## Setup Steps

1. Start backend: `cd <PROJECT_ROOT> && uv run fleet web`
2. Start frontend: `cd src/frontend && bun run dev`
3. Navigate to http://localhost:5173

## Known Quirks

- Microsoft Entra auth requires environment variables - shows error if not configured
- Backend must be running for chat functionality
- Theme switching uses next-themes with localStorage persistence
- Mobile breakpoint is 768px

## Verification Checklist

After ai-elements migration, verify:
- [ ] Chat interface loads at /app/workspace
- [ ] User can send a message
- [ ] Assistant response renders correctly
- [ ] Reasoning sections expand/collapse
- [ ] Tool execution displays work
- [ ] Theme switching works (Light/Dark)
- [ ] Settings page loads
- [ ] Command palette (⌘K) works
