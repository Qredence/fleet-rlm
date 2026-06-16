import { describe, expect, it } from "vite-plus/test";
import {
  parseRawSnippet,
  dedent,
  shouldRepairSerializedPython,
  formatSerializedPythonSnippet,
  cleanAndFormatSnippet,
  extractCommandSummary,
  summarizeField,
  inferCodeLanguage,
} from "./code-repair";

describe("code-repair utilities", () => {
  describe("parseRawSnippet", () => {
    it("handles plain strings", () => {
      expect(parseRawSnippet("echo hello")).toBe("echo hello");
    });

    it("parses valid JSON structured strings", () => {
      const jsonStr = JSON.stringify({ code: "import os\nprint(os.getcwd())" });
      expect(parseRawSnippet(jsonStr)).toBe("import os\nprint(os.getcwd())");
    });

    it("parses python dict representations of snippets", () => {
      const pythonDict = "{'code': 'import sys\\nprint(sys.argv)'}";
      expect(parseRawSnippet(pythonDict)).toBe("import sys\nprint(sys.argv)");
    });

    it("parses repl_execute call strings", () => {
      const replCall = "Calling tool: repl_execute({'code': 'print(\"hello\")'})";
      expect(parseRawSnippet(replCall)).toBe('print("hello")');
    });
  });

  describe("dedent", () => {
    it("removes common leading indentation", () => {
      const code = "  def foo():\n    return 42";
      expect(dedent(code)).toBe("def foo():\n  return 42");
    });

    it("keeps code intact if no common indentation", () => {
      const code = "def foo():\n  return 42";
      expect(dedent(code)).toBe("def foo():\n  return 42");
    });
  });

  describe("shouldRepairSerializedPython", () => {
    it("returns false for multiline code", () => {
      expect(shouldRepairSerializedPython("import sys\nx = 1")).toBe(false);
    });

    it("returns false for short strings", () => {
      expect(shouldRepairSerializedPython("x = 1")).toBe(false);
    });

    it("returns true for single-line serialized python with variables and keywords", () => {
      const serialized =
        "import os, sys # load modules sys.path.append('.') # add path x = 1 # define x and make this string long enough to trigger python serialization repair";
      expect(shouldRepairSerializedPython(serialized)).toBe(true);
    });
  });

  describe("formatSerializedPythonSnippet", () => {
    it("reconstructs single line python snippets into readable multiline", () => {
      const serialized =
        "import sys # load path sys.path.append('.') # append path x = sys.argv[0] # script name and make this string long enough to exceed the length threshold";
      const formatted = formatSerializedPythonSnippet(serialized);
      expect(formatted).toContain("\n");
      expect(formatted).toBe(
        "import sys\n# load path sys.path.append('.')\n# append path\nx = sys.argv[0]\n# script name and make this string long enough to exceed the length threshold",
      );
    });
  });

  describe("cleanAndFormatSnippet", () => {
    it("converts br tags, smart quotes, dedents, and repairs", () => {
      const raw = "  import sys<br>  x = 1 # define x";
      expect(cleanAndFormatSnippet(raw)).toBe("import sys\nx = 1 # define x");
    });
  });

  describe("extractCommandSummary", () => {
    it("extracts executable command heads for pipe pipelines", () => {
      const pipeline = "cat file.txt | grep 'pattern' | head -n 10";
      expect(extractCommandSummary(pipeline)).toBe("cat, grep, head");
    });
  });

  describe("summarizeField", () => {
    it("collapses spaces and truncates to 72 chars if needed", () => {
      expect(summarizeField("short text")).toBe("short text");
      expect(summarizeField("a".repeat(100))).toBe("a".repeat(69) + "...");
    });
  });

  describe("inferCodeLanguage", () => {
    it("uses explicit language if provided", () => {
      expect(inferCodeLanguage("echo 1", "shell")).toBe("shell");
    });

    it("infers python", () => {
      expect(inferCodeLanguage("import sys\nprint(1)")).toBe("python");
    });

    it("infers typescript/javascript", () => {
      expect(inferCodeLanguage("const x = 1;\nexport default x;")).toBe("typescript");
    });

    it("infers sql", () => {
      expect(inferCodeLanguage("SELECT * FROM users")).toBe("sql");
    });

    it("infers json", () => {
      expect(inferCodeLanguage('{"name": "test"}')).toBe("json");
    });

    it("defaults to bash", () => {
      expect(inferCodeLanguage("ls -la")).toBe("bash");
    });
  });
});
