from __future__ import annotations

import dspy


class ExtractArchitecture(dspy.Signature):
    """Extract architectural information from DSPy documentation."""

    docs: str = dspy.InputField(desc="Full DSPy documentation text")
    query: str = dspy.InputField(desc="What to extract")
    modules: list = dspy.OutputField(desc="List of DSPy modules")
    optimizers: list = dspy.OutputField(desc="List of optimizers")
    design_principles: str = dspy.OutputField(desc="Key design principles")


class ExtractAPIEndpoints(dspy.Signature):
    """Extract API endpoints using batched analysis."""

    docs: str = dspy.InputField(desc="API documentation")
    api_endpoints: list = dspy.OutputField(desc="List of API endpoints")


class FindErrorPatterns(dspy.Signature):
    """Find and categorize error patterns."""

    docs: str = dspy.InputField(desc="Documentation text")
    error_categories: dict = dspy.OutputField(desc="Error types mapped to solutions")
    total_errors_found: int = dspy.OutputField(desc="Total errors identified")


class ExtractWithCustomTool(dspy.Signature):
    """Extract specific patterns using custom regex tool.

    Strategy:
    1. Use regex_extract() to find all markdown headers
    2. Use regex_extract() to find all code blocks
    3. Summarize structure
    """

    docs: str = dspy.InputField(desc="Documentation to analyze")
    headers: list = dspy.OutputField(desc="All markdown headers found")
    code_blocks: list = dspy.OutputField(desc="All code block languages found")
    structure_summary: str = dspy.OutputField(desc="Summary of document structure")
