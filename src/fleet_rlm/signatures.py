"""DSPy signatures for information extraction tasks.

This module defines DSPy Signature classes that specify the input/output
structure for various information extraction tasks using the Recursive
Language Model (RLM).

Each signature defines:
    - Input fields: What data the model receives
    - Output fields: What structured data the model should extract

Available signatures:
    - ExtractArchitecture: Extract DSPy framework architecture details
    - ExtractAPIEndpoints: Extract API endpoint definitions
    - FindErrorPatterns: Identify and categorize error patterns
    - ExtractWithCustomTool: Extract patterns using regex tool assistance
"""

from __future__ import annotations

import dspy


class ExtractArchitecture(dspy.Signature):
    """Extract architectural information from DSPy documentation.

    Analyzes documentation to identify the modular components of the DSPy
    framework, available optimizers, and key design principles.

    Input Fields:
        docs: Full DSPy documentation text to analyze
        query: Specific extraction query or focus area

    Output Fields:
        modules: List of DSPy modules identified in documentation
        optimizers: List of available optimizers and their purposes
        design_principles: Key design principles and architectural decisions
    """

    docs: str = dspy.InputField(desc="Full DSPy documentation text")
    query: str = dspy.InputField(desc="What to extract")
    modules: list = dspy.OutputField(desc="List of DSPy modules")
    optimizers: list = dspy.OutputField(desc="List of optimizers")
    design_principles: str = dspy.OutputField(desc="Key design principles")


class ExtractAPIEndpoints(dspy.Signature):
    """Extract API endpoints using batched analysis.

    Scans documentation to identify and catalog API endpoints,
    their parameters, and usage patterns.

    Input Fields:
        docs: API documentation text to analyze

    Output Fields:
        api_endpoints: List of API endpoints with details
    """

    docs: str = dspy.InputField(desc="API documentation")
    api_endpoints: list = dspy.OutputField(desc="List of API endpoints")


class FindErrorPatterns(dspy.Signature):
    """Find and categorize error patterns in documentation.

    Identifies common errors, their causes, and solutions from
    troubleshooting documentation or error guides.

    Input Fields:
        docs: Documentation text containing error information

    Output Fields:
        error_categories: Dictionary mapping error types to solutions
        total_errors_found: Total number of errors identified
    """

    docs: str = dspy.InputField(desc="Documentation text")
    error_categories: dict = dspy.OutputField(desc="Error types mapped to solutions")
    total_errors_found: int = dspy.OutputField(desc="Total errors identified")


class ExtractWithCustomTool(dspy.Signature):
    """Extract specific patterns using custom regex tool.

    Uses the regex_extract tool to parse documentation structure,
    finding markdown headers and code blocks for structural analysis.

    Strategy:
        1. Use regex_extract() to find all markdown headers
        2. Use regex_extract() to find all code blocks
        3. Summarize document structure

    Input Fields:
        docs: Documentation to analyze

    Output Fields:
        headers: All markdown headers found in the document
        code_blocks: All code block languages/content found
        structure_summary: Summary of document organization
    """

    docs: str = dspy.InputField(desc="Documentation to analyze")
    headers: list = dspy.OutputField(desc="All markdown headers found")
    code_blocks: list = dspy.OutputField(desc="All code block languages found")
    structure_summary: str = dspy.OutputField(desc="Summary of document structure")
