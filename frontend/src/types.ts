export type WorkspaceKey = "ask" | "ingest" | "inspect";

export type RuntimeDocumentSummary = {
  doc_id: string;
  doc_title: string;
  doc_type: string;
  related_docs: string[];
};

export type RuntimeSummary = {
  project: string;
  provider: string;
  model: string;
  index_dir: string;
  doc_count: number;
  docs: RuntimeDocumentSummary[];
};

export type TraceSummary = {
  trace_id: string;
  timestamp: string;
  query_preview: string;
  docs_routed: number;
  sections_retrieved: number;
  ttft_seconds: number;
  total_seconds: number;
  routing_seconds: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  routing_broadened: boolean;
  retrieval_mode: string;
  model: string;
};

export type QuerySource = {
  node_ref: string;
  doc_id: string;
  section: string;
  page_range: string;
};

export type QueryMetrics = {
  ttft_seconds: number;
  total_time_seconds: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  llm_calls: number;
  estimated_token_usage?: boolean;
};

export type QueryResponse = {
  answer: string;
  selected_docs: string[];
  selected_nodes: string[];
  retrieved_context: string;
  sources: QuerySource[];
  trace?: Record<string, unknown> | null;
  metrics?: QueryMetrics | null;
};

export type QueryRequest = {
  project: string;
  model?: string | null;
  user_query: string;
  conversation_context?: Array<{ role: string; content: string }> | null;
  max_docs: number;
  reasoning_effort?: string | null;
  retrieval_mode: "hybrid" | "pageindex";
  advanced_retrieval: {
    enabled: boolean;
    enable_planning: boolean;
    enable_adaptive_width: boolean;
    enable_node_expansion: boolean;
    max_docs_cap: number;
    max_nodes_cap: number;
  };
};

export type IngestionRelationshipMode = "off" | "basic" | "enhanced";

export type IngestionRequest = {
  project: string;
  model?: string | null;
  doc_id: string;
  doc_title: string;
  doc_type: string;
  file: File;
  top_sections_target?: number | null;
  relationship_mode: IngestionRelationshipMode;
};

export type IngestionStep = {
  name: string;
  detail: string;
  payload?: Record<string, unknown> | unknown[] | string | null;
};

export type IngestionTrace = {
  doc_id: string;
  file_path: string;
  file_type: string;
  tree_path: string;
  steps: IngestionStep[];
  relationship_mode: IngestionRelationshipMode;
  relationship_reconciliation?: Record<string, unknown> | null;
};

export type IngestionResponse = {
  master_node: Record<string, unknown>;
  per_doc_tree: Record<string, unknown>;
  trace: IngestionTrace;
};

export type DocumentTreeResponse = {
  doc_id: string;
  tree: Record<string, unknown>;
};

export type TraceSection = {
  node_ref: string;
  doc_id: string;
  node_id: string;
  title: string;
  page_start: number;
  page_end: number;
  estimated_tokens: number;
  truncated: boolean;
  text: string;
};

export type TraceMetricsSummary = {
  ttft_seconds: number;
  total_seconds: number;
  routing_seconds: number;
  pipeline_seconds: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  llm_calls: number;
  estimated_token_usage: boolean;
  context_tokens: number;
  docs_routed: number;
  sections_retrieved: number;
  sections_dropped_by_verifier: number;
  truncation_occurred: boolean;
  routing_broadened: boolean;
  context_utilization_pct: number | null;
  retrieval_token_ratio: number | null;
  estimated_cost_usd: number | null;
};

export type TraceDetail = {
  trace_id: string;
  project: string;
  timestamp: string;
  model: string;
  query: string;
  conversation_snapshot: Array<Record<string, unknown>>;
  routing: {
    context_snapshot: string;
    raw_response: string;
    broadened: boolean;
    selected_doc_ids: string[];
  };
  navigation_map: Record<string, string[]>;
  sections_used: TraceSection[];
  answer: string;
  retrieved_context_preview: string;
  retrieval_mode: string;
  verification_applied: boolean;
  metrics: TraceMetricsSummary;
};

export type PostHocAnalysis = {
  trace_id: string;
  routing_explanation: string;
  section_scores: Array<{
    node_ref: string;
    title: string;
    score: number;
    explanation: string;
  }>;
  generated_at: string;
  model_used: string;
};

export type WorkspaceSidebarSection = {
  title: string;
  description?: string;
  children: React.ReactNode;
};

export type WorkspaceRenderModel = {
  sidebarSections: WorkspaceSidebarSection[];
  content: React.ReactNode;
};
