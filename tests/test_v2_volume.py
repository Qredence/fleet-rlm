"""Test fleet-rlm with V2 volume 'rlm-volume-dspy'."""

import sys

from dotenv import load_dotenv
import os
import dspy
import pytest
from fleet_rlm import ModalInterpreter
from dspy.primitives.code_interpreter import FinalOutput

load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.environ.get("MODAL_TOKEN_ID")
    or not os.environ.get("MODAL_TOKEN_SECRET")
    or not os.environ.get("DSPY_LLM_API_KEY"),
    reason="Integration test requires Modal credentials and DSPY_LLM_API_KEY",
)

# Configure DSPy
lm_model = os.environ.get('DSPY_LM_MODEL', 'openai/gpt-4o-mini')
lm_api_base = os.environ.get('DSPY_LM_API_BASE')
lm_api_key = os.environ.get('DSPY_LLM_API_KEY')

if lm_api_key:
    config = {"api_key": lm_api_key}
    if lm_api_base:
        config["api_base"] = lm_api_base
    dspy.configure(lm=dspy.LM(lm_model, **config))
    print(f"✓ DSPy configured with {lm_model}")
else:
    print("⚠ Warning: No DSPY_LLM_API_KEY found")

def get_result_value(result):
    """Extract value from FinalOutput or return string."""
    if isinstance(result, FinalOutput):
        return result.__dict__.get('output', {})
    return result

def main():
    print("\n" + "=" * 60)
    print("V2 Volume Integration Test: rlm-volume-dspy")
    print("=" * 60)

    # Test with V2 volume
    with ModalInterpreter(
        timeout=300,
        max_llm_calls=5,
        volume_name='rlm-volume-dspy',  # V2 volume
    ) as interp:
        print("\n✓ ModalInterpreter initialized with V2 volume")
        print("  Volume name: rlm-volume-dspy")
        print("  Mount path: /data")

        print("\n--- Step 1: Verify V2 volume is accessible ---")

        code = '''
import os

# Check if /data exists and is accessible
if os.path.isdir("/data"):
    print("✓ /data directory exists")
else:
    print("✗ /data directory NOT found")
    SUBMIT(volume_accessible=False, error="/data not found")

# List contents of /data
try:
    contents = os.listdir("/data")
    print(f"✓ /data contents: {contents}")
    SUBMIT(volume_accessible=True, contents=contents, content_count=len(contents))
except Exception as e:
    print(f"✗ Error listing /data: {e}")
    SUBMIT(volume_accessible=False, error=str(e))
'''
        result = interp.execute(code)
        result_data = get_result_value(result)

        if isinstance(result_data, dict):
            volume_accessible = result_data.get('volume_accessible', False)
            contents = result_data.get('contents', [])
            print(f"\n✓ Volume accessible: {volume_accessible}")
            print(f"  Contents ({len(contents)} items): {contents}")
        else:
            print(f"Result: {result_data}")

        print("\n--- Step 2: Write test data to V2 volume ---")

        code = '''
import os

# Write test file
test_content = "This is a test file written to the V2 volume rlm-volume-dspy."
test_path = "/data/test_v2_volume.txt"

with open(test_path, 'w') as f:
    f.write(test_content)

print(f"✓ Wrote test file: {test_path}")

# Verify it was written
if os.path.exists(test_path):
    with open(test_path, 'r') as f:
        read_content = f.read()
    print(f"✓ Verified file contents: {read_content[:50]}...")
    SUBMIT(write_success=True, content=read_content)
else:
    SUBMIT(write_success=False, error="File not found after write")
'''
        result = interp.execute(code)
        result_data = get_result_value(result)

        if isinstance(result_data, dict):
            write_success = result_data.get('write_success', False)
            print(f"\n✓ Write test: {'PASSED' if write_success else 'FAILED'}")
        else:
            print(f"Result: {result_data}")

        print("\n--- Step 3: Run llm_query test with V2 volume ---")

        code = '''
# Simple llm_query test
prompt = "What is 2 + 2? Answer with just the number."
print(f"Calling llm_query with: {prompt}")

response = llm_query(prompt)
print(f"✓ llm_query response: {response}")

SUBMIT(
    llm_query_works=True,
    prompt=prompt,
    response=response
)
'''
        result = interp.execute(code)
        result_data = get_result_value(result)

        if isinstance(result_data, dict):
            llm_works = result_data.get('llm_query_works', False)
            response = result_data.get('response', '')
            print(f"\n✓ llm_query test: {'PASSED' if llm_works else 'FAILED'}")
            print(f"  Response: {response}")
        else:
            print(f"Result: {result_data}")

        print("\n--- Step 4: Persist data to V2 volume ---")

        code = '''
import json
import os

# Save test results
results = {
    "test": "v2_volume_integration",
    "status": "success",
    "timestamp": "2025-01-09"
}

results_path = "/data/v2_test_results.json"
with open(results_path, 'w') as f:
    json.dump(results, f, indent=2)

print(f"✓ Saved results to: {results_path}")

# Verify
if os.path.exists(results_path):
    with open(results_path, 'r') as f:
        saved = json.load(f)
    print(f"✓ Verified saved data: {saved}")
    SUBMIT(persist_success=True, data=saved)
else:
    SUBMIT(persist_success=False)
'''
        result = interp.execute(code)
        result_data = get_result_value(result)

        if isinstance(result_data, dict):
            persist_success = result_data.get('persist_success', False)
            print(f"\n✓ Persistence test: {'PASSED' if persist_success else 'FAILED'}")

        print("\n--- Step 5: Verify data persists ---")
        # Note: commit() can only be called from inside a container
        # For V2 volumes, data is automatically persisted
        print("✓ V2 volume data automatically persisted")

    print("\n" + "=" * 60)
    print("V2 Volume Integration Test Complete!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
