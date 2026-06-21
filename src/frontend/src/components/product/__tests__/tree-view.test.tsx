import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { TreeView, type TreeViewNode } from "@/components/product/tree-view";

interface TestNode extends TreeViewNode {
  label: string;
}

const sampleNodes: TestNode[] = [
  {
    id: "root",
    label: "Root",
    children: [
      {
        id: "child-a",
        label: "Child A",
        children: [
          { id: "grandchild-1", label: "Grandchild 1" } as TestNode,
          { id: "grandchild-2", label: "Grandchild 2" } as TestNode,
        ],
      } as TestNode,
      { id: "child-b", label: "Child B" } as TestNode,
    ],
  },
  { id: "leaf", label: "Standalone Leaf" },
];

describe("TreeView rendering", () => {
  it("renders root-level nodes collapsed by default", () => {
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain("Root");
    expect(html).toContain("Standalone Leaf");
    expect(html).not.toContain("Child A");
    expect(html).not.toContain("Child B");
  });

  it("renders expanded children when expandedIds is provided", () => {
    const expanded = new Set(["root", "child-a"]);
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} expandedIds={expanded} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain("Root");
    expect(html).toContain("Child A");
    expect(html).toContain("Grandchild 1");
    expect(html).toContain("Grandchild 2");
    expect(html).toContain("Child B");
  });

  it("renders only first-level children when only root is expanded", () => {
    const expanded = new Set(["root"]);
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} expandedIds={expanded} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain("Child A");
    expect(html).toContain("Child B");
    expect(html).not.toContain("Grandchild 1");
  });
});

describe("TreeView ARIA", () => {
  it("uses tree and treeitem roles", () => {
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain('role="tree"');
    expect(html).toContain('role="treeitem"');
  });

  it("sets aria-expanded on expandable nodes", () => {
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain('aria-expanded="false"');
  });

  it("sets aria-expanded=true for expanded nodes", () => {
    const expanded = new Set(["root"]);
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} expandedIds={expanded} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain('aria-expanded="true"');
  });

  it("sets aria-level based on depth", () => {
    const expanded = new Set(["root", "child-a"]);
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} expandedIds={expanded} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain('aria-level="1"');
    expect(html).toContain('aria-level="2"');
    expect(html).toContain('aria-level="3"');
  });

  it("sets aria-selected when selectedId is provided", () => {
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} selectedId="leaf" renderLabel={(n) => n.label} />,
    );

    expect(html).toContain('aria-selected="true"');
  });
});

describe("TreeView custom rendering", () => {
  it("renders custom icons via renderIcon", () => {
    const html = renderToStaticMarkup(
      <TreeView
        nodes={sampleNodes}
        renderLabel={(n) => n.label}
        renderIcon={(n, expanded) => (
          <span data-testid={`icon-${n.id}-${expanded ? "open" : "closed"}`} />
        )}
      />,
    );

    expect(html).toContain('data-testid="icon-root-closed"');
    expect(html).toContain('data-testid="icon-leaf-closed"');
  });

  it("renders trailing content via renderTrailing", () => {
    const html = renderToStaticMarkup(
      <TreeView
        nodes={sampleNodes}
        renderLabel={(n) => n.label}
        renderTrailing={(n) => <span data-testid={`trailing-${n.id}`} />}
      />,
    );

    expect(html).toContain('data-testid="trailing-root"');
    expect(html).toContain('data-testid="trailing-leaf"');
  });
});

describe("TreeView tree-row utility", () => {
  it("applies tree-row class for token-based indentation", () => {
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain("tree-row");
  });

  it("sets --tree-depth CSS custom property", () => {
    const html = renderToStaticMarkup(
      <TreeView nodes={sampleNodes} renderLabel={(n) => n.label} />,
    );

    expect(html).toContain("--tree-depth");
  });
});
