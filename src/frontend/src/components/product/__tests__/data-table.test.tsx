import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vite-plus/test";

import { DataTable, type ColumnDef } from "@/components/product/data-table";

interface Row extends Record<string, unknown> {
  id: string;
  name: string;
  score: number;
  status: string;
}

const rows: Row[] = [
  { id: "r1", name: "Alpha", score: 0.9, status: "completed" },
  { id: "r2", name: "Beta", score: 0.7, status: "running" },
  { id: "r3", name: "Gamma", score: 0.5, status: "failed" },
];

const columns: ColumnDef<Row>[] = [
  { header: "Name", accessor: "name", sortable: true },
  { header: "Score", accessor: "score", sortable: true },
  { header: "Status", accessor: (row) => <span className="badge">{row.status}</span> },
];

describe("DataTable onRowClick", () => {
  it("renders clickable rows when onRowClick is provided", () => {
    const handler = vi.fn();
    const html = renderToStaticMarkup(
      <DataTable columns={columns} data={rows} onRowClick={handler} />,
    );

    expect(html).toContain("cursor-pointer");
    expect(html).toContain("Alpha");
  });

  it("does not add cursor-pointer to rows when onRowClick is absent", () => {
    const html = renderToStaticMarkup(
      <DataTable columns={columns} data={rows} />,
    );

    // cursor-pointer on <th> is from sortable headers, not rows
    const rowMatch = html.match(/<tr[^>]*class="([^"]*)"/g);
    const dataRows = rowMatch?.filter((tr) => !tr.includes("border-b border-border bg-muted")) ?? [];
    for (const tr of dataRows) {
      expect(tr).not.toContain("cursor-pointer");
    }
  });
});

describe("DataTable rowClassName", () => {
  it("applies custom rowClassName to data rows", () => {
    const html = renderToStaticMarkup(
      <DataTable
        columns={columns}
        data={rows}
        rowClassName="custom-row-class"
      />,
    );

    expect(html).toContain("custom-row-class");
  });
});

describe("DataTable rowKey", () => {
  it("uses rowKey function for key extraction", () => {
    const html = renderToStaticMarkup(
      <DataTable
        columns={columns}
        data={rows}
        rowKey={(row) => row.id}
      />,
    );

    expect(html).toContain("Alpha");
    expect(html).toContain("Beta");
    expect(html).toContain("Gamma");
  });
});

describe("DataTable render function accessor", () => {
  it("renders custom content via function accessor", () => {
    const html = renderToStaticMarkup(
      <DataTable columns={columns} data={rows} />,
    );

    expect(html).toContain("completed");
    expect(html).toContain("running");
    expect(html).toContain("failed");
  });
});

describe("DataTable empty state", () => {
  it("shows empty message when data is empty", () => {
    const html = renderToStaticMarkup(
      <DataTable columns={columns} data={[]} emptyMessage="No runs found." />,
    );

    expect(html).toContain("No runs found.");
  });
});
