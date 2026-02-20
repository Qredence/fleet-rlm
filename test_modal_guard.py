import asyncio
from dotenv import load_dotenv

# Load env before imports that might need it
load_dotenv()

from src.fleet_rlm.core.tools import execute_workspace_code, search_evolutive_memory  # noqa: E402


async def run_guard_test():
    print("=== Testing Modal Execution Guard ===")

    # We will generate a python script that prints 50,000 characters
    # This simulates a runaway dataframe print that would cause Context Rot
    massive_print_code = """
for i in range(1000):
    print("This is a very long line of text that will eventually trigger the truncation guard. " * 5)
"""

    print("Dispatching heavy code to Modal workspace...")
    result = execute_workspace_code(code=massive_print_code)

    # We expect this string length to be exactly 2000 + length of the guard warning
    print(f"\\nResult received. Length: {len(result)} characters")
    print("--- SNIPPET ---")

    # Print the last 300 characters to show the warning is appended
    print(result[-300:])
    print("----------------")

    if "Context window protected" in result:
        print("✅ SUCCESS: Context Window Truncation Guard is active.")
    else:
        print("❌ FAILED: Truncation guard did not trigger.")

    print("\\n=== Testing Evolutive Memory Search ===")
    print("Searching for: 'test concept'")
    mem_result = search_evolutive_memory(query="test concept")
    print("Memory Search Result:")
    print(mem_result)


if __name__ == "__main__":
    asyncio.run(run_guard_test())
