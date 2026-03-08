# Environment

Environment variables, external dependencies, and setup notes for the Frontend Architecture Optimization mission.

**What belongs here:** Required env vars, external API keys/services, dependency quirks, platform-specific notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

## Environment Variables

The frontend expects these environment variables (see `.env.example`):

- `VITE_FLEET_API_URL` - Backend API URL
- `VITE_FLEET_WS_URL` - WebSocket URL
- `VITE_FLEET_WORKSPACE_ID` - Default workspace ID
- `VITE_FLEET_USER_ID` - Default user ID
- `VITE_FLEET_TRACE` - Enable tracing
- `VITE_ENTRA_CLIENT_ID` - Microsoft Entra client ID
- `VITE_ENTRA_AUTHORITY` - Microsoft Entra authority URL
- `VITE_ENTRA_SCOPES` - Microsoft Entra scopes

## Package Manager

- **Runtime:** bun 1.3.9
- **Node.js:** v22.22.0+

## Key Dependencies

- React 19.2.4
- React Router 7.13.1
- Tailwind CSS 4.2.0
- Vite 7.3.1
- shadcn/ui (via components.json)
- ai-elements registry configured

## Notes

- The backend must be running on port 8000 for full functionality
- Microsoft Entra auth is optional - login page shows error if not configured
