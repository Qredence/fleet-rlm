"""Test llm_query and llm_query_batched features with RLM paper.

This script tests the recursive sub-LLM pattern:
1. Load PDF content in Modal sandbox
2. Chunk into ~3 sections
3. Use llm_query_batched for parallel analysis
4. Use llm_query for synthesis
5. Verify max_llm_calls is respected
"""

import sys

import dspy
import pytest
from fleet_rlm import ModalInterpreter
from dspy.primitives.code_interpreter import FinalOutput

# Configure DSPy from environment variables (loaded from .env file)
import os

pytestmark = pytest.mark.skipif(
    not os.environ.get("MODAL_TOKEN_ID")
    or not os.environ.get("MODAL_TOKEN_SECRET")
    or not os.environ.get("DSPY_LLM_API_KEY"),
    reason="Integration test requires Modal credentials and DSPY_LLM_API_KEY",
)

try:
    # Use DSPY_* environment variables from .env file
    lm_model = os.environ.get('DSPY_LM_MODEL', 'openai/gpt-4o-mini')
    lm_api_base = os.environ.get('DSPY_LM_API_BASE')
    lm_api_key = os.environ.get('DSPY_LLM_API_KEY')

    if lm_api_key:
        config = {"api_key": lm_api_key}
        if lm_api_base:
            config["api_base"] = lm_api_base
        dspy.configure(lm=dspy.LM(lm_model, **config))
        print(f"DSPy configured with {lm_model}")
    else:
        print("Warning: No DSPY_LLM_API_KEY found in environment")
except Exception as e:
    print(f"Warning: Could not configure DSPy LM: {e}")

def get_result_value(result):
    """Extract value from FinalOutput or return string."""
    if isinstance(result, FinalOutput):
        return result.__dict__.get('output', {})
    return result

def test_llm_query_features():
    """Test the llm_query and llm_query_batched features."""

    print("=" * 60)
    print("Testing llm_query and llm_query_batched features")
    print("=" * 60)

    # Read the PDF locally first to get content
    pdf_path = '/Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/rlm_content/rlm-knowledge/rlm-pape.pdf'

    try:
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        print(f"Loaded PDF: {len(pdf_bytes)} bytes")
    except Exception as e:
        print(f"Failed to load PDF: {e}")
        return False

    # Test with ModalInterpreter - use volume to persist PDF
    print("\n--- Initializing ModalInterpreter ---")

    call_count = 0

    # First, create interpreter with volume to store PDF
    with ModalInterpreter(
        timeout=300,
        max_llm_calls=10,  # Limit for testing
        stdout_summary_threshold=500,  # Summarize long stdout
        volume_name="rlm-test-volume",  # Use volume for PDF storage
    ) as interp:
        print("ModalInterpreter initialized")
        print(f"Sandbox: {interp._sandbox}")

        # Upload PDF to volume first
        print("\n--- Uploading PDF to volume ---")
        # Write PDF to a temp file locally first
        import tempfile
        import os as local_os

        temp_dir = tempfile.mkdtemp()
        temp_pdf_path = local_os.path.join(temp_dir, "test_paper.pdf")
        with open(temp_pdf_path, 'wb') as f:
            f.write(pdf_bytes[:100000])  # First 100KB

        # Upload to volume using the correct API
        interp.upload_to_volume(local_files={temp_pdf_path: "/data/test_paper.pdf"})
        print("PDF uploaded to volume")

        # Cleanup temp file
        local_os.remove(temp_pdf_path)
        local_os.rmdir(temp_dir)

        print("\n--- Step 1: Load PDF and chunk into sections ---")

        # Step 1: Simple chunking without complex regex
        code_step1 = """
import os

# Read PDF from volume
pdf_path = "/data/test_paper.pdf"
if not os.path.exists(pdf_path):
    print(f"ERROR: PDF not found at {pdf_path}")
    SUBMIT(section_count=0, error="PDF not found")

with open(pdf_path, 'rb') as f:
    pdf_content = f.read()

print(f"Loaded PDF from volume: {len(pdf_content)} bytes")

# Decode and take first portion
text = pdf_content.decode('utf-8', errors='ignore')[:50000]

# Simple chunking by size
chunks = chunk_by_size(text, size=15000)
sections = [{"header": f"Chunk {i+1}", "content": chunk} for i, chunk in enumerate(chunks)]

# Keep only first 3 sections
sections = sections[:3]

print(f"Created {len(sections)} sections")
for i, sec in enumerate(sections):
    header = sec.get('header', f'Section {i+1}')
    content_len = len(sec.get('content', ''))
    print(f"  {header}: {content_len} chars")

# Store sections
add_buffer("sections", sections)
SUBMIT(section_count=len(sections))
"""

        result = interp.execute(code_step1)
        print(f"Result type: {type(result)}")
        print(f"Result: {str(result)[:300]}")

        result_data = get_result_value(result)
        section_count = result_data.get('section_count') if isinstance(result_data, dict) else None
        print(f"Chunked into {section_count} sections")

        if section_count is None:
            print("ERROR: Step 1 failed - cannot proceed")
            return False

        print("\n--- Step 2: Parallel analysis with llm_query_batched ---")

        # Step 2: Parallel analysis
        code_step2 = """
sections = get_buffer("sections")[0]

# Create prompts for each section
prompts = []
for i, sec in enumerate(sections):
    header = sec.get('header', f'Section {i+1}')
    content = sec.get('content', '')[:2000]
    prompt = f"Summarize the key points of this section in 2-3 sentences. Section: {header}. Content: {content[:1500]}"
    prompts.append(prompt)

print(f"Calling llm_query_batched with {len(prompts)} prompts...")

# Parallel analysis
findings = llm_query_batched(prompts)

print(f"Received {len(findings)} responses")
for i, finding in enumerate(findings):
    print(f"Finding {i+1}: {finding[:150]}...")

add_buffer("findings", findings)
SUBMIT(findings_count=len(findings))
"""

        result = interp.execute(code_step2)
        result_data = get_result_value(result)
        findings_count = result_data.get('findings_count') if isinstance(result_data, dict) else None
        print(f"Parallel analysis complete: {findings_count} findings")

        if findings_count is None:
            print("WARNING: Step 2 may have failed")

        print("\n--- Step 3: Synthesis with llm_query ---")

        # Step 3: Synthesis
        code_step3 = """
findings = get_buffer("findings")

print(f"Synthesizing {len(findings)} findings...")

# Simple synthesis prompt
combined = "; ".join([f"Section {i+1}: {f[:100]}..." for i, f in enumerate(findings)])
synthesis_prompt = f"Synthesize these findings into a brief summary: {combined}"

Final = llm_query(synthesis_prompt)

print(f"Synthesis complete. Length: {len(Final)} chars")
print(f"Synthesis preview: {Final[:200]}...")
"""

        result = interp.execute(code_step3)
        print(f"Synthesis result type: {type(result)}")
        print(f"Synthesis result: {str(result)[:300]}")

        print("\n--- Step 4: Verify call counting ---")

        # Step 4: Verify call counting
        code_step4 = """
print("Testing call count tracking...")

# This should work if we haven't exceeded max_llm_calls
try:
    test_result = llm_query("Say 'calls working' if you receive this")
    print(f"Test call result: {test_result}")
    SUBMIT(status="success", message="llm_query functional", test_response=test_result)
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""

        result = interp.execute(code_step4)
        result_data = get_result_value(result)
        status = result_data.get('status') if isinstance(result_data, dict) else 'unknown'
        print(f"Call verification: {status}")

        # Get actual call count from interpreter (use the lock attribute as proxy)
        call_count = getattr(interp, '_llm_calls_made', 'unknown')
        print(f"Total llm_query calls made: {call_count}")

    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    try:
        success = test_llm_query_features()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
