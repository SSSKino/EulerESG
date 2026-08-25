import type {
  DisclosureGraphEdge,
  DisclosureGraphFilters,
  DisclosureGraphNode,
  DisclosureGraphResponse,
  GraphDisplayData,
  GraphDisplayMode,
  StoredGraphPositions,
} from "./types";

const REPORT_TYPES = new Set(["report", "reportnode"]);
const METRIC_TYPES = new Set(["metric", "metricitem", "metric_item"]);
const DISCLOSURE_TYPES = new Set(["disclosure", "assessment", "disclosureitem"]);
const EVIDENCE_TYPES = new Set(["evidence", "evidenceblock", "evidence_block"]);

export function normalizeNodeType(type: unknown): "report" | "metric" | "disclosure" | "evidence" | "other" {
  const normalized = String(type ?? "").replace(/[\s-]/g, "_").toLowerCase();
  if (REPORT_TYPES.has(normalized)) return "report";
  if (METRIC_TYPES.has(normalized)) return "metric";
  if (DISCLOSURE_TYPES.has(normalized)) return "disclosure";
  if (EVIDENCE_TYPES.has(normalized)) return "evidence";
  return "other";
}

export function propertyValue(
  properties: Record<string, unknown> | undefined,
  ...keys: string[]
): unknown {
  if (!properties) return undefined;
  for (const key of keys) {
    const value = properties[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

export function propertyString(
  properties: Record<string, unknown> | undefined,
  ...keys: string[]
): string {
  const value = propertyValue(properties, ...keys);
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join(", ");
  return value === undefined ? "" : String(value).trim();
}

export function normalizeDisclosureStatus(value: unknown): string {
  const normalized = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  if (["fully", "full", "fully_disclosed", "disclosed"].includes(normalized)) {
    return "fully_disclosed";
  }
  if (["partial", "partially", "partially_disclosed"].includes(normalized)) {
    return "partially_disclosed";
  }
  if (["not", "none", "not_disclosed", "undisclosed"].includes(normalized)) {
    return "not_disclosed";
  }
  return normalized;
}

export function disclosureStatus(node: DisclosureGraphNode): string {
  return normalizeDisclosureStatus(
    propertyValue(node.properties, "disclosure_status", "status", "assessment_status"),
  );
}

export function metricCode(node: DisclosureGraphNode): string {
  return (
    propertyString(node.properties, "metric_code", "code", "metric_id") ||
    node.group_id ||
    node.label
  );
}

export function reportId(node: DisclosureGraphNode): string {
  return propertyString(node.properties, "file_id", "report_id") || node.id.replace(/^report:/, "");
}

export function graphNodeSearchText(node: DisclosureGraphNode): string {
  const metricNames = node.properties.metric_names;
  const searchTerms = node.properties.search_terms;
  return [
    node.label,
    node.id,
    node.group_id,
    propertyString(node.properties, "filename", "report_name", "display_name"),
    metricCode(node),
    propertyString(
      node.properties,
      "metric_name",
      "name",
      "family_label",
      "description",
      "definition",
      "topic",
    ),
    Array.isArray(metricNames) ? metricNames.join(" ") : String(metricNames || ""),
    Array.isArray(searchTerms) ? searchTerms.join(" ") : String(searchTerms || ""),
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

interface GraphEdgeLike {
  id: string;
  source: string;
  target: string;
}

interface IndexedGraphEdge<T extends GraphEdgeLike> {
  edge: T;
  otherId: string;
}

function buildEdgeIndex<T extends GraphEdgeLike>(
  edges: T[],
): Map<string, IndexedGraphEdge<T>[]> {
  const index = new Map<string, IndexedGraphEdge<T>[]>();
  const append = (nodeId: string, item: IndexedGraphEdge<T>) => {
    const bucket = index.get(nodeId);
    if (bucket) bucket.push(item);
    else index.set(nodeId, [item]);
  };
  for (const edge of edges) {
    append(edge.source, { edge, otherId: edge.target });
    append(edge.target, { edge, otherId: edge.source });
  }
  return index;
}

/**
 * Return a deterministic n-degree neighborhood for Kumu-style focus. The edge
 * index keeps this linear in the visible graph instead of rescanning every edge
 * for every selected root.
 */
export function graphNeighborhood(
  data: GraphDisplayData,
  seedIds: string[],
  degree: number,
): { nodeIds: string[]; edgeIds: string[] } {
  const validNodeIds = new Set(data.nodes.map((node) => node.id));
  const includedNodes = new Set(
    seedIds.filter((nodeId) => validNodeIds.has(nodeId)),
  );
  const includedEdges = new Set<string>();
  const edgeIndex = buildEdgeIndex(data.edges);
  let frontier = [...includedNodes];
  const maxDegree = Math.max(0, Math.floor(Number.isFinite(degree) ? degree : 0));

  for (let step = 0; step < maxDegree && frontier.length; step += 1) {
    const nextFrontier = new Set<string>();
    for (const nodeId of frontier) {
      for (const relation of edgeIndex.get(nodeId) || []) {
        includedEdges.add(relation.edge.id);
        if (!includedNodes.has(relation.otherId)) {
          includedNodes.add(relation.otherId);
          nextFrontier.add(relation.otherId);
        }
      }
    }
    frontier = [...nextFrontier];
  }

  return {
    nodeIds: data.nodes
      .filter((node) => includedNodes.has(node.id))
      .map((node) => node.id),
    edgeIds: data.edges
      .filter((edge) => includedEdges.has(edge.id))
      .map((edge) => edge.id),
  };
}

function firstNodeOfType(
  ids: string[],
  nodesById: Map<string, DisclosureGraphNode>,
  type: "report" | "metric" | "evidence",
): DisclosureGraphNode | undefined {
  return ids.map((id) => nodesById.get(id)).find((node) => node && normalizeNodeType(node.type) === type);
}

function matchesOne(value: string, selected: string[]): boolean {
  return selected.length === 0 || selected.includes(value);
}

function matchesDisclosureFilters(
  disclosure: DisclosureGraphNode,
  report: DisclosureGraphNode | undefined,
  metric: DisclosureGraphNode | undefined,
  filters: DisclosureGraphFilters,
): boolean {
  if (!report || !metric) return false;
  const reportProperties = report.properties;
  const metricProperties = metric.properties;
  const disclosureProperties = disclosure.properties;
  const framework =
    propertyString(disclosureProperties, "framework") ||
    propertyString(metricProperties, "framework") ||
    propertyString(reportProperties, "framework");
  const scope =
    propertyString(disclosureProperties, "scope_key", "scope") ||
    propertyString(metricProperties, "scope_key", "scope") ||
    propertyString(reportProperties, "scope_key", "scope");
  const year =
    propertyString(disclosureProperties, "report_year", "year") ||
    propertyString(reportProperties, "report_year", "year");
  const topic =
    propertyString(metricProperties, "topic", "category", "dimension") ||
    propertyString(disclosureProperties, "topic", "category", "dimension");

  return (
    matchesOne(reportId(report), filters.reportIds) &&
    matchesOne(framework, filters.frameworks) &&
    matchesOne(scope, filters.scopes) &&
    matchesOne(year, filters.years) &&
    matchesOne(topic, filters.topics) &&
    matchesOne(disclosureStatus(disclosure), filters.statuses)
  );
}

const KUMU_CURVATURES: Record<number, number[]> = {
  1: [0],
  2: [-0.7, 0.7],
  3: [-0.7, 0, 0.7],
  4: [-0.7, -0.2, 0.2, 0.7],
  5: [-0.7, -0.3, 0, 0.3, 0.7],
  6: [-0.7, -0.4, -0.15, 0.15, 0.4, 0.7],
  7: [-0.7, -0.5, -0.25, 0, 0.25, 0.5, 0.7],
  8: [-0.7, -0.5, -0.3, -0.1, 0.1, 0.3, 0.5, 0.7],
};

// These are the concise code-level titles used by the supplied Kumu Hardware
// map. Other standards continue to use explicit family metadata or a safe
// topic/name fallback, so the projection remains framework agnostic.
const KUMU_METRIC_FAMILY_TITLES: Record<string, string> = {
  "TC-HW-000.A": "Units produced by product category",
  "TC-HW-000.B": "Area of manufacturing facilities",
  "TC-HW-000.C": "Production from owned facilities",
  "TC-HW-230A.1": "Product data security risk management",
  "TC-HW-330A.1": "Employee diversity representation",
  "TC-HW-410A.1": "IEC 62474 declarable substances",
  "TC-HW-410A.2": "EPEAT registration",
  "TC-HW-410A.3": "Energy efficiency certification",
  "TC-HW-410A.4": "End-of-life products & e-waste",
  "TC-HW-430A.1": "Tier 1 supplier facility audits",
  "TC-HW-430A.2": "Supplier non-conformance & corrective actions",
  "TC-HW-440A.1": "Critical materials risk management",
};

function normalizedMetricCodeKey(code: string): string {
  return code.replace(/\s+/g, "").toLocaleUpperCase();
}

function metricFamilyTitle(
  code: string,
  metrics: DisclosureGraphNode[],
  useTopicFallback = true,
): string {
  const referenceTitle = KUMU_METRIC_FAMILY_TITLES[normalizedMetricCodeKey(code)];
  if (referenceTitle) return referenceTitle;
  for (const metric of metrics) {
    const explicit = propertyString(
      metric.properties,
      "family_label",
      "metric_family_label",
      "code_label",
      "graph_label",
    ).trim();
    if (!explicit) continue;
    const withoutCode = explicit.replace(code, "").replace(/^\s*[—–-]\s*/, "").trim();
    return withoutCode || explicit;
  }
  if (!useTopicFallback) return "";
  return propertyString(metrics[0]?.properties || {}, "topic", "category").trim()
    || String(metrics[0]?.label || "").trim();
}

// These are the only curvature selectors defined by the supplied Kumu theme
// and understood by DisclosureGraphCanvas. Larger metric families must reuse
// an available selector rather than emitting an inert tag such as curve-n52.
const SUPPORTED_KUMU_CURVATURES = [
  -0.7,
  -0.6,
  -0.5,
  -0.4,
  -0.3,
  -0.25,
  -0.2,
  -0.15,
  -0.1,
  0,
  0.1,
  0.15,
  0.2,
  0.25,
  0.3,
  0.4,
  0.5,
  0.6,
  0.7,
] as const;

function metricSort(left: DisclosureGraphNode, right: DisclosureGraphNode): number {
  return left.label.localeCompare(right.label, "en", {
    numeric: true,
    sensitivity: "base",
  }) || left.id.localeCompare(right.id);
}

function curvatureForMetric(index: number, count: number): number {
  const configured = KUMU_CURVATURES[count];
  if (configured) return configured[index] ?? 0;
  if (count <= 1) return 0;
  const boundedIndex = Math.max(0, Math.min(count - 1, index));
  const selectorIndex = Math.round(
    ((SUPPORTED_KUMU_CURVATURES.length - 1) * boundedIndex) / (count - 1),
  );
  return SUPPORTED_KUMU_CURVATURES[selectorIndex] ?? 0;
}

function curvatureTag(curvature: number): string {
  if (Math.abs(curvature) < 0.005) return "curve-0";
  const direction = curvature < 0 ? "n" : "p";
  return `curve-${direction}${Math.round(Math.abs(curvature) * 100)}`;
}

function withKumuCurveTag(
  properties: Record<string, unknown>,
  tag: string,
): Record<string, unknown> {
  const rawTags = properties.tags;
  const tags = Array.isArray(rawTags)
    ? rawTags.map(String)
    : String(rawTags || "")
      .split(/[\s,|]+/)
      .filter(Boolean);
  if (tags.some((item) => /^curve-(?:0|[np]\d+)$/i.test(item))) {
    return properties;
  }
  return { ...properties, tags: [...tags, tag] };
}

function metricDisplayLabel(
  node: DisclosureGraphNode,
  useFamilyTitle = true,
): string {
  const code = metricCode(node).trim();
  const label = String(node.label || "").trim();
  const familyTitle = useFamilyTitle ? metricFamilyTitle(code, [node], false) : "";
  if (code && familyTitle && familyTitle !== label) {
    return `${code} — ${familyTitle}`;
  }
  if (!code || !label || label.toLocaleLowerCase().includes(code.toLocaleLowerCase())) {
    return label || code;
  }
  return `${code} — ${label}`;
}

/**
 * Produces the visual graph without mutating the canonical response. In overview
 * mode each Disclosure is projected to one report-to-metric edge, while the
 * original disclosure node remains attached to that edge for details.
 */
export function deriveGraphDisplayData(
  graph: DisclosureGraphResponse,
  filters: DisclosureGraphFilters,
  mode: GraphDisplayMode,
): GraphDisplayData {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const edgeIndex = buildEdgeIndex(graph.edges);
  const disclosures = graph.nodes.filter((node) => normalizeNodeType(node.type) === "disclosure");
  const includedDisclosures = new Set<string>();
  const canonicalMetricFamilies = new Map<string, DisclosureGraphNode[]>();
  const disclosureRelations = new Map<
    string,
    {
      report?: DisclosureGraphNode;
      metric?: DisclosureGraphNode;
      evidence: Array<{ node: DisclosureGraphNode; relationType: string }>;
    }
  >();

  for (const disclosure of disclosures) {
    const relations = edgeIndex.get(disclosure.id) || [];
    const neighborIds = relations.map((relation) => relation.otherId);
    const report = firstNodeOfType(neighborIds, nodesById, "report");
    const metric = firstNodeOfType(neighborIds, nodesById, "metric");
    const evidence = relations.flatMap(({ edge, otherId }) => {
      const node = nodesById.get(otherId);
      if (!node || normalizeNodeType(node.type) !== "evidence") return [];
      return [{ node, relationType: edge.type }];
    });
    disclosureRelations.set(disclosure.id, { report, metric, evidence });
    if (report && metric) {
      const code = metricCode(metric);
      const family = canonicalMetricFamilies.get(code) ?? [];
      if (!family.some((item) => item.id === metric.id)) family.push(metric);
      canonicalMetricFamilies.set(code, family);
    }
    if (matchesDisclosureFilters(disclosure, report, metric, filters)) {
      includedDisclosures.add(disclosure.id);
    }
  }

  const visibleNodeIds = new Set<string>();
  const displayEdges: GraphDisplayData["edges"] = [];
  const collapsedMetricCodes = new Set(filters.collapsedMetricCodes);
  const metricGroups = new Map<
    string,
    { id: string; metrics: DisclosureGraphNode[]; disclosures: DisclosureGraphNode[] }
  >();

  // Build the visible code family before projecting edges. It controls which
  // nodes are rendered, while canonicalMetricFamilies above controls stable
  // curve assignment independently of report/status/topic filters.
  for (const disclosureId of includedDisclosures) {
    const disclosure = nodesById.get(disclosureId)!;
    const relation = disclosureRelations.get(disclosureId)!;
    const metric = relation.metric!;
    const code = metricCode(metric);
    const groupId = `metric-group:${encodeURIComponent(code)}`;
    const group = metricGroups.get(code) ?? { id: groupId, metrics: [], disclosures: [] };
    if (!group.metrics.some((item) => item.id === metric.id)) group.metrics.push(metric);
    group.disclosures.push(disclosure);
    metricGroups.set(code, group);
  }

  const metricCurveTags = new Map<string, string>();
  for (const metrics of canonicalMetricFamilies.values()) {
    metrics.sort(metricSort);
    metrics.forEach((metric, index) => {
      metricCurveTags.set(
        metric.id,
        curvatureTag(curvatureForMetric(index, metrics.length)),
      );
    });
  }

  for (const disclosureId of includedDisclosures) {
    const disclosure = nodesById.get(disclosureId)!;
    const relation = disclosureRelations.get(disclosureId)!;
    const report = relation.report!;
    const metric = relation.metric!;
    const code = metricCode(metric);
    const group = metricGroups.get(code)!;
    const curveTag = metricCurveTags.get(metric.id) || "curve-0";
    let metricTargetId = metric.id;

    if (collapsedMetricCodes.has(code)) {
      metricTargetId = group.id;
    } else {
      visibleNodeIds.add(metric.id);
    }

    visibleNodeIds.add(report.id);
    if (mode === "overview") {
      displayEdges.push({
        id: `projected:${disclosure.id}`,
        type: "disclosure",
        source: report.id,
        target: metricTargetId,
        label: disclosureStatus(disclosure),
        properties: withKumuCurveTag(
          { ...disclosure.properties, disclosure_id: disclosure.id },
          curveTag,
        ),
        disclosure_id: disclosure.id,
        disclosure,
      });
    } else {
      visibleNodeIds.add(disclosure.id);
      displayEdges.push(
        {
          id: `has-disclosure:${disclosure.id}`,
          type: "has_disclosure",
          source: report.id,
          target: disclosure.id,
          properties: { disclosure_id: disclosure.id },
          disclosure_id: disclosure.id,
          disclosure,
        },
        {
          id: `assesses:${disclosure.id}`,
          type: "assesses",
          source: disclosure.id,
          target: metricTargetId,
          properties: withKumuCurveTag(
            { ...disclosure.properties, disclosure_id: disclosure.id },
            curveTag,
          ),
          disclosure_id: disclosure.id,
          disclosure,
        },
      );
      for (const evidenceRelation of relation.evidence) {
        const evidence = evidenceRelation.node;
        visibleNodeIds.add(evidence.id);
        const relationType = evidenceRelation.relationType === "candidate_evidence"
          ? "candidate_evidence"
          : "supported_by";
        displayEdges.push({
          id: `${relationType}:${disclosure.id}:${evidence.id}`,
          type: relationType,
          source: disclosure.id,
          target: evidence.id,
          properties: {
            disclosure_id: disclosure.id,
            evidence_role: relationType === "candidate_evidence" ? "candidate" : "supporting",
          },
          disclosure_id: disclosure.id,
          disclosure,
        });
      }
    }
  }

  const displayNodes: GraphDisplayData["nodes"] = graph.nodes
    .filter((node) => visibleNodeIds.has(node.id))
    .map((node) => ({
      ...node,
      short_label: normalizeNodeType(node.type) === "metric" ? metricCode(node) : node.label,
      display_label: normalizeNodeType(node.type) === "metric"
        ? metricDisplayLabel(
            node,
            (canonicalMetricFamilies.get(metricCode(node))?.length || 1) === 1,
          )
        : node.label,
    }));

  for (const [code, group] of metricGroups) {
    const collapsed = collapsedMetricCodes.has(code);
    // The code node is a visual projection only. When a family is expanded,
    // injecting an extra hub and member edges changes both cardinality and the
    // Force topology, which is why the old graph did not resemble Kumu.
    if (!collapsed) continue;
    const topic = propertyString(group.metrics[0]?.properties, "topic", "category");
    const familyTitle = metricFamilyTitle(code, group.metrics);
    const groupLabel = familyTitle ? `${code} — ${familyTitle}` : code;
    const metricNames = group.metrics.map((metric) => metric.label).filter(Boolean);
    const description = [
      topic ? `Topic: ${topic}` : "",
      metricNames.length > 1
        ? `Sub-items:\n${metricNames.map((name) => `- ${name}`).join("\n")}`
        : "",
    ].filter(Boolean).join("\n");
    displayNodes.push({
      id: group.id,
      type: "metric",
      label: groupLabel,
      short_label: code,
      display_label: groupLabel,
      group_id: code,
      synthetic: true,
      properties: {
        metric_code: code,
        name: familyTitle,
        family_label: familyTitle,
        description,
        metric_names: metricNames,
        search_terms: metricNames,
        topic,
        metric_count: group.metrics.length,
        metric_ids: group.metrics.map((metric) => metric.id),
        disclosure_count: group.disclosures.length,
        collapsed,
      },
    });
    visibleNodeIds.add(group.id);
  }

  return {
    nodes: displayNodes,
    edges: displayEdges.filter(
      (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
    ),
    underlyingDisclosureCount: includedDisclosures.size,
  };
}

export function mergeDisclosureGraphs(
  graphs: DisclosureGraphResponse[],
  ownerLabel = "Selected reports",
): DisclosureGraphResponse {
  const nodes = new Map<string, DisclosureGraphNode>();
  const edges = new Map<string, DisclosureGraphEdge>();

  for (const graph of graphs) {
    for (const node of graph.nodes) {
      const existing = nodes.get(node.id);
      if (!existing) {
        nodes.set(node.id, node);
      } else if (normalizeNodeType(node.type) === "metric") {
        nodes.set(node.id, {
          ...existing,
          ...node,
          properties: { ...existing.properties, ...node.properties },
        });
      }
    }
    for (const edge of graph.edges) {
      let id = edge.id;
      let suffix = 2;
      while (edges.has(id)) {
        const existing = edges.get(id)!;
        if (
          existing.source === edge.source &&
          existing.target === edge.target &&
          existing.type === edge.type
        ) {
          id = "";
          break;
        }
        id = `${edge.id}:${suffix++}`;
      }
      if (id) edges.set(id, id === edge.id ? edge : { ...edge, id });
    }
  }

  const nodeList = [...nodes.values()];
  const edgeList = [...edges.values()];
  const countTypes = (values: Array<{ type: string }>) =>
    values.reduce<Record<string, number>>((accumulator, item) => {
      accumulator[item.type] = (accumulator[item.type] || 0) + 1;
      return accumulator;
    }, {});
  return {
    schema_version: graphs[0]?.schema_version || "1.0",
    graph_id: `reports:${graphs.map((graph) => graph.graph_id || graph.owner?.id || "unknown").join(",")}`,
    graph_revision: graphs.map((graph) => graph.graph_revision).join("|"),
    owner: { type: "reports", id: "selected", label: ownerLabel },
    scope_key: graphs.every((graph) => graph.scope_key === graphs[0]?.scope_key)
      ? graphs[0]?.scope_key
      : null,
    framework: graphs.every((graph) => graph.framework === graphs[0]?.framework)
      ? graphs[0]?.framework
      : null,
    nodes: nodeList,
    edges: edgeList,
    stats: {
      node_count: nodeList.length,
      edge_count: edgeList.length,
      node_types: countTypes(nodeList),
      edge_types: countTypes(edgeList),
    },
    truncated: graphs.some((graph) => graph.truncated),
  };
}

export function mergeGraphExpansion(
  base: DisclosureGraphResponse,
  expansion: DisclosureGraphResponse,
): DisclosureGraphResponse {
  const merged = mergeDisclosureGraphs([base, expansion], base.owner?.label || "Graph");
  return {
    ...merged,
    graph_id: base.graph_id,
    graph_revision: base.graph_revision,
    owner: base.owner,
    scope_key: base.scope_key,
    framework: base.framework,
  };
}

export function parseStoredGraphPositions(
  raw: string | null,
  validNodeIds: Iterable<string>,
): StoredGraphPositions {
  const valid = new Set(validNodeIds);
  try {
    const parsed = JSON.parse(raw || "") as Partial<StoredGraphPositions>;
    const positions: StoredGraphPositions["positions"] = {};
    if (parsed && typeof parsed.positions === "object" && parsed.positions) {
      for (const [id, position] of Object.entries(parsed.positions)) {
        const x = Number((position as { x?: unknown }).x);
        const y = Number((position as { y?: unknown }).y);
        if (valid.has(id) && Number.isFinite(x) && Number.isFinite(y)) {
          positions[id] = { x, y };
        }
      }
    }
    return { revision: String(parsed?.revision || ""), positions };
  } catch {
    return { revision: "", positions: {} };
  }
}

export function graphFilterOptions(graph: DisclosureGraphResponse) {
  const reports = graph.nodes.filter((node) => normalizeNodeType(node.type) === "report");
  const metrics = graph.nodes.filter((node) => normalizeNodeType(node.type) === "metric");
  const disclosures = graph.nodes.filter((node) => normalizeNodeType(node.type) === "disclosure");
  const unique = (values: string[]) => [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
  return {
    frameworks: unique([
      ...graph.nodes.map((node) => propertyString(node.properties, "framework")),
      graph.framework || "",
    ]),
    scopes: unique([
      ...graph.nodes.map((node) => propertyString(node.properties, "scope_key", "scope")),
      graph.scope_key || "",
    ]),
    years: unique(reports.map((node) => propertyString(node.properties, "report_year", "year"))).sort(
      (a, b) => Number(b) - Number(a),
    ),
    topics: unique(metrics.map((node) => propertyString(node.properties, "topic", "category", "dimension"))),
    statuses: unique(disclosures.map(disclosureStatus)),
    metricCodes: unique(metrics.map(metricCode)),
  };
}
