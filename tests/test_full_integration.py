"""Full integration test for llm_query and llm_query_batched with real API calls."""

import sys
sys.path.insert(0, '/Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src')

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import os
import dspy
from fleet_rlm import ModalInterpreter
from dspy.primitives.code_interpreter import FinalOutput

# Configure DSPy from environment variables
lm_model = os.environ.get('DSPY_LM_MODEL', 'openai/gpt-4o-mini')
lm_api_base = os.environ.get('DSPY_LM_API_BASE')
lm_api_key = os.environ.get('DSPY_LLM_API_KEY')

if not lm_api_key:
    print("ERROR: DSPY_LLM_API_KEY not found in environment")
    sys.exit(1)

config = {"api_key": lm_api_key}
if lm_api_base:
    config["api_base"] = lm_api_base

dspy.configure(lm=dspy.LM(lm_model, **config))
print(f"✓ DSPy configured with {lm_model}")

def get_result_value(result):
    """Extract value from FinalOutput or return string."""
    if isinstance(result, FinalOutput):
        return result.__dict__.get('output', {})
    return result

def main():
    print("\n" + "=" * 60)
    print("Full Integration Test: llm_query with Real API Calls")
    print("=" * 60)

    with ModalInterpreter(
        timeout=300,
        max_llm_calls=10,
        stdout_summary_threshold=500,
    ) as interp:
        print("\n--- Step 1: Store test content in sandbox ---")

        # Store chunks in sandbox
        code_step1 = '''
chunks = [
    "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed.",
    "Deep learning is part of machine learning methods based on artificial neural networks with supervised, semi-supervised or unsupervised learning.",
    "Natural language processing is a subfield of AI concerned with the interactions between computers and human language."
]

add_buffer("chunks", chunks)
print(f"Stored {len(chunks)} chunks in buffer")
SUBMIT(chunk_count=len(chunks))
'''
        result = interp.execute(code_step1)
        result_data = get_result_value(result)
        chunk_count = result_data.get('chunk_count') if isinstance(result_data, dict) else 0
        print(f"✓ Stored {chunk_count} chunks")

        print("\n--- Step 2: Parallel analysis with llm_query_batched ---")

        code_step2 = '''
chunks = get_buffer("chunks")[0]

# Create analysis prompts
prompts = []
for i, chunk in enumerate(chunks):
    prompt = "Analyze this text and extract 2 key concepts: " + chunk[:150]
    prompts.append(prompt)

print(f"Calling llm_query_batched with {len(prompts)} prompts...")
print("This will make parallel LLM calls...")

# Parallel analysis
findings = llm_query_batched(prompts)

print(f"\\n✓ Received {len(findings)} responses:")
for i, finding in enumerate(findings):
    print(f"\\n--- Finding {i+1} ---")
    preview = finding[:300] + "..." if len(finding) > 300 else finding
    print(preview)

add_buffer("findings", findings)
SUBMIT(findings_count=len(findings))
'''
        result = interp.execute(code_step2)
        result_data = get_result_value(result)
        findings_count = result_data.get('findings_count') if isinstance(result_data, dict) else 0
        print(f"\n✓ Parallel analysis complete: {findings_count} findings")

        print("\n--- Step 3: Synthesis with llm_query ---")

        code_step3 = '''
findings = get_buffer("findings")

print(f"Synthesizing {len(findings)} findings...")

# Combine findings
combined = "; ".join([f"Analysis {i+1}: {f[:100]}" for i, f in enumerate(findings)])

synthesis_prompt = "Synthesize these AI concept analyses into a brief summary (2-3 sentences): " + combined

print("Calling llm_query for synthesis...")
Final = llm_query(synthesis_prompt)

print(f"\\n✓ Synthesis complete!")
print(f"Response length: {len(Final)} characters")
print(f"\\nSynthesis preview: {Final[:200]}...")
'''
        result = interp.execute(code_step3)
        result_data = get_result_value(result)

        if isinstance(result_data, str):
            print(f"\n✓ Synthesis result:")
            print("-" * 60)
            print(result_data)
            print("-" * 60)
        else:
            print(f"Result type: {type(result_data)}")
            print(f"Result: {result_data}")

        print("\n--- Step 4: Final verification ---")

        code_step4 = '''
# Verify llm_query still works
print("Making final verification call...")
verification = llm_query("Say 'Integration test successful' if you receive this message.")
print(f"Verification response: {verification}")

SUBMIT(
    status="success",
    verification=verification,
    message="Full integration test completed"
)
'''
        result = interp.execute(code_step4)
        result_data = get_result_value(result)
        status = result_data.get('status') if isinstance(result_data, dict) else 'unknown'
        verification = result_data.get('verification') if isinstance(result_data, dict) else None

        print(f"✓ Final status: {status}")
        if verification:
            print(f"✓ Verification response: {verification}")

    print("\n" + "=" * 60)
    print("Integration test completed!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
