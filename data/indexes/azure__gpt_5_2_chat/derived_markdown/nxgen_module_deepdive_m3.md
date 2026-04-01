NxGen AI Sales Platform

Technical Deep-Dive: Module 3

Knowledge Base Engine

Ingestion, Processing, Retrieval, Graph, Cache, and Lifecycle

February 2026  |  Internal — Engineering Team

| Document Scope This document covers the complete KB Engine module: ingestion pipeline with fan-out routing, content classification with cross-KB intelligence extraction, Foundry IQ integration and configuration, Product Relationship Graph (schema, 4-layer building strategy, confidence model, traversal algorithms), five structured knowledge stores with schemas and metadata models, multi-source retrieval orchestration with per-agent source mapping, cache strategy with invalidation, KB lifecycle management (updates, removal cascades, confidence decay), and all architectural decisions with rationale. |
| --- |

# Table of Contents

3.1 Ingestion Layer — Fan-out pipeline, caveats, file types, parsing strategy

3.2 Processing Pipeline — Content Classifier, cross-KB intelligence extraction, chunking

3.3 KB Core — Foundry IQ architecture, structured stores, metadata per KB type

3.4 Product Relationship Graph — Schema, relationship types, 4-layer building, confidence, traversal

3.5 Retrieval Layer — Multi-source query orchestration, per-agent source mapping, query construction

3.6 Cache Layer — What’s cached, TTLs, invalidation strategy

3.7 KB Lifecycle — Update mechanisms, document removal cascade, confidence decay

3.8 Nuances and Risks — Foundry IQ preview status, LLM costs, cold start, quality monitoring

3.9 Key Technical Decisions Summary

# Module 3: Knowledge Base Engine

| Module Purpose The KB Engine is the intelligence layer of the platform. It ingests, processes, classifies, indexes, and retrieves all knowledge that agents use to generate recommendations, discovery questions, emails, and competitive positioning. It is two parallel systems working together: (1) Azure Foundry IQ — a managed RAG-as-a-service that handles document parsing, chunking, vectorization, and hybrid retrieval for unstructured content, and (2) Our custom KB layer — which handles the Product Relationship Graph, Content Classification with cross-KB intelligence extraction, structured knowledge stores (competitor profiles, customer patterns, playbook index), tenant KB lifecycle management, caching, and the Context Assembler that merges all sources into optimized agent context. |
| --- |

| Critical Architecture Distinction Foundry IQ eliminates the need to build: document parsing, content chunking, embedding generation, vector store management, full-text index management, hybrid query engine (vector + keyword + reranking), query planning and decomposition, and multi-source routing. What we still build: Product Relationship Graph (our proprietary moat), Content Classifier (cross-KB intelligence extraction), structured stores, tenant KB lifecycle management, Context Assembler, and cache layer. This distinction must be understood by the entire team — we are building ON TOP of Foundry IQ, not replacing it. |
| --- |

## 3.1 Ingestion Layer

The ingestion layer is a fan-out pipeline. A single file upload triggers multiple parallel downstream processes: raw storage, Foundry IQ indexing, content classification with intelligence extraction, and metadata cataloging. It is not a simple file upload.

### 3.1.1 Ingestion Flow — Step by Step

Upload to tenant-scoped Blob Storage: Every file goes to a blob container organized as {tenant_id}/kb/{category}/{filename}. Tenant isolation is enforced from the first byte. The blob container is configured as a Foundry IQ knowledge source, so Foundry IQ will automatically detect new files.

Deduplication and Versioning: Check the KB catalog (PostgreSQL) for an existing file with the same name. If found, this is a version update — increment version counter, update blob URL, trigger re-indexing. If not found, create a new catalog entry with version 1. Naive 'just upload to blob' would create duplicates and stale data.

Write to KB Catalog (PostgreSQL): Insert a record with doc_id, tenant_id, filename, blob_url, declared_category, version, file_type, size_bytes, status='processing', uploaded_at. This is the source of truth for what documents exist per tenant.

Trigger Content Classifier: Enqueue an async background job to classify the document and extract structured intelligence. This runs independently of the Foundry IQ indexing pipeline.

Foundry IQ Auto-Indexing: Foundry IQ's indexer detects the new blob and automatically parses, chunks, vectorizes, and indexes it. This happens on a configurable schedule (hourly for MVP) or can be triggered explicitly via API. We do NOT build this pipeline — it is fully managed.

### 3.1.2 Ingestion Caveats

Caveat 1 — Multi-destination routing: A case study PDF about 'How Acme Manufacturing deployed monitoring' simultaneously: lands in Blob Storage (raw), gets indexed by Foundry IQ (for semantic retrieval), gets analyzed by Content Classifier to extract product mentions and update the Product Relationship Graph, gets competitor mentions extracted to the Competitor Store, gets customer outcome data extracted to Customer Patterns, and gets metadata written to the KB Catalog. One upload, six destinations.

Caveat 2 — Deduplication and versioning: When a tenant uploads a new product catalog CSV, the system must detect this is an update to an existing catalog (not a second catalog), version it, trigger re-indexing in Foundry IQ, and re-run both the Product Graph co-occurrence analysis (Layer 1) and LLM inference (Layer 2) on the updated data.

Caveat 3 — Tenant-scoped isolation: Every upload is tenant-scoped from the moment it enters the system. The blob container path, the Foundry IQ knowledge base, and all PostgreSQL metadata include tenant_id. A bug that leaks Tenant A's data into Tenant B's KB is a catastrophic trust violation. Row-level isolation in PostgreSQL + separate blob containers per tenant.

### 3.1.3 Supported File Types and Parsing Strategy

| File Type | Parser | Destination | Notes |
| --- | --- | --- | --- |
| CSV / Excel (.xlsx) | Custom (pandas) | Product catalog table + Foundry IQ | Structured data. Each row becomes a product record in PostgreSQL. Product descriptions also sent to Foundry IQ as individual documents for semantic search. |
| PDF | Foundry IQ (Azure Content Understanding) | Foundry IQ index + Content Classifier | Layout-aware: tables extracted as units, headers preserved as metadata, figures get alt-text. |
| Word (.docx) | Foundry IQ | Foundry IQ index + Content Classifier | Sections, headings, and formatting preserved during chunking. |
| Markdown (.md) | Foundry IQ | Foundry IQ index + Content Classifier | Header-based chunking. Code blocks preserved. |
| PowerPoint (.pptx) | Foundry IQ | Foundry IQ index + Content Classifier | Each slide becomes a chunk with slide title as metadata. |
| Plain text (.txt) | Foundry IQ | Foundry IQ index + Content Classifier | Paragraph-level chunking. |
| Transaction CSV | Custom (pandas) | Product Graph (Layer 1 analysis) ONLY | Not sent to Foundry IQ. Used exclusively for co-occurrence and sequential pattern analysis to build the Product Relationship Graph. |

| DECISION: Document Parsing Strategy Decided: Lean on Foundry IQ for all unstructured document parsing. Build custom parsers only for structured data (product catalog CSV/Excel, transaction history CSV). Rationale: Foundry IQ handles PDF, Word, PowerPoint, Markdown, HTML, and plain text with layout-aware enrichment via Azure Content Understanding. Building our own multi-format parser would take 3-4 weeks of engineering time and produce inferior results compared to Microsoft's managed service. Our custom parsing is limited to CSV/Excel where we need structured row-level control. |
| --- |

## 3.2 Processing Pipeline

### 3.2.1 Content Classifier — Cross-KB Intelligence Extraction

The Content Classifier is the intelligence extraction hub. When a document is uploaded, it does not just get stored — it gets analyzed by an LLM to extract structured intelligence that feeds multiple downstream stores simultaneously.

A single case study document can and should update:

Foundry IQ: Full text indexed for semantic retrieval (handled automatically by Foundry IQ’s indexer).

Product Relationship Graph: If the case study mentions 'Acme deployed firewall AND monitoring together', extract a CROSS_SELL edge between those products.

Competitor Store: If the case study mentions 'Acme considered SolarWinds but chose our solution', add that to the SolarWinds competitor profile.

Customer Patterns: If the case study reports '60% improvement in incident response, ROI in 8 months', add this as a success pattern for manufacturing + monitoring.

KB Catalog Metadata: Tag the document with products mentioned, primary category, secondary categories, and classification timestamp.

Classification categories:

| Category | Description | Intelligence Extracted |
| --- | --- | --- |
| product_catalog | Product listings, specs, feature sheets | SKUs, features, pricing, tier classification, category mapping |
| case_study | Customer success stories, deployment narratives | Products used, customer industry/segment, pain points, outcomes, metrics, competitors considered, decision makers involved |
| sales_playbook | Selling methodologies, objection handling, discovery guides | Applicable workflows, target segments, key objections, discovery questions, positioning approaches, competitive differentiators |
| competitor_intel | Battle cards, competitive analysis, win/loss reports | Competitor names, strengths/weaknesses, win/loss themes, counter-arguments, competing product mapping |
| pricing_sheet | Price lists, discount structures, bundle offers | SKU-price mapping, tier pricing, bundle definitions, discount rules |
| technical_spec | Datasheets, architecture diagrams, integration guides | Product capabilities, compatibility requirements, integration points, technical prerequisites |
| training_material | Sales training, onboarding guides, methodology docs | Best practices, common mistakes, talk tracks, qualification criteria |
| general | Anything that doesn’t fit above categories | Basic metadata only |

The classifier assigns a primary category AND secondary categories (a document can be both a case study and competitor intel). The LLM extraction prompt returns structured JSON with all intelligence fields:

| // LLM Classification Prompt (simplified) System: Classify this document and extract structured intelligence. Categories: product_catalog, case_study, sales_playbook, competitor_intel, pricing_sheet, technical_spec, training_material, general Return JSON: { "primary_category": "case_study", "secondary_categories": ["competitor_intel"], "extracted_intelligence": { "products_mentioned": [ {"name": "PA-5200 Firewall", "sku_if_found": "FW-PA-5200", "context": "deployed as perimeter defense"} ], "product_relationships_detected": [ {"product_a": "FW-PA-5200", "product_b": "MON-DATDOG-ENT", "relationship_type": "CROSS_SELL", "evidence": "Acme deployed both in their network refresh"} ], "competitors_mentioned": [ {"name": "SolarWinds", "context": "considered but rejected", "compared_to_our_product": "MON-DATDOG-ENT"} ], "customer_info": { "company_name": "Acme Manufacturing", "industry": "manufacturing", "segment": "mid_market", "outcome": "60% improvement in incident response", "products_used": ["FW-PA-5200", "MON-DATDOG-ENT"] }, "key_metrics": {"revenue_impact": "$48K deal", "timeline": "3 months", "roi": "8 months"} } } |
| --- |

| DECISION: Classification Model Decided: Use Gpt-4.1-mini for content classification and intelligence extraction. Reserve Gpt-4.1 for agent conversations. Rationale: Every uploaded document triggers an LLM call. A tenant uploading 500 documents during onboarding means 500 LLM calls. Gpt-4.1-mini is 10-15x cheaper than Gpt-4.1 and produces sufficient quality for structured extraction tasks. The classification prompt is well-constrained with a clear JSON schema, which mini handles well. |
| --- |

### 3.2.2 Intelligence Routing — Fan-Out From Classifier

After classification, the extracted intelligence is routed to the appropriate stores. Each routing step is independent and can fail without affecting the others:

Product Graph edges: For each product_relationships_detected entry, call product_graph.add_or_strengthen_edge() with confidence_source='document_extracted' and confidence_score=0.5 (medium confidence from document mention). If the edge already exists, strengthen it by updating the evidence array and boosting the LLM confidence score.

Competitor profiles: For each competitors_mentioned entry, call competitor_store.upsert() to add or update the competitor profile. New competitor mentions create a new profile; existing competitors get their context enriched with new data points.

Customer patterns: If the document contains customer outcome data (industry, segment, products used, metrics), add this as a success pattern to the customer_patterns table. Multiple case studies for the same industry+segment+product combination aggregate into a stronger pattern with higher sample_count.

KB Catalog metadata: Update the catalog entry with: status='indexed', primary_category, secondary_categories, products_mentioned array, classification_metadata (full JSON), classified_at timestamp.

### 3.2.3 Chunking Strategy

Chunking is handled by Foundry IQ for all unstructured content. We configure but do not build the chunking pipeline.

Foundry IQ’s chunking behavior:

Text documents: Split by semantic boundaries (paragraphs, sections). Configurable max chunk size. Overlapping windows maintain context across chunk boundaries.

Layout-aware documents (with Azure Content Understanding): Tables extracted as complete units (never split mid-table). Headers and section titles preserved as metadata on each chunk. Figures get alt-text descriptions.

Vectorization: Each chunk is automatically vectorized using Azure OpenAI embedding model (text-embedding-3-large). Both the vector and original text are stored for hybrid retrieval (keyword + vector + semantic reranking).

The one area where we do custom 'chunking' is the product catalog. Products are structured data, not prose. Each product becomes its own document in the Foundry IQ index with fielded data: SKU, name, description, category, price, features. This is not chunked — it is indexed as structured fielded documents.

| DECISION: Chunking Ownership Decided: Delegate all unstructured document chunking to Foundry IQ. Custom chunking only for product catalog structured data. Rationale: Building a high-quality chunking pipeline that handles tables, figures, headers, multi-column layouts, and embedded images is a multi-month engineering effort. Foundry IQ with Azure Content Understanding handles all of this out of the box with layout-aware enrichment. Our chunking responsibility is limited to converting product catalog CSV rows into individual searchable documents. |
| --- |

## 3.3 KB Core — Knowledge Stores

| Two Types of Stores Per Tenant Each tenant has: (1) ONE Foundry IQ Knowledge Base — the unified semantic search layer containing all unstructured content (product descriptions, case studies, playbooks, competitor sheets, technical specs). Not separated into multiple indexes per content type — it is one KB with multiple knowledge sources. (2) FIVE PostgreSQL structured stores — holding queryable structured data: products table, product_relationships (graph), competitor_profiles, customer_patterns, playbook_index, plus the kb_documents catalog. |
| --- |

### 3.3.1 Foundry IQ Knowledge Base Architecture

Per-tenant architecture in Foundry IQ:

| Tenant KB: tenant-{tenant_id}-kb (Foundry IQ Knowledge Base) ├── Knowledge Source: product-descriptions    (blob: {tenant_id}/kb/product_catalog/) ├── Knowledge Source: case-studies            (blob: {tenant_id}/kb/case_studies/) ├── Knowledge Source: sales-playbooks         (blob: {tenant_id}/kb/sales_playbooks/) ├── Knowledge Source: competitor-intel         (blob: {tenant_id}/kb/competitor_intel/) └── Knowledge Source: technical-specs          (blob: {tenant_id}/kb/technical_specs/) Configuration: LLM Model: gpt-4.1 (for query planning) Retrieval Reasoning Effort: configurable per agent call (low/medium/high) Indexer Schedule: every 1 hour (MVP), configurable per tenant (full product) Semantic Ranker: enabled |
| --- |

When an agent queries this KB, Foundry IQ’s agentic retrieval engine decides which sources to hit based on the query. A query about 'monitoring solutions for manufacturing' pulls from product descriptions AND case studies. A query about 'competing against SolarWinds' pulls from competitor intel. The source routing is handled by Foundry IQ’s query planning LLM, not our code.

Retrieval reasoning effort levels:

| Level | Behavior | Cost | Use Case |
| --- | --- | --- | --- |
| Low | Direct keyword + vector search, no LLM query planning. Fast. | Lowest (no LLM tokens for planning) | KB Search plugin (rep typing a search query), quick action plugins, background enrichment agents |
| Medium | LLM decomposes query into 2-3 subqueries, runs in parallel, semantic reranking. | Medium (query planning tokens) | Cross-Sell agent, Upsell agent — standard agent queries during conversation |
| High | Full multi-step reasoning: plans queries, iterates if initial results insufficient, follows chains across sources. | Highest (multiple planning iterations) | Full Solution agent — complex multi-hop queries spanning case studies + product specs + playbooks |

### 3.3.2 Metadata Model Per KB Type

Beyond what Foundry IQ indexes, we maintain rich structured metadata in PostgreSQL for filtered retrieval and agent consumption:

Product Catalog metadata: sku, name, category, subcategory, price, price_unit, tier (entry/mid/top), status (active/discontinued/legacy), features array, target_segment array, vendor, last_updated. Used for: direct SQL lookups by SKU, category filtering, tier-based upgrade path detection.

Case Study metadata: title, customer_industry, customer_segment, customer_size, products_featured array, pain_points_addressed array, outcome_metrics (JSON with improvement percentages, ROI timeline, deal size), sales_cycle_length, decision_makers_involved, competing_solutions_considered, published_date. Used for: 'find case studies matching this customer’s industry and segment' queries from agents.

Sales Playbook metadata: title, applicable_workflows array (cross-sell, upsell, full-solution), source_product_categories, target_product_categories, target_segments, target_industries, key_objections, discovery_questions, positioning_approaches, competitive_differentiators. Used for: 'find the right playbook for this workflow + customer combination'.

Competitor Intel metadata: competitor_name, competing_product_skus array, strengths, weaknesses, win_themes, loss_themes, typical_objections, counter_arguments, win_rate, average_deal_size_when_competing, source_documents array. Used for: instant competitor card generation when a competitor is mentioned in conversation.

Customer Patterns metadata: pattern_type (successful_cross_sell, upgrade_journey, churn_risk), industry, segment, starting_products, outcome_products, average_deal_size, average_sales_cycle_days, success_rate, sample_count, common_triggers, common_blockers, derived_from (transaction_analysis, case_study_extraction, conversation_mining). Used for: 'Based on 12 similar customers, this opportunity has a 68% success rate with $35K average deal.'

## 3.4 Product Relationship Graph

| Why This Is Our Proprietary Moat Foundry IQ does not know that Product A cross-sells with Product B, that Product C is an upgrade from Product D, or that Products E+F+G form a bundle. This domain-specific knowledge is what makes our recommendations intelligent rather than generic. The Product Relationship Graph is what we build; Foundry IQ cannot replace it. No competitor can replicate a tenant’s graph without their data and usage history. |
| --- |

### 3.4.1 Graph Schema

| CREATE TABLE product_relationships ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id), -- The relationship product_a_sku VARCHAR(100) NOT NULL, product_b_sku VARCHAR(100) NOT NULL, relationship_type VARCHAR(50) NOT NULL, -- Types: CROSS_SELL, UPGRADE_PATH, BUNDLE, REPLACES, REQUIRES, COMPETES_WITH is_directional BOOLEAN DEFAULT true,  -- A→B differs from B→A for upgrades -- Multi-source confidence scores confidence_statistical FLOAT DEFAULT 0.0,  -- From co-occurrence (Layer 1) confidence_llm FLOAT DEFAULT 0.0,           -- From LLM inference (Layer 2) confidence_usage FLOAT DEFAULT 0.0,          -- From platform usage (Layer 3) confidence_admin INT DEFAULT 50,             -- 100=confirmed, 50=not reviewed, 0=rejected -- Composite confidence (computed column) confidence_composite FLOAT GENERATED ALWAYS AS ( confidence_statistical * 0.35 + confidence_llm * 0.20 + confidence_usage * 0.30 + (confidence_admin / 100.0) * 0.15 ) STORED, -- Evidence trail evidence JSONB DEFAULT '[]', -- Example: [{"source": "co_occurrence", "detail": "58% of customers..."}, --          {"source": "case_study_doc_123", "detail": "Acme deployed both"}] -- Freshness tracking last_statistical_update TIMESTAMPTZ, last_usage_update TIMESTAMPTZ, last_admin_review TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(tenant_id, product_a_sku, product_b_sku, relationship_type) ); -- Indexes for graph traversal CREATE INDEX idx_graph_a ON product_relationships(tenant_id, product_a_sku); CREATE INDEX idx_graph_b ON product_relationships(tenant_id, product_b_sku); CREATE INDEX idx_graph_conf ON product_relationships(tenant_id, confidence_composite DESC); |
| --- |

### 3.4.2 Relationship Types

| Type | Meaning | Directional? | Example (IT) | Example (Insurance) |
| --- | --- | --- | --- | --- |
| CROSS_SELL | Products that complement each other | No (bidirectional) | Firewall ↔ Network Monitoring | Auto Policy ↔ Home Policy |
| UPGRADE_PATH | Higher tier of the same product | Yes (A→B = upgrade) | Basic Firewall → Enterprise Firewall | $500K Liability → $1M Liability |
| BUNDLE | Products sold together as a package | No | Switch + APs + Controller | Auto + Home + Umbrella |
| REPLACES | New product supersedes old | Yes (A→B = B replaces A) | Legacy Monitoring → Cloud Monitoring | Old Policy Type → New Policy Type |
| REQUIRES | Product A needs Product B to function | Yes (A requires B) | Wireless APs → Wireless Controller | Umbrella Policy → Underlying Auto/Home |
| COMPETES_WITH | External competitor product match | No | Our Firewall ↔ Palo Alto Firewall | Our Auto Policy ↔ State Farm Auto |

### 3.4.3 Four-Layer Graph Building Strategy

The graph builds itself through four layers, starting with zero-effort automated methods and only asking for human input where machines cannot figure it out:

Layer 1 — Purchase History Analysis (zero customer effort): Upload transaction CSV. Run co-occurrence analysis (if 50%+ of customers with Product A also have Product B → CROSS_SELL edge). Run sequential pattern mining (Product B bought 3-12 months after A → sequenced cross-sell). Run upgrade detection (customer migrates from $100 to $250 version → UPGRADE_PATH). Run basket analysis (products in same transaction → BUNDLE). Run replacement detection (stop buying A when start buying B → REPLACES). High confidence statistical relationships.

Layer 2 — LLM Catalog Inference (zero additional effort): Uses product catalog already uploaded for KB. LLM analyzes: category complementarity (Security + Networking = logical cross-sell), naming tiers (Basic → Pro → Elite = upgrade path), description references ('works with Product B'), price-based tiering (same category, 3x price, superset features = upgrade), feature superset detection (B has all of A’s features + more, A is legacy = replaces). Medium confidence inferred relationships.

Layer 3 — Usage Learning (zero ongoing effort): Automatic as platform is used. Rep accepts cross-sell rec A→B and deal closes → strengthen edge. Customers with Product A consistently mention needing Category Y in conversations → suggest new relationship. Discovery uncovers that customers with A have pain that B solves → infer relationship. Growing confidence from real usage data.

Layer 4 — Human Review (minimal, optional, targeted): Admin sees a review queue, NOT a blank canvas: 'We found 47 likely relationships. Review them?' Each shows: Product A → Product B, type, confidence %, evidence. Actions: Confirm/Edit/Reject/Skip. Can bulk-confirm high-confidence in one click. Platform works WITHOUT admin review; review makes it better but is not a gate.

### 3.4.4 Confidence Model

Each graph edge has a composite confidence score computed from four weighted sources:

| Source | Weight | Range | How It’s Computed | Decay |
| --- | --- | --- | --- | --- |
| Statistical (Layer 1) | 35% | 0.0 – 1.0 | Co-occurrence rate from purchase history | Decays 10%/month if no new co-purchases in 6 months |
| LLM Inference (Layer 2) | 20% | 0.0 – 1.0 | AI confidence from catalog analysis | No decay (re-computed on catalog update) |
| Usage (Layer 3) | 30% | 0.0 – 1.0 | Rep acceptance rate + deal close rate | Decays 15%/month if no rep acceptance in 3 months |
| Admin (Layer 4) | 15% | 0 / 50 / 100 | 100 = confirmed, 50 = not reviewed, 0 = rejected | Never decays (explicit human judgment) |

Composite formula: score = (statistical × 0.35) + (llm × 0.20) + (usage × 0.30) + (admin/100 × 0.15). Stored as a PostgreSQL generated column for query efficiency.

### 3.4.5 Graph Traversal

Agents traverse the graph using a breadth-first search from the customer’s current products:

| async def get_product_neighbors(tenant_id, product_sku, relationship_types=None, min_confidence=0.3, max_depth=2): visited = set() results = [] queue = [(product_sku, 0)]  # (sku, depth) while queue: current_sku, depth = queue.pop(0) if current_sku in visited or depth > max_depth: continue visited.add(current_sku) query = """ SELECT product_b_sku, relationship_type, confidence_composite, evidence FROM product_relationships WHERE tenant_id = $1 AND product_a_sku = $2 AND confidence_composite >= $3 ORDER BY confidence_composite DESC""" rows = await db.fetch(query, tenant_id, current_sku, min_confidence) for row in rows: results.append({ "from_product": current_sku, "to_product": row["product_b_sku"], "type": row["relationship_type"], "confidence": row["confidence_composite"], "depth": depth + 1 }) if depth + 1 < max_depth: queue.append((row["product_b_sku"], depth + 1)) return deduplicate_and_sort(results) |
| --- |

Different agents use different traversal parameters:

| Agent | Relationship Types Requested | Min Confidence | Max Depth |
| --- | --- | --- | --- |
| Cross-Sell Agent | CROSS_SELL, BUNDLE | 0.3 | 2 (direct + 1 hop) |
| Upsell Agent | UPGRADE_PATH | 0.3 | 1 (direct only) |
| Full Solution Agent | CROSS_SELL, UPGRADE_PATH, BUNDLE, REQUIRES | 0.2 (wider net) | 3 (deep traversal) |
| Account Overview Plugin | All types | 0.3 | 1 (direct only) |
| Background Graph Preloader | All types | 0.2 | 2 (preload for session) |

| DECISION: Graph Storage Decided: PostgreSQL with indexed adjacency list for MVP. Graph database (Neo4j) considered for full product when graph exceeds 10K edges per tenant. Rationale: PostgreSQL handles graph traversal well at MVP scale (hundreds of products, thousands of edges). The indexed adjacency list pattern with BFS traversal in application code is simple and performant. A graph database adds operational complexity (another service to manage) and is only justified when traversal patterns become complex (multi-hop with path filtering, cycle detection). Most tenants will have 50-500 products with 200-2000 edges — well within PostgreSQL’s capabilities. |
| --- |

## 3.5 Retrieval Layer — Multi-Source Query Orchestration

When an agent needs knowledge, the Context Assembler (Module 1) calls the KB retrieval layer. This is not a single query — it is a parallel fan-out to multiple sources, with the sources and parameters determined by the agent’s manifest.

### 3.5.1 Retrieval Flow

The unified retrieval function executes up to five parallel queries:

Foundry IQ semantic retrieval: Construct a contextual query from conversation state + customer profile. Set reasoning effort based on agent type. Returns text results with citations.

Product Graph traversal: Starting from the customer’s current products, traverse the graph for relevant relationship types. Returns product recommendations with confidence scores and evidence.

Competitor Store lookup: If any competitors were mentioned in the conversation (from intent_signals), fetch their structured profiles. Returns strengths, weaknesses, win themes, counter-arguments.

Customer Patterns query: Filter patterns by customer’s industry + segment + current product categories. Returns success rates, average deal sizes, common triggers/blockers.

Playbook Index query: Filter by active workflow type + source/target product categories + customer segment. Returns applicable playbook metadata (discovery questions, objection handling, positioning).

All five queries execute in parallel (asyncio.gather). Total retrieval latency is the maximum of the individual queries, not the sum.

### 3.5.2 Which Agent Uses Which Sources

| Agent / Plugin | Foundry IQ | Product Graph | Competitor Store | Customer Patterns | Playbooks |
| --- | --- | --- | --- | --- | --- |
| Cross-Sell Agent | Case studies for similar customers | CROSS_SELL + BUNDLE edges | Only if competitor mentioned | Matching success patterns | Cross-sell positioning |
| Upsell Agent | Case studies + tech specs | UPGRADE_PATH edges | If competing upgrades exist | Upgrade journey patterns | Upsell playbooks |
| Full Solution Agent | Case studies + tech specs + playbooks | ALL types, deep traversal | Full competitive landscape | Full pattern analysis | Solution playbooks |
| Competitor Card Plugin | Battle cards from Foundry IQ | COMPETES_WITH edges | Full profile for mentioned competitor | Win/loss patterns vs competitor | No |
| Email Generator Plugin | No | No | No | No | No (uses existing recs) |
| KB Search Plugin | Direct query with rep’s search term | No | No | No | No |
| Account Overview Plugin | No | All edges from customer products | No | Matching patterns | No |

### 3.5.3 Query Construction for Foundry IQ

The retrieval query sent to Foundry IQ is not the user’s raw message. It is constructed from the current conversation state to maximize retrieval relevance:

| def build_retrieval_query(current_state, agent_id): parts = [] # Customer context profile = current_state.customer.profile parts.append(f"{profile.industry} {profile.segment} company") # Current products (what they already have) product_categories = set(p['category'] for p in current_state.customer.products) parts.append(f"with existing {', '.join(product_categories)} infrastructure") # Discovery context (what we’ve learned) if current_state.intent_signals.get('detected_pain_points'): pains = current_state.intent_signals.detected_pain_points[:2] parts.append(f"experiencing {', '.join(pains)}") # Agent-specific focus if agent_id == 'core-cross-sell': parts.append('complementary products and solutions') elif agent_id == 'core-upsell': parts.append('upgrade options and premium features') elif agent_id == 'core-full-solution': parts.append('comprehensive solution architecture') return ' '.join(parts) # Result: 'manufacturing mid_market company with existing networking security #          infrastructure experiencing poor support response times #          complementary products and solutions' |
| --- |

## 3.6 Cache Layer

Caching strategy is differentiated by data volatility and access pattern:

| What’s Cached | Cache Location | Key Pattern | TTL | Invalidation Trigger |
| --- | --- | --- | --- | --- |
| Product graph neighbors (traversal results) | Redis | graph:{tenant_id}:{product_sku} | 6 hours | Graph edge update for this tenant |
| Product details (name, price, features) | Redis | product:{tenant_id}:{sku} | 24 hours | Product catalog re-upload |
| Competitor profiles | Redis | competitor:{tenant_id}:{name} | 12 hours | Competitor intel document update |
| Customer patterns (industry+segment) | Redis | patterns:{tenant_id}:{industry}:{segment} | 24 hours | Pattern recomputation (daily batch) |
| Foundry IQ results | NOT cached by us | N/A | N/A | Every query is contextual (includes conversation state). Caching would return stale results as conversation evolves. Foundry IQ handles its own internal caching. |
| Preloaded graph context per session | Session state (Redis via Module 1) | Part of agent_state.graph_context | Session lifetime | Computed once at session start by background enrichment agent |

Cache invalidation is event-driven. When KB content changes, a cascading invalidation function clears affected cache keys:

| async def invalidate_kb_caches(tenant_id, change_type, details): if change_type == 'product_catalog_updated': await redis.delete_pattern(f'product:{tenant_id}:*') await redis.delete_pattern(f'graph:{tenant_id}:*') # Also trigger re-run of LLM inference (Layer 2) await graph_builder_queue.enqueue('rerun_llm_inference', tenant_id) elif change_type == 'graph_edge_updated': # Targeted: only affected products await redis.delete(f'graph:{tenant_id}:{details.product_a_sku}') await redis.delete(f'graph:{tenant_id}:{details.product_b_sku}') elif change_type == 'competitor_intel_updated': await redis.delete(f'competitor:{tenant_id}:{details.competitor_name}') elif change_type == 'patterns_recomputed': await redis.delete_pattern(f'patterns:{tenant_id}:*') |
| --- |

| DECISION: Foundry IQ Result Caching Decided: Do NOT cache Foundry IQ results in our Redis layer. Rationale: Foundry IQ queries are contextual: they include conversation state, customer details, and pain points. The same agent querying for the same customer 5 minutes later in the conversation needs different results because the discovery has progressed. Caching would return stale context. Foundry IQ has its own internal optimizations. Our caching is reserved for slow-changing structured data (graph, products, competitor profiles, patterns). |
| --- |

## 3.7 KB Lifecycle Management

### 3.7.1 How Different KB Components Update

| KB Component | Update Trigger | Update Mechanism | Latency |
| --- | --- | --- | --- |
| Foundry IQ content | Admin uploads/deletes a file | Auto-reindex on blob change. Incremental indexer runs on schedule (hourly MVP, configurable). | Minutes to hours |
| Product Catalog (structured) | Admin uploads new CSV/Excel | Full re-import with diff detection. Shows what changed before applying. | Immediate for PostgreSQL, minutes for Foundry IQ re-index |
| Graph — statistical (Layer 1) | Admin uploads new transaction history | Recomputes co-occurrence, sequential patterns, upgrade detection. | Minutes (batch computation) |
| Graph — LLM-inferred (Layer 2) | Product catalog changes | Re-runs LLM analysis on updated catalog. | Minutes (LLM batch processing) |
| Graph — usage (Layer 3) | Platform usage signals | Updated continuously from rep accepts/dismisses, deal closes. | Real-time (event-driven) |
| Graph — admin (Layer 4) | Admin reviews suggestions | Admin confirms/rejects in Admin Portal. | Immediate |
| Competitor Intel | New competitor docs uploaded or extracted | Document classification + extraction triggers upsert. | Minutes |
| Customer Patterns | Scheduled batch job (daily or weekly) | Recomputes patterns from transaction data + case studies + conversations. | Hours (batch) |

### 3.7.2 Document Removal Flow

Removing a document triggers a cascade that cleans up all extracted intelligence:

Delete from Blob Storage: Remove the raw file from the tenant’s blob container.

Trigger Foundry IQ re-index: Foundry IQ’s next indexer run will detect the missing blob and remove its chunks from the search index. Or trigger an explicit re-index.

Remove extracted graph edges: Delete any product_relationships rows that cite this document as evidence source.

Remove competitor intel contributions: Remove entries in competitor_profiles that were sourced exclusively from this document.

Remove customer pattern contributions: Remove patterns sourced exclusively from this document. If a pattern had multiple sources and this was only one of them, decrement the sample_count and remove this document’s evidence.

Soft delete in KB Catalog: Set status='deleted' and deleted_at timestamp. Don’t hard delete — audit trail matters.

Invalidate caches: Clear all Redis caches that might contain data derived from this document.

| Critical: Cascade Safety Each removal step tracks source_doc_id. When a document is removed, we query 'all intelligence sourced from this doc_id' and remove or downgrade it. Intelligence from other sources remains untouched. This is why every extracted piece of intelligence records its source_doc_id — it enables clean, surgical removal without collateral damage. |
| --- |

### 3.7.3 Confidence Decay

Product graph relationships age without reinforcement. This prevents stale relationships from dominating recommendations:

Statistical confidence decay: If no new co-purchases for a product pair in 6 months, statistical confidence decays 10% per month. A relationship that was 80% confident will drop to 48% after 6 months of no reinforcement.

Usage confidence decay: If no rep accepts a recommendation for this product pair in 3 months, usage confidence decays 15% per month. A relationship that reps stop accepting gets deprioritized.

Admin confidence: Never decays. Explicit human judgment is permanent until explicitly changed.

LLM confidence: Does not decay — it is recomputed whenever the product catalog is updated. If the products still exist and their descriptions still suggest a relationship, the confidence remains.

Decay is computed lazily (on read, not on a schedule). When the graph is queried, the confidence_composite is adjusted based on the time since the last update for each source.

## 3.8 Nuances, Risks, and Design Decisions

| Nuance 1: Foundry IQ is in Preview As of February 2026, some Foundry IQ features (MCP knowledge sources, Azure Content Understanding enrichment) are in preview. For MVP, use stable features only: blob storage as knowledge source, basic agentic retrieval with medium reasoning effort. Do not depend on preview features for the first tenant. |
| --- |

| Nuance 2: Classification LLM Cost Every uploaded document triggers an LLM call for classification and intelligence extraction. A tenant uploading 500 documents during onboarding means 500 LLM calls. At Gpt-4.1-mini pricing this is manageable (~$2-5 per batch), but must be budgeted. Batch processing with rate limiting prevents API throttling. |
| --- |

| Nuance 3: Product Graph Cold Start A brand-new tenant with no transaction history only gets Layer 2 (LLM inference) producing lower-confidence relationships. Be transparent: 'Upload transaction history to significantly improve recommendation quality.' The graph works without Layer 1, but it is noticeably better with it. Layer 2 alone provides a usable starting point. |
| --- |

| Nuance 4: Cross-Tenant Pattern Sharing (Future) Eventually, anonymized patterns from Tenant A’s insurance data could inform Tenant B’s insurance recommendations: 'Across all insurance tenants on our platform, umbrella policies cross-sell with auto at 75%.' Powerful but has data governance implications. Not MVP — flagged for future product roadmap. |
| --- |

| Nuance 5: KB Quality Monitoring Track per tenant: how many documents per category (gap detection), when each document was last updated (staleness), which documents are never retrieved (dead content), and which agent queries return no results (coverage gaps). This is the KB health dashboard in the Admin Portal — critical for customer success. |
| --- |

## 3.9 Key Technical Decisions Summary

| DECISION: Foundry IQ as Managed RAG Layer Decided: Use Foundry IQ for all unstructured document processing instead of building a custom RAG pipeline. Rationale: Eliminates 2-3 weeks of engineering effort on document parsing, chunking, vectorization, and hybrid retrieval. Microsoft’s managed service handles layout-aware enrichment, incremental indexing, and semantic reranking. Our engineering time is redirected to our differentiators: Product Graph, Content Classifier, and structured stores. |
| --- |

| DECISION: One Foundry IQ KB Per Tenant (Not Per Content Type) Decided: All content types share one knowledge base with multiple knowledge sources, not separate KBs per category. Rationale: Foundry IQ’s agentic retrieval engine can route queries across sources within a single KB. Separate KBs would require us to implement source selection logic. A single KB lets Foundry IQ’s query planner decide the optimal sources per query. |
| --- |

| DECISION: PostgreSQL for Product Graph (Not Neo4j) Decided: Store graph as an adjacency list in PostgreSQL with indexed columns for MVP. Rationale: Graph traversal with BFS in application code handles the scale we expect (50-500 products, 200-2000 edges per tenant). Neo4j adds operational complexity. Migrate if traversal patterns become complex or scale demands it. |
| --- |

| DECISION: Content Classifier as Cross-KB Intelligence Hub Decided: Every document passes through an LLM classifier that extracts and routes intelligence to multiple stores. Rationale: Without cross-KB extraction, a case study that mentions competitors and products would only be retrievable via semantic search. By extracting structured intelligence, we enrich the Product Graph, Competitor Store, and Customer Patterns simultaneously. This creates compounding value — every document upload makes multiple parts of the system smarter. |
| --- |

| DECISION: Cache Structured Data, Not Foundry IQ Results Decided: Cache graph traversals, product details, competitor profiles, and customer patterns in Redis. Do not cache Foundry IQ results. Rationale: Foundry IQ queries are contextual and evolve with the conversation. Caching them would return stale context. Structured data (graph, products, competitors) changes slowly and benefits enormously from caching (avoiding PostgreSQL hits during active conversations). |
| --- |
