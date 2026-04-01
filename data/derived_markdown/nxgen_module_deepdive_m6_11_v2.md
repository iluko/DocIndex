NxGen AI Sales Platform

Technical Deep-Dive: Modules 6–11

Core Agent Layer (Revised)

Cross-Sell, Upsell, Full Solution, Renewal, Win-Back, Net-New

March 2026  |  Internal — Engineering Team

| Document Scope This revised document covers the complete Core Agent Layer with all corrections from review: per-agent context assembly with confidence thresholds for ALL agents, enrichment computation with cross-industry interpretation, complete data source origin map for every field agents consume, trigger matrix clarified (cold-start vs mid-conversation via Handoff Manager), threshold-as-minimum behavior with rep override, phase-aware output structures with full JSON schemas, industry-neutral agent profiles with multi-domain comparison tables, Business Rules Engine cross-reference to Module 5, prompt architecture with dual enforcement, prompt versioning with feedback-driven evolution, prompt research and writing guide with testing framework and development order, agent-orchestrator communication protocol, and comprehensive gap coverage. |
| --- |

# Table of Contents

6.1   Context Assembly — Per-agent context with confidence thresholds; enrichment computation and cross-industry interpretation

6.2   Data Source Origins — Where every piece of agent context comes from; manual vs automatic; what happens when missing

6.3   Complete Trigger Matrix — Cold-start vs mid-conversation triggers; active_workflow handoff mechanics

6.4   Discovery Threshold Behavior — Minimum gate, not switch; rep override; phase reversal

6.5   Conversation Stages and Phase-Aware Output — Discovery, Recommendation, Action with JSON schemas; plugin activation clarification

6.6   Individual Agent Profiles — Industry-neutral stages with multi-domain comparison tables

6.7   Prompt Architecture — 9-section template; Business Rules Engine cross-reference to Module 5; domain-specific examples

6.8   Prompt Versioning and Evolution — Version tracking; feedback-driven updates; auto-update policy

6.9   Prompt Research and Writing Guide — Step-by-step methodology; testing framework; development order; research sources

6.10  Agent ↔ Orchestrator Communication Protocol

6.11  Nuances, Risks, and Gaps Filled

6.12  Key Technical Decisions Summary

# Modules 6–11: Core Agent Layer

| Module Purpose The Core Agent Layer is the domain intelligence of the platform. Modules 1–5 provide plumbing (state management, orchestration, knowledge retrieval, data integration, multi-tenancy). Modules 6–11 are the actual brain — they read context, reason about it using LLMs, and produce the outputs reps see: discovery questions, product recommendations, talking points, objection handlers, and evidence. These are the ONLY modules that make LLM calls for conversation-facing output. Each module implements a specialized sales motion: Cross-Sell (adjacent products), Upsell (upgrade paths), Full Solution (initiative-based selling), Renewal (contract retention), Win-Back (re-engagement), and Net-New (prospect entry). |
| --- |

| Architectural Clarification — What Lives Here vs. Elsewhere The mermaid diagram shows Context Assembly and Workflow Selection inside this layer. This is visually convenient but architecturally incorrect. Context Assembly lives in Module 1 (State Engine’s assembleContext function). Workflow Selection lives in Module 2 (Orchestrator’s Trigger Evaluator + Priority Resolver). Business Rules Engine lives in Module 5 (stores and defines rules); Modules 6–11 consume rules via prompt injection and post-processing. What Modules 6–11 actually own is: (1) the agent’s reasoning logic and prompt engineering, (2) the phase-aware output generation (discovery vs recommendation vs action), (3) the business rules integration within prompt construction, (4) the state patches each agent writes back, and (5) prompt versioning and evolution. |
| --- |

| MVP Scope MVP: Cross-Sell (M6), Upsell (M7), Full Solution (M8). Post-MVP: Renewal (M9), Win-Back (M10), Net-New (M11). The MVP agents cover the primary selling motions for existing customers. Post-MVP agents require additional data inputs (contract dates, decline trends, prospect matching) that are lower priority for first tenant value. |
| --- |

## 6.1 Context Assembly — How Each Agent Gets Different Context

Context Assembly is executed by Module 1’s assembleContext(sessionId, agentId) function, called by the Orchestrator before agent execution. Each agent receives a DIFFERENT context package because each agent’s manifest declares different stateNeeds. The Context Assembler reads the manifest and builds a tailored context window.

| Agent | State Fields Pulled | Conversation History | KB Sources Queried | Product Graph Edges (with confidence threshold) | Token Budget |
| --- | --- | --- | --- | --- | --- |
| Cross-Sell (M6) | customer.profile, customer.products, discovery.*, enrichment.whitespace_%, enrichment.segment_benchmark, intent_signals | Last 15 messages | Product catalog, case studies, competitor intel | CROSS_SELL edges from owned products (confidence > 0.3) | ~8,000 tokens |
| Upsell (M7) | customer.profile, customer.products (with current tiers), enrichment.purchase_trend, intent_signals | Last 15 messages | Product catalog (tier comparisons), case studies | UPGRADE_PATH edges from owned products (confidence > 0.3) | ~7,000 tokens |
| Full Solution (M8) | customer.profile, customer.products, ALL intent_signals, stakeholder details, initiative description, ALL discovery dimensions | Last 20 messages (needs full initiative context) | Product catalog, case studies, playbooks, competitor intel | ALL edge types from owned products (confidence > 0.2 — lower threshold for holistic view) | ~12,000 tokens (largest) |
| Renewal (M9) | customer.profile, customer.products with contract_dates, enrichment.account_health, enrichment.yoy_growth, churn_risk_signals | Last 10 messages | Competitor intel, case studies (retention-focused) | COMPLEMENT edges for retention bundles (confidence > 0.4 — higher threshold, don’t risk weak bundles in retention) | ~6,000 tokens |
| Win-Back (M10) | customer.profile, customer.products (historical), enrichment.decline_analysis, enrichment.months_since_last_purchase, full purchase_history_summary | Last 10 messages | Product catalog (new products since last purchase), case studies | CROSS_SELL edges for re-entry products (confidence > 0.3) | ~6,000 tokens |
| Net-New (M11) | customer.profile (company only), industry segment, similar_customer_patterns (from segment_benchmarks) | Last 15 messages | Product catalog, case studies (industry-matched), playbooks | No graph traversal — no owned products to start from. Uses segment-average purchase patterns instead. | ~7,000 tokens |

### 6.1.1 Enrichment Data — How It’s Computed and What It Means Per Industry

The enrichment fields consumed by agents are computed by Module 4’s enrichment batch job (Section 4.4 of the M4 document). These computations run OFFLINE after data import — not during conversations. The results sit in the account_enrichment table and are loaded into session state when a rep opens a customer. All enrichment fields use the same FORMULA regardless of industry. What changes per industry is how agents INTERPRET the numbers, which is handled by tenant prompt modifiers.

| Enrichment Field | How It’s Computed | What It Means for IT Reseller | What It Means for Insurance | What It Means for Food Distributor |
| --- | --- | --- | --- | --- |
| whitespace_percentage | (categories_tenant_sells - categories_customer_uses) / categories_tenant_sells. E.g., tenant sells 10 categories, customer uses 3 = 70% whitespace. | ‘They’re only in 3 of 10 product categories — massive cross-sell opportunity in networking, security, cloud, etc.’ | ‘They have 3 of 6 policy types — look for coverage gaps in umbrella, life, commercial.’ | ‘They order from 3 of 12 product categories — opportunity in beverages, frozen, cleaning supplies.’ |
| segment_benchmark | For each segment (e.g., ‘mid-market manufacturing’): compute average products owned, average lifetime value, average categories covered across ALL accounts in that segment. | ‘Similar mid-market manufacturers own 7 products on average; this customer owns 3.’ | ‘Similar households in this segment carry 4.2 policies on average; this household has 2.’ | ‘Similar-sized restaurants order from 8 categories on average; this one uses 3.’ |
| yoy_revenue_growth | (this_year_spend - last_year_spend) / last_year_spend. Computed from transaction history. E.g., $100K last year, $75K this year = -25%. | ‘Spending declined 25% — they may be shifting to a competitor for some product lines.’ | ‘Premium volume down 25% — they may have moved a policy to another broker.’ | ‘Order volume down 25% — they may be splitting orders with another distributor.’ |
| account_health_score | Composite score (0–1): purchase_frequency (25%), yoy_growth (25%), product_breadth (20%), recency (20%), support_interactions (10%). Weights configurable per tenant. | ‘Health 0.35 — at-risk. Declining spend + reduced engagement.’ | ‘Health 0.8 — healthy. Renewed all policies + added new coverage.’ | ‘Health 0.5 — moderate. Consistent orders but narrowing category spread.’ |
| months_since_last_purchase | Current date minus most recent transaction date, in months. | ‘No purchase in 8 months for an account that used to buy quarterly — dormant signal.’ | ‘No policy change in 14 months — normal for insurance (annual renewals).’ | ‘No order in 3 weeks for a weekly orderer — urgent churn signal.’ |

| Key Insight: Enrichment Is Universal, Interpretation Is Tenant-Specific The enrichment COMPUTATION is built once and works for all industries. The enrichment INTERPRETATION is configured during onboarding via tenant prompt modifiers (Section 4 of the prompt template). The Cross-Sell agent for an IT reseller sees whitespace_percentage = 70% and hears in its prompt: ‘Focus on technology stack gaps.’ The same agent for an insurance brokerage sees the same 70% and hears: ‘Focus on coverage gaps in lines of business.’ The number is the same; the instruction is different. Some industries MAY need custom enrichment fields in Phase 2 (e.g., ‘coverage_gap_score’ for insurance, ‘equipment_age_risk’ for industrial). For MVP, the universal enrichment fields cover 80%+ of use cases. |
| --- |

## 6.2 Data Source Origins — Where Every Piece of Context Comes From

Agents consume data from many sources, but reps and admins do not manually enter most of it. This section maps every data field agents use to its origin, so it is clear how data flows into the system and what happens when a source is unavailable.

| Data Field | Source | When It’s Populated | Manual or Automatic | What If Missing? |
| --- | --- | --- | --- | --- |
| customer.profile.company_name, industry, segment, revenue, employee_count | CRM CSV import (Module 4). Tenant uploads account list during onboarding. | During onboarding (Day 2–3). Re-uploaded when CRM data changes. | Automatic from import. Admin exports from CRM and uploads. | BLOCKER for company_name. Without it, the platform cannot identify the customer. Industry/segment can be inferred by enrichment if missing. |
| customer.products[] (current products owned) | CRM CSV import (Module 4) — transaction history + product catalog cross-reference. | During onboarding import. Updated on re-upload. | Automatic from import. | BLOCKER for most agents. Without products, only Net-New agent can function. Cross-Sell, Upsell, Renewal all require knowing what the customer owns. |
| customer.lifecycle_stage | Primary: CRM import (Salesforce ‘Account Stage’, HubSpot ‘Lifecycle Stage’). Fallback: Module 4 enrichment computes it from transaction data (has products? → active_customer; no products? → prospect; months_since_last_purchase > 12? → dormant). | Import time. Enrichment job updates the computed version after each sync. | Automatic from import or computed by enrichment. NEVER manually entered by rep during conversation. | Default: ‘prospect’ if no products, ‘active_customer’ if any products exist. Win-Back agent cannot distinguish ‘at_risk’ from ‘active’ without explicit data. |
| enrichment.whitespace_percentage | Module 4 enrichment batch job. Formula: (tenant_categories - customer_categories) / tenant_categories. | Computed after data import completes. Recomputed when product catalog or transactions change. | Fully automatic — no human input. | Null if no product catalog uploaded (BLOCKER: can’t compute without knowing what the tenant sells). Agent falls back to conversation-based discovery without whitespace data. |
| enrichment.yoy_revenue_growth | Module 4 enrichment batch job. Formula: (this_year_spend - last_year_spend) / last_year_spend. Computed from transaction history. | Computed after transaction history import. Requires at least 2 years of data for meaningful calculation. | Fully automatic — derived from transaction data. | Null if no transaction history or less than 1 year of data. Win-Back agent’s primary trigger cannot fire. Agent states: ‘Unable to assess spend trends without transaction history.’ |
| enrichment.segment_benchmark | Module 4 enrichment batch job. Aggregates across all accounts in same segment: avg products, avg spend, avg categories. | Computed after all account data is imported. More accurate with more accounts. | Fully automatic. | Weak or null if tenant has very few accounts in a segment. Benchmark comparison not shown. Agent uses absolute whitespace instead of relative. |
| enrichment.account_health_score | Module 4 enrichment batch job. Composite: purchase_frequency (25%) + yoy_growth (25%) + product_breadth (20%) + recency (20%) + support_interactions (10%). | Computed after import. Recomputed on re-upload. | Fully automatic. | Null if insufficient data (no transactions). Renewal agent cannot assess risk. Falls back to contract-date-only risk assessment. |
| enrichment.months_since_last_purchase | Module 4 enrichment batch job. Current date minus most recent transaction date. | Computed after import. | Fully automatic. | Null if no transactions. Win-Back agent’s secondary trigger cannot fire. |
| customer.contract_expiry_days | CRM CSV import — contract end dates. Can also be manually set by admin in Admin Portal. | During import if CRM has contract fields. Admin can add/edit in customer management. | Primarily from import. Admin can manually enter for key accounts. | Null if CRM doesn’t track contracts. Renewal agent’s primary trigger cannot fire. Tenant must add contract dates manually or Renewal agent is disabled. |
| intent_signals.* (competitor_mentioned, pain_point_detected, solution_initiative, etc.) | NLU extraction from conversation messages. Module 1’s lightweight LLM call parses each user message and extracts entities/intents. | Real-time, on every message the rep sends during conversation. | Fully automatic — LLM-extracted from conversation text. | Never missing — defaults to false. If NLU extraction fails, all signals stay at their current values. Conversation continues without new trigger evaluation. |
| discovery.dimensions.* | Combination of: auto-fill from CRM data (Module 4 import) AND conversation extraction (real-time, from NLU + agent reasoning). | CRM-sourced dimensions filled at session start. Conversation-sourced dimensions filled progressively during chat. | Hybrid: CRM fields are automatic, conversation fields are extracted by agents. | Unfilled dimensions stay as ‘empty’. Discovery progress is lower. Agent asks more questions before recommending. |
| product_graph edges (CROSS_SELL, UPGRADE_PATH, etc.) | Module 3 (KB Engine) Product Relationship Graph. Layer 1: co-occurrence from transaction history. Layer 2: LLM inference from product catalog. Layer 3: usage learning. Layer 4: human review. | Layer 1+2 built during onboarding (after catalog and transaction import). Layer 3+4 are ongoing. | Automatic for Layers 1–3. Layer 4 is optional human review. | If no transaction history: Layer 1 missing, graph relies on Layer 2 (LLM inference) only. Recommendations are less data-driven but still functional. |
| KB results (product descriptions, case studies, playbooks, competitor intel) | Module 3 (KB Engine) Foundry IQ. Documents uploaded during onboarding by tenant admin. | During onboarding (Day 4–6). Admin can upload additional docs anytime via Admin Portal. | Admin uploads documents. Ingestion/chunking/embedding is automatic. | If no case studies: Full Solution agent cannot reference success stories. If no playbooks: discovery questions are generic. If no competitor intel: Competitor Card plugin has nothing to show. |
| account_owner_email (rep assignment) | CRM CSV import — account owner field. | During onboarding import. | Automatic from import. | If missing: all accounts visible to all reps (no scoping). Not a functional blocker but a usability problem — reps see accounts that aren’t theirs. |

| Summary: What Reps Must Manually Provide During Conversation Almost nothing. The rep’s job is to HAVE the sales conversation. The system extracts information automatically from what the rep types. The only things a rep might manually input are: (1) selecting which customer to open (clicking an account name), (2) optionally providing context the CRM doesn’t have (‘I know they’re planning to expand to a second location’), and (3) accepting or dismissing recommendations. Everything else — enrichment, signals, discovery progress, product graph traversal — is automatic. |
| --- |

## 6.3 Complete Trigger Matrix

| Cold-Start vs. Mid-Conversation Triggers The primary trigger for most agents includes ‘active_workflow == null.’ This is a COLD-START condition — it only fires at session start when no agent is active yet. Once any agent is active, active_workflow is NEVER set back to null during a handoff. Mid-conversation activation happens through SECONDARY triggers, which route through the Handoff Manager (Module 2). The Handoff Manager evaluates confidence, suspends the current agent, transfers context, and activates the new agent — active_workflow changes directly from one agent to another without passing through null. |
| --- |

| Agent | Cold-Start Trigger (active_workflow == null) | Mid-Conversation Triggers (route through Handoff Manager) | Re-trigger Within Session | Edge Case Triggers |
| --- | --- | --- | --- | --- |
| Cross-Sell | customer.products >= 1 AND active_workflow == null | intent_signals.cross_sell_opportunity == true; enrichment.whitespace_percentage > 0.3 | discovery.progress crosses threshold AND recommendations is empty (first recommendation generation) | Manual switch by rep via UI dropdown; resume from suspended state after returning from another agent |
| Upsell | product_graph.upgrade_paths_available > 0 AND active_workflow == null | intent_signals.upsell_opportunity == true; intent_signals.tier_dissatisfaction == true; customer mentions scaling, growth, or hitting limits | discovery.progress crosses threshold AND recommendations is empty | Reactive suggestion from background enrichment detecting tier mismatch between customer usage and current plan |
| Full Solution | intent_signals.solution_initiative == true (no cold-start — requires explicit signal) | intent_signals.strategic_project_mentioned == true; intent_signals.multiple_pain_points >= 3 (enough pain points to suggest holistic approach) | discovery.progress crosses threshold (typically 0.5 — lower than others because blueprints need iteration before full threshold) | Handoff FROM Cross-Sell when conversation scope expands beyond individual products into a strategic initiative |
| Renewal | customer.contract_expiry_days <= 90 AND active_workflow == null | intent_signals.renewal_discussed == true; enrichment.account_health_score < 0.4 (at-risk account) | churn_risk_score recalculated after new signals surface during conversation | Tenant override: insurance/subscription tenants may configure Renewal as the default start agent instead of Cross-Sell |
| Win-Back | enrichment.yoy_revenue_growth < -0.2 AND active_workflow == null | enrichment.months_since_last_purchase > 6; intent_signals.churn_signal == true; customer.lifecycle_stage in [at_risk, dormant, churned] | Re-trigger if new decline data arrives mid-session (rare — enrichment is pre-computed) | Often triggered by background enrichment agent detecting decline patterns at session start. Cannot trigger if no transaction history exists. |
| Net-New | customer.products.length == 0 AND customer.lifecycle_stage == prospect AND active_workflow == null | Session starts with no purchase history; explicit rep selection of ‘new prospect’ mode in UI | discovery.progress crosses threshold (typically 0.35 — lowest threshold because limited data makes early recs valuable) | Fallback agent: if no other agent’s triggers fire (no products, no contracts, no history, no signals), Net-New activates as default |

| DECISION: active_workflow Never Returns to Null During Session Decided: When switching agents mid-conversation, active_workflow changes directly from one agent ID to another. It never passes through null. The Handoff Manager handles suspension, context transfer, and activation as a single atomic operation. Rationale: Setting active_workflow to null mid-conversation would cause ALL cold-start triggers to re-evaluate simultaneously, potentially causing multiple agents to compete for activation. Direct swap avoids this race condition entirely. |
| --- |

## 6.4 Discovery Threshold Behavior — Minimum, Not a Switch

| Core Principle The discovery threshold is a MINIMUM gate, not an automatic switch. Crossing the threshold ENABLES recommendations but does NOT force them. The agent reads the conversation flow and decides whether to lead with recommendations or continue discovery. The rep can always keep discovering past the threshold. |
| --- |

### 6.4.1 What Happens When the Threshold Is Crossed

Step 1: The agent’s discovery.progress reaches or exceeds the configured threshold (e.g., 0.4 for Cross-Sell). This is calculated from dimension fill status and weights, updated after each conversation turn.

Step 2: On the NEXT agent turn, the agent’s prompt instructions change. Before threshold: ‘You are in DISCOVERY mode. Do NOT recommend products yet.’ After threshold: ‘Discovery threshold met. You MAY now generate recommendations. However, if the conversation is still in active discovery (the rep is asking questions and receiving new information), continue prioritizing discovery and include preliminary recommendations as secondary output.’

Step 3: The LLM assesses the conversation flow and decides. If the rep just asked a discovery question (‘What about their security setup?’), the agent continues discovery and may add a brief note: ‘Based on what we know so far, monitoring looks promising — I’ll have detailed recommendations once we understand their security posture.’ If the rep asks for recommendations (‘What should we propose?’), the agent shifts fully to recommendation mode.

Step 4: The phase field in state (agent_state.{agent}.phase) transitions from ‘discovery’ to ‘recommendation’ only when the agent actually generates full recommendations. The Orchestrator validates: if the agent claims phase=‘recommendation’ but discovery.progress < threshold, the Orchestrator overrides and keeps it in discovery. This prevents premature recommendations from a hallucinating LLM.

### 6.4.2 Rep Wants to Keep Discovering Past Threshold

MVP approach: The agent follows the rep’s lead naturally. If the rep keeps asking questions, the agent keeps answering with discovery output. Recommendations are mentioned as early_signals but not fully generated. No explicit UI control needed — the LLM reads conversational cues.

Full product approach: A UI toggle appears once the threshold is met: ‘Ready for recommendations?’ with options ‘Show recommendations’ and ‘Keep exploring.’ If the rep clicks ‘Keep exploring,’ a state flag is set: agent_state.{agent}.hold_discovery = true. The agent stays in discovery mode. When the rep is ready, they click the toggle or explicitly ask. This gives the rep explicit control.

### 6.4.3 Can the Rep Go Back from Recommendation to Discovery?

Yes. If the rep ignores recommendations and starts asking discovery questions again, the agent follows. The prompt instructs: ‘If the rep shifts back to exploration after you have presented recommendations, follow their lead. Update recommendations based on new discoveries. Do not oscillate — if the rep is clearly back in discovery mode for 2+ turns, shift your output emphasis back to questions.’ The phase in state may revert to ‘discovery’ if the agent determines the conversation has genuinely shifted back. This is intentional flexibility, not a bug.

| DECISION: Threshold as Minimum, Not Switch Decided: MVP: Agent uses conversational cues to decide when to present full recommendations. Threshold enables but does not force. Full product: explicit UI toggle for rep control. Rationale: Forcing recommendations the moment the threshold is crossed would feel jarring and robotic. Reps need to control the conversation pace. The threshold ensures the agent has enough context to make good recommendations, but the rep decides when to surface them. |
| --- |

## 6.5 Conversation Stages and Phase-Aware Output

Every agent operates in phases, and its output structure CHANGES based on which phase it is in. The phase is tracked in agent_state.{agent}.phase and progresses: discovery → recommendation → action.

### 6.5.1 Phase 1: Discovery Mode

The agent guides the rep toward asking the right questions. No product recommendations yet.

| // Discovery Mode output (all agents share this structure) { "phase": "discovery", "chat_response": "string",  // Message shown in chat "next_questions": [ { "question": "...", "dimension_it_fills": "...", "priority": 1, "why_asking": "...", "question_type": "open" \| "probing" \| "confirming" } ], "discovery_progress_update": { "dimensions_updated": [ { "dimension": "...", "old_status": "empty", "new_status": "partial", "value_extracted": "..." } ], "overall_progress": 0.45 }, "findings_extracted": [ { "finding": "...", "source": "conversation", "confidence": 0.9 } ], "early_signals": [ { "product_category": "...", "signal_strength": 0.6, "reason": "..." }  // Pre-recommendation hints, not full recs ], "coaching_note": "string",  // Optional guidance to rep "state_patch": {}  // What to write back to state } |
| --- |

### 6.5.2 Phase 2: Recommendation Mode

Enabled when discovery.progress crosses the threshold. Concrete product recommendations with supporting material.

| // Recommendation Mode output (all agents share this structure) { "phase": "recommendation", "chat_response": "string", "recommendations": [ { "product_sku": "...", "product_name": "...", "confidence": 0.85, "reasoning": "...", "revenue_potential": 15000, "graph_source": "CROSS_SELL edge (confidence 0.72)", "segment_benchmark": "82% of similar accounts have this" } ], "talking_points": [ { "point": "...", "for_recommendation": 0, "positioning_angle": "integration_ease" \| "roi" \| "risk_reduction" } ], "objection_preempts": [ { "likely_objection": "...", "response": "...", "evidence_type": "roi_calculation" \| "case_study" \| "benchmark" } ], "next_best_actions": [ { "action": "...", "urgency": "high" \| "medium" \| "low", "expected_outcome": "..." } ], "evidence": [ { "type": "case_study" \| "data_point" \| "benchmark", "content": "...", "source": "foundry_iq_retrieval" } ], "follow_up_questions": [  // Can still ask questions in rec mode { "question": "...", "purpose": "deepen" \| "validate" \| "close" } ], "state_patch": {} } |
| --- |

### 6.5.3 Phase 3: Action Mode

| Action Mode Clarification Action mode is a phase of the CORE agent, not a separate activation. The core agent (e.g., Cross-Sell) transitions from recommendation → action when the rep accepts a recommendation. Plugins (quick actions) are INDEPENDENT of the core agent’s phase — they are surfaced by the Orchestrator based on their own trigger conditions and can appear during any phase. The core agent does NOT activate plugins. The Orchestrator detects state changes (e.g., recommendations written to state) and surfaces relevant plugins automatically. |
| --- |

| // Action Mode output { "phase": "action", "chat_response": "string", "accepted_recommendations": [ { "product_sku": "...", "accepted_at": "...", "rep_notes": "..." } ], "email_context": {  // Pre-populated FOR the Email Gen plugin "subject_suggestion": "...", "key_points": ["..."], "tone": "consultative", "recipient_context": { "name": "...", "title": "..." } }, "crm_opportunity_data": {  // For write-back (full product) "product": "...", "estimated_value": 15000, "stage": "Qualification", "source": "AI Cross-Sell Recommendation", "notes": "..." }, "conversation_summary": "string",  // For logging/audit "state_patch": {} } |
| --- |

### 6.5.4 Plugin Activation — Orchestrator-Owned, Phase-Independent

Plugins (Email Gen, KB Search, Competitor Card, Account Overview, etc.) are NOT activated by core agents and NOT tied to the core agent’s phase. They are activated by the Orchestrator based on their own trigger conditions evaluated on every state change:

| Plugin | Trigger Condition | Can Appear During Which Phases? | How Core Agent Interacts |
| --- | --- | --- | --- |
| Email Gen | ‘Any agent_state.*.recommendations.length >= 1’ — fires when any agent has produced recommendations | Recommendation + Action (because recommendations must exist first) | Core agent writes email_context to its state during action mode. Email Gen reads it when rep clicks the button. |
| KB Search | ‘conditionType: always’ — available from session start | Discovery + Recommendation + Action (always available) | Core agent does not interact. Rep uses KB Search independently. |
| Competitor Card | ‘intent_signals.competitor_mentioned == true’ | Any phase where a competitor is mentioned | Core agent does not interact. Orchestrator surfaces the button when NLU detects competitor mention. |
| Account Overview | ‘conditionType: always’ — available from session start | All phases | Core agent does not interact. Shows enrichment data and product history. |

Key insight: the Orchestrator surfaces plugins, not the core agent. The core agent PREPARES data that plugins consume (e.g., email_context), but the activation decision is entirely the Orchestrator’s based on trigger evaluation. This means plugins can appear mid-discovery if their conditions are met (e.g., Competitor Card appears during discovery when a competitor is mentioned).

## 6.6 Individual Agent Profiles — Industry-Neutral with Multi-Domain Examples

| Why Industry-Neutral Stages Each agent’s conversation stages follow the same PATTERN regardless of industry. The specific questions, product types, and terminology change per tenant via configuration (discovery dimensions, prompt modifiers, terminology map from Module 5). The stage descriptions below use generic language. A multi-industry comparison table follows each agent to show how the same stage looks across domains. |
| --- |

### 6.6.1 Module 6: Cross-Sell Agent

Mission: Identify products or services the customer doesn’t currently have but should, based on: what they own (product graph CROSS_SELL edges), what similar customers in their segment own (benchmarking), and what needs emerge during discovery (conversation signals).

| Stage | What the Agent Does | Output Emphasis | Edge Cases |
| --- | --- | --- | --- |
| Opening (0–10% discovery) | Acknowledge customer context from CRM data. Ask about their current environment beyond what data shows. Begin mapping needs landscape. | next_questions focused on the primary discovery dimensions configured for this tenant. early_signals may begin forming from product graph analysis. | Edge: CRM data is empty (no products imported). Agent shifts to open-ended discovery: ‘Tell me about their current setup.’ Should suggest handoff to Net-New agent if truly no history exists. |
| Mid-Discovery (10–40%) | Probe deeper into specific areas of need. Follow up on pain points mentioned. Map competitive alternatives they use. Explore growth or change plans. | next_questions become more targeted based on findings. coaching_notes guide the rep toward promising angles. findings_extracted accumulate in state. | Edge: Customer mentions a large-scale initiative (‘we’re overhauling everything’). Should trigger Full Solution handoff evaluation via Orchestrator, not continue narrow cross-sell. |
| Late Discovery (40–70%) | Begin forming hypotheses about which products/services fit. May surface early_signals as pre-recommendation hints. Continue filling remaining dimensions. | Mix of next_questions and early_signals. Agent may say: ‘Based on what we’ve discussed, [category] looks like a strong fit. Want me to explore that further?’ | Edge: Rep asks for recommendations before threshold. Agent should comply but caveat: ‘Based on what we know so far, here are initial thoughts — more information would help me refine these.’ |
| Recommendation (threshold+) | Generate ranked product/service recommendations with full supporting material. | Full recommendation output: recommendations[], talking_points[], objection_preempts[], evidence[]. Also includes follow_up_questions[] for deepening. | Edge: All recommendations are low confidence (<0.3). Agent surfaces them transparently: ‘I have some ideas but they’re tentative.’ Does not force weak recommendations. |
| Action (post-acceptance) | Rep accepted one or more recs. Prepare follow-up email context, CRM opportunity data, suggest next steps. | email_context, crm_opportunity_data, conversation_summary. Plugins (Email Gen, Account Overview) become most relevant. | Edge: Rep dismisses ALL recommendations. Agent asks why (captures feedback signal), then either re-discovers or suggests switching to a different sales motion. |

Multi-industry comparison — how the same Cross-Sell stages look across three domains:

| Stage | IT Reseller | Insurance Brokerage | Food Distributor |
| --- | --- | --- | --- |
| Opening question | What does their current technology environment look like beyond the firewall and switches we sold them? | Beyond the auto and home policies we carry for them, what other coverage are they thinking about? | Beyond the dry goods they order weekly, what other product categories are they sourcing from competitors? |
| Mid-discovery probe | Are they experiencing any network performance issues? Any security concerns keeping them up at night? | Have there been any life changes recently — new home, new vehicle, growing family? Any upcoming renewals with other carriers? | Are they planning any menu changes this season? Any events or catering that would change their order patterns? |
| Early signal | No monitoring solution detected in their product list. 82% of similar manufacturers have monitoring. | No umbrella policy. 75% of households with both auto and home also carry umbrella coverage. | They order produce but no beverages. 90% of similar-sized restaurants order both from the same distributor. |
| Recommendation positioning | This monitoring suite integrates natively with your Palo Alto firewall and gives the visibility you mentioned needing. | An umbrella policy closes the liability gap between your auto and home coverage limits — especially important with your rental property. | Adding our beverage line to your existing weekly delivery saves a separate vendor relationship and qualifies you for volume pricing. |
| Objection preempt | Budget concern: The suite typically saves 15–20 hours/month in troubleshooting, paying for itself in 3 months. | Cost concern: Umbrella policies average $200–300/year for $1M in additional coverage — pennies per day for significant protection. | Switching cost concern: We can run both vendors in parallel for 2 weeks so you can compare quality and pricing risk-free. |

### 6.6.2 Module 7: Upsell Agent

Mission: Identify upgrade opportunities for products or services the customer already owns. Traverse UPGRADE_PATH edges in the product graph. Compare current tier to higher tiers. Build ROI justification for the upgrade.

| Stage | What the Agent Does | Output Emphasis | Edge Cases |
| --- | --- | --- | --- |
| Opening | Understand which products/services the customer actively uses. Probe satisfaction with current tier, capacity, or level. | next_questions: ‘How are they finding their current [product/service]? Are they hitting any limits?’ Focus on usage_satisfaction and growth_trajectory dimensions. | Edge: Customer is already on the highest tier for all products. No UPGRADE_PATH edges exist. Agent should say so honestly and suggest Cross-Sell instead. |
| Mid-Discovery | Explore growth indicators. Probe specific pain points with current tier. Understand decision-making process for upgrades. | next_questions become tier-specific: ‘Their current plan supports X. How close are they to that limit?’ coaching_notes on timing opportunities. | Edge: Customer wants to DOWNGRADE. Agent should NOT fight this. Acknowledge, capture the signal, suggest the rep address the underlying dissatisfaction. |
| Recommendation | Present upgrade recommendations with specific ROI: cost delta, feature/capacity delta. Reference case studies of similar customers who upgraded. | recommendations with tier_comparison data: current vs proposed. talking_points focused on ROI and growth enablement. | Edge: The price delta is very large (3x current spend). Agent preemptively addresses cost and suggests phased upgrades if available. |
| Action | Prepare upgrade quote context, comparison email to decision-maker, CRM opportunity data. | email_context with comparison table, crm_opportunity_data with upgrade-specific fields. | Edge: Customer wants to upgrade one product but downgrade another. Agent handles both with net impact calculation. |

### 6.6.3 Module 8: Full Solution Agent

Mission: Design a complete solution blueprint when the customer has a stated initiative. This is NOT product-by-product selling — it’s architecting a multi-product, multi-phase solution with implementation planning.

| Stage | What the Agent Does | Output Emphasis | Edge Cases |
| --- | --- | --- | --- |
| Initiative Capture | Understand the strategic initiative. What are they trying to achieve? Why now? Who is driving it? What does success look like? | next_questions on strategic_initiative, success_criteria, stakeholder_map. This agent asks BIGGER questions than Cross-Sell/Upsell. | Edge: Initiative is vague (‘we want to improve things’). Agent must help sharpen it: ‘Can you help me understand which specific areas they want to improve?’ |
| Current State Mapping | Map what they have today against what the initiative requires. Identify gaps between current state and desired state. | findings_extracted on current-state gaps. early_signals on which product categories will be needed. Inherits findings from any previous agent via inherited_context. | Edge: Customer has NO current products (greenfield). Full Solution becomes the landing strategy — similar to Net-New but with a defined initiative driving product selection. |
| Blueprint Design | Architect the solution: which products/services, in what order, with what dependencies. Create implementation phases. | recommendations structured as a BLUEPRINT with phases[], dependencies[], total_investment, implementation_timeline. This structure is unique to Full Solution — not a flat recommendation list. | Edge: Total solution cost exceeds budget. Agent must present a phased approach with a clear Phase 1 that delivers value within budget, with later phases conditional on success. |
| Validation & Action | Validate the blueprint with the rep. Prepare executive summary, proposal context, stakeholder-specific talking points. | email_context with executive summary format. meeting_prep with stakeholder-specific angles. conversation_summary as proposal narrative. | Edge: Multiple stakeholders have competing priorities. Agent shows how the solution addresses each perspective (technical, financial, operational). |

### 6.6.4 Modules 9–11: Renewal, Win-Back, Net-New (Post-MVP)

| Agent | Mission | Unique Discovery Dimensions | Unique Output Elements | Key Edge Cases |
| --- | --- | --- | --- | --- |
| Renewal (M9) | Assess renewal risk, build retention strategy, prepare competitive defense | contract_terms_understood, satisfaction_level, competitive_threats, renewal_budget_confirmed, stakeholder_alignment | churn_risk_score (0–1), competitive_defense_talking_points[], retention_bundle_recommendations[], early_renewal_incentive_options[] | Customer has already decided to leave → shift from retention to graceful exit + future win-back setup. Customer wants to renegotiate price → agent needs pricing authority rules from business rules engine. |
| Win-Back (M10) | Analyze spend decline, hypothesize cause, design re-engagement strategy | decline_cause_identified, last_positive_interaction, competitor_capture_analysis, re-entry_product_identified, budget_recovery_feasibility | decline_analysis{period, amount, categories_affected}, cause_hypothesis (competitor_capture \| budget_cut \| dissatisfaction \| org_change), re-engagement_play{entry_product, incentive, messaging} | Customer left due to bad experience → agent must acknowledge this, not ignore it. Requires different messaging than competitor capture. Customer is now buying from competitor → need competitive displacement strategy. |
| Net-New (M11) | Profile the prospect, match to similar existing customers, identify entry wedge product | company_profile_confirmed, industry_pain_points_mapped, current_vendor_landscape, budget_authority, decision_timeline, entry_wedge_validated | similar_customer_matches[] (existing customers with same profile), entry_wedge_recommendation (single best starting product), landing_strategy{product, positioning, differentiation} | Zero information about prospect → rely purely on conversation. Prospect is a former customer → check for Win-Back history and adjust approach accordingly. |

## 6.7 Prompt Architecture

| Business Rules Engine Cross-Reference The Business Rules Engine is defined and stored in Module 5 (Section 5.4 of the M5 document). Module 5 owns rule types (inclusion, exclusion, bundling, sequencing, conditional, priority override), rule schema (JSON structure with conditions, actions, scoping), enforcement timing, and onboarding sources. What Modules 6–11 own is: (1) HOW rules are formatted into prompts (Section 3 of the prompt template below), and (2) HOW post-processing filters apply rules to agent output after LLM generation. Module 5 stores and defines rules. Modules 6–11 consume and enforce them. |
| --- |

### 6.7.1 Prompt Structure — 9-Section Template

Every agent prompt follows the same template with agent-specific content:

| # PROMPT TEMPLATE (assembled programmatically per agent per turn) SECTION 1: AGENT IDENTITY + ROLE 'You are the {agent_type} Agent for {tenant_name}.' 'Your role is to {agent_mission_statement}.' SECTION 2: CURRENT PHASE INSTRUCTIONS IF phase == 'discovery': 'You are in DISCOVERY mode. Generate the next best question(s).' 'Do NOT recommend products yet. Focus on: {unfilled_dimensions}.' ELIF phase == 'recommendation': 'Discovery is {progress}% complete. Generate recommendations.' 'If the rep is still actively discovering, prioritize questions' 'and include preliminary recommendations as secondary output.' ELIF phase == 'action': 'The rep has accepted recommendations. Prepare next steps:' 'email context, CRM opportunity data, meeting prep.' SECTION 3: BUSINESS RULES (from Module 5 tenant_config.business_rules) 'RULES YOU MUST FOLLOW:' '{each active rule formatted as natural language instruction}' // Injected from tenant config; empty if no rules configured SECTION 4: TENANT PROMPT MODIFIERS (from tenant_config.agent_config) 'ADDITIONAL CONTEXT FOR THIS TENANT:' '{each modifier as a line of instruction}' // Configured during onboarding; industry-specific guidance SECTION 5: OUTPUT FORMAT SPECIFICATION 'Respond ONLY in the following JSON format:' '{JSON schema matching current phase output structure}' SECTION 6: CUSTOMER CONTEXT (from context assembly) 'CUSTOMER: {customer.profile}' 'CURRENT PRODUCTS: {customer.products}' 'ENRICHMENT: {enrichment data relevant to this agent}' SECTION 7: PRODUCT GRAPH CONTEXT (from context assembly) 'PRODUCT RELATIONSHIPS: {graph edges with confidence scores}' SECTION 8: KB RETRIEVAL RESULTS (from Foundry IQ via context assembly) 'RELEVANT KNOWLEDGE: {foundry_iq_results}' SECTION 9: DISCOVERY STATE (from session state) 'DISCOVERY PROGRESS: {progress}%' 'FILLED DIMENSIONS: {filled dimensions with extracted values}' 'UNFILLED DIMENSIONS: {unfilled dimensions}' 'FINDINGS SO FAR: {findings_so_far array}' USER MESSAGES = [last N messages of conversation history] |
| --- |

### 6.7.2 Business Rules: Dual Enforcement

Business rules are enforced at TWO points, providing defense-in-depth:

Enforcement Point 1 — Prompt Injection (Section 3): Rules are formatted as natural language instructions in the system prompt. The LLM sees them as constraints during reasoning. This means the LLM can REASON about rules naturally: ‘I’m recommending a product, and the rules say I must include licensing — let me add that and explain why to the rep.’

Enforcement Point 2 — Post-Processing Filter (Module 5 Rules Engine): After the LLM produces output, the Business Rules Engine evaluates recommendations against all configured rules. If the LLM forgot to include a required companion product, the Rules Engine adds it. If the LLM recommended an excluded product, the Rules Engine removes it. This catches anything the LLM missed or ignored.

Both enforcement points are needed. Prompt injection alone is unreliable (LLMs can ignore instructions). Post-processing alone misses the opportunity for the LLM to explain WHY a bundle includes certain items. Together they ensure rules are both followed and explained.

### 6.7.3 Domain-Specific Business Rules — How They Affect Prompts Across Industries

| Domain | Common Rule Types | Example Rules Injected into Section 3 | Impact on Agent Behavior |
| --- | --- | --- | --- |
| IT Reseller | Product dependencies, vendor compatibility, refresh cycle timing, managed services bundling | ALWAYS include licensing with hardware. NEVER mix Cisco and Aruba networking in same proposal. If contract expires within 60 days, prioritize renewal discussion over new cross-sell. | Agent checks vendor compatibility before combining products. Adjusts recommendation timing based on contract dates. Automatically adds license SKU to hardware recs. |
| Insurance Brokerage | Regulatory compliance, age restrictions, coverage prerequisites, carrier restrictions | NEVER recommend life insurance to customers under 25 (regulatory). ALWAYS suggest umbrella when both auto and home are present. NEVER recommend a carrier the customer had a bad claims experience with. | Agent verifies age/eligibility before policy recommendations. Checks claims history in customer data. Compliance rules are non-negotiable and override confidence scores. |
| Food Distributor | Dietary/allergen constraints, seasonal availability, storage requirements, order minimums | NEVER suggest shellfish to customers flagged as kosher/halal. Summer months: prioritize beverages and frozen desserts. Do NOT recommend cold-storage products to customers without refrigeration capabilities. | Agent checks customer facility attributes before recommending. Seasonal rules change which products are surfaced. Dietary flags are hard exclusions. |
| Industrial Distributor | Safety compliance, equipment compatibility, certification requirements, maintenance schedules | Only recommend parts certified for customer’s equipment make/model. If upcoming safety inspection, prioritize compliance products. NEVER recommend products requiring certification the customer lacks. | Agent cross-references equipment compatibility matrix. Safety/compliance rules override revenue optimization. Certification checks are mandatory pre-recommendation validation. |

## 6.8 Prompt Versioning and Evolution

### 6.8.1 Versioning Ownership and Schema

Prompt versioning is owned by Modules 6–11. Each agent manages its own prompt template versions.

| CREATE TABLE prompt_versions ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), agent_id VARCHAR(100) NOT NULL,  -- 'core-cross-sell', etc. version INT NOT NULL, is_active BOOLEAN DEFAULT false, prompt_template JSONB NOT NULL,  -- Full template with all 9 sections change_notes TEXT, performance_metrics JSONB,  -- After eval: acceptance rate, quality created_at TIMESTAMPTZ DEFAULT NOW(), created_by VARCHAR(255),  -- 'manual', 'feedback_engine', 'a_b_test' UNIQUE(agent_id, version) ); -- Rollback: set current version inactive, reactivate previous version |
| --- |

### 6.8.2 Feedback-Driven Evolution

| Feedback Signal | What It Means | How It Affects Prompts | MVP vs Full Product |
| --- | --- | --- | --- |
| High dismiss rate for specific product | Reps don’t think this product fits in this context | Add exclusion rule (Module 5) or adjust prompt modifiers to reduce product’s prominence | MVP: engineer reviews monthly. Full: semi-auto suggestion. |
| Reps heavily edit AI-generated emails | Email tone or structure doesn’t match tenant’s communication style | Adjust Email Gen plugin’s prompt modifiers (Section 4). Add style examples. | MVP: engineer reviews. Full: detect edit patterns automatically. |
| Discovery questions are skipped by reps | Questions are irrelevant or awkwardly phrased for this industry | Adjust discovery dimension questions in tenant_config. May tune Section 2 instructions for more natural phrasing. | MVP: engineer reviews. Full: track skip rates per question. |
| Recommendations accepted but deal lost | Product fit was wrong, timing was wrong, or factors outside AI control | Captured for long-term learning. May indicate prompt needs better qualification criteria. | Full product only — requires CRM outcome data. |
| Rep overrides recommendation order | Rep has better judgment on priority for this specific customer | Capture as training signal for ranking model. Does NOT directly change prompts. | Full product: collaborative filtering (Level 4 learning). |

| DECISION: Prompt Auto-Update Policy Decided: MVP: No auto-update. Manual tuning only. Full product: Semi-automatic (propose + approve). Phase 3+: Supervised automatic with A/B testing and rollback. Rationale: Automatic prompt updates in a B2B sales tool are high risk. A bad change could cause inappropriate recommendations for ALL reps. Semi-automatic (ML proposes, human approves) balances learning speed with safety. |
| --- |

## 6.9 Prompt Research and Writing Guide

| Purpose This section provides a practical guide for researching, writing, and testing prompts for each core agent. The goal is to give the engineering team a clear methodology so that prompt creation is systematic rather than ad-hoc. |
| --- |

### 6.9.1 Step-by-Step Process

Step 1 — Define the agent’s job in one sentence. This becomes the first line of Section 1 in the prompt template. Examples: Cross-Sell: ‘Identify products the customer doesn’t have but should, based on ownership gaps, peer benchmarks, and discovery signals.’ Full Solution: ‘Design a multi-product, multi-phase solution blueprint aligned to the customer’s stated strategic initiative.’ The sentence must be precise enough that you could evaluate any output against it.

Step 2 — Write 3–5 ‘golden conversations.’ Before writing any prompt, write IDEAL conversation transcripts by hand. What would a perfect conversation look like turn by turn? What would the agent say? What questions would it ask? What recommendations would it make and how would it present them? Include at least one conversation per phase (one that stays in discovery, one that reaches recommendations, one that reaches action). These golden conversations are your ground truth for evaluating prompt quality.

Step 3 — Map the information diet. For each turn in the golden conversations, ask: ‘What information did the agent need to produce this output?’ Map each turn to specific state fields, KB results, and product graph data. This becomes the agent’s stateNeeds in its manifest and validates that your context assembly is pulling the right data.

Step 4 — Write the prompt iteratively. Start with a minimal prompt: identity (Section 1) + task (Section 2) + output format (Section 5). Run it against one golden conversation. Compare output to expected. Identify gaps (‘it didn’t ask about competitors,’ ‘it recommended too many products,’ ‘the talking points were generic’). Add instructions to fix each gap. Repeat. Typical iteration count: 15–30 iterations to get a solid base prompt.

Step 5 — Add context sections. Once the core logic works, add Sections 6–9 (customer context, product graph, KB results, discovery state). Run the full prompt with realistic context data. Verify that the agent correctly uses the additional context (‘it should reference the customer’s current products in its recommendations,’ ‘it should cite the case study from KB results’).

Step 6 — Add business rules and modifiers. Add Sections 3 and 4. Verify that rules are respected (‘it should NOT recommend the excluded product,’ ‘it SHOULD bundle licensing with hardware’). Test rule edge cases: what happens when two rules conflict? What happens when a rule excludes the agent’s top recommendation?

Step 7 — Adversarial testing. Design failure mode test cases: ambiguous customer responses (‘maybe, I’m not sure’), contradictory information (‘we love our firewall but also hate it’), edge cases (no products, all products, single product, 50+ products), reps who ignore guidance (‘just give me the recommendations now’), customers who give one-word answers. Each failure mode may require prompt adjustments.

### 6.9.2 Testing Framework

| Test Type | What It Tests | How to Run | Target Metric | When to Run |
| --- | --- | --- | --- | --- |
| Unit Tests (20–30 per agent) | Given a specific context JSON, does the agent produce output that matches expected structure and content? | Feed fixed context JSON to the agent. Parse output. Check: correct phase? Correct number of recommendations? Rules respected? Required fields present? | 100% structural pass rate. 80%+ content relevance score. | Every prompt change. Automated in CI/CD. |
| Quality Rubric (5–10 scenarios per agent) | Is the output genuinely useful to a rep? | Score each output on: relevance (1–5), specificity (1–5), actionability (1–5), rule compliance (pass/fail), tone appropriateness (1–5). Two reviewers score independently. | Average 4.0+ across all dimensions. Zero rule compliance failures. | Weekly during development. On each major prompt version. |
| Golden Conversation Replay | Does the agent produce output comparable to the hand-written ideal? | Feed golden conversation turn by turn. Compare agent output to expected output at each turn. Score: information coverage, question quality, recommendation relevance. | Agent covers 80%+ of the same information as the golden conversation. Recommendations overlap 70%+. | Every prompt version change. |
| Adversarial Tests (10–15 scenarios) | Does the agent handle edge cases gracefully? | Feed adversarial inputs: vague responses, contradictions, impossible requests, missing data, rep overrides. Check: does the agent crash? Hallucinate? Ignore rules? Produce empty output? | Zero crashes. Zero rule violations. Graceful fallback in all edge cases. | Every prompt version change. |
| A/B Testing (full product only) | Does the new prompt version perform better than the current one? | Run both prompt versions in parallel for 1–2 weeks. Measure: recommendation acceptance rate, dismiss rate, rep feedback, time-to-recommendation. | New version must outperform by 5%+ on acceptance rate to be promoted. No regression on any metric. | When promoting a new prompt version to production. |

### 6.9.3 Recommended Agent Development Order

| Order | Agent | Why This Order | Estimated Prompt Iterations | Dependencies |
| --- | --- | --- | --- | --- |
| 1st | Cross-Sell (M6) | Most common sales motion. Clearest logic (product graph + whitespace). Establishes the base prompt patterns that other agents will adapt. | 25–35 iterations | Product graph must have edges. Enrichment must be computed. At least one golden conversation per industry. |
| 2nd | Upsell (M7) | Adapts from Cross-Sell. Key difference: ‘what’s the next tier’ instead of ‘what’s missing.’ Requires tier comparison logic and ROI calculation in the prompt. | 15–20 iterations (reuses Cross-Sell patterns) | UPGRADE_PATH edges must exist in product graph. Product catalog must have tier information. |
| 3rd | Full Solution (M8) | Hardest prompt. Must think in blueprints (multi-product, multi-phase) rather than individual recommendations. Requires the most golden conversations and the most iterations. | 30–40 iterations | All KB sources needed (case studies, playbooks). Complete product catalog. Multiple golden conversations showing initiative-based selling. |
| 4th | Renewal (M9) — Post-MVP | Requires contract date data. Churn risk scoring logic. Competitive defense strategy. | 20–25 iterations | Contract data must be available. Account health scores computed. Competitor intel in KB. |
| 5th | Win-Back (M10) — Post-MVP | Requires historical decline analysis. Cause hypothesis reasoning. Re-engagement strategy design. | 20–25 iterations | Transaction history must span 2+ years. Decline analysis computed by enrichment. |
| 6th | Net-New (M11) — Post-MVP | Requires prospect matching (similar customer patterns). Most uncertain reasoning (no customer data to work with). | 25–30 iterations | Enough existing customers in the tenant’s database to establish segment patterns for matching. |

### 6.9.4 Prompt Research Sources

Where to find the knowledge needed to write effective prompts for each agent:

| Source | What It Tells You | Which Agents Benefit | How to Access It |
| --- | --- | --- | --- |
| Domain research interview (Module 5, Section 5.2) | How reps actually sell. What questions they ask. What products go together. What the unwritten rules are. | ALL agents. This is the #1 source for prompt instructions. | 30-minute structured interview with tenant’s top rep and/or sales manager during onboarding. |
| Sales training materials / playbooks | How new reps are taught to sell. Objection handling scripts. Discovery question frameworks. Product positioning guides. | Cross-Sell, Upsell, Full Solution (primary selling agents) | Request from tenant during onboarding. Upload to KB. |
| CRM custom fields and picklists | What data the tenant actually tracks. Reveals which customer attributes matter for their selling process. | ALL agents (shapes discovery dimensions and state schema) | Export CRM field metadata during onboarding. |
| Recorded sales calls (Gong, Chorus, etc.) | How top reps actually have conversations vs. how training says they should. Reveals natural language patterns, transition phrases, real objections. | ALL agents (shapes conversational tone and question phrasing) | Request access during onboarding. Listen to 3–5 calls. Transcribe key patterns. |
| Product catalog with descriptions | What each product does, who it’s for, how it relates to other products. Raw material for KB and product graph. | Cross-Sell, Upsell, Full Solution, Net-New | CSV/Excel upload during onboarding. The richer the descriptions, the better the KB retrieval. |
| Competitor battle cards | How to position against specific competitors. Win/loss analysis. Competitive differentiators. | Cross-Sell (competitive displacement), Renewal (competitive defense), Win-Back (re-engagement) | Request from marketing/sales ops during onboarding. |
| Historical deal data (won/lost with reasons) | What worked and what didn’t. Which products are hard to sell. Which segments convert best. | ALL agents (calibrates confidence scoring and recommendation ranking) | CRM export with opportunity outcomes. Most valuable for Phase 2+ learning. |

## 6.10 Agent ↔ Orchestrator Communication Protocol

| async def execute_agent(agent_id, session_id): # 1. Get agent manifest manifest = agent_registry.get(agent_id).manifest # 2. Assemble context (Module 1 State Engine) context = await state_engine.assemble_context(session_id, agent_id) # 3. Load tenant config (Module 5 — rules, modifiers) tenant_config = await config_store.get(context.tenant_id) # 4. Determine current phase from agent state agent_state = context.state.get(f'agent_state.{agent_id}', {}) current_phase = agent_state.get('phase', 'discovery') # 5. Load active prompt version prompt_version = await prompt_store.get_active(agent_id) # 6. Build prompt (agent-owned logic, fills template with context) prompt = agent.build_prompt( template=prompt_version.prompt_template, context=context, tenant_config=tenant_config, current_phase=current_phase ) # 7. Call LLM llm_response = await llm.call( prompt, model=tenant_config.agent_config[agent_id].llm_model ) # 8. Parse structured output (validate JSON schema for phase) agent_output = agent.parse_output(llm_response, current_phase) # 9. Apply business rules post-processing (Module 5 Rules Engine) if current_phase == 'recommendation' and agent_output.recommendations: agent_output.recommendations = await rules_engine.apply( context.tenant_id, agent_output.recommendations, context.state ) # 10. Validate phase transition proposed_phase = agent_output.state_patch.get( f'agent_state.{agent_id}.phase', current_phase) if proposed_phase == 'recommendation': if context.state.discovery.progress < threshold: # Override: LLM tried to recommend too early agent_output.state_patch[f'agent_state.{agent_id}.phase'] = 'discovery' # 11. Write state patch (Module 1 State Engine) await state_engine.write_state(session_id, agent_output.state_patch) # 12. Send to UI via WebSocket await websocket.send(session_id, { 'type': 'agent_response', 'phase': current_phase, 'chat_message': agent_output.chat_response, 'recommendations': agent_output.recommendations, 'next_questions': agent_output.next_questions, 'evidence': agent_output.evidence, }) |
| --- |

Key principle: agents are PURE FUNCTIONS. They receive assembled context and produce structured output. They never access the database, KB, or state directly. The Orchestrator handles all I/O, rule enforcement, phase validation, state writing, and UI delivery. This makes agents testable in isolation: given this context JSON, does the agent produce the correct output?

## 6.11 Nuances, Risks, and Gaps Filled

| Gap Filled: Phase Transition Validation The agent PROPOSES phase transitions by setting its phase in the state_patch. The Orchestrator VALIDATES: if the agent says phase=‘recommendation’ but discovery.progress < threshold, the Orchestrator overrides and keeps it in discovery. This prevents premature recommendations from a hallucinating LLM. The Orchestrator is the authority on phase transitions, not the agent. |
| --- |

| Gap Filled: LLM Failure Fallback If the LLM call times out or returns unparseable output: (1) Retry once with a simpler prompt (reduce context, remove optional sections). (2) If retry fails, return a generic discovery question from the template (not LLM-generated). (3) Log the failure for debugging. (4) The rep sees a natural question, not an error message. The conversation continues gracefully. The fallback question pool is pre-written per agent during prompt development. |
| --- |

| Gap Filled: Multi-Turn Recommendation Refinement After the agent presents recommendations, the rep might say ‘that’s too expensive’ or ‘they’re not interested in that category.’ The agent must handle iterative refinement: re-rank or replace recommendations based on new input while staying in recommendation mode. This means the recommendation phase is a LOOP, not a single output. Each turn, the agent re-evaluates its recommendations with the updated context (which now includes the rep’s objection). |
| --- |

| Gap Filled: Agent Cooperation via Shared State Agents never communicate directly. They cooperate through state. The background enrichment agent writes segment_benchmark data. The Cross-Sell agent reads it. The Cross-Sell agent writes findings_so_far. If a handoff occurs, the Full Solution agent inherits those findings via inherited_context. This indirect cooperation through shared state is the ‘agent mesh’ pattern — no agent-to-agent coupling, just shared state. |
| --- |

| Gap Filled: Confidence Score Calibration Each recommendation includes a confidence score (0–1). For MVP, confidence combines: LLM’s self-assessed relevance (40%), product graph edge confidence (30%), segment benchmark match (20%), and recency of supporting data (10%). In full product, confidence is calibrated against actual outcomes: if recommendations with confidence 0.7 are accepted 70% of the time, the scoring is well-calibrated. If accepted only 40% of the time, the model is overconfident and weights need adjustment. The Feedback Engine (Module 16) tracks this per agent per tenant. |
| --- |

| Nuance: Tenant Prompt Modifiers vs. Base Prompts Base prompts (Sections 1, 2, 5) are shared across all tenants. Tenant modifiers (Section 4) customize behavior. Modifiers are APPENDED, never replacing the base. A bad modifier can degrade performance but cannot break core logic. Modifiers should be under 200 words and are reviewed during onboarding. Examples: ‘This tenant sells IT infrastructure. Focus on technology stack gaps.’ vs. ‘This tenant is an insurance brokerage. Focus on coverage gaps and life events.’ |
| --- |

## 6.12 Key Technical Decisions Summary

| DECISION: Phase-Aware Output Structure Decided: Agent output changes shape based on conversation phase (discovery → recommendation → action). Each phase has a different JSON schema. Rationale: A single output format would be wasteful and confusing. Discovery needs questions, not product cards. Recommendations need evidence, not discovery questions. Phase-awareness keeps responses focused and UI rendering simple. |
| --- |

| DECISION: Business Rules: Dual Injection (Prompt + Post-Processing) Decided: Rules are injected into the LLM prompt AND applied as post-processing filters. Both enforcement points required. Rationale: Prompt injection alone is unreliable (LLMs can ignore instructions). Post-processing alone misses LLM reasoning about rules. Together: defense-in-depth. The Rules Engine is defined in Module 5; Modules 6–11 consume it. |
| --- |

| DECISION: Agents Are Pure Functions (Context In, Output Out) Decided: Agents never access database, KB, or state directly. They receive assembled context and produce structured output. Orchestrator handles all I/O. Rationale: Makes agents testable in isolation. Swapping LLM provider or KB engine requires zero agent code changes. Clear separation of concerns. |
| --- |

| DECISION: Threshold as Minimum Gate, Not Automatic Switch Decided: Crossing the discovery threshold enables recommendations but does not force them. The agent follows conversational cues and rep intent. MVP: LLM-assessed. Full product: explicit UI toggle. Rationale: Forcing recommendations at threshold would feel robotic. Reps need control over conversation pace. The threshold ensures quality; the rep controls timing. |
| --- |

| DECISION: MVP: 3 Agents Only (Cross-Sell, Upsell, Full Solution) Decided: Post-MVP: Renewal, Win-Back, Net-New. Rationale: These three cover 80%+ of B2B selling scenarios. Post-MVP agents require data that’s often missing in MVP (contract dates, multi-year history, prospect databases). |
| --- |

| DECISION: No Auto-Prompt Updates in MVP Decided: Feedback signals captured, prompts tuned manually. Semi-automatic updates in full product. Supervised automatic in Phase 3+. Rationale: Auto prompt changes in a sales tool are high-risk. One bad update affects all reps. Semi-auto (propose + approve) is the safest path. |
| --- |

| DECISION: Prompt Development: Cross-Sell First Decided: Build and perfect Cross-Sell prompt first, then adapt for Upsell and Full Solution. Rationale: Cross-Sell has the clearest logic and establishes patterns reused by other agents. Full Solution is hardest and benefits from lessons learned on Cross-Sell. |
| --- |
