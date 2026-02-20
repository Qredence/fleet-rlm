import type { Skill } from "../../components/data/types";
import type { GraphEdge, GraphNode } from "../../components/data/graph-layouts";

export const domainColorMap: Record<string, string> = {
  analytics: "--chart-1",
  development: "--chart-2",
  nlp: "--chart-3",
  devops: "--chart-4",
};

export function getDomainColor(domain: string): string {
  return `var(${domainColorMap[domain] ?? "--chart-5"})`;
}

export function resolveCSSVar(varName: string, el: HTMLElement): string {
  const styles = getComputedStyle(el);
  const raw = varName.replace(/^var\(/, "").replace(/\)$/, "");
  return styles.getPropertyValue(raw).trim() || "rgb(143, 143, 143)";
}

export function withAlpha(color: string, alpha: number): string {
  const rgbaMatch = color.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (rgbaMatch) {
    const [, r = "143", g = "143", b = "143"] = rgbaMatch;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  const hexMatch = color.match(/^#([0-9a-f]{6})$/i);
  if (hexMatch) {
    const hex = hexMatch[1];
    if (!hex) return color;
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  return color;
}

export function buildGraph(skills: Skill[]): {
  nodes: GraphNode[];
  edges: GraphEdge[];
} {
  const nodes: GraphNode[] = skills.map((skill, i) => {
    const angle = (i / skills.length) * Math.PI * 2;
    const spread = 160;
    return {
      id: skill.id,
      domain: skill.domain,
      displayName: skill.displayName,
      qualityScore: skill.qualityScore,
      tags: skill.tags,
      dependencies: skill.dependencies,
      x: Math.cos(angle) * spread + (Math.random() - 0.5) * 40,
      y: Math.sin(angle) * spread + (Math.random() - 0.5) * 40,
      vx: 0,
      vy: 0,
      radius: 18 + (skill.qualityScore / 100) * 14,
      pinned: false,
    };
  });

  const edges: GraphEdge[] = [];
  for (let i = 0; i < skills.length; i++) {
    for (let j = i + 1; j < skills.length; j++) {
      const a = skills[i];
      const b = skills[j];
      if (!a || !b) continue;

      let weight = 0;
      if (a.domain === b.domain) weight += 2;
      const sharedTags = a.tags.filter((t) => b.tags.includes(t));
      weight += sharedTags.length;
      if (a.dependencies.includes(b.name) || b.dependencies.includes(a.name)) {
        weight += 3;
      }

      if (weight > 0) {
        edges.push({ source: a.id, target: b.id, weight });
      }
    }
  }

  return { nodes, edges };
}

export function clientToGraph(
  clientX: number,
  clientY: number,
  canvas: HTMLCanvasElement,
  transform: { x: number; y: number; scale: number },
): { gx: number; gy: number } {
  const rect = canvas.getBoundingClientRect();
  const gx = (clientX - rect.left - rect.width / 2) / transform.scale - transform.x;
  const gy = (clientY - rect.top - rect.height / 2) / transform.scale - transform.y;
  return { gx, gy };
}

export function hitTestNode(
  nodes: GraphNode[],
  gx: number,
  gy: number,
): GraphNode | null {
  for (const node of nodes) {
    const dx = gx - node.x;
    const dy = gy - node.y;
    if (dx * dx + dy * dy <= (node.radius + 4) * (node.radius + 4)) {
      return node;
    }
  }
  return null;
}
