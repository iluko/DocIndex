NxGen AI Sales Platform

Technical Deep-Dive: Module Specifications

Architecture, Logic, Code Patterns, and Decision Records

Modules Covered: State Engine (M1), Agent Orchestrator (M2)

February 2026  |  Internal — Engineering Team

| Document Purpose This document captures the detailed technical specifications, design decisions, code-level logic, and implementation nuances for each module. It is intended as the primary reference for engineering team discussions and implementation planning. Each module section covers: purpose, API surface, processing logic (with code examples), state interactions, tenant configurability, and architectural decision records with rationale. |
| --- |

# Table of Contents

Module 1: State Engine

1.1 API Surface — readState, writeState, subscribe, assembleContext, queryHistory

1.2 Processing Layer — Schema Validator, Merge Engine, Event Emitter, Context Window Builder

1.3 Tenant Configuration Layer — Schema Registry, Discovery Dimensions

1.4 Storage Layer — Redis (Hot), PostgreSQL (Warm), Archive (Cold)

1.5 Exact Message Processing Flow — 9-step walkthrough

1.6 Complete State Object Shape — full JSON reference

Module 2: Agent Orchestrator

2.1 Agent Manifest System — Fields, consumers, four agent types

2.2 Trigger System — Conditions, operators, tenant overrides

2.3 Priority Resolution — Cascade tiebreakers, same-priority handling

2.4 Handoff Manager — Confidence scoring, five atomic operations, auto-fill, dimension preservation

2.5 StateOutputs.namespace — Write boundary enforcement

2.6 Key Technical Decisions Summary

# Module 1: State Engine

| Module Purpose The State Engine is the central nervous system of the entire platform. It manages session state, tracks discovery progress, accumulates context from every source (user messages, CRM data, agent outputs, background enrichment), broadcasts state changes as events to drive the reactive agent mesh, and assembles optimized context windows for LLM calls. Every other module in the platform reads from or writes to the State Engine. It is not just session management — it is the coordination backbone through which all agents communicate without direct coupling. |
| --- |

## 1.1 API Surface

The State Engine exposes five core API methods. Each serves a distinct purpose and is consumed by different parts of the platform.

### 1.1.1 readState(sessionId, scope?)

Reads the current state of a session. The scope parameter controls what slice of state is returned:

| Scope Value | What It Returns | Who Uses It |
| --- | --- | --- |
| "full" | All three layers: Universal + Domain + Agent-Specific | Agent Orchestrator (for trigger evaluation) |
| "universal" | Only the Universal layer (session metadata, customer basics) | Simple status checks, session management |
| "agent:cross-sell" | Only the specified agent’s sandboxed state namespace | Individual agents reading their own prior state |
| "discovery" | Only discovery progress, dimensions, and scores | Conversation UI (to render progress bar) |
| "intent_signals" | Only the intent signals block | Trigger Evaluator (fast evaluation of agent conditions) |

Returning the full state on every call is wasteful. The UI only needs discovery progress to update a progress bar. A plugin agent only needs the universal layer plus its own namespace. Scoped reads reduce payload size and improve performance.

### 1.1.2 writeState(sessionId, patch)

Applies a partial update (patch) to the session state. This does NOT overwrite the entire state — it merges only the fields present in the patch into the existing state.

Why patches instead of full writes: Multiple processes may update state close together. The NLU extracts intent signals, a background agent writes enrichment data, and the active agent updates discovery progress — all within seconds. If each overwrote the full state, they would clobber each other. Patches ensure only the changed fields are affected.

Example patch from NLU extraction after a user message:

| { "intent_signals.competitor_mentioned": true, "intent_signals.detected_competitors": ["current firewall vendor"], "intent_signals.pain_point_detected": true, "intent_signals.detected_pain_points": ["poor support response times"], "conversation.message_count": 15, "conversation.last_message_at": "2026-02-25T10:35:12Z" } |
| --- |

### 1.1.3 subscribe(sessionId, eventTypes, callback)

Registers a subscriber to receive notifications when specific state changes occur. Without subscriptions, every module would have to constantly poll the State Engine asking "did anything change?" which is wasteful and introduces latency.

Instead, modules declare what events they care about. When the State Engine processes a write that produces one of those events, it pushes a notification to all matching subscribers. This is how the platform achieves real-time reactivity.

| Subscriber | Events Subscribed To | What They Do With It |
| --- | --- | --- |
| Agent Orchestrator | intent_signal_detected, discovery_progress_updated, customer_resolved, workflow_switched | Evaluate trigger conditions for all registered agents. Activate, dismiss, or suggest agents based on state changes. |
| Conversation UI (via WebSocket) | discovery_progress_updated, quick_actions_changed, recommendation_generated, agent_activated | Update progress bar, show/hide quick action buttons, display new recommendation cards, update active agent badge. |
| Analytics Engine | session_started, session_ended, recommendation_generated, recommendation_accepted, recommendation_dismissed | Write event records to analytics log for reporting and feedback learning. |
| Audit Layer | * (all events) | Write immutable audit record with full event details and timestamp for compliance. |

### 1.1.4 assembleContext(sessionId, agentId)

Purpose-built for preparing the LLM prompt context when an agent is about to be called. This is NOT just "read the state differently" — it intelligently constructs the right context window for a specific agent’s LLM call.

The problem it solves: When the Cross-Sell agent needs to generate a recommendation, you need to send context to the LLM. But you cannot dump the entire state, full conversation history, and all KB results into the prompt. You would blow the token limit and most of it would be irrelevant noise. assembleContext solves this through five steps:

Read the agent’s manifest requirements. The manifest declares what state fields, conversation depth, and KB sources this agent needs.

Pull relevant state fields. Only the fields declared in the manifest’s stateNeeds.requiredFields and optionalFields are included.

Pull conversation history. Not the entire chat. The manifest specifies depth (e.g., last 15 messages). Older messages may be summarized.

Pull KB context. Query Foundry IQ with a context-appropriate query constructed from the current state. Include top results.

Apply token budget. Count approximate tokens for each section. If total exceeds the budget, trim: reduce conversation history, reduce KB results, compress customer profile to essential fields.

Token budget allocation per agent is defined in the manifest. Example for Cross-Sell:

| tokenBudget: total: 8000 systemPrompt: 800      # fixed prompt template customerContext: 500    # profile + products conversationHistory: 3000  # most variable section kbResults: 2500        # Foundry IQ results productGraph: 700      # relationship edges discoveryProgress: 500  # dimension status |
| --- |

| DECISION: Context Assembly Strategy Decided: Fixed allocation budgets per section for MVP. Surplus from under-used sections redistributed to conversation history and KB results. Rationale: Cross-domain relevance scoring adds complexity without proportional value early on. Fixed budgets are predictable and debuggable. Relevance scoring can be layered on in the full product once we have data on which context sections agents actually use. |
| --- |

### 1.1.5 queryHistory(sessionId, filters)

Returns the history of state changes within a single session (not across sessions). This is a changelog: at what time, what field changed, from what value to what value, and who made the change.

Use cases:

Debugging: "Why did the agent recommend product X?" Trace back through state changes to see what information was available at that moment.

Session replay: Step through a session chronologically to understand how the conversation evolved.

Audit: Compliance requires knowing what data was used to generate what recommendation at what time.

Analytics: Track how discovery progressed through the session to identify patterns across many sessions.

| DECISION: History Storage Decided: PostgreSQL state_changelog table with session_id, timestamp, field_changed, old_value, new_value, source (who made the change). Rationale: Simple relational storage is sufficient. No need for event sourcing complexity at MVP scale. The changelog table supports all four use cases (debug, replay, audit, analytics) without specialized infrastructure. |
| --- |

## 1.2 Processing Layer

The processing layer sits between the API surface and the storage layer. Every writeState call passes through four processing stages: Schema Validation, Merge Engine, Persist, and Event Emission.

### 1.2.1 Schema Validator

Every write to the state is validated against the expected structure for that tenant. The state has three layers, each with its own validation rules:

| Layer | What Gets Validated | Who Defines the Schema | Example Rules |
| --- | --- | --- | --- |
| Universal | Fixed fields that every tenant has. Session ID must be UUID. Status must be one of: active, suspended, closed. Timestamps must be ISO 8601. | Platform (hardcoded) | session.status IN ["active", "suspended", "closed"] |
| Domain | Tenant-specific fields. Customer profile structure, product fields, purchase history shape. | Tenant configuration (set during onboarding) | customer.profile.industry IN ["manufacturing", "healthcare"], customer.purchase_history_summary.total_annual_spend > 0 |
| Agent | Each agent’s namespace has a defined shape. The Cross-Sell agent’s state always has recommendations (array), confidence_score (number 0-1). | Agent manifest (stateOutputs declaration) | agent_state.cross_sell.confidence_score BETWEEN 0 AND 1 |

Four categories of validation occur on every write:

Type validation: Is the value the correct data type? String, number, array, object, boolean.

Required field validation: Does the write include all fields marked as required for this tenant?

Value constraints: Is the value within allowed bounds? Annual revenue > 0, industry IN allowed list, confidence between 0 and 1.

Structural validation: Do array elements have the expected shape? If customer.products is an array, does each element have sku, name, and category?

What changes per tenant: The Domain layer fields change entirely. An IT reseller has product.category IN ["networking", "security", "cloud"]. An insurance brokerage has policy.type IN ["auto", "home", "life", "umbrella"]. Required fields, allowed values, and structural constraints are all tenant-specific.

| DECISION: Schema Format Decided: JSON Schema stored per tenant in a tenant_schemas PostgreSQL table. Standard JSON Schema validation library used at runtime. Rationale: JSON Schema is a well-established standard with mature Python/JS libraries. No custom validation framework needed. Schemas are easy to read, version, and modify. |
| --- |

### 1.2.2 Merge Engine and Conflict Resolution

When a writeState patch arrives, it must be merged with the existing state without corrupting concurrent writes. The Merge Engine performs a deep merge: the patch is overlaid onto the existing state tree, adding or updating only the specified fields while leaving everything else untouched.

Optimistic locking handles the rare case where two writes target the same field simultaneously:

Read: Process A reads state and sees discovery_progress: 45, version: 11.

Compute: Process A computes new progress should be 55.

Write with condition: Process A writes discovery_progress: 55 WHERE version = 11.

Success: Version was still 11, write succeeds. Version becomes 12.

Conflict: If Process B also read version 11 and tries to write, it finds version is now 12. Write fails. Process B re-reads, recomputes, and retries.

| DECISION: Conflict Resolution Strategy Decided: Optimistic locking with version number on the state row. Implemented as a simple WHERE version = X clause in the SQL UPDATE statement. Rationale: For MVP with sequential agent execution, conflicts are extremely rare. Optimistic locking adds approximately 5 lines of code. No need for distributed locks, CRDTs, or other complex concurrency mechanisms at this scale. |
| --- |

### 1.2.3 Event Emitter

After every successful state write, the State Engine analyzes what changed and emits corresponding events. This is the reactive nervous system of the platform — it enables the state-driven agent mesh architecture.

Event generation logic: The emitter compares the patch with the previous state to determine what events to broadcast:

| # Event mapping rules if "intent_signals.competitor_mentioned" changed to true: emit("intent_signal_detected", signal="competitor_mentioned") if "intent_signals.pain_point_detected" changed to true: emit("intent_signal_detected", signal="pain_point_detected") if "discovery.progress" changed: emit("discovery_progress_updated", progress=new_value) if "session.active_workflow" changed: emit("workflow_switched", from=old_value, to=new_value) if "agent_state.*.recommendations" length increased: emit("recommendation_generated", agent=agent_id) |
| --- |

| DECISION: Event Transport Mechanism Decided: In-process Python pub/sub pattern (callback dictionary) for MVP. Redis Pub/Sub for multi-server deployment in full product. Rationale: In-process events are fast (microseconds), simple, and work perfectly for a single-server MVP deployment. When scaling to multiple servers behind a load balancer, Redis Pub/Sub provides cross-server event delivery with minimal code changes. The subscriber interface stays identical — only the transport changes. |
| --- |

### 1.2.4 Context Window Builder

Responsible for assembling optimized LLM prompt contexts when agents are called. This is detailed in section 1.1.4 (assembleContext). The key technical detail: no LLM is used for context assembly in MVP. It is entirely deterministic code: read manifest requirements, pull state fields, pull conversation history (last N messages), query Foundry IQ for KB results, apply token budget via fixed section allocations.

## 1.3 Tenant Configuration Layer

### 1.3.1 Schema Registry

The Schema Registry is a PostgreSQL table that stores the JSON Schema definition for each tenant’s domain-specific state fields. It is the source of truth for what each tenant’s state looks like.

Without it, the system has no idea what fields Tenant A’s domain state should contain versus Tenant B’s. The Schema Validator would have nothing to validate against. The Admin Portal would have no way to show tenant-specific configuration options. The Context Assembler would not know what customer profile fields exist for this tenant.

| tenant_schemas table: tenant_id: "tenant-itreseller-001" layer: "domain" schema_json: { "customer.profile": { "type": "object", "required": ["company_name", "industry"], "properties": { "company_name": { "type": "string" }, "industry": { "type": "string", "enum": ["manufacturing", ...] }, "annual_revenue": { "type": "number", "minimum": 0 } } }, "customer.products": { "type": "array", "items": { "required": ["sku", "name"], ... } } } |
| --- |

For MVP: JSON schemas are created manually during tenant onboarding. For full product: the Admin Portal provides a UI where tenant admins can add, remove, and modify their domain fields, generating the JSON Schema automatically.

### 1.3.2 Discovery Dimensions (Tenant-Specific Progress Model)

Each tenant defines what "discovery" means for their business. Discovery dimensions are the specific things an agent needs to learn before it can generate high-quality recommendations. Dimensions vary completely by industry and by agent.

Three critical functions of discovery dimensions:

Guide the adaptive question engine: "What should the AI suggest asking next?" is answered by "which unfilled dimension has the highest weight?"

Gate recommendations: Prevent agents from making shallow suggestions before sufficient context is gathered. If discovery is below the threshold, the agent stays in discovery mode.

Track progress visually: The UI shows a progress bar computed from the weighted completion of all dimensions.

Example: How dimensions differ between an IT Reseller and an Insurance Brokerage for the same Cross-Sell agent:

| Dimension | IT Reseller | Insurance Brokerage |
| --- | --- | --- |
| Dimension 1 | current_products_identified (weight: 0.15, auto-fill from CRM) | current_policies_identified (weight: 0.15, auto-fill from CRM) |
| Dimension 2 | adjacent_needs_uncovered (weight: 0.25, conversation only) | life_events_uncovered (weight: 0.25, conversation only) |
| Dimension 3 | budget_timing_understood (weight: 0.20, conversation) | coverage_gaps_identified (weight: 0.25, conversation) |
| Dimension 4 | decision_maker_confirmed (weight: 0.15, conversation) | risk_tolerance_understood (weight: 0.15, conversation) |
| Dimension 5 | competitor_landscape_mapped (weight: 0.15, conversation) | renewal_dates_confirmed (weight: 0.10, auto-fill) |
| Dimension 6 | industry_segment_confirmed (weight: 0.10, auto-fill) | household_members_mapped (weight: 0.10, conversation) |
| Threshold | 60% to unlock recommendations | 55% to unlock recommendations |

Each dimension has: a unique ID, a human-readable label, a weight (0.0 to 1.0, all weights sum to 1.0), a status (empty, partial, complete), a filled_by field (data_import, conversation, auto_fill), and optional suggested_questions to guide the rep. Progress is calculated as the weighted sum of dimension completion: complete = full weight, partial = half weight, empty = zero.

## 1.4 Storage Layer

### 1.4.1 Redis Cache (Hot Storage)

Redis stores the full current state object for every session that is currently active (a rep has the chat window open). During an active conversation, state is read and written many times per minute. Every user message triggers: read state, NLU processing, write state, read state for context assembly, agent call, write results. If every operation hit PostgreSQL, latency would be 5-50ms per query. Redis responds in sub-millisecond.

What is stored in Redis:

Session state: Key = "session:{session_id}", Value = JSON string of full state object, TTL = 2 hours (auto-evict if session goes idle).

Subscriber registry: Key = "session:{session_id}:subscribers", Value = set of subscriber IDs for this session’s events.

What is NOT in Redis: Conversation message history (too large, stored in PostgreSQL), state changelog entries (written directly to PostgreSQL), closed/expired sessions (evicted from Redis when session ends or TTL expires).

### 1.4.2 PostgreSQL (Warm Storage)

PostgreSQL is the durable store for ALL session data. Even while a session is active in Redis, every write also goes to PostgreSQL (write-through pattern). Tables:

sessions: Session metadata — id, tenant_id, rep_id, customer_id, status, created_at, ended_at.

session_state: Full state JSON per session, updated on every write.

conversation_messages: Every message with role, content, timestamp, extracted entities.

state_changelog: Every state mutation with field_changed, old_value, new_value, source, timestamp.

feedback_signals: Accept, dismiss, usage events for the Feedback Engine.

### 1.4.3 Cold Archive (Full Product Only)

Sessions older than 6-12 months are archived to Azure Blob Storage as compressed JSON. Summary records remain in PostgreSQL for long-term analytics. For MVP, only Redis + PostgreSQL are needed. Cold archival is a full-product concern when data volume warrants it.

## 1.5 Exact Message Processing Flow

This section traces every step that occurs when a rep sends a message, showing exactly what happens at each stage, what data is produced, and whether an LLM is involved.

| Critical Note The State Engine processing pipeline uses NO LLMs except for one focused NLU extraction call (Step 2). Everything else — validation, merging, persisting, event emission — is deterministic code. This keeps the pipeline fast and predictable. |
| --- |

Scenario: Rep types "They mentioned they’re frustrated with their current firewall vendor’s support response times."

Step 0 — Message Received: The Conversation UI sends the message via WebSocket to the backend.

Step 1 — Validate Session: Is this a valid session? (check Redis, fallback to PostgreSQL). Is the user authorized? (user_id matches session rep_id). Is the session active? (status == "active"). Is the content safe? (Azure Content Safety API call, async). No LLM involved — pure authorization and safety checks.

Step 2 — NLU Extraction (ONLY LLM CALL): A focused, fast LLM call extracts structured data from the message. Uses GPT-4o-mini for speed (~200ms). The prompt asks the model to extract: pain_points, competitors, products, budget signals, timeline signals, decision maker signals. Returns structured JSON. This is the ONLY AI in the State Engine processing pipeline.

Step 3 — Build Patch: Construct the state patch from the NLU extraction results. Set intent_signals.competitor_mentioned = true, add detected pain points to the array, increment message count, check if any discovery dimensions should be updated based on what was extracted, recalculate discovery progress.

Step 4 — Validate Patch: Load the tenant’s schema from the Schema Registry. Validate each field in the patch against the schema: type checks, required fields, value constraints.

Step 5 — Merge: Deep merge the patch into the existing state in Redis. Check optimistic lock (version match). Increment version number.

Step 6 — Persist: Write-through to PostgreSQL: update session_state row, insert new conversation_messages row, insert changelog entries. This happens in parallel with the Redis write.

Step 7 — Broadcast: Analyze the patch to determine what events to emit. Competitor mentioned → emit intent_signal_detected. Pain point detected → emit intent_signal_detected. Discovery progress changed → emit discovery_progress_updated. Push events to all subscribers.

Step 8 — Subscribers React: Agent Orchestrator receives intent_signal_detected, evaluates trigger conditions, surfaces Competitor Card plugin. Conversation UI receives discovery_progress_updated via WebSocket, updates progress bar.

Step 9 — Context Assembly (conditional): Only if the Orchestrator decides to call an agent (e.g., discovery crossed the threshold). Calls assembleContext with the agent’s manifest requirements. Returns an optimized context window for the LLM call.

## 1.6 Complete State Object Shape

This is the full JSON structure of a session state object. Every field is annotated with who writes it and when. This is the definitive reference for understanding what the state looks like at runtime.

| { // === METADATA (Written by State Engine) === "_meta": { "session_id": "sess_abc123", "tenant_id": "tenant-itreseller-001", "version": 47,        // Incremented on every write (optimistic lock) "created_at": "2026-02-25T09:00:00Z", "updated_at": "2026-02-25T10:35:12Z" }, // === SESSION (Written by Orchestrator) === "session": { "status": "active", "active_workflow": "core-cross-sell", "workflow_status": "discovery", "previous_workflow": null, "workflow_history": [], "active_agents": ["core-cross-sell", "background-enrichment"], "message_count": 14 }, // === CUSTOMER (Written by Data Integration + NLU) === "customer": { "customer_id": "cust_acme_001", "profile": { "company_name": "Acme Manufacturing", "industry": "manufacturing", "segment": "mid_market", "annual_revenue": 85000000 }, "products": [ { "sku": "FW-PA-5200", "name": "Palo Alto 5200", ... }, { "sku": "SW-CISCO-9300", "name": "Cisco Catalyst 9300", ... } ] }, // === INTENT SIGNALS (Written by NLU on every message) === "intent_signals": { "competitor_mentioned": true, "competitor_names": ["current firewall vendor"], "pain_point_detected": true, "detected_pain_points": ["poor support response times"], "solution_initiative": false, "budget_signal": false, "timeline_signal": false, "decision_maker_mentioned": false }, // === DISCOVERY (Written by active agent) === "discovery": { "progress": 0.55, "threshold": 0.60, "active_dimensions": "cross_sell_dimensions", "dimensions": { /* per-dimension status objects */ } }, // === AGENT STATE (Each agent writes to its own namespace) === "agent_state": { "cross_sell": { "phase": "discovery", "findings_so_far": [...], ... }, "enrichment": { "purchase_trend": "stable", "last_run": "..." } }, // === UI (Written by Orchestrator) === "ui": { "available_quick_actions": ["kb-search", "competitor-card"], "active_workflow_display": { "agent_name": "Cross-Sell", ... } }, // === CONVERSATION (Written by State Engine message handler) === "conversation": { "last_message": "...", "last_message_role": "user", "message_count": 14 } } |
| --- |

| DECISION: State Schema Design Decided: Three-layer state schema: Universal (platform-wide, fixed) + Domain (tenant-configured, JSONB) + Agent-Specific (per-agent namespace, JSONB). Stored in PostgreSQL with JSONB columns for flexibility. Rationale: The three-layer design is the architectural foundation. Getting the Universal layer wrong means every tenant becomes a custom project. Using JSONB for Domain and Agent layers provides schema flexibility without database migrations. Each agent writes only to its declared namespace, preventing cross-contamination. |
| --- |

# Module 2: Agent Orchestrator

| Module Purpose The Agent Orchestrator is the traffic controller of the platform. It decides WHEN agents should activate, WHICH agent takes priority when multiple trigger simultaneously, HOW to handle transitions between core workflow agents, and WHERE to route agent outputs. It contains zero AI — it is entirely deterministic, rule-based logic. The LLMs are only involved when the actual agents execute (Modules 6-12). The Orchestrator is pure traffic control executing in sub-millisecond time. |
| --- |

## 2.1 Agent Manifest System

Every agent in the platform registers itself via a manifest — a declarative document that tells the Orchestrator everything it needs to know about the agent without the Orchestrator understanding the agent’s internal logic. Think of it as a job application: the agent declares who it is, what it needs, when it should be called, and what it will produce.

### 2.1.1 Manifest Fields and Who Consumes Them

| Manifest Field | Purpose | Consumed By |
| --- | --- | --- |
| agentId, name, type | Identity and classification | Agent Registry for lookup and identification |
| triggers[] | Declares WHEN this agent should be triggered | Trigger Evaluator checks these against current state |
| stateNeeds.requiredFields | What state fields the agent needs for its LLM context | Context Assembler (Module 1) pulls exactly these fields |
| stateNeeds.conversationHistoryDepth | How much conversation history to include | Context Assembler determines message count |
| stateNeeds.kbContextNeeded | Which KB sources to query in Foundry IQ | Context Assembler constructs KB queries |
| stateOutputs.namespace | Where in the state this agent is allowed to write | Schema Validator enforces write boundaries |
| stateOutputs.mergeStrategy | How outputs merge into state (deep_merge vs replace) | Merge Engine applies the correct merge mode |
| defaultPriority | Priority relative to other agents of the same type | Priority Resolver for ordering when multiple trigger |
| isCore | Whether this is a core workflow (only one active at a time) | Handoff Manager for core workflow switching logic |
| discoveryConfig | Dimensions, weights, thresholds, auto-fill mappings | Discovery initialization during activation and handoff |

### 2.1.2 The Four Agent Types

Each agent type has fundamentally different execution behavior. The type field in the manifest determines how the Orchestrator routes and manages the agent:

CORE_WORKFLOW: Exclusive execution. Only one core agent active at a time. Gets full context assembly. Drives the primary conversation flow. Responses go directly into the chat. Examples: Cross-Sell, Upsell, Full Solution. If a second core agent triggers while one is active, the Handoff Manager evaluates whether to switch.

PLUGIN_QUICK_ACTION: Lazy execution. When triggered, it is surfaced as a button in the UI but does NOT auto-execute. Only runs when the rep explicitly clicks the button. Results shown as a card or panel, not in the main chat. Smaller context assembly. Examples: Email Generator, KB Search, Competitor Card, Account Overview. When trigger conditions are no longer met, the button is removed from the UI.

BACKGROUND_ENRICHMENT: Silent execution. Runs immediately but invisibly. The rep never sees it execute or its raw output. Results are written to state for OTHER agents to consume. Smallest context (often no conversation history needed). Fire-and-forget. Examples: Account Enrichment, Purchase Pattern Detector, Product Graph Neighbor Preloader.

REACTIVE_SUGGESTION: Suggestive execution. Shows a suggestion card in the UI proactively (not a button the rep seeks out). Can be accepted (triggers a handoff) or dismissed. Different from plugins: suggestions appear proactively based on signals, plugins are tools the rep invokes. Example: "This looks like an upsell opportunity. Want to switch?"

## 2.2 Trigger System

### 2.2.1 Trigger Conditions Deep Dive

Each trigger block in a manifest contains conditions, a conditionType, and a combineWith directive. These are the building blocks of the reactive agent mesh.

conditionType: Tells the evaluator what kind of evaluation logic to use:

state_match: Evaluate conditions against the current state field values. The most common type.

always: Trigger unconditionally. Used for always-available plugins like KB Search.

event_match: Trigger on a specific state change event, not the current state value. Useful for "fire once when threshold crossed" scenarios. Example: trigger when discovery_progress_updated event fires with progress crossing 0.6 for the first time, rather than triggering every time progress is above 0.6.

composite: Combines state_match AND event_match conditions for complex triggers.

combineWith: Controls how multiple conditions within a single trigger block are combined:

AND: ALL conditions must be true. Example: customer has products AND no workflow active.

OR: ANY condition being true is enough. Example: solution_initiative detected OR strategic_project mentioned.

Between trigger blocks in the triggers array, the relationship is always OR. Any single block being satisfied triggers the agent. Within a block, combineWith controls AND/OR.

### 2.2.2 Complete Operator Reference

The operator field in each condition specifies the comparison function applied to the state field’s value:

| Operator | Meaning | Data Types | Example |
| --- | --- | --- | --- |
| equals | Exact match | string, number, boolean, null | active_workflow == null |
| not_equals | Not equal | string, number, boolean, null | active_workflow != "core-cross-sell" |
| gt / gte | Greater than / greater or equal | number | discovery.progress >= 0.6 |
| lt / lte | Less than / less or equal | number | discovery.progress < 0.3 |
| length_gte | Array length >= value | array | customer.products has at least 1 item |
| length_equals | Array length exact match | array | customer.products has exactly 0 items (Net-New agent) |
| contains | String contains substring OR array contains element | string, array | detected_pain_points contains "poor support" |
| not_contains | Inverse of contains | string, array | message does not contain "budget" |
| exists | Field is not null/undefined | any | enrichment.last_run is set (enrichment has run) |
| not_exists | Field is null or missing | any | enrichment.last_run is not set (enrichment should run) |
| in | Value is one of a set | any vs array | industry IN ["manufacturing", "healthcare"] |
| not_in | Value is not in a set | any vs array | industry NOT IN ["food", "beverage"] |
| regex_match | Regex pattern match (full product) | string | message matches "moderniz\|overhaul\|refresh" |

### 2.2.3 Tenant Trigger Overrides

Every agent has default trigger conditions defined in its manifest. Tenant overrides allow modification of these defaults without changing agent code. Three override types exist:

disable: Completely turns off an agent for this tenant. Example: Insurance brokerage disables Competitor Card because regulations prevent showing carrier comparisons.

replace_trigger: Replaces the agent’s default trigger with entirely new conditions. Example: SaaS company replaces Cross-Sell’s auto-activate trigger with one that requires 5+ messages first, so reps can build rapport before the agent activates.

add_trigger: Adds additional conditions that must ALSO be met (AND logic with the default). Example: Medical distributor adds "customer annual spend must be >= $100K" to the Upsell agent’s trigger, restricting upsell to high-value accounts.

For MVP: Overrides are hardcoded JSON per tenant, set during onboarding. For full product: configurable in the Admin Portal.

## 2.3 Priority Resolution

When multiple agents trigger simultaneously (common — a single message can trigger a core agent, two plugins, and a background agent), the Priority Resolver determines ordering.

Resolution follows a cascade of tiebreakers:

Separate by type: Core workflows, plugins, background, and reactive agents are handled independently. Core agents compete for the single active slot. Plugins all get surfaced (ordered by priority). Background agents all execute silently.

Priority enum: CRITICAL > HIGH > MEDIUM > LOW > BACKGROUND. Different priorities resolve immediately.

Trigger specificity: If same priority, the agent with MORE trigger conditions matched (more specific match) wins.

Signal strength: If still tied, the agent triggered by more intent signals wins.

Hardcoded preference order: Ultimate tiebreaker. A configured ranking: Full Solution > Upsell > Cross-Sell > Renewal > Win-Back > Net-New. This is tenant-configurable (insurance might rank Renewal first).

Multiple agents CAN have the same priority level. Priority is a hint, not a unique identifier. The cascade ensures deterministic resolution regardless of ties.

Losing core agents do not disappear. If Full Solution wins over Cross-Sell in a tie, Cross-Sell becomes a reactive suggestion: "Cross-sell opportunities also detected. Want to explore that instead?" The rep sees a subtle card and can choose to switch.

## 2.4 Handoff Manager

The Handoff Manager handles transitions between core workflow agents. This is fundamentally different from simple activation because it involves suspending one agent, transferring context, switching discovery dimensions, and enabling future resume.

### 2.4.1 Handoff Confidence Scoring

When a new core agent triggers while another is active, the Handoff Manager calculates a confidence score to decide the appropriate action:

| Confidence Range | Action | Rationale |
| --- | --- | --- |
| >= 0.8 | Auto-switch: immediately suspend current agent and activate new one | Strong signals. The conversation has clearly shifted. Example: rep explicitly says "they want to modernize everything" while in Cross-Sell mode. |
| 0.5 – 0.79 | Suggest: show a suggestion card asking the rep if they want to switch | Medium signals. Possible shift but not certain. Let the rep decide. Example: customer mentions a strategic project but conversation could go either way. |
| < 0.5 | Ignore: log the signal but do not switch or suggest | Weak signal. Disrupting the current workflow would be counterproductive. Example: tangential mention while deep in productive discovery. |

Confidence is calculated from three factors:

Signal count: How many intent signals point to the new agent? 2+ signals = +0.4, 1 signal = +0.2.

Discovery depth: How far along is the current agent? Low progress (< 0.3) = +0.2 (easy to switch). High progress (> 0.7) = -0.2 (deep investment, don’t disrupt).

Keyword match: Did the rep use an explicit keyword strongly associated with the new agent? = +0.3.

Manual switches (rep clicks workflow dropdown) always set confidence = 1.0 and auto-switch immediately.

### 2.4.2 The Five Atomic Handoff Operations

Every handoff executes five operations in order. These are atomic — if any fails, the handoff is rolled back:

Suspend current agent: Preserve the agent’s entire state in its namespace: set _suspended = true, _suspended_at = timestamp, phase = "suspended". Critically: save the current discovery dimensions into the agent’s namespace as _suspended_discovery. This is how dimensions are preserved for future resume.

Transfer context: Copy key findings from the suspended agent into the new agent’s inherited_context block. The new agent starts with intelligence gathered by the previous agent.

Switch discovery dimensions: Initialize the new agent’s discovery dimensions in the top-level discovery object. This uses the new agent’s discoveryConfig from its manifest, with auto_fill_from mappings pulling data from the current state (including the suspended agent’s findings). The previous agent’s dimensions live in agent_state.{agent}._suspended_discovery.

Activate new agent: Set session.active_workflow to the new agent. Update workflow_history array with the suspension record. Execute the new agent with assembled context.

Update UI: Change active workflow badge, update discovery progress bar (now showing new agent’s progress), update available quick actions to match the new workflow, and show a "Resume [previous agent]" option.

### 2.4.3 Discovery Dimension Auto-Fill During Handoff

When a new agent activates (whether from handoff or fresh start), its discovery dimensions are initialized by looking at the current state. There is NO direct dimension-to-dimension mapping between agents. Instead, each agent’s dimensions independently declare where to look for auto-fill data via their discoveryConfig’s auto_fill_from field.

Each auto_fill_from entry specifies:

state_path: Where to look in the state object (e.g., "customer.products", "agent_state.enrichment.purchase_trend", "agent_state.cross_sell.findings_so_far").

transform: A deterministic function that converts the raw state value into a format meaningful for this dimension. Examples: list_product_names extracts product names from product objects. extract_tier_info adds tier classification. extract_pain_points filters findings for frustration keywords.

fills_status: Whether successful auto-fill marks the dimension as "complete" (full weight) or "partial" (half weight). Data-sourced fills are typically "complete"; inherited-from-other-agent fills are "partial" because they may need conversation confirmation.

Multiple auto-fill sources can be specified per dimension, tried in order. First successful match wins.

This design means: adding a new agent does not require updating any other agent’s manifests. Each agent is self-contained. The state is the shared data layer that agents read from independently.

### 2.4.4 Discovery Dimension Preservation

When discovery dimensions switch during handoff, the previous agent’s dimensions are NOT deleted. They are preserved inside the suspended agent’s state namespace:

The top-level discovery object always reflects the ACTIVE agent’s dimensions. This is what the UI reads for the progress bar and what the Orchestrator reads for threshold checks.

Each suspended agent’s dimensions live in agent_state.{agentId}._suspended_discovery. When that agent is resumed, the Handoff Manager copies _suspended_discovery back into the top-level discovery object, restoring the agent’s progress exactly where it was suspended.

Additionally, any intelligence learned during the time the agent was suspended (from the agent that was active in between) gets added as inherited_post_suspension context. The resumed agent benefits from everything that happened while it was dormant.

## 2.5 StateOutputs.namespace — Write Boundary Enforcement

The namespace field in stateOutputs serves as a write boundary. Every agent declares which parts of the state it is allowed to write to. The Schema Validator enforces this: if an agent attempts to write outside its declared namespace, the write is rejected.

Why this matters: Without namespace enforcement, a buggy agent could write garbage into the customer profile, a plugin could corrupt another agent’s findings, or a malformed agent output could overwrite session metadata. Namespaces are the safety net.

| Agent | Can Write To | Cannot Write To |
| --- | --- | --- |
| Cross-Sell Agent | agent_state.cross_sell.*, discovery.progress, discovery.dimensions | agent_state.upsell.*, customer.*, session.*, intent_signals.* |
| Email Generator Plugin | agent_state.email_gen.* | agent_state.cross_sell.*, discovery.*, customer.* |
| Background Enrichment | agent_state.enrichment.* | agent_state.*.* (any other agent), discovery.*, session.* |
| Orchestrator (internal) | session.*, ui.* | agent_state.*.* (never directly modifies agent data) |
| NLU Extraction (internal) | intent_signals.*, conversation.* | agent_state.*, customer.profile.*, session.* |

The mergeStrategy on each stateOutput controls HOW writes are applied: deep_merge keeps existing fields and adds/updates only the patched fields (used by core agents that build state incrementally). replace blows away the entire namespace and writes fresh (used by plugins that produce standalone results on each execution).

## 2.6 Key Technical Decisions Summary

| DECISION: No LLM in Orchestrator Decided: The entire Agent Orchestrator operates on deterministic, rule-based logic with zero LLM calls. Rationale: Trigger evaluation, priority resolution, handoff confidence scoring, and context routing must be fast (sub-millisecond) and predictable. Adding LLM calls would introduce latency and non-determinism into the platform’s core routing layer. The LLMs are reserved for the agents themselves (Modules 6-12). |
| --- |

| DECISION: Manifest-Based Registration Decided: All agent capabilities and requirements are declared in a static manifest document rather than discovered at runtime. Rationale: Manifests make the system inspectable and debuggable. You can read any agent’s manifest and know exactly: when it triggers, what it needs, where it writes, and what priority it has. This is critical for a platform that will have 15+ agents registered simultaneously. Runtime discovery would make the system opaque and harder to troubleshoot. |
| --- |

| DECISION: Handoff via Suggestion at Medium Confidence Decided: The system suggests handoffs rather than forcing them when confidence is between 0.5 and 0.8. Rationale: Forced handoffs disrupt the rep’s flow. If the rep is deep in a productive cross-sell conversation and a tangential signal suggests a solution opportunity, auto-switching would frustrate the rep. The suggestion model keeps the rep in control while surfacing opportunities they might miss. |
| --- |

| DECISION: Discovery Dimensions Use Auto-Fill from State Paths, Not Cross-Agent Dimension Mapping Decided: Each agent’s dimensions independently declare state paths to look at for auto-fill data, using transform functions to convert raw values. Rationale: Direct dimension-to-dimension mapping would create tight coupling between agents (adding a new agent requires updating every other agent’s mappings). State-path auto-fill is self-contained: each agent only knows about its own needs and where to find relevant data in the shared state. The state is the integration layer, not the agents. |
| --- |
