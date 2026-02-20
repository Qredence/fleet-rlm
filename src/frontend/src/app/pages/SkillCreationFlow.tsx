/**
 * Re-export from the decomposed skill-creation module.
 *
 * The monolithic SkillCreationFlow was split into focused sub-components:
 *
 *   pages/skill-creation/
 *     ├── SkillCreationFlow.tsx   — Orchestrator (wires hook + renders list + composer)
 *     ├── ChatMessageList.tsx     — Renders the message array (welcome + all types)
 *     ├── AssistantMessage.tsx    — Individual assistant bubble (Streamdown wrapper)
 *     ├── HitlCard.tsx            — Human-in-the-loop checkpoint card
 *     ├── UserMessage.tsx         — User message bubble
 *     ├── useChatSimulation.ts    — Custom hook: mock AI responses, phase transitions
 *     └── animation-presets.ts    — fadeUp, fadeUpReduced spring configs
 */
export { SkillCreationFlow } from "@/app/pages/skill-creation/SkillCreationFlow";
