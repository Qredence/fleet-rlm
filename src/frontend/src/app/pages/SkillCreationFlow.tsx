/**
 * Re-export from the decomposed skill-creation module.
 *
 * The live flow is now backend-driven. `pages/skill-creation/` owns the
 * route orchestrator, message list rendering, backend websocket adapters,
 * runtime contracts, and animation presets used by the chat surface.
 */
export { SkillCreationFlow } from "@/app/pages/skill-creation/SkillCreationFlow";
