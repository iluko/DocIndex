NxGen AI Sales Platform

Technical Deep-Dive: Module 5

Multi-Tenancy & Tenant Configuration Framework

Tenant Isolation, Domain Research, Business Rules, Agent Configuration, State Schemas, and Onboarding

February 2026  |  Internal — Engineering Team

| Document Scope This document covers the complete Multi-Tenancy and Configuration Framework: tenant isolation strategy with three-layer enforcement (application middleware, PostgreSQL RLS, query auditing), domain research methodology for entering new industries, per-industry state schema examples for all Tier 1 verticals, configuration framework architecture with UI strategy, business rules engine with rule types, evaluation schema, enforcement timing, and onboarding sources, agent configuration fields with enforcement points, state schema configuration with discovery dimensions, and a complete day-by-day onboarding flow including exact information required, degraded states when data is missing, and industry template strategy. |
| --- |

# Table of Contents

5.1  Tenant Isolation Layer — Strategy, enforcement mechanisms, what’s isolated

5.2  Domain Research — Framework for entering new industries, state differences per domain

5.3  Configuration Framework — Architecture, UI strategy, JSON structure, caching

5.4  Business Rules Engine — Rule types, schema, enforcement timing, onboarding sources

5.5  Agent Configuration — Config fields, enforcement points, per-tenant customization

5.6  State Schema Configuration — Three layers, domain field definitions, discovery dimensions

5.7  Onboarding Flow — Day-by-day timeline, exact info required, degraded states, templates

5.8  Nuances, Risks, and Gaps Filled

5.9  Key Technical Decisions Summary

# Module 5: Multi-Tenancy & Tenant Configuration Framework

| Module Purpose Module 5 is the horizontal SaaS backbone. It ensures every tenant operates in complete isolation (data, configuration, intelligence) while sharing the same codebase and infrastructure. It provides the configuration framework that lets the platform adapt to any B2B industry without code changes: tenant-specific state schemas, business rules, agent settings, discovery dimensions, and UI branding. It also owns the onboarding pipeline — the end-to-end process of taking a new tenant from zero to first value. |
| --- |

| MVP vs Full Product MVP: white-glove onboarding where we manually configure each tenant. JSON configuration files per tenant, edited by the engineering team. Two roles (Rep, Admin). Row-level isolation with tenant_id on all tables. Full Product: self-serve onboarding with industry templates. Visual configuration UI in the Admin Portal. Custom roles, territory-based account scoping. Configuration versioning and rollback. |
| --- |

## 5.1 Tenant Isolation Layer

Tenant isolation is non-negotiable. Tenant A must never see Tenant B’s data, rules, KB content, conversations, or intelligence. A security breach in isolation would be catastrophic — a tenant’s customer data, competitive intelligence, and sales strategies exposed to a competitor.

### 5.1.1 Isolation Strategy: Row-Level with tenant_id

Every table in the database includes a tenant_id column. Every query includes a WHERE tenant_id = X clause. This is the simplest, most cost-effective isolation model for a multi-tenant SaaS:

| Isolation Model | Description | Pros | Cons | Our Choice |
| --- | --- | --- | --- | --- |
| Separate databases per tenant | Each tenant gets their own PostgreSQL database | Strongest isolation, simple backup/restore per tenant | Expensive at scale, connection pool management nightmare, schema migrations across N databases | NO |
| Separate schemas per tenant | One database, each tenant gets their own PostgreSQL schema | Good isolation, per-tenant schema evolution possible | Schema migrations still multiply by N, connection pool overhead, complex ORM configuration | NO |
| Row-level isolation (tenant_id) | One database, shared tables, tenant_id column on every row | Lowest cost, simplest deployment, one schema migration affects all tenants, single connection pool | Must enforce tenant_id in every query (risk: accidental cross-tenant data leak if missed) | YES |

### 5.1.2 Enforcement Mechanisms

Row-level isolation only works if tenant_id is ALWAYS enforced. A single missing WHERE clause leaks data. Three layers of defense:

Layer 1 — Application middleware: Every API request passes through a tenant context middleware. This middleware extracts tenant_id from the authenticated user’s JWT token and injects it into a request-scoped context object. Every database query function reads tenant_id from context — it is never passed manually. This eliminates the risk of a developer forgetting to include it.

Layer 2 — PostgreSQL Row-Level Security (RLS): A second line of defense at the database level. RLS policies on every table ensure that even if the application layer has a bug, the database itself rejects cross-tenant queries. The database session variable current_setting(‘app.tenant_id’) is set at connection time, and RLS policies filter on it automatically.

Layer 3 — Query auditing in staging/test: A test-mode middleware that intercepts all SQL queries and verifies that every query touching a tenanted table includes a tenant_id condition. Runs in CI/CD pipeline. Fails the build if any unscoped query is detected.

| -- PostgreSQL Row-Level Security setup ALTER TABLE accounts ENABLE ROW LEVEL SECURITY; CREATE POLICY tenant_isolation_accounts ON accounts USING (tenant_id = current_setting('app.tenant_id')::uuid); -- Applied to ALL tenanted tables: -- accounts, contacts, transactions, account_products, account_enrichment, -- sessions, messages, recommendations, feedback_signals, products, -- product_relationships, field_mappings, sync_cursors, tenant_config, ... -- Application sets the session variable at connection time: -- SET app.tenant_id = 'tenant-uuid-here'; # Python middleware (FastAPI) async def tenant_middleware(request: Request, call_next): tenant_id = request.state.user.tenant_id  # From JWT # Set on the database session for RLS await db.execute(f"SET app.tenant_id = '{tenant_id}'") request.state.tenant_id = tenant_id response = await call_next(request) return response |
| --- |

### 5.1.3 What Is Isolated Per Tenant

| Resource | Isolation Method | Shared or Separate? |
| --- | --- | --- |
| PostgreSQL data (accounts, transactions, sessions, etc.) | Row-level: tenant_id on every row + RLS policies | Shared tables, isolated rows |
| Redis cache (active session state) | Key prefix: tenant:{tenant_id}:session:{session_id} | Shared Redis instance, prefixed keys |
| Azure Blob Storage (KB documents) | Container per tenant: tenant-{tenant_id}/ | Shared storage account, separate containers |
| Foundry IQ Knowledge Base | One KB per tenant: tenant-{tenant_id}-kb | Shared Azure AI Search service, separate KBs |
| Product Relationship Graph | tenant_id column on product_relationships table | Shared table, isolated rows |
| Configuration (rules, schemas, agent settings) | tenant_config table, one row per tenant | Shared table, isolated configs |
| Feedback signals and analytics | tenant_id on all analytics tables | Shared tables, isolated rows |
| LLM prompts and system messages | Tenant-specific prompt modifiers stored in config | Shared base prompts, tenant-specific overlays |

## 5.2 Domain Research — How to Prepare for a New Industry

| The Core Challenge When a new tenant from a new industry signs up (say, an insurance brokerage after we’ve only served IT resellers), the platform needs industry-specific configuration: what does their state schema look like? What discovery dimensions matter? What business rules exist? What terminology do they use? Much of this knowledge does NOT exist in official documentation — it lives in the heads of experienced salespeople, in tribal knowledge, and in how their CRM is actually structured. This section defines how to systematically extract that knowledge. |
| --- |

### 5.2.1 The Domain Research Framework

For every new industry we enter, we conduct a structured research process before configuring the tenant. This produces the Industry Blueprint — a reusable document that accelerates onboarding for all future tenants in the same industry.

| Research Phase | What We Learn | Sources | Output |
| --- | --- | --- | --- |
| Phase 1: Sales Motion Analysis | How do reps sell in this industry? What are the primary sales motions (cross-sell, upsell, renewal)? What does the typical sales cycle look like? How long is the buying cycle? | Interviews with 2-3 experienced reps at the tenant, observation of actual sales calls, tenant’s CRM data structure (what fields they track), sales manager input on KPIs | Sales Motion Map: which agents matter, which are irrelevant, typical deal progression |
| Phase 2: Product/Catalog Taxonomy | How are products organized? What categories exist? How do products relate to each other? What are the natural cross-sell and upgrade paths? | Product catalog export, vendor documentation, product manager interviews, pricing sheets, competitive landscape | Product Taxonomy: categories, subcategories, natural groupings, tier structures, bundling patterns |
| Phase 3: Customer Segmentation | How do they segment their customers? What attributes matter for segmentation? How do segments affect selling strategy? | CRM account fields, marketing materials, sales playbooks, customer success team input | Segment Model: dimensions (size, industry, geography, lifecycle stage), how segments affect recommendations |
| Phase 4: Discovery Mapping | What questions do great reps ask? What information is needed before making a good recommendation? What are the blind spots? | Ride-alongs with top reps, recorded call libraries (if available, e.g. Gong), sales training materials, onboarding scripts for new reps | Discovery Dimensions: 4-8 dimensions with specific questions per dimension, mapped to what they unlock in recommendations |
| Phase 5: Rules & Constraints | What should never be recommended together? What must always go together? What regulatory or compliance constraints exist? What is the internal pricing logic? | Compliance team input, product management, legal constraints, pricing team, experienced sales managers who know the edge cases | Business Rules: inclusion/exclusion/bundling/sequencing/conditional rules ready for configuration |
| Phase 6: Terminology & UX | What do they call things? ‘Products’ vs ‘Solutions’ vs ‘Policies’ vs ‘Lines’? What does a rep’s daily workflow look like? What tools are they in all day? | Rep interviews, CRM screenshots, internal wikis, training decks, marketing style guides | Terminology Map + UX Requirements: field labels, UI language, workflow integration points |

| Critical Insight: The Best Sources Are NOT Official Documentation Official product docs tell you features and specs. They do not tell you how reps actually sell, what questions they ask, what products go together, or what the unwritten rules are. The BEST sources are: (1) A 30-minute interview with the tenant’s top-performing rep, (2) Their CRM’s custom fields (these reveal what data they actually track vs. what’s irrelevant), (3) Their sales training materials for new hires (these codify tribal knowledge), (4) A conversation with their sales manager about what reps get wrong most often. If the tenant cannot provide access to reps, request their sales playbook, training deck, and a CRM export with field descriptions. |
| --- |

### 5.2.2 State Schema Differences Per Domain

The three-layer state schema (Universal → Domain → Agent-Specific) means each industry has different domain-layer fields. Here are concrete examples for our Tier 1 industries:

| Industry | Domain State Fields (examples) | Discovery Dimensions (examples) | Key Terminology |
| --- | --- | --- | --- |
| IT Value-Added Reseller | technology_stack[], contract_expirations{}, vendor_preferences[], compliance_requirements[], managed_services_flag, cloud_migration_status, refresh_cycle_months | Infrastructure Needs, Security Posture, Cloud Readiness, Growth Trajectory, Budget Cycle, Vendor Satisfaction | Products, SKUs, Vendors, Refresh Cycle, Managed Services, SLA |
| Telecom / UCaaS | current_carrier, contract_months_remaining, locations[], seats_count, current_bandwidth, call_volume_monthly, has_contact_center, unified_comms_platform | Communication Needs, Location Complexity, Growth Plans, Current Pain Points, Budget Authority, Migration Readiness | Services, Lines, Seats, Bandwidth, Circuit, UCaaS, CCaaS, SIP Trunks |
| Insurance Brokerage | policy_types_held[], coverage_limits{}, life_events_recent[], risk_profile, household_members[], vehicles[], property_details{}, claims_history_count | Coverage Gaps, Life Changes, Risk Appetite, Budget Sensitivity, Loyalty Indicators, Claims Experience | Policies, Lines of Business, Coverage, Premium, Deductible, Endorsement, Rider |
| Industrial Distributor | product_categories_used[], order_frequency, avg_order_value, facility_type, maintenance_schedule, equipment_age{}, seasonal_patterns[], certifications_required[] | Operations Scale, Equipment Lifecycle, Maintenance Urgency, Expansion Plans, Budget Cycle, Vendor Consolidation | SKUs, Parts, Consumables, MRO, Safety Stock, Lead Time, MOQ |
| Medical Device / Healthcare | facility_type, bed_count, specialties[], current_equipment{}, group_purchasing_org, formulary_status, compliance_level, capital_budget_cycle | Clinical Needs, Equipment Age, Budget Authority, Compliance Requirements, Formulary Position, Growth Plans | Products, Devices, Consumables, Capital Equipment, Formulary, GPO, IDN |

Key observation: Universal fields (company_name, industry, segment, revenue, contact info) are the same everywhere. Domain fields capture industry-specific context that fundamentally changes how agents discover and recommend. The domain layer is stored as JSONB in PostgreSQL, so adding new fields requires zero schema migrations — just a configuration update.

## 5.3 Configuration Framework

The configuration framework is the machinery that makes the platform behave differently for each tenant without code changes. Configuration is stored in a tenant_config table as structured JSONB, versioned for rollback, and cached in Redis for fast access during conversations.

### 5.3.1 Configuration Architecture

| CREATE TABLE tenant_config ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id), config_version INT NOT NULL DEFAULT 1, is_active BOOLEAN DEFAULT true, -- The entire config tree as structured JSONB state_schema JSONB NOT NULL,      -- Domain fields, types, defaults discovery_config JSONB NOT NULL,   -- Dimensions, questions, thresholds agent_config JSONB NOT NULL,       -- Which agents, trigger settings business_rules JSONB NOT NULL,     -- All rule types ui_config JSONB NOT NULL,          -- Branding, terminology, layout integration_config JSONB NOT NULL, -- Data source settings, field mappings created_at TIMESTAMPTZ DEFAULT NOW(), created_by VARCHAR(255), change_notes TEXT, UNIQUE(tenant_id, config_version) ); -- Version history: when we update config, we create a new version -- and mark the old one is_active = false. Rollback = reactivate old version. |
| --- |

Configuration is loaded once per session start and cached in Redis. Within a conversation, configuration does not change. If an admin updates config mid-conversation, existing conversations use the old config; new conversations pick up the new config. This prevents jarring mid-conversation behavior changes.

### 5.3.2 Configuration UI Architecture (Full Product)

In MVP, configuration is a JSON file edited by the engineering team during white-glove onboarding. In full product, the Admin Portal provides a visual configuration interface:

| Config Area | MVP: How It’s Set | Full Product: Admin Portal UI | Where It’s Stored |
| --- | --- | --- | --- |
| State schema (domain fields) | Engineering team edits JSON file during onboarding | Visual field editor: add/remove/rename domain fields, set types, set defaults. Drag-and-drop field ordering. | tenant_config.state_schema JSONB |
| Discovery dimensions | Engineering team writes dimensions + questions in JSON | Dimension builder: name each dimension, add questions per dimension, set discovery threshold (what % coverage needed before recs appear), preview how it looks in chat | tenant_config.discovery_config JSONB |
| Business rules | Engineering team writes rules in JSON. Simple for/against lists. | Visual rule builder: IF [condition] THEN [action]. Dropdowns for products, segments, conditions. Test mode: ‘Try this rule against a sample account.’ | tenant_config.business_rules JSONB |
| Agent settings | Engineering team enables/disables agents, sets thresholds in JSON | Agent dashboard: toggle agents on/off, configure trigger thresholds (sliders), set per-agent prompts and behaviors | tenant_config.agent_config JSONB |
| UI & branding | Engineering team sets logo URL, color, terminology in JSON | Brand settings page: upload logo, pick primary color, set terminology map (Product → Solution), preview in mock chat window | tenant_config.ui_config JSONB |
| Integrations | Engineering team configures field mappings, import settings in JSON | Integration wizard: connect CRM (OAuth flow), map fields with auto-suggest, set sync schedule, test connection | tenant_config.integration_config JSONB |

| DECISION: Configuration UI Timing Decided: MVP uses JSON files edited by engineering team. Admin Portal configuration UI is built incrementally: business rules UI first (Month 5), then agent settings (Month 6), then state schema editor (Month 8+). Rationale: Building a visual configuration UI for every config area is months of frontend work. Prioritize: business rules UI first because rules change most frequently and have the highest risk of engineering-team-as-bottleneck. Other config areas change rarely after initial setup. |
| --- |

## 5.4 Business Rules Engine

| What Business Rules Are Business rules are tenant-specific constraints and directives that override or augment agent recommendations. They encode domain expertise that the AI cannot learn from data alone: regulatory constraints (‘never recommend life insurance to minors’), product dependencies (‘firewall always needs a license’), strategic priorities (‘push the new cloud product line this quarter’), and sales best practices (‘always offer installation services with hardware over $10K’). |
| --- |

### 5.4.1 Rule Types

| Rule Type | Structure | Example — IT Reseller | Example — Insurance | Example — Food Distributor |
| --- | --- | --- | --- | --- |
| Inclusion | ALWAYS recommend B when A is recommended | ALWAYS include licensing when recommending any Cisco hardware | ALWAYS suggest umbrella policy when auto + home are both present | ALWAYS suggest food-safe gloves when recommending any raw meat products |
| Exclusion | NEVER recommend X to segment Y / under condition Z | NEVER recommend end-of-life products to new customers | NEVER cross-sell life insurance to customers under 25 (regulatory) | NEVER suggest shellfish products to customers with kosher/halal flags |
| Bundling | ALWAYS present A + B + C as a package | Bundle: Firewall + License + Installation as ‘Security Starter Kit’ | Bundle: Auto + Home + Umbrella as ‘Total Protection Package’ | Bundle: Fryer Oil + Filters + Cleaning Kit as ‘Fryer Maintenance Bundle’ |
| Sequencing | Recommend A BEFORE B (A is prerequisite or better entry point) | Recommend network assessment BEFORE any infrastructure upgrade | Recommend auto policy BEFORE umbrella (umbrella requires underlying policy) | Recommend basic pantry staples BEFORE specialty ingredients (build basket first) |
| Conditional | IF condition THEN action | IF deal > $50K THEN add project management services to recommendation | IF customer age > 55 THEN prioritize Medicare supplement recommendations | IF order frequency > 3x/week THEN suggest dedicated delivery route |
| Priority Override | For this quarter/campaign, prioritize specific products | Q1 2026: Prioritize cloud migration services (strategic initiative) | Open enrollment period: Prioritize health insurance recommendations | Summer season: Prioritize beverage and ice cream categories |

### 5.4.2 Rule Schema

| // Business rule structure in tenant_config.business_rules { "rules": [ { "id": "rule-001", "name": "Always bundle firewall with license", "type": "inclusion", "active": true, "priority": 1,  // Higher priority rules evaluated first "conditions": { "operator": "AND", "clauses": [ { "field": "recommendation.product_category", "op": "eq", "value": "firewall" }, { "field": "recommendation.type", "op": "in", "value": ["cross_sell", "full_solution"] } ] }, "action": { "type": "add_product", "product_sku": "LIC-FW-STANDARD", "position": "bundled", "message": "Licensing is required for all firewall deployments." }, "scope": { "segments": ["all"],  // or ["enterprise", "mid_market"] "agents": ["cross_sell", "full_solution"], "industries": ["all"] }, "metadata": { "created_by": "admin@tenant.com", "reason": "Regulatory: all firewalls require active license", "expires_at": null  // null = permanent, or ISO date for campaign rules } } ] } |
| --- |

### 5.4.3 When Rules Are Enforced — Execution Timing

Business rules are evaluated at TWO critical points in the execution pipeline:

Enforcement Point 1 — Pre-Recommendation Filtering (inside Agent, before output): After the agent generates candidate recommendations but BEFORE they are sent to the user. The agent calls the Rules Engine with its candidate list. The Rules Engine applies exclusion rules (remove banned products), inclusion rules (add required companions), bundling rules (group products), and sequencing rules (reorder). The filtered list is what the user sees.

Enforcement Point 2 — Context Assembly (inside State Engine, during prompt construction): When the Context Assembler builds the LLM prompt for an agent call, it injects active rules as system instructions. The LLM sees: ‘RULES: Never recommend product X to customers in segment Y. Always mention installation services with hardware over $10K.’ This means the LLM itself respects rules during its reasoning, not just as a post-filter.

Both enforcement points are needed because: Point 1 catches anything the LLM missed or misinterpreted. Point 2 gives the LLM awareness of rules so it can reason about them (e.g., explain WHY a bundle includes licensing). Together they provide defense-in-depth.

| async def apply_business_rules(tenant_id, recommendations, context): rules = await get_active_rules(tenant_id) for rule in sorted(rules, key=lambda r: r['priority']): if not rule['active']: continue if rule.get('expires_at') and now() > rule['expires_at']: continue # Check scope: does this rule apply to this agent/segment? if not matches_scope(rule['scope'], context): continue # Evaluate conditions against recommendations + context if evaluate_conditions(rule['conditions'], recommendations, context): recommendations = execute_action(rule['action'], recommendations) return recommendations def execute_action(action, recs): if action['type'] == 'remove_product':      # Exclusion return [r for r in recs if r.sku != action['product_sku']] elif action['type'] == 'add_product':        # Inclusion if not any(r.sku == action['product_sku'] for r in recs): recs.append(create_bundled_rec(action)) return recs elif action['type'] == 'reorder':            # Sequencing return apply_sequencing(recs, action) elif action['type'] == 'group_bundle':       # Bundling return group_as_bundle(recs, action) elif action['type'] == 'boost_priority':     # Priority Override return boost_products(recs, action) |
| --- |

### 5.4.4 How Rules Are Onboarded

Rules come from three sources during onboarding:

Source 1 — Industry template defaults: Each industry template includes common rules for that vertical. IT Reseller template includes: ‘always bundle licensing with hardware.’ Insurance template includes: ‘never sell to minors.’ These are starting points that the tenant reviews and customizes.

Source 2 — Tenant’s sales manager interview: During onboarding, we ask: ‘What should we NEVER recommend? What must ALWAYS go together? Any regulatory constraints? Any current campaigns or strategic pushes?’ This 30-minute conversation typically yields 10-20 rules.

Source 3 — Iteration after first use: Reps start using the platform and dismiss recommendations that violate unwritten rules. The Feedback Engine captures dismiss-with-reason signals. Patterns emerge: ‘Reps always dismiss Product X for SMB customers.’ This triggers a rule suggestion: ‘Should we exclude Product X for SMB?’

| DECISION: Rule Complexity for MVP Decided: MVP supports inclusion, exclusion, and bundling rules only. Conditions are limited to: product category, customer segment, and deal size. No complex nested logic. Rationale: Sequencing, conditional, and priority override rules require a more sophisticated evaluation engine. Inclusion/exclusion/bundling cover 80% of real-world rules. Conditional rules (IF/THEN with complex predicates) are Phase 2 when the visual rule builder UI is available. |
| --- |

## 5.5 Agent Configuration

Agent configuration determines which agents are active, how they trigger, and how they behave for each tenant.

### 5.5.1 Configuration Fields

| Config Field | Description | Default | Where It’s Enforced |
| --- | --- | --- | --- |
| enabled | Is this agent active for this tenant? | true for MVP agents (cross-sell, upsell, full-solution), false for post-MVP agents | Agent Orchestrator: skips disabled agents during trigger evaluation |
| trigger_conditions | State conditions that activate this agent | Varies per agent: cross-sell triggers on whitespace detected, upsell on upgrade path available | Agent Orchestrator: evaluates triggers against current session state |
| discovery_threshold | What percentage of discovery must be complete before recommendations appear? | 0.4 (40%) — meaning at least 40% of discovery dimensions must have signal before recs surface | Agent itself: checks discovery_progress before generating recommendations |
| max_recommendations | Maximum number of product recommendations per turn | 3 | Agent: limits output list length |
| confidence_floor | Minimum confidence score for a recommendation to be shown | 0.3 (30%) | Agent: filters out low-confidence recommendations |
| llm_model | Which LLM model to use for this agent | gpt-4o (same for all in MVP) | Agent: selects model at call time |
| retrieval_effort | Foundry IQ retrieval reasoning effort level | medium for workflow agents, low for quick actions | Context Assembler: sets effort level on Foundry IQ query |
| prompt_modifiers | Tenant-specific additions to the base agent prompt | [] empty | Agent: appended to system prompt. E.g., ‘Keep recommendations under $50K for this tenant.’ |
| quick_action_ids | Which quick action plugins are available | [email_gen, kb_search, account_overview] in MVP | Agent Orchestrator + UI: determines which quick action buttons appear |
| custom_discovery_questions | Tenant-provided questions to inject into discovery | [] empty, uses template defaults | Agent: merges with template questions during discovery |

### 5.5.2 Enforcement Points

Agent configuration is checked at multiple points during execution:

Session start: Orchestrator loads tenant’s agent_config. Builds the list of available agents and quick actions for this session. Sends to UI (which renders the quick action bar and agent switcher accordingly).

Trigger evaluation: When state changes, Orchestrator evaluates trigger_conditions for each ENABLED agent only. Disabled agents are never evaluated.

Agent execution: When an agent runs, it reads its own config: discovery_threshold, max_recommendations, confidence_floor, prompt_modifiers. These shape its behavior at runtime.

Context assembly: The Context Assembler uses retrieval_effort to set the Foundry IQ reasoning level. Quick actions use ‘low’, workflow agents use ‘medium’, full solution uses ‘high’.

| // Agent config example for an IT Reseller tenant { "agents": { "cross_sell": { "enabled": true, "discovery_threshold": 0.4, "max_recommendations": 3, "confidence_floor": 0.3, "retrieval_effort": "medium", "prompt_modifiers": [ "This tenant sells IT infrastructure. Focus on technology stack gaps.", "Always mention vendor compatibility when recommending across brands." ], "trigger_conditions": { "operator": "AND", "conditions": [ { "field": "customer.products", "op": "not_empty" }, { "field": "enrichment.whitespace_percentage", "op": "gt", "value": 0.2 } ] } }, "upsell": { "enabled": true, "discovery_threshold": 0.35, "max_recommendations": 2, "trigger_conditions": { "conditions": [ { "field": "product_graph.upgrade_paths_available", "op": "gt", "value": 0 } ] } }, "renewal": { "enabled": false }, "win_back": { "enabled": false } } } |
| --- |

## 5.6 State Schema Configuration

The state schema defines what data is tracked in each conversation session. It has three layers (as designed in Module 1), and Module 5 owns the configuration of the Domain layer:

### 5.6.1 The Three Layers

| Layer | Owner | Content | Configurable? |
| --- | --- | --- | --- |
| Universal | Platform (hardcoded) | session_id, tenant_id, rep_id, customer.profile, customer.products, conversation.messages[], active_agent, discovery_progress, recommendations[] | NO — same structure for every tenant. Never changes. |
| Domain | Module 5 (tenant-configured) | Industry-specific fields: technology_stack, policy_types_held, facility_type, etc. Whatever is relevant for this tenant’s sales process. | YES — configured per tenant during onboarding. Stored in tenant_config.state_schema. |
| Agent-Specific | Module 2 (agent-scoped) | Sandboxed state per agent: cross_sell.candidates[], upsell.upgrade_paths[], full_solution.blueprint{}. Each agent reads/writes only its own namespace. | PARTIALLY — agents define their own state shape, but per-tenant prompt_modifiers can influence what agents write. |

### 5.6.2 Domain Schema Configuration

The domain schema is defined as a JSON document that specifies: field name, data type, default value, whether it’s required, display label, and which agents can read/write it.

| // State schema configuration for an IT Reseller tenant { "domain_fields": [ { "field_name": "technology_stack", "type": "array<string>", "default": [], "required": false, "display_label": "Current Technology Stack", "description": "Technologies/vendors the customer currently uses", "populated_by": ["crm_import", "discovery"], "used_by_agents": ["cross_sell", "upsell", "full_solution"], "discovery_prompt": "Ask about their current technology environment" }, { "field_name": "contract_expirations", "type": "object<string, date>", "default": {}, "required": false, "display_label": "Contract Expiry Dates", "description": "Map of vendor/product to contract end date", "populated_by": ["crm_import"], "used_by_agents": ["renewal", "cross_sell"], "discovery_prompt": null  // Not asked during discovery; comes from CRM data }, { "field_name": "cloud_migration_status", "type": "enum", "enum_values": ["not_started", "planning", "in_progress", "mostly_cloud", "cloud_native"], "default": null, "required": false, "display_label": "Cloud Migration Status", "populated_by": ["discovery"], "used_by_agents": ["cross_sell", "full_solution"], "discovery_prompt": "Where are they in their cloud journey?" } ], "discovery_dimensions": [ { "dimension_name": "infrastructure_needs", "display_label": "Infrastructure Needs", "weight": 0.25,  // Contributes 25% to overall discovery progress "questions": [ "What does your current network infrastructure look like?", "Are you experiencing any performance bottlenecks?", "What are your plans for infrastructure in the next 12 months?" ], "unlocks": ["cross_sell", "full_solution"]  // Which agents benefit }, { "dimension_name": "security_posture", "display_label": "Security Posture", "weight": 0.20, "questions": [ "What security solutions do you currently have in place?", "Have you had any security incidents in the past year?", "Do you have compliance requirements (PCI, HIPAA, SOC2)?" ], "unlocks": ["cross_sell"] } ] } |
| --- |

Where is this configured? In MVP: the engineering team writes this JSON during onboarding based on the Domain Research output (Section 5.2). In full product: the Admin Portal provides a visual field editor where an admin can add, rename, or remove domain fields, define discovery dimensions, and preview how changes affect the conversation flow.

## 5.7 Onboarding Flow — Complete Deep Dive

| The Onboarding Goal ‘Zero to first value in under 2 weeks.’ The tenant should be able to have their first real AI-assisted sales conversation within 10 business days of signing. Everything in the onboarding flow is designed to hit this target while collecting enough configuration to make the platform genuinely useful (not a generic chatbot). |
| --- |

### 5.7.1 Onboarding Timeline — Day by Day

| Day | Phase | What Happens | Who’s Involved | Deliverable |
| --- | --- | --- | --- | --- |
| Day 1 | Kickoff Call | Introductions, platform demo, set expectations. Explain what we need from them: data exports, 30-min rep interview, sales playbook. Schedule all follow-up sessions. | Us: Implementation Lead. Them: Sales Manager + Admin + (ideally) 1 top rep. | Onboarding project plan with dates. Shared checklist of deliverables. |
| Day 1-2 | Tenant Creation | Create tenant record in platform. Generate tenant_id. Set up: Blob Storage container, Foundry IQ Knowledge Base, initial config from industry template (if exists) or blank template. | Us: Engineering team. | Tenant exists in system. Admin can log in (email/password created). |
| Day 2-3 | Data Collection | Tenant exports and sends us: (1) Account/customer CSV, (2) Product catalog CSV/Excel, (3) Transaction history CSV, (4) Any KB docs (playbooks, case studies, battle cards). We provide CSV templates with example data. | Them: Admin or data analyst exports from CRM. Us: Provide templates, answer questions about format. | Raw data files received. |
| Day 3-4 | Domain Research Interview | 30-minute structured interview with their top rep and/or sales manager. Follow the Domain Research Framework (Section 5.2). Record the call. Extract: discovery dimensions, business rules, terminology, selling patterns. | Us: Implementation Lead (with interview guide). Them: Top rep + sales manager. | Domain Research Notes: discovery dimensions, 10-20 business rules, terminology map, state schema fields identified. |
| Day 4-5 | Data Import + Configuration | Import CSVs via Module 4 pipeline. Configure field mappings. Review data quality report. Meanwhile: write tenant_config JSON based on domain research: state_schema, discovery_config, business_rules, agent_config, ui_config. | Us: Engineering team. Them: Admin reviews data quality report, flags issues. | Data imported. Configuration written. Product Graph Layer 1 + 2 running. |
| Day 5-6 | KB Ingestion | Upload KB documents to Foundry IQ. Product catalog indexed. Case studies and playbooks ingested. Product Graph edges generated. Verify retrieval quality with test queries. | Us: Engineering team. Them: Provide any missing documents. | KB operational. Test queries return relevant results. |
| Day 6-7 | Configuration Tuning | Test the platform end-to-end: pick a real account, start a conversation, verify discovery questions make sense, verify recommendations are relevant, verify business rules fire correctly. Iterate on prompts and rules. | Us: Engineering team tests. Them: Sales manager reviews test output and gives feedback. | Configuration validated. Known issues listed and addressed. |
| Day 8 | User Setup | Create user accounts for reps and admins. Map reps to their accounts (account_owner_email). Brief training session (30 min): how to start a conversation, how to use quick actions, how to give feedback. | Us: Implementation Lead conducts training. Them: All reps + admin attend. | Users can log in. Reps know how to use the platform. |
| Day 9-10 | Guided Pilot | 2-3 reps use the platform with REAL accounts in real conversations. Implementation Lead is available for questions. Monitor: are recommendations relevant? Are discovery questions helpful? Are there rules we missed? | Us: Monitor usage, collect feedback. Them: Reps use platform, provide verbal feedback. | Pilot feedback collected. Final adjustments made. |
| Day 10 | Handoff to Production | All reps enabled. Admin trained on: re-uploading data, reviewing recommendations, basic troubleshooting. Ongoing support channel established (Slack channel or email). | Us: Implementation Lead. Them: Admin. | Tenant live in production. First value achieved. |

### 5.7.2 Exact Information Required from Client

| Information | Required? | Format | Why We Need It | What Happens If Missing |
| --- | --- | --- | --- | --- |
| Account/Customer list | REQUIRED | CSV with: company name, industry, segment, revenue, rep assignment | Populates State Engine. Without it, reps must manually describe every customer. | BLOCKER: Platform is essentially useless without knowing who the customers are. Cannot proceed without at minimum company names. |
| Product catalog | REQUIRED | CSV/Excel with: SKU, name, description, category, price | Populates KB and Product Graph. Without it, the AI has no products to recommend. | BLOCKER: Cannot generate any recommendations. Must have at minimum SKU + name + category. |
| Transaction/purchase history | STRONGLY RECOMMENDED | CSV with: account, product SKU, amount, date | Feeds Product Graph Layer 1 (co-occurrence). Feeds enrichment (lifetime value, whitespace). | DEGRADED: Product Graph runs on Layer 2 only (LLM inference from catalog). No co-occurrence analysis. Recommendations are less accurate but functional. |
| Rep interview / sales playbook | STRONGLY RECOMMENDED | 30-min call or sales training documents | Defines discovery dimensions, business rules, terminology. | DEGRADED: We use industry template defaults. Discovery questions are generic. Business rules are limited to obvious ones. Platform works but feels generic rather than tailored. |
| Case studies / battle cards | RECOMMENDED | PDF, Word, Markdown documents | Enriches KB for case study matching, competitor comparison. | DEGRADED: Agents cannot reference case studies or provide competitive positioning. Recommendations are product-focused only. Platform is functional but less compelling. |
| Contact list | RECOMMENDED | CSV with: name, email, title, account association | Email gen plugin can address contacts by name/title. Agents know decision-makers. | DEGRADED: Email generator uses generic salutations. Agents don’t know who the stakeholders are. Rep must provide context manually during conversation. |
| Business rules list | RECOMMENDED | From interview or written document | Configures Rules Engine to prevent bad recommendations. | DEGRADED: No rules configured. Platform may recommend products that should be excluded. Risk of embarrassing recommendations. Rules added iteratively from rep feedback. |
| Logo + branding | OPTIONAL | PNG/SVG logo, hex color code | Customizes the UI to feel like the tenant’s own tool. | MINIMAL IMPACT: Platform uses default NxGen branding. Functional but looks generic. |
| CRM field descriptions | OPTIONAL | Export of CRM field metadata or screenshots | Helps with field mapping during data import. | MINIMAL IMPACT: Field mapping takes slightly longer because we’re guessing column meanings from sample data. |

### 5.7.3 Degraded States — What Works Without Full Data

Not every tenant will provide everything. The platform must degrade gracefully:

| Missing Data | What Still Works | What Doesn’t Work | Mitigation Strategy |
| --- | --- | --- | --- |
| No transaction history | KB search, case studies, discovery guidance, product catalog browsing, email gen, competitor cards. Product Graph uses Layer 2 (LLM inference) only. | Cross-sell recommendations are based on catalog relationships only (not proven co-purchase patterns). Whitespace percentage unavailable. No lifetime value computation. | Be transparent: ‘Recommendations are based on product relationships. Upload transaction history to unlock data-driven recommendations.’ Quality improves dramatically when transaction data arrives. |
| No rep interview / no playbook | Platform uses industry template defaults for discovery dimensions and business rules. | Discovery questions feel generic (‘Tell me about your current challenges’ instead of ‘What does your current network infrastructure look like?’). May recommend products that the tenant would never sell. | Run with templates for 2 weeks. Collect feedback from reps (dismiss reasons). Use feedback to refine discovery questions and add rules. Schedule a follow-up interview after reps have used the platform and can give concrete feedback. |
| No case studies | All agent recommendations, discovery guidance, KB product search, email gen. | Full Solution agent cannot match case studies to customer situations. Competitor cards lack win/loss stories. Recommendations lack social proof. | Agents adjust: ‘We recommend Product X because...’ without ‘...and here’s a customer who did the same thing.’ Prompt the admin to upload case studies over time. |
| No contacts | Conversations, recommendations, discovery, all agents. | Email generator uses generic ‘Dear [Customer]’ instead of ‘Dear Sarah’. Agents cannot reference decision-maker roles. | Rep provides contact context during conversation: ‘I’m talking to their CTO.’ Agent adapts on the fly. Less ideal but functional. |
| Minimal product catalog (name only, no descriptions) | Basic recommendations by category. | KB semantic search is poor (no descriptions to embed). LLM cannot infer product relationships from names alone. | Strongly push for descriptions. Even one-line descriptions dramatically improve retrieval and inference. Offer to help write descriptions from vendor documentation. |

### 5.7.4 Industry Templates

To accelerate onboarding, each industry gets a pre-built template — a starting configuration that encodes common patterns for that vertical:

| Template Component | What It Contains | How It’s Used |
| --- | --- | --- |
| Domain state fields | Pre-defined domain fields with types, defaults, and descriptions for the industry | Loaded as the starting state_schema. Admin can add/remove fields. |
| Discovery dimensions | 4-6 discovery dimensions with 3-5 questions each, based on industry sales patterns | Loaded as starting discovery_config. Tuned after rep interview. |
| Business rules | 5-10 common rules for the industry (obvious exclusions, common bundles) | Loaded as starting business_rules. Expanded from sales manager input. |
| Terminology map | Industry-specific labels: ‘Products’ vs ‘Policies’ vs ‘SKUs’ vs ‘Lines’ | Loaded into ui_config. Admin can customize further. |
| Agent prompt modifiers | Industry-specific instructions appended to base agent prompts | Loaded into agent_config.prompt_modifiers. Tuned during configuration. |
| CSV templates | Pre-formatted CSV templates with column headers matching the industry’s typical data structure | Given to tenant during data collection phase. Reduces mapping effort. |
| Sample data | Synthetic dataset for the industry (fake companies, fake products, fake transactions) | Used for testing configuration before real data arrives. |

| Template Strategy MVP: Build templates for 2 industries (IT Reseller — our origin market, plus one other Tier 1 industry selected based on first customer). Full product: Templates for all 10 target industries, built progressively as we onboard tenants from each vertical. Every onboarding produces a template for the next tenant in that industry. |
| --- |

## 5.8 Nuances, Risks, and Gaps Filled

| Gap Filled: Configuration Versioning and Rollback Every configuration change creates a new version. If an admin changes a business rule and recommendations break, we can instantly revert to the previous version. The tenant_config table tracks version history. In MVP, versioning is manual (engineering team creates a new row). In full product, every Admin Portal save auto-increments the version with a change note. |
| --- |

| Gap Filled: Configuration Validation Before a new configuration is activated, it must pass validation: required fields in state_schema have compatible types, business rules reference valid product SKUs and segments, agent triggers reference valid state fields, discovery dimensions have at least one question each, and no circular dependencies in sequencing rules. Invalid configurations are rejected with specific error messages. |
| --- |

| Gap Filled: Tenant Deprovisioning When a tenant churns, their data must be completely removable: delete all rows with their tenant_id from all tables, delete their Blob Storage container, delete their Foundry IQ Knowledge Base, delete their Redis keys. This must be automated and auditable. GDPR/CCPA may require this to happen within a specific timeframe. Build the tenant_delete(tenant_id) function early even if you don’t expose it in UI. |
| --- |

| Gap Filled: Cross-Tenant Insights (Future) In full product, anonymized patterns from one tenant could benefit another: ‘IT reseller tenants on average have 15 business rules. You have 3 — you may be missing constraints.’ Or: ‘Tenants in your industry typically track 6 discovery dimensions. You have 2.’ This requires explicit opt-in and careful anonymization. Not MVP — but the data model should not prevent this future capability. |
| --- |

| Nuance: Config Changes and Active Sessions If an admin changes configuration while reps are in active conversations, those conversations should NOT be affected. Config is loaded at session start and cached for the session lifetime. New sessions pick up the new config. This prevents a rule change from causing a jarring mid-conversation behavior change. |
| --- |

| Nuance: Multi-Admin Conflict Two admins editing configuration simultaneously could overwrite each other. In MVP (JSON file), this is managed by the engineering team (single point of edit). In full product, the Admin Portal needs optimistic locking: when Admin A saves, if the version they loaded is no longer the latest, show a conflict warning: ‘Another admin has made changes since you started editing. Review their changes before saving.’ |
| --- |

## 5.9 Key Technical Decisions Summary

| DECISION: Row-Level Isolation with tenant_id Decided: Shared tables with tenant_id column and PostgreSQL RLS policies. Not separate databases or schemas. Rationale: At our scale (10-100 tenants initially), separate databases add massive operational overhead with no real security benefit. RLS provides database-level enforcement as a safety net. If we ever need to move a specific tenant to their own database (for compliance or performance), the tenant_id column makes this migration straightforward. |
| --- |

| DECISION: JSONB for All Configuration Decided: Store all tenant configuration as structured JSONB in a single tenant_config table, not normalized relational tables. Rationale: Configuration is read-heavy, write-rare. JSONB allows us to evolve the config schema without migrations. Each industry may have completely different config shapes. JSONB supports this naturally. PostgreSQL JSONB operators provide efficient querying when needed. |
| --- |

| DECISION: White-Glove Onboarding for MVP Decided: Engineering team manually configures each tenant. No self-serve onboarding UI. Rationale: Self-serve onboarding requires: industry template library, visual configuration UI, data import wizard, automated validation, guided setup flow. This is 2-3 months of UI work. White-glove lets us learn what tenants actually need (vs. what we assume) and build self-serve informed by that experience. Target: self-serve onboarding by Month 8. |
| --- |

| DECISION: Industry Templates as Accelerators Decided: Pre-built configuration templates per industry, used as starting points not rigid structures. Rationale: Templates solve the cold start problem: a new tenant doesn’t face a blank configuration. But every tenant is different, so templates must be customizable. Templates are living documents that improve with each onboarding in that industry. |
| --- |

| DECISION: Config Cached Per Session Decided: Configuration is loaded from DB at session start, cached in Redis, and immutable for the session lifetime. Rationale: Real-time config updates mid-conversation would be confusing for reps and complex to implement. Session-scoped caching provides consistency: the platform behaves the same throughout a conversation. New sessions automatically pick up config changes. |
| --- |
