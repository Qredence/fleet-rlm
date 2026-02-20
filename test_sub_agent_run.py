import os
import asyncio
import dspy
from dotenv import load_dotenv

# Load credentials early
load_dotenv()

model = os.getenv("DSPY_LM_MODEL", "openai/gpt-4o")
api_key = os.getenv("DSPY_LLM_API_KEY") or os.getenv("DSPY_LM_API_KEY")
api_base = os.getenv("DSPY_LM_API_BASE")

kwargs = {}
if api_key:
    kwargs["api_key"] = api_key
if api_base:
    kwargs["api_base"] = api_base

lm = dspy.LM(model, **kwargs)
dspy.settings.configure(lm=lm)

from src.fleet_rlm.react.agent import RLMReActChatAgent  # noqa: E402


async def run_sub_agent_execution():
    print("=== LIVE INTEGRATION TEST: sub-agent via Modal Sandbox ===")

    # We pass verbose=True for visibility. ModalInterpreter will auto-start.
    with RLMReActChatAgent(
        verbose=True, rlm_max_iterations=5, react_max_iters=5
    ) as agent:
        prompt = (
            "You must delegate a task to your sub-agent. Do not write code yourself. "
            "Use the 'rlm_query' tool to ask your recursive sub-agent to compute the 25th "
            "Fibonacci number using the Modal code interpreter. Return exactly what your "
            "sub-agent answers."
        )
        print(f"\n[Prompt]: {prompt}\n")
        print("Dispatching via iter_chat_turn_stream...\n")

        for event in agent.iter_chat_turn_stream(message=prompt, trace=True):
            print(f"DEBUG: type={type(event)} value={event!r}")
            if event.kind == "assistant_token":
                print(event.text, end="", flush=True)
            elif event.kind == "reasoning_step":
                pass  # We do not print reasoning steps to keep the output clean
            elif event.kind == "status":
                print(f"\n[Status]: {event.text}")
            elif event.kind == "tool_call":
                payload = event.payload or {}
                # Handle case where payload is a string somehow?
                if isinstance(payload, str):
                    print(f"\n[Odd Payload String]: {payload}")
                else:
                    tool_name = payload.get("tool_name", "")
                    tool_args = payload.get("tool_args", "")
                    print(
                        f"\n\n🛠️  [Agent Tool Call]: {tool_name}\n   Args: {tool_args}\n"
                    )
            elif event.kind == "tool_result":
                payload = event.payload or {}
                if isinstance(payload, str):
                    result_preview = payload[:300]
                else:
                    result_preview = str(payload.get("result", ""))[:300]
                print(f"✅ [Tool Result]:\n{result_preview}...\n")
            elif event.kind == "depth_change":
                payload = event.payload or {}
                if isinstance(payload, str):
                    print(f"\n🌀 [Recursion Depth] Shift payload: {payload}\n")
                else:
                    print(
                        f"\n🌀 [Recursion Depth] Shifted to depth {payload.get('new_depth')}\n"
                    )
            elif event.kind == "error":
                print(f"\n❌ [Agent Error]: {event.text}\n")

        print("\n\n=== RUN COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(run_sub_agent_execution())
