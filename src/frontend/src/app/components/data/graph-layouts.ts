/**
 * Graph layout algorithms for the TaxonomyGraph canvas.
 *
 * Three layout modes:
 *   - `force`   — force-directed simulation (gravity, repulsion, edge springs)
 *   - `cluster` — circular clusters grouped by domain
 *   - `tree`    — hierarchical top-down tree based on dependencies
 *
 * All functions operate on mutable GraphNode arrays in place.
 */

// ── Graph types ─────────────────────────────────────────────────────

export interface GraphNode {
  id: string;
  domain: string;
  displayName: string;
  qualityScore: number;
  tags: string[];
  dependencies: string[];
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  /** When true, force simulation will not move this node */
  pinned: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
}

export type LayoutMode = "force" | "cluster" | "tree";

// ── Force-directed simulation tick ──────────────────────────────────

export function simulateForces(
  nodes: GraphNode[],
  edges: GraphEdge[],
  _width: number,
  _height: number,
  alpha: number,
) {
  const centerX = 0;
  const centerY = 0;

  // Center gravity
  for (const node of nodes) {
    if (node.pinned) continue;
    node.vx += (centerX - node.x) * 0.005 * alpha;
    node.vy += (centerY - node.y) * 0.005 * alpha;
  }

  // Node-node repulsion
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i];
      const b = nodes[j];
      if (!a || !b) continue;
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 1) {
        dx = 1;
        dy = 1;
        dist = 1.41;
      }
      if (dist < (a.radius + b.radius + 40) * 3) {
        const force = (alpha * 800) / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        if (!a.pinned) {
          a.vx -= fx;
          a.vy -= fy;
        }
        if (!b.pinned) {
          b.vx += fx;
          b.vy += fy;
        }
      }
    }
  }

  // Edge attraction
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  for (const edge of edges) {
    const a = nodeMap.get(edge.source);
    const b = nodeMap.get(edge.target);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < 1) continue;
    const idealDist = 120 + (5 - edge.weight) * 20;
    const force = (dist - idealDist) * 0.003 * alpha;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    if (!a.pinned) {
      a.vx += fx;
      a.vy += fy;
    }
    if (!b.pinned) {
      b.vx -= fx;
      b.vy -= fy;
    }
  }

  // Velocity damping and position update
  for (const node of nodes) {
    if (node.pinned) {
      node.vx = 0;
      node.vy = 0;
      continue;
    }
    node.vx *= 0.85;
    node.vy *= 0.85;
    node.x += node.vx;
    node.y += node.vy;
  }
}

// ── Cluster layout ──────────────────────────────────────────────────

/**
 * Arranges nodes in circular clusters grouped by domain.
 * Each domain occupies a wedge of the circle; within each wedge
 * nodes are arranged in a tight sub-circle.
 */
export function applyClusterLayout(nodes: GraphNode[]) {
  // Group by domain
  const groups = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const list = groups.get(node.domain) ?? [];
    list.push(node);
    groups.set(node.domain, list);
  }

  const domainKeys = [...groups.keys()];
  const clusterRadius = Math.max(140, domainKeys.length * 60);

  domainKeys.forEach((domain, di) => {
    const angle = (di / domainKeys.length) * Math.PI * 2 - Math.PI / 2;
    const cx = Math.cos(angle) * clusterRadius;
    const cy = Math.sin(angle) * clusterRadius;

    const members = groups.get(domain)!;
    const subRadius = Math.max(40, members.length * 18);

    members.forEach((node, ni) => {
      const subAngle = (ni / members.length) * Math.PI * 2;
      node.x = cx + Math.cos(subAngle) * subRadius;
      node.y = cy + Math.sin(subAngle) * subRadius;
      node.vx = 0;
      node.vy = 0;
    });
  });
}

// ── Hierarchical tree layout ────────────────────────────────────────

/**
 * Builds a simple top-down tree from dependency relationships.
 * Nodes with no dependencies are roots; others are children.
 * Nodes without clear parent/child relations are placed in a
 * separate "unlinked" row at the bottom.
 */
export function applyTreeLayout(nodes: GraphNode[], edges: GraphEdge[]) {
  // Build adjacency: who depends on whom → parent → children
  const idToNode = new Map<string, GraphNode>();
  for (const n of nodes) {
    idToNode.set(n.id, n);
  }

  // Determine children: if A depends on B, then B is parent of A
  const childrenOf = new Map<string, string[]>();
  const hasParent = new Set<string>();

  for (const edge of edges) {
    // Use edges with highest weight as the primary parent-child
    if (edge.weight >= 3) {
      const existing = childrenOf.get(edge.target) ?? [];
      existing.push(edge.source);
      childrenOf.set(edge.target, existing);
      hasParent.add(edge.source);
    }
  }

  // Roots: nodes with no parent in the dependency tree
  const roots = nodes.filter((n) => !hasParent.has(n.id));
  const unlinked: GraphNode[] = [];
  const placed = new Set<string>();

  // BFS to assign levels
  const levels: GraphNode[][] = [];
  let currentLevel = roots.length > 0 ? roots : nodes[0] ? [nodes[0]] : [];

  while (currentLevel.length > 0) {
    levels.push(currentLevel);
    for (const n of currentLevel) placed.add(n.id);
    const nextLevel: GraphNode[] = [];
    for (const node of currentLevel) {
      const children = childrenOf.get(node.id) ?? [];
      for (const childId of children) {
        if (!placed.has(childId)) {
          const childNode = idToNode.get(childId);
          if (childNode) {
            nextLevel.push(childNode);
            placed.add(childId);
          }
        }
      }
    }
    currentLevel = nextLevel;
  }

  // Unplaced nodes go into unlinked row
  for (const n of nodes) {
    if (!placed.has(n.id)) unlinked.push(n);
  }
  if (unlinked.length > 0) levels.push(unlinked);

  // Position nodes
  const levelSpacing = 100;
  const totalHeight = (levels.length - 1) * levelSpacing;
  const startY = -totalHeight / 2;

  for (let li = 0; li < levels.length; li++) {
    const level = levels[li];
    if (!level) continue;
    const y = startY + li * levelSpacing;
    const totalWidth = (level.length - 1) * 90;
    const startX = -totalWidth / 2;

    for (let ni = 0; ni < level.length; ni++) {
      const node = level[ni];
      if (!node) continue;
      node.x = startX + ni * 90;
      node.y = y;
      node.vx = 0;
      node.vy = 0;
    }
  }
}
