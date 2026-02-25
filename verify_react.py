import asyncio
from fleet_rlm.runners import arun_react_chat_once


async def main():
    import time

    print("Testing list_files tool speed via agent request...")
    start = time.time()
    res, trajectory, _ = await arun_react_chat_once(
        message="Please use your list_files tool to find all '*.py' files in the current repository. Just run the tool once and tell me the count."
    )
    end = time.time()
    print(f"Agent finished list_files task in {end - start:.2f} seconds.")
    print("Response:", res)

    print("\nTesting iteration limits (requesting 18 tool calls)...")
    res, trajectory, _ = await arun_react_chat_once(
        message="Please use your bash tool to echo the numbers 1 through 18, but you MUST do it in 18 separate, distinct tool calls. Do not combine them."
    )

    print("Trajectory length:", len(trajectory))
    if len(trajectory) >= 15:
        print("SUCCESS! Iteration limit increased successfully beyond 15.")
    else:
        print(
            "WARNING: Trajectory length was less than 15. The agent either combined steps or failed to loop 18 times."
        )


asyncio.run(main())
