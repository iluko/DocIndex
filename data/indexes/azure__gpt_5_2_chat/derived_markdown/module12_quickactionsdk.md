Sales Intelligence Platform

Technical Deep-Dive: Module 12

Quick Action Agent SDK — Plugin Framework

SDK Architecture, Security Model, UI Rendering, Built-In Plugins, Lifecycle, Analytics & Open Framework

March 2026  |  Internal — Engineering Team

| Document Scope This document covers the complete Plugin Agent SDK: execution model and Plugin Gateway pipeline, five-layer security model (sandboxed execution, scoped APIs, state write sandboxing, output sanitization, review gate), Agent Interface Contract (manifest specification, execute function, onFeedback handler), Platform Access APIs (state read/write permissions, KB query, history read, LLM connector), UI Rendering Contract (Card, Table, Form, File, Custom Component), Plugin Output Bus for inter-plugin communication, rep interface layout with Customer Context Panel and Accepted Products Tray, all 8 built-in quick actions with per-plugin specifications and data diets, agent manifest control (versioning, tenant customization levels, deployment), feedback and analytics signal taxonomy, complete plugin lifecycle (develop, test, register, review, deploy, monitor), alternative Open Plugin Framework for future evolution, and key technical decisions. |
| --- |

# Table of Contents

12.1  SDK Architecture Overview — Execution model, Plugin Gateway pipeline

12.2  Security Model — Five layers of isolation, enforcement points

12.3  Agent Interface Contract — Manifest spec, execute(), onFeedback()

12.4  Platform Access APIs — State read/write permissions, KB, history, LLM connector

12.5  UI Rendering Contract — Card, Table, Form, File, Custom Component

12.6  Plugin Output Bus — Inter-plugin communication

12.7  Rep Interface Layout — Where plugins render, Customer Context Panel

12.8  Built-In Quick Actions — All 8 plugins: triggers, data, output, LLM usage

12.9  Agent Manifest Control — Versioning, customization, domain-agnostic design

12.10 Feedback and Analytics — Signal taxonomy, improvement mechanisms

12.11 Plugin Lifecycle — Develop, test, register, review, deploy, monitor

12.12 Alternative: Open Plugin Framework — Less restrictive evolution path

12.13 Key Technical Decisions Summary

# Module 12: Quick Action Agent SDK — Plugin Framework

| Module Purpose Module 12 defines the Plugin Agent SDK — the interface contract, execution runtime, and security model that all quick action plugins (built-in and custom) are built on. It provides: the Agent Interface Contract (manifest, execute, feedback), Platform Access APIs (scoped read/write to state, KB, history), a UI Rendering Contract (Card, Table, Form templates), 8 built-in quick action plugins, inter-plugin communication via the Plugin Output Bus, and a complete plugin lifecycle from development through monitoring. The SDK is used by our engineering team (for built-in plugins) and by tenant developers (for custom plugins in full product). Built-in plugins are NOT special — they use the exact same SDK as any custom plugin, which dogfoods the framework and proves its robustness. |
| --- |

| MVP vs Full Product MVP: 8 built-in plugins developed by us, deployed as part of the platform. All plugins execute within the Orchestrator service via the Plugin Gateway. UI output limited to Card, Table, and Form templates. Plugin state is read-only (no State Write). No third-party plugin development. Full Product: custom plugin development by tenant developers. State Write API (sandboxed). Custom Component output type (iframe-sandboxed React). Plugin review and approval workflow. Marketplace for shared plugins across tenants. External LLM connectors (declared in manifest). |
| --- |

| Who Uses This Module? Two classes of developers: (1) Our engineering team builds the 8 built-in plugins using this SDK. (2) In full product, tenant developers build custom plugins using this SDK. Both follow the same interface contract, go through the same Plugin Gateway, and are subject to the same security enforcement. The only difference: built-in plugins skip the review gate (we wrote them, we trust them). Custom plugins go through review before activation. |
| --- |

## 12.1 SDK Architecture Overview

The Plugin Agent SDK is NOT a standalone library that runs anywhere. It is an interface contract plus a runtime sandbox that lives inside the platform. Think of it like a Shopify App SDK or Slack Bolt framework — you build to a defined interface, and the platform executes your code within its controlled environment. The plugin never runs independently; the Orchestrator (Module 2) invokes it through the Plugin Gateway.

### 12.1.1 Execution Model

When the Orchestrator decides to execute a plugin (because its trigger conditions are met or the rep clicked its button), the execution follows this path:

Step 1 — Trigger Evaluation: The Orchestrator evaluates all enabled plugin trigger conditions against the current session state. Plugins whose conditions are met are marked for execution. The rep can also manually trigger any enabled plugin by clicking its button.

Step 2 — Context Assembly: The Plugin Gateway assembles the execution context: it reads the state fields declared in the plugin’s manifest stateRequirements, queries KB if kbRequirements exist, retrieves conversation history up to conversationHistoryDepth, loads previous plugin outputs from the Plugin Output Bus, and provisions an LLM client if llmRequired is true.

Step 3 — Sandboxed Execution: The Plugin Gateway calls the plugin’s execute(context) function within an isolated execution context. The plugin has access ONLY to the provisioned context object — no raw database connections, no core module imports, no cross-tenant data. Execution is time-limited (default 5 seconds, configurable per manifest).

Step 4 — Output Validation: The Plugin Gateway validates the returned output against the plugin’s declared outputSchema. If the output is invalid (wrong type, missing required fields, contains raw HTML), the Gateway rejects it and shows a fallback error card.

Step 5 — Output Sanitization: All text content in the output is sanitized: no script tags, no event handlers, no external resource URLs. The sanitized output is stored in session.plugin_outputs for inter-plugin access and sent to the UI for rendering.

Step 6 — Analytics Logging: The Gateway logs execution metrics: plugin_id, execution_time_ms, llm_tokens_used, state_reads_count, kb_queries_count, output_type, trigger_type (auto or manual). These feed Module 16 (Feedback Engine).

| // Plugin Gateway execution pipeline (inside Module 2 Orchestrator) async function executePlugin(pluginId: string, session: Session): Promise<PluginResult> { const manifest = await pluginRegistry.getActive(pluginId, session.tenantId); if (!manifest) throw new PluginNotFoundError(pluginId); // Step 2: Assemble scoped context const context = await assemblePluginContext(manifest, session); // Step 3: Execute with timeout const timeout = manifest.resourceLimits?.maxExecutionMs \|\| 5000; const rawOutput = await withTimeout( pluginRuntime.execute(manifest, context), timeout, new PluginTimeoutError(pluginId, timeout) ); // Step 4: Validate output against declared schema const validation = validateOutput(rawOutput, manifest.outputSchema); if (!validation.valid) { logPluginError(pluginId, 'schema_validation', validation.errors); return { type: 'card', title: 'Plugin Error', body: 'Unable to load. Please try again.' }; } // Step 5: Sanitize const sanitized = sanitizePluginOutput(rawOutput); // Store in Plugin Output Bus for inter-plugin access session.pluginOutputs[pluginId] = { lastExecuted: new Date().toISOString(), output: sanitized }; // Step 6: Log analytics await logPluginExecution({ pluginId, tenantId: session.tenantId, sessionId: session.id, executionTimeMs: elapsed, ...context.metrics }); return sanitized; } |
| --- |

| Plugin Initialization at Session Start At session start, the Orchestrator loads all enabled plugins for the tenant. Plugins with autoSurface triggers are evaluated immediately. Meeting Prep Brief and Account Overview typically fire at session start. Other plugins wait for their trigger conditions. The load order does not affect functionality — plugins are independent and do not depend on load sequence. |
| --- |

## 12.2 Security Model — Five Layers of Isolation

| Security Philosophy A plugin must never be able to: read another tenant’s data, corrupt core conversation state, inject malicious content into the UI, make undeclared external network calls, or escalate its own permissions. Five layers enforce this. Even if one layer fails, the remaining four prevent a breach. |
| --- |

| Layer | Mechanism | What It Prevents | Enforcement Point |
| --- | --- | --- | --- |
| 1. Sandboxed Execution | Plugin runs in isolated context — separate function invocation (MVP) or isolated container/Lambda (full product). Cannot import core modules or access raw database connections. | Plugin accessing State Engine directly, reading raw SQL, importing Orchestrator internals, accessing file system. | Plugin Gateway provisions a clean execution environment per invocation. |
| 2. Scoped API Access | Plugins interact with platform ONLY through the PluginContext object. Every API call (state read, KB query, history read) goes through the Plugin Gateway, which enforces tenant_id, field-level permissions, and rate limits. | Cross-tenant data access, reading auth tokens, reading business rules or agent config internals, exceeding resource limits. | Plugin Gateway mediates every API call. SDK client serializes requests; Gateway validates before forwarding to State Engine. |
| 3. State Write Sandboxing | Plugins can ONLY write to plugin_state.{own_plugin_id}.* namespace. Any write to agent_state, discovery, customer, or recommendations is rejected. | Buggy or malicious plugin corrupting conversation state, discovery progress, recommendations, or customer data. | State Engine validates write path on every write request. Rejects paths not matching plugin_state.{caller_plugin_id}.*. |
| 4. Output Sanitization | Plugin output goes through sanitization before UI rendering. No raw HTML, no <script> tags, no event handlers, no external resource URLs. UI renders structured data only. | XSS attacks, content injection, external resource loading (tracking pixels, malware), DOM manipulation. | Plugin Gateway sanitizes output. UI rendering engine only accepts structured template data (Card/Table/Form JSON). |
| 5. Review Gate (Full Product) | Custom plugins undergo review before activation: manifest audit, code review, security scan, test harness results. | Malicious plugins, data exfiltration attempts, undeclared external network calls, permission overreach. | Plugin registry enforces status flow: pending_review → approved → active. Only approved plugins can be activated. |

Rate limiting is enforced by the Plugin Gateway per execution: default 10 state reads, 3 KB queries, 1 LLM call, 5-second timeout. Exceeding any limit kills the execution and returns a fallback error card. These limits are configurable per plugin via the manifest’s resourceLimits field.

| DECISION: Security Enforcement Location Decided: The Plugin Gateway is a component WITHIN Module 2 (Orchestrator), not a separate service. It extends the existing agent execution pipeline with plugin-specific sandboxing and permission checks. Rationale: The Orchestrator already handles agent invocation, context assembly, and state management. Plugin execution is architecturally identical to agent execution (context in, structured output out). Adding a Plugin Gateway as an Orchestrator subcomponent avoids a new service while reusing existing infrastructure. If plugin volume grows significantly in full product, the Gateway can be extracted into a dedicated service. |
| --- |

## 12.3 Agent Interface Contract

### 12.3.1 The Manifest

Every plugin — built-in or custom — is defined by a manifest. The manifest declares everything the platform needs to know about the plugin: what it does, when it triggers, what data it needs, what it outputs, what resources it consumes, and what feedback it collects. The manifest is a JSON document.

| { "id": "builtin-email-gen", "name": "Generate Email", "version": "1.2.0", "icon": "email", "description": "Generates a personalized follow-up email based on recommendations.", "triggerConditions": { "type": "state_condition", "conditions": [ { "field": "agent_state.*.recommendations", "op": "not_empty" } ] }, "manualTrigger": true,       // Rep can also click the button manually "autoSurface": true,         // Card appears automatically when triggered "surfacePriority": 3,        // Lower = higher priority in side panel ordering "stateRequirements": [ "customer.profile", "customer.products", "agent_state.*.recommendations", "agent_state.*.phase", "enrichment.segment_benchmark" ], "kbRequirements": { "sources": ["case_studies"], "maxResults": 3, "retrievalEffort": "low" }, "conversationHistoryDepth": 15, "llmRequired": true, "llmConfig": { "model": "tenant_default", "maxTokens": 1500, "temperature": 0.7 }, "outputSchema": { "type": "form", "formFields": ["to", "subject", "tone", "body"] }, "permissions": { "stateRead": ["customer.*", "agent_state.*", "enrichment.*", "intent_signals.*"], "stateWrite": ["plugin_state.email_gen.*"], "kbRead": true, "kbWrite": false, "externalNetwork": false }, "resourceLimits": { "maxExecutionMs": 5000, "maxStateReads": 10, "maxKbQueries": 3, "maxLlmCalls": 1 }, "feedback": { "collectSignals": ["used", "dismissed", "edited", "copied", "regenerated"], "onFeedback": true } } |
| --- |

### 12.3.2 Manifest Field Reference

| Field | Type | Purpose | Who Sets It |
| --- | --- | --- | --- |
| id | string | Unique plugin identifier. Built-in: prefixed with builtin-. Custom: prefixed with custom-{tenant_short_name}-. | Developer (immutable after registration). |
| triggerConditions | object | State conditions that auto-trigger the plugin. Same condition syntax as agent triggers (Module 2). When conditions are met AND autoSurface is true, the plugin card appears automatically in the side panel. | Developer defines. Tenant admin can override thresholds in full product. |
| manualTrigger | boolean | Whether the plugin’s button appears in the Quick Action Bar for manual invocation. Most plugins have both auto and manual triggers. | Developer defines. Tenant admin can disable manual trigger. |
| surfacePriority | number (1–10) | When multiple plugins trigger simultaneously, lower numbers appear higher in the side panel. 1 = urgent (Objection Handler), 5 = informational (Find Case Study), 10 = background. | Developer defines. Platform can override for system plugins. |
| stateRequirements | string[] | Exact state field paths this plugin needs. The Plugin Gateway provisions ONLY these fields in the context. Attempt to read undeclared fields is rejected. | Developer defines. Reviewed during plugin approval. |
| kbRequirements | object | KB query configuration: which sources to search, max results, retrieval effort level. Set to null if plugin does not query KB. | Developer defines. |
| conversationHistoryDepth | number | How many conversation messages (rep + AI) the plugin can read. 0 = no history access. System prompts are stripped — only user/assistant visible messages. | Developer defines. Max 50 enforced by Gateway. |
| llmRequired / llmConfig | boolean / object | Whether the plugin needs LLM access. model: 'tenant_default' uses the tenant’s configured model. maxTokens and temperature control generation. Built-in LLM connector only in MVP; external connectors allowed in full product. | Developer defines. Token cost charged to tenant. |
| outputSchema | object | Declares the output template type: card, table, form, file, or custom (full product). The Gateway validates output against this schema. | Developer defines. Tenant admin can override template fields in full product (Level 2 customization). |
| permissions | object | Explicit permission declarations. stateRead: array of readable field patterns. stateWrite: array of writable field patterns (plugin_state.{id}.* only). kbRead/kbWrite: boolean. externalNetwork: boolean (requires justification in review). | Developer declares. Gateway enforces. Reviewer audits. |
| resourceLimits | object | Execution constraints. Enforced by Plugin Gateway — exceeding any limit kills the execution. Defaults: 5000ms timeout, 10 state reads, 3 KB queries, 1 LLM call. | Developer can request higher limits in manifest. Platform caps apply. |
| feedback | object | Which engagement signals to collect and whether the onFeedback handler exists for real-time session-scoped learning. Signals feed Module 16 (Feedback Engine). | Developer defines which signals are meaningful for this plugin. |

### 12.3.3 The execute() Function

The execute function is the plugin’s main logic. It receives a PluginContext object containing all provisioned data (state fields, KB results, conversation history, LLM client, plugin outputs from other plugins) and returns a structured output matching the declared outputSchema.

| import { PluginContext, FormOutput } from '@sip/plugin-sdk'; export async function execute(context: PluginContext): Promise<FormOutput> { // Read provisioned state (only declared fields available) const customer = context.state.customer.profile; const recommendations = context.state.agentState.recommendations; const contacts = context.state.customer.contacts \|\| []; // Check if competitor comparison exists from another plugin const competitorData = context.pluginOutputs.read('builtin-competitor-card'); // Query KB for relevant case study const caseStudies = await context.kb.search({ query: `${customer.industry} ${recommendations[0]?.product_name}`, sources: ['case_studies'], maxResults: 1 }); // Generate email via built-in LLM connector const emailDraft = await context.llm.call({ prompt: buildEmailPrompt(customer, recommendations, caseStudies, competitorData), maxTokens: 1500 }); // Return structured form output return { type: 'form', title: 'Email Draft', fields: [ { id: 'to', type: 'email', label: 'To', value: contacts[0]?.email \|\| '' }, { id: 'subject', type: 'text', label: 'Subject', value: emailDraft.subject }, { id: 'tone', type: 'dropdown', label: 'Tone', options: ['Formal','Consultative','Casual'], value: 'Consultative' }, { id: 'body', type: 'textarea', label: 'Email Body', value: emailDraft.body, rows: 12 } ], actions: [ { label: 'Copy Draft', action: 'copy_to_clipboard', style: 'primary' }, { label: 'Regenerate', action: 're_execute', style: 'secondary' } ] }; } |
| --- |

### 12.3.4 The onFeedback() Handler

The onFeedback handler is OPTIONAL and serves a specific, narrow purpose: real-time, session-scoped adaptation. It fires when the rep interacts with the plugin’s output (dismiss, edit, regenerate) and allows the plugin to adjust its NEXT execution within the same session. It is NOT the primary learning mechanism — persistent learning goes through Module 16 (Feedback Engine).

| export async function onFeedback(signal: FeedbackSignal, context: PluginContext): void { if (signal.type === 'regenerated') { // Rep clicked Regenerate - adjust for next attempt context.sessionMemory.set('regeneration_count', (context.sessionMemory.get('regeneration_count') \|\| 0) + 1); context.sessionMemory.set('last_tone', signal.formState?.tone); // Next execute() call will read sessionMemory and try a different approach } if (signal.type === 'edited' && signal.editDistance > 0.5) { // Rep heavily edited - log that the template needs improvement context.sessionMemory.set('heavy_edit', true); } } |
| --- |

| onFeedback vs Module 16 Feedback Engine onFeedback is session-scoped and ephemeral: it adjusts the plugin’s behavior within the current conversation (e.g., try a different tone after regeneration). Module 16 is persistent and aggregative: it collects signals across all sessions, all reps, all tenants, identifies patterns (e.g., ‘40% edit rate on subject lines for insurance tenants”), and proposes systemic changes (prompt modifier adjustments, trigger condition tuning, plugin disabling). Both are needed. onFeedback handles real-time micro-adaptation. Module 16 handles strategic, long-term improvement. They share the same signal data — onFeedback processes the signal first (synchronously), then the signal is also logged to Module 16 (asynchronously). |
| --- |

## 12.4 Platform Access APIs

### 12.4.1 State Read API — What Plugins Can and Cannot Read

| Field Path | Readable? | Rationale |
| --- | --- | --- |
| customer.profile (company_name, industry, segment, revenue, employee_count) | YES | Core context for all plugins. Industry-agnostic. No security risk. |
| customer.products[] | YES | What the customer owns. Needed for cross-sell gap analysis, quote generation, email personalization. |
| customer.contacts[] | YES | Names, titles, emails for email generation and stakeholder mapping. |
| enrichment.* (whitespace_pct, health_score, yoy_growth, segment_benchmark, etc.) | YES | Computed intelligence. No raw data — pre-aggregated metrics safe for plugin consumption. |
| agent_state.{active_agent}.recommendations[] | YES | Core data for Email Gen, Quote Gen, and any plugin that acts on recommendations. |
| agent_state.{active_agent}.phase | YES | Lets plugins adapt output to conversation phase (discovery vs recommendation vs action). |
| agent_state.{active_agent}.talking_points[] | YES | Pre-computed talking points from core agents. Email Gen and Objection Handler reference these. |
| discovery.progress | YES | Discovery completion percentage. Plugins can show how much discovery is left. |
| discovery.dimensions.* | YES | Individual dimension status. Meeting Prep Brief uses this for briefing context. |
| intent_signals.* (competitor_mentioned, objection_detected, etc.) | YES | NLU-extracted signals. Trigger data for Competitor Card, Objection Handler, etc. |
| plugin_state.{own_plugin_id}.* | YES | Plugin’s own previous state from earlier in this session. |
| session.plugin_outputs.{any_plugin_id} | YES (read-only) | Other plugins’ rendered output. Powers inter-plugin data sharing (Plugin Output Bus). |
| session.auth_token, session.jwt | NO | Authentication credentials. Absolute security boundary. |
| tenant_config.* (business_rules, agent_config, etc.) | NO | Platform internals. Plugins operate on DATA, not configuration. Rules are enforced by the Orchestrator, not consumed by plugins. |
| plugin_state.{other_plugin_id}.* | NO | Other plugins’ private state. Only their rendered OUTPUT is shared (via Plugin Output Bus), not their internal working data. |
| conversation.messages[].system_prompt | NO | System prompts contain agent instructions, prompt engineering, and business rules injected by the Orchestrator. Exposing these would leak proprietary platform IP and rule logic. |
| Raw SQL queries, database connections | NO | Absolute boundary. Plugins have zero database access. All data access through the Gateway-mediated API. |

### 12.4.2 State Write API

MVP: plugins are read-only. No state writes. Full Product: sandboxed writes to plugin_state.{own_plugin_id}.* only.

| Write Target | Allowed? | Example Use Case |
| --- | --- | --- |
| plugin_state.{own_plugin_id}.* | YES (full product) | Email Gen saves plugin_state.email_gen.last_draft so the rep can recall it. Competitor Card saves plugin_state.competitor.comparison_data for audit. |
| agent_state.* | NEVER | Would corrupt core agent reasoning, phase transitions, and recommendations. |
| discovery.* | NEVER | Would confuse discovery progress tracking and threshold-based phase transitions. |
| customer.* | NEVER | Customer data is source-of-truth from CRM import (Module 4). Plugins cannot modify it. |
| active_workflow | NEVER | Only the Orchestrator controls agent switching. A plugin switching agents would cause chaos. |
| recommendations.* | NEVER | Only core agents (Modules 6–11) produce recommendations. Plugin cannot inject fake recommendations. |
| intent_signals.* | NEVER | Only the NLU extraction layer (Module 1) writes intent signals. Plugin-written signals would bypass NLU validation. |

### 12.4.3 KB Query API and History Read API

KB Query API: The plugin provides a search query string and optional filters (source type, max results). The Plugin Gateway routes the query to the tenant’s Foundry IQ Knowledge Base (Module 3) with the tenant’s retrieval effort setting. Results come back as title + content snippet + relevance score. The plugin CANNOT modify, add, or delete KB entries. Read-only.

History Read API: The plugin receives the last N messages of the current conversation (N = conversationHistoryDepth from manifest, max 50). Messages include role (user/assistant), content, and timestamp. System prompts are stripped. The plugin CANNOT read conversations from other sessions, other reps, or other tenants.

### 12.4.4 LLM Connector

Plugins that need natural language generation declare llmRequired: true in their manifest. The SDK provides a context.llm.call() function that routes through the platform’s LLM connector.

| Aspect | MVP | Full Product |
| --- | --- | --- |
| LLM Source | Built-in connector only. Uses tenant’s configured model (from tenant_config.agent_config.llm_model). Plugin cannot choose provider. | Built-in connector (default) OR declared external connector. Plugin manifest specifies externalLlm: { provider: 'openai', endpoint: '...' }. Gateway whitelists declared endpoints. |
| Token Tracking | Platform tracks tokens per plugin execution. Logged in analytics. Charged to tenant. | Same, plus per-plugin token budgets. Tenant admin can set monthly token limits per plugin. |
| Prompt Injection Protection | Plugin’s prompt is wrapped in a system envelope that prevents the plugin from overriding platform-level instructions. | Same, plus automated prompt scanning for injection patterns during review. |
| Streaming | No. Plugin receives complete LLM response. UI shows a loading spinner during execution. | Yes. Plugin can opt into streaming for long-form generation (email body). UI progressively renders as tokens arrive. |

| DECISION: LLM Access Model Decided: MVP: built-in connector only. No external LLM access. Full product: external connectors allowed with manifest declaration and Gateway whitelisting. Rationale: Built-in only in MVP controls cost, ensures consistency, and simplifies security. External connectors in full product enable specialized models (a medical device tenant might use a healthcare-tuned LLM for their custom compliance-check plugin) while maintaining visibility through manifest declaration. |
| --- |

## 12.5 UI Rendering Contract

| Design Principle Plugins output STRUCTURED DATA. The platform renders it. This separation ensures consistent UX across all plugins, prevents UI injection attacks, enables the platform to adapt rendering for different devices (desktop sidebar vs. mobile sheet), and allows the platform to evolve its design system without breaking plugins. A plugin never outputs HTML — it outputs JSON that the platform’s rendering engine transforms into UI components. |
| --- |

### 12.5.1 Card Template

The Card is the primary output type. It displays structured information in a panel with a title, optional subtitle, one or more content sections, and action buttons. Cards appear in the Side Panel and can be pinned, dismissed, or expanded by the rep.

| { "type": "card", "title": "Competitor Comparison: Cisco vs. Aruba", "subtitle": "Based on customer's current Cisco environment", "icon": "versus", "status": "info",  // info (blue), success (green), warning (amber), urgent (red) "sections": [ { "heading": "Key Differentiators", "content": "Cisco offers deeper integration with the customer's existing...", "style": "text"  // text, key_value, list, highlight }, { "heading": "Price Comparison", "content": [ { "label": "Cisco Meraki MR46", "value": "$1,295" }, { "label": "Aruba AP-535", "value": "$1,150" }, { "label": "Delta", "value": "+$145", "highlight": "negative" } ], "style": "key_value" }, { "heading": "Recommended Positioning", "content": "Focus on total cost of ownership. Cisco Meraki includes...", "style": "highlight"  // highlighted box for key talking points } ], "actions": [ { "label": "Copy to Email", "action": "copy_to_clipboard", "style": "primary" }, { "label": "Dismiss", "action": "dismiss", "style": "tertiary" } ], "footer": "Source: Foundry IQ knowledge base" } |
| --- |

Section styles: ‘text’ renders as plain paragraph. ‘key_value’ renders as a two-column label:value list. ‘list’ renders as bullet points. ‘highlight’ renders as a colored callout box. The platform’s design system determines exact colors, fonts, spacing — the plugin just declares the content structure.

### 12.5.2 Table Template

The Table Template renders comparison grids and structured tabular data. Primary use cases: product-vs-competitor comparison, tier-vs-tier comparison (Upsell), multi-product feature matrix (Full Solution), and quote line items (Generate Quote). Core agents provide the comparison DATA in their recommendation output; the plugin formats it into a Table template for rendering.

| { "type": "table", "title": "Tier Comparison: Standard vs. Premium", "columns": [ { "key": "feature", "label": "Feature" }, { "key": "current", "label": "Standard (Current)", "highlight": false }, { "key": "recommended", "label": "Premium (Recommended)", "highlight": true } ], "rows": [ { "feature": "Max Users", "current": "50", "recommended": "Unlimited" }, { "feature": "Storage", "current": "100GB", "recommended": "1TB" }, { "feature": "Support", "current": "Email only", "recommended": "24/7 Phone + Email" }, { "feature": "Price/month", "current": "$499", "recommended": "$899" } ], "summary": "Premium unlocks unlimited users and 24/7 support for +$400/month.", "actions": [{ "label": "Include in Email", "action": "copy_to_clipboard" }] } |
| --- |

### 12.5.3 Form Template

The Form Template is for plugins that need INPUT from the rep, not just display output. The plugin pre-populates field values (from LLM generation or data assembly), and the rep can edit before submitting. When the rep submits or clicks an action, the form data goes back to the plugin’s execute() function (with the form state as additional context) or is handled by a built-in action (copy_to_clipboard, re_execute).

Use cases: Email Gen (edit email draft before copying/sending), Generate Quote (adjust quantities, apply discounts before generating PDF), custom plugins (data entry forms like warranty lookup serial number input).

| { "type": "form", "title": "Email Draft", "fields": [ { "id": "to", "type": "email", "label": "To", "value": "sarah.johnson@acmemfg.com", "required": true }, { "id": "cc", "type": "email", "label": "CC", "value": "", "required": false }, { "id": "subject", "type": "text", "label": "Subject", "value": "Following up on our discussion about network monitoring" }, { "id": "tone", "type": "dropdown", "label": "Tone", "options": ["Formal","Consultative","Casual"], "value": "Consultative" }, { "id": "body", "type": "textarea", "label": "Email Body", "value": "Dear Sarah,\n\nThank you for our discussion today...", "rows": 12 } ], "actions": [ { "label": "Copy Draft", "action": "copy_to_clipboard", "style": "primary" }, { "label": "Regenerate", "action": "re_execute", "style": "secondary" }, { "label": "Cancel", "action": "dismiss", "style": "tertiary" } ] } |
| --- |

Supported field types: text, textarea, email, number, dropdown, checkbox, date, hidden. Each field has: id (unique identifier), type, label (display text), value (pre-populated default), required (boolean), options (for dropdowns), placeholder (hint text), validation (optional regex or min/max constraints).

### 12.5.4 File Output

Some plugins produce downloadable files rather than (or in addition to) inline UI. Generate Quote produces a formatted quote document. Meeting Prep Brief might produce a printable one-page brief. The file output type returns a file reference that the UI renders as a download button.

| { "type": "file", "filename": "Quote_AcmeManufacturing_2026-03-04.pdf", "mimeType": "application/pdf", "data": "<base64-encoded-content>", "preview": {  // Optional inline preview card "type": "card", "title": "Quote: Acme Manufacturing", "sections": [{ "heading": "Summary", "content": "3 line items, total $24,500", "style": "text" }], "actions": [{ "label": "Download PDF", "action": "download_file" }] } } |
| --- |

### 12.5.5 Custom Component (Full Product Only)

For use cases where Card, Table, Form, and File templates are insufficient, a plugin can provide a custom React component. This component renders inside an iframe sandbox in the Side Panel. The iframe has: sandbox="allow-scripts" (no same-origin access, no top-level navigation, no form submission outside the frame), a strict Content Security Policy (no external resource loading), and communication via postMessage only. The component talks to the platform through a structured message API, not direct DOM access.

Custom Components are NOT available in MVP. They are a full-product escape hatch for complex interactive visualizations (e.g., a network topology diagram, a drag-and-drop solution builder, a calendar scheduling widget). The review process for plugins with Custom Components is more rigorous: the React bundle is security-scanned, size-limited (max 500KB), and manually reviewed for DOM manipulation attempts.

| DECISION: UI Template Strategy Decided: MVP uses Card, Table, Form, and File templates only. These cover all 8 built-in plugins without limitation. Custom Components are full-product only, iframe-sandboxed, and require enhanced review. Rationale: Constrained templates ensure consistent UX, prevent injection attacks, enable device-adaptive rendering, and dramatically simplify plugin development (plugins output JSON, not UI code). Custom Components exist for the 5–10% of use cases that genuinely need rich interactivity, with security enforced by iframe isolation. |
| --- |

## 12.6 Plugin Output Bus — Inter-Plugin Communication

Plugins operate independently but sometimes benefit from each other’s output. Email Gen might want to include a competitor comparison table. Meeting Prep Brief might reference case studies found by Find Case Study. The Plugin Output Bus enables this without creating plugin-to-plugin dependencies.

How it works: The Orchestrator maintains a session.pluginOutputs object for the current session. Every time a plugin executes and produces output, the sanitized result is stored here. Any plugin can read any other plugin’s OUTPUT (the rendered card/table/form data) via context.pluginOutputs.read(pluginId). This is READ-ONLY access to rendered output — not the other plugin’s private state.

What plugins can read: The other plugin’s final rendered output (the same JSON the UI received). This is the same data the rep already saw — no hidden information exposure.

What plugins cannot do: Read another plugin’s plugin_state (private working data). Modify another plugin’s output. Invoke another plugin directly. Create dependency chains (if Plugin B depends on Plugin A’s output but Plugin A hasn’t run, Plugin B proceeds without it gracefully — the read returns null).

| // Email Gen reading Competitor Card's output const competitorOutput = context.pluginOutputs.read('builtin-competitor-card'); if (competitorOutput && competitorOutput.output.type === 'table') { // Include competitor data in email emailBody += '\n\nHere is how we compare:\n'; emailBody += formatTableForEmail(competitorOutput.output); } else { // Competitor Card hasn't run - proceed without it // Email is still complete, just without comparison data } |
| --- |

## 12.7 Rep Interface Layout — Where Plugins Render

| UI Context This section provides enough UI context to understand how plugins integrate into the rep’s workflow. The full UI specification is covered in a dedicated UI/UX module. Here we define the layout zones and how plugin output maps to each zone. |
| --- |

| Zone | Location | Content | Plugin Integration |
| --- | --- | --- | --- |
| Customer Context Panel | Left panel (always visible when customer selected) | Customer Profile Card: company name, industry, segment, revenue, lifecycle stage, health score (color-coded). Owned Products List: grouped by category with spend amounts. Key Contacts: name, title, email (clickable to insert into Email Gen). Discovery Progress: visual progress bar across dimensions. | NOT a plugin. Core UI component that reads customer.profile, customer.products, enrichment.*, discovery.* directly from state. Always present, always current. |
| Accepted Products Tray | Bottom of left panel or floating tray | Running list of products the rep has accepted from AI recommendations during this conversation. Shows product name, price, quantity. Products can be removed. This is the ‘shopping cart’ for the conversation. | NOT a plugin. Core UI component. Generate Quote plugin reads from agent_state.*.accepted_recommendations to build quotes. The tray is the product selection mechanism. |
| Chat Panel | Center panel (primary workspace) | The conversation: rep input, AI coaching output. Inline recommendation cards from core agents (Cross-Sell, Upsell, Full Solution). Discovery questions. Agent phase transitions. | Plugins do NOT render here (except when a plugin is referenced inline by the core agent). This panel is owned by the core agent conversation flow. |
| Side Panel | Right panel (collapsible) | Plugin output cards, tables, forms. Multiple outputs stack as tabs or accordion sections. Rep can pin, dismiss, expand, or collapse each output. Ordered by surfacePriority. | PRIMARY plugin rendering zone. All Card, Table, Form, File, and Custom Component outputs render here. Auto-triggered plugins appear here; manually triggered plugins also open here. |
| Quick Action Bar | Bottom bar or toolbar | Row of plugin buttons with icons and labels. Some always visible (KB Search, Account Overview). Others appear contextually (Competitor Card appears when competitor mentioned). Active plugin button is highlighted. | Plugin trigger buttons. Clicking a button manually executes the plugin. Contextual appearance controlled by triggerConditions + manualTrigger in manifest. |

| Product Catalog Browser (Full Product) In MVP, reps find products via KB Search plugin or through core agent recommendations. In full product, a Product Catalog Browser is a dedicated panel (or expandable section within the Side Panel) where reps can search, filter by category, and manually add products to the Accepted Products Tray — independent of AI recommendations. This is important for experienced reps who already know what to sell and want to use the platform for quoting and email generation rather than AI discovery. |
| --- |

## 12.8 Built-In Quick Actions — All 8 Plugins

All 8 built-in plugins are built on the same SDK described above. They are NOT special — they use the same manifest, execute function, Plugin Gateway, and rendering templates as any custom plugin. This section specifies each plugin’s trigger, data diet, output, LLM usage, and evolution during a conversation.

### 12.8.1 Availability and Trigger Matrix

| Plugin | Button Visible | Auto-Triggers When | Surfaces In Phase | LLM? | Priority |
| --- | --- | --- | --- | --- | --- |
| KB Search | Always | Never (manual only) | All phases | No | N/A (manual) |
| Account Overview | Always | Session start when customer is known | All phases | No | 2 |
| Meeting Prep Brief | Session start only | Session start IF customer known AND rep hasn’t seen this customer in 7+ days | Discovery (early) | Yes | 1 |
| Competitor Comparison | After competitor mentioned | intent_signals.competitor_mentioned == true | Any phase | Yes | 3 |
| Find Case Study | After industry/use case detected | intent_signals.use_case_detected == true OR discovery.dimensions.industry_fit has signal | Discovery, Recommendation | No | 5 |
| Generate Email | After recommendations exist | agent_state.*.recommendations.length >= 1 | Recommendation, Action | Yes | 4 |
| Generate Quote | After products accepted | agent_state.*.accepted_recommendations.length >= 1 | Action | No | 6 |
| Objection Handler | After objection detected | intent_signals.objection_detected == true | Any phase (most common in Recommendation) | Yes | 2 |

| Simultaneous Plugin Triggering and Priority When multiple plugins trigger simultaneously, they all surface in the Side Panel ordered by surfacePriority (lower = higher). The Orchestrator does NOT choose one — it shows all triggered plugins. The rep can engage with whichever is most relevant. This avoids the platform making a judgment call about which plugin the rep needs most. |
| --- |

### 12.8.2 Per-Plugin Specification

KB Search: The simplest plugin. Rep types a search query, plugin routes it to Foundry IQ, returns top results as a Card with clickable result entries. No LLM — pure retrieval. Reads customer.profile for context-aware search (industry, owned products improve relevance). Reads last 5 conversation messages to infer search context. Output: Card with list-style sections, one per result (title, snippet, relevance score). Always available, never auto-triggers.

Account Overview: Pure data assembly. Reads customer.profile, customer.products, all enrichment fields (whitespace_pct, health_score, yoy_growth, ltv, purchase_frequency, months_since_last_purchase, segment_benchmark). No LLM, no KB query. Computes nothing — reads pre-computed enrichment data and formats it into a Card with key_value sections: Account Health (color-coded score), Revenue Trend (YoY growth with arrow icon), Whitespace (X% of categories uncovered), Lifetime Value (total spend), Segment Benchmark (‘similar companies own Y products, this customer owns Z’). Auto-surfaces at session start for known customers.

Meeting Prep Brief: Generates a narrative pre-call briefing. Reads customer.profile, enrichment.*, customer.products, agent_state.*.last_session_summary (if previous sessions exist). Queries KB for case studies matching the customer’s industry. Uses LLM to synthesize a 3–5 paragraph brief: who the customer is, what they own, where the opportunities are, what happened last time (if applicable), and what to ask in this call. Output: Card with text sections + a highlight section for ‘Top 3 Questions to Ask.’ Auto-surfaces at session start if the customer hasn’t been contacted in 7+ days (configurable per tenant).

Competitor Comparison: Triggered when NLU detects a competitor mention in the conversation. Reads intent_signals.competitor_name to identify which competitor. Queries KB for competitor intel documents (battle cards, win/loss data). Reads customer.products to understand the customer’s current stack. Uses LLM to generate: our product vs. competitor product comparison, key differentiators, pricing positioning, recommended talk tracks. Output: Table template (feature-by-feature comparison) + Card with highlight section for positioning advice. Reads last 10 messages for context about what specifically the customer said about the competitor.

Find Case Study: Retrieval-only plugin. Reads customer.profile.industry and discovery dimension signals to construct a KB query for matching case studies. No LLM — uses Foundry IQ semantic search. Returns top 3 matching case studies as a Card with list sections: case study title, customer industry, challenge summary, results achieved, relevance score. Rep can click to expand or copy case study details into the conversation. Auto-surfaces when a specific use case or industry need is detected during discovery.

Generate Email: The most LLM-intensive plugin. Reads customer.profile, customer.contacts (for personalization), agent_state.*.recommendations (the products to reference), agent_state.*.talking_points (pre-computed by core agent), and last 15 conversation messages (for conversational context). Reads Plugin Output Bus for competitor comparison data (if available) and case study data (if available) to enrich the email. Uses LLM to generate a personalized email draft with subject line and body. Output: Form template with editable fields (To, CC, Subject, Tone dropdown, Body textarea). Auto-surfaces after recommendations are made. The most frequently edited plugin output — edit metrics are critical for tuning.

Generate Quote: Template-based, no LLM. Reads agent_state.*.accepted_recommendations (products the rep accepted), customer.profile (company name, contact for header), and product catalog pricing from KB query. Assembles a structured quote with: header (company name, date, quote number), line items (product name, description, unit price, quantity, line total), subtotal, optional discount line, total. Output: File (PDF) with a Card preview showing summary (‘3 line items, total $24,500’). Form template for adjustments: quantity inputs, discount percentage field, notes textarea. In MVP, produces a structured Card + File rather than a formatted PDF — PDF generation is full-product polish.

Objection Handler: Triggered by intent_signals.objection_detected. Reads intent_signals.objection_text to understand the specific objection. Reads agent_state.*.recommendations for context on what was recommended. Queries KB for competitor intel and product specs relevant to the objection. Uses LLM to generate 2–3 counter-arguments with different approaches: fact-based rebuttal, reframing the value proposition, and social proof (referencing similar customers). Output: Card with highlight sections for each counter-argument, labeled by approach. Actions: ‘Use this response’ (copies to chat input), ‘Save for later.’

### 12.8.3 Data Diet Summary

| Plugin | State Fields | KB Sources | History Depth | LLM Tokens (typical) |
| --- | --- | --- | --- | --- |
| KB Search | customer.profile | All sources (query-driven) | 5 messages | 0 |
| Account Overview | customer.profile, customer.products, enrichment.* | None | 0 | 0 |
| Meeting Prep Brief | customer.profile, customer.products, enrichment.*, agent_state.*.last_session_summary | Case studies | 0 | ~800 |
| Competitor Comparison | customer.profile, customer.products, intent_signals.competitor_name | Competitor intel, product catalog | 10 messages | ~1,000 |
| Find Case Study | customer.profile.industry, discovery.dimensions.* | Case studies | 5 messages | 0 |
| Generate Email | customer.profile, customer.contacts, agent_state.*.recommendations, agent_state.*.talking_points, enrichment.* | Case studies | 15 messages | ~1,500 |
| Generate Quote | agent_state.*.accepted_recommendations, customer.profile | Product catalog (pricing) | 0 | 0 |
| Objection Handler | intent_signals.objection_text, agent_state.*.recommendations, customer.profile | Competitor intel, product specs | 10 messages | ~800 |

### 12.8.4 Deployment Strategy

| DECISION: All Built-In Plugins Bundled in Orchestrator Service Decided: All 8 built-in plugins run within the Orchestrator service (Module 2), executed by the Plugin Gateway. They are NOT separate microservices. Each plugin is a function invocation, not a persistent process. Rationale: Plugins are short-lived (< 5 seconds), stateless functions. Deploying 8 separate services for 8 small functions is massive over-engineering — 8 Kubernetes pods, 8 pipelines, 8 health checks for functions that run 2–3 seconds each. The Plugin Gateway handles isolation via sandboxed execution contexts. Built-in code is trusted. Custom plugins in full product MAY run in isolated containers (Lambda/Azure Functions) because we don’t trust external code, but built-in plugins always execute in-process. |
| --- |

## 12.9 Agent Manifest Control — Versioning, Customization, and Deployment

### 12.9.1 Where Manifests Live

Built-in plugins: Manifests are checked into the codebase at /plugins/{plugin_id}/manifest.json alongside the plugin code. They are registered in the plugin_registry database table during platform deployment via the CI/CD pipeline. The pipeline reads all manifest files and upserts them into the registry.

Custom plugins (full product): Manifests are submitted via the Admin Portal and stored in the plugin_registry table with the tenant’s tenant_id. The code bundle is stored in the tenant’s Blob Storage container.

| CREATE TABLE plugin_registry ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), plugin_id VARCHAR(100) NOT NULL, tenant_id UUID,                   -- NULL for built-in (all tenants) version VARCHAR(20) NOT NULL, manifest JSONB NOT NULL, status VARCHAR(20) DEFAULT 'pending_review', -- Status flow: pending_review -> approved -> active -> disabled -> deprecated code_bundle_url TEXT,             -- Blob Storage URL for custom plugins created_at TIMESTAMPTZ DEFAULT NOW(), approved_by VARCHAR(255), approved_at TIMESTAMPTZ, UNIQUE(plugin_id, tenant_id, version) ); |
| --- |

### 12.9.2 Are Built-In Plugins Domain-Specific?

No. Built-in plugins are domain-AGNOSTIC by design. They work identically for IT resellers, insurance brokerages, food distributors, and any other industry. What changes per domain is the DATA they operate on, not the plugin code. Email Gen generates emails for any product in any industry because it reads customer.profile and recommendations which are already tenant-specific. Competitor Card pulls from competitor intel in the KB, which is different per tenant. The plugin logic is universal; the data is tenant-specific.

Domain-specific behavior comes from three sources external to the plugin: (1) Tenant prompt modifiers from Module 5 are injected into the LLM prompt when context.llm.call() is invoked. The modifier says ‘This tenant is an insurance brokerage. Use professional tone. Reference policy types, not products.’ Same Email Gen code, different prompt context. (2) Tenant business rules from Module 5 are enforced by the Orchestrator BEFORE and AFTER plugin execution, not by the plugin itself. (3) Tenant KB content is different — the same Foundry IQ search returns different competitor intel, case studies, and product data per tenant.

### 12.9.3 Tenant Customization — Three Levels

| Level | What Changes | How It’s Configured | Where It’s Stored | Code Deployment? | Branch Risk |
| --- | --- | --- | --- | --- | --- |
| Level 1: Prompt Modifiers | LLM behavior within the plugin. Tone, terminology, emphasis, domain-specific instructions. | Engineering team writes during onboarding (MVP). Tenant admin edits in Admin Portal (full product). | tenant_config.agent_config.{plugin_id}.prompt_modifiers[] | NO. Configuration change only. No code touches. | NONE. One codebase. Config is per-tenant data, not a branch. |
| Level 2: Template Overrides | Plugin output structure. Add/remove form fields, change card sections, alter table columns. | Tenant admin configures in Admin Portal (full product only). Not available in MVP. | tenant_config.plugin_overrides.{plugin_id}.outputSchema | NO. The plugin reads its output schema from config at runtime. Schema is data, not code. | NONE. One codebase. Template schemas are per-tenant config. |
| Level 3: Custom Plugin Fork | Fundamentally different logic. The tenant builds their own plugin that replaces the built-in. | Tenant developer builds using SDK. Submits via Admin Portal. Goes through review. | plugin_registry with tenant_id. Code in tenant Blob Storage. | YES — but it’s THEIR code, not a branch of ours. They own it. We maintain built-in separately. | NONE for us. The tenant’s custom plugin is independent. We update built-in freely. Their custom plugin is their responsibility. |

The key insight: there are no branches. Our codebase has one version of each built-in plugin. Tenant-specific behavior comes from configuration (Levels 1–2) or from entirely separate custom plugins (Level 3). When we update Email Gen from v1.2 to v1.3, every tenant gets v1.3, and their prompt modifiers are automatically applied to the new version. No merge conflicts, no per-tenant branches, no version matrix.

| What About Enable/Disable Per Tenant? Not all tenants want all 8 plugins. An insurance brokerage might disable Generate Quote (they use their carrier’s quoting system). This is controlled by tenant_config.agent_config.quick_action_ids — the list of enabled plugin IDs, configured during onboarding. Additionally, individual reps can hide plugins they personally don’t use via user_settings.hidden_plugins — a user-level preference separate from tenant-level enablement. |
| --- |

## 12.10 Feedback and Analytics — What We Collect and How We Use It

| Connection to Module 16 Plugin analytics feed into Module 16 (Feedback Engine), which is the platform’s unified feedback processing system. This section defines WHAT signals plugins generate. Module 16 defines HOW those signals are aggregated, analyzed, and used for system-wide improvement. The signals defined here are the plugin-specific subset of Module 16’s full signal taxonomy. |
| --- |

### 12.10.1 Signal Taxonomy

| Signal Category | Specific Signals | Collected By | Purpose |
| --- | --- | --- | --- |
| Execution Metrics | execution_count, execution_time_ms, llm_tokens_used, error_count, error_types, trigger_type (auto/manual) | Plugin Gateway (automatic on every execution) | System health monitoring. Cost tracking. Performance optimization. Error rate alerting. |
| Engagement Signals | used (interacted with output), dismissed (closed without interaction), time_to_first_interaction_ms, dwell_time_ms (how long output was visible) | UI event tracking (automatic) | Plugin usefulness assessment. High dismissal rate = plugin needs tuning or different trigger conditions. Low dwell time on auto-triggered plugins = rep doesn’t find them valuable. |
| Edit Signals (Form outputs) | fields_edited[] (which fields), edit_distance (0–1 normalized change), characters_added, characters_removed, regeneration_count, final_action (copied/sent/abandoned) | UI event tracking (on form submit or dismiss) | Output quality assessment. High edit rate on specific fields = generation needs improvement. High regeneration count = prompt needs tuning. Edit distance per field identifies which parts of the output need work. |
| Dismiss Reasons (optional) | dismiss_reason (free text or picklist: not relevant, already knew this, wrong timing, wrong competitor) | UI micro-survey (optional, shown on dismiss for high-value plugins) | Qualitative feedback for trigger condition tuning. ‘Wrong timing’ = adjust trigger. ‘Not relevant’ = improve context matching. ‘Already knew this’ = suppress for experienced reps. |
| Outcome Correlation (full product) | recommendation_accepted_after_plugin, session_duration_with_vs_without, deal_outcome (won/lost from CRM write-back) | Module 16 cross-referencing plugin logs with recommendation and deal data | Plugin ROI measurement. Do deals where Competitor Card was used have higher win rates? Does Email Gen correlate with faster deal progression? |

| LLM Cost Tracking and Token Budgets Each LLM-using plugin consumes tokens that cost money. The platform tracks token usage per plugin per tenant per month. In full product, tenant admins can see cost-per-plugin in their dashboard and set monthly token budgets per plugin. If a plugin exceeds its budget, it degrades gracefully: switches to template-based output instead of LLM generation, with a notice to the rep. |
| --- |

### 12.10.2 How Feedback Improves Plugins

| Timeframe | Mechanism | Example | Who Acts |
| --- | --- | --- | --- |
| Real-time (same session) | onFeedback handler adjusts next execution | Rep clicks Regenerate on Email Gen. Next generation tries different tone based on sessionMemory. | Plugin’s own onFeedback function (automatic). |
| Weekly (MVP) | Engineer reviews aggregated metrics dashboard | Email Gen subject line edit rate is 55% for insurance tenants. Engineer adds prompt modifier: ‘For insurance, lead subject with coverage type, not product name.’ | Engineering team manually adjusts prompt modifiers in tenant_config. |
| Automated proposal (full product) | Module 16 identifies pattern and proposes change | Competitor Card dismissed 72% for Tenant X. Module 16 proposes: ‘Disable Competitor Card for this tenant OR review KB competitor intel quality.’ Proposal goes to tenant admin for approval. | Module 16 proposes. Tenant admin or engineering team approves. |
| A/B testing (full product) | Two prompt versions run in parallel for 2 weeks | Email Gen v1.2 prompt vs. v1.3 prompt. Measure edit rate, regeneration count, copy rate. Winner becomes default. | Engineering team sets up test. Module 16 tracks metrics. Engineer applies winner. |

## 12.11 Plugin Lifecycle — Complete Deep Dive

### 12.11.1 Step 1: Develop

The developer (our team for built-in, tenant developer for custom) implements three files:

manifest.json: Declares everything about the plugin (triggers, permissions, resources, output schema, feedback signals). This is the contract. The Plugin Gateway uses the manifest to provision the execution context, enforce permissions, and validate output.

execute.ts: The main function. Receives PluginContext, returns structured output matching outputSchema. All business logic lives here: data assembly, LLM calls, formatting, conditional logic.

onFeedback.ts (optional): Real-time feedback handler for session-scoped adaptation. Most plugins don’t need this. Email Gen and Objection Handler benefit from it (regeneration with adjustments).

The SDK provides TypeScript types for PluginContext, all output schemas (CardOutput, TableOutput, FormOutput, FileOutput), FeedbackSignal, and API clients (state, kb, llm, pluginOutputs). The developer installs @sip/plugin-sdk as a dev dependency for type checking but the types are compile-time only — the runtime SDK is injected by the Plugin Gateway.

### 12.11.2 Step 2: Test

The Plugin Test Harness is a local testing tool that simulates the Plugin Gateway. It is included in @sip/plugin-sdk as a CLI tool.

Load mock context: Developer creates a JSON file with fake customer data, fake recommendations, fake KB results. The harness loads this as the PluginContext.

Execute against mock: Harness calls execute(mockContext) and captures the output.

Schema validation: Harness validates the output against the declared outputSchema. Reports: missing required fields, wrong types, undeclared section styles, over-size content.

Permission compliance: Harness wraps the mock context in a permission proxy. If the plugin tries to read a field NOT declared in stateRequirements, the proxy throws a PermissionError. This catches permission overreach during development, not in production.

Performance benchmark: Harness measures execution time and reports whether it exceeds maxExecutionMs. For LLM-using plugins, the harness can use a mock LLM (returns canned responses) for fast testing or a real LLM call for quality testing.

Edge case testing: Developer creates mock contexts representing edge cases: customer with no products, empty KB, LLM timeout, no contacts available, no recommendations yet. Verifies the plugin handles gracefully (returns a sensible fallback card, not an error).

| # Plugin test harness CLI $ sip-plugin test ./manifest.json --mock ./test/mock-context.json Running plugin: builtin-email-gen v1.2.0 Schema validation: PASS (form output, 4 fields) Permission compliance: PASS (4 state reads, all declared) Execution time: 2,340ms (limit: 5,000ms) PASS LLM calls: 1 (limit: 1) PASS KB queries: 1 (limit: 3) PASS Running edge case: no-contacts Schema validation: PASS (to field empty but not errored) Execution time: 1,890ms PASS Running edge case: llm-timeout Fallback handling: PASS (returned error card) All tests passed. Ready for registration. |
| --- |

### 12.11.3 Step 3: Register

Built-in plugins: Registration is automatic. The CI/CD pipeline reads /plugins/*/manifest.json and upserts into plugin_registry. Status is set to ‘active’ immediately (no review gate for built-in).

Custom plugins (full product): Tenant admin uploads the plugin bundle (manifest + code) via Admin Portal. The bundle is validated (schema compliance, size limits, required fields). The manifest is parsed and inserted into plugin_registry with status: ‘pending_review.’

### 12.11.4 Step 4: Review (Full Product Custom Plugins Only)

Built-in plugins skip this step entirely. Custom plugins undergo review before activation:

Manifest audit: Does it declare only the permissions it actually needs? Is externalNetwork: true justified? Are resource limits reasonable?

Code review: Does the code do what the manifest says? Any hidden API calls? Any data exfiltration attempts? Any attempt to access DOM or browser APIs?

Security scan: Automated static analysis for common vulnerabilities: injection patterns, eval() usage, external URL construction, obfuscated code.

Test results: Did it pass the test harness? All edge cases handled? Performance within limits?

Review result: approved (status changes to ‘approved’, can be activated by tenant admin) or rejected with specific feedback for the developer to address.

### 12.11.5 Step 5: Deploy / Activate

Built-in plugins: Deployed with every platform release. Available to all tenants. Whether a specific plugin is ENABLED for a tenant depends on tenant_config.agent_config.quick_action_ids.

Custom plugins: After approval, tenant admin activates the plugin in Admin Portal. Status changes from ‘approved’ to ‘active.’ The Orchestrator’s plugin trigger evaluation now includes this plugin for this tenant’s sessions.

Rollback: If a plugin update introduces a regression, the previous version can be re-activated. plugin_registry stores version history. Rollback = deactivate current version, activate previous version. One database update, immediate effect for new sessions.

### 12.11.6 Step 6: Monitor

Every plugin execution is logged with the metrics defined in Section 12.10. The monitoring dashboard (Admin Portal, full product) shows per-plugin health:

Usage frequency: How often each plugin is triggered (auto + manual) and used (interacted with vs. dismissed).

Dismissal rate: Percentage of auto-triggered appearances that the rep dismisses without interaction. Above 60% = flag for review.

Edit rate (form plugins): Percentage of form outputs where the rep edits content before using it. High edit rate on specific fields = generation quality issue.

Error rate: Percentage of executions that fail (timeout, LLM error, schema validation error). Above 5% = engineering alert.

LLM cost: Token consumption per plugin per tenant. Monthly trend. Enables cost-per-plugin and cost-per-tenant reporting.

Circuit breaker: If a plugin fails 3 consecutive times within a session, the Plugin Gateway disables it for the remainder of that session and logs an alert. The rep sees remaining plugins working normally. The plugin is re-enabled for the next session.

## 12.12 Alternative Architecture: Open Plugin Framework

| Why This Section Exists The Controlled Framework (Sections 12.1–12.11) prioritizes safety, consistency, and predictability. It is the recommended approach for MVP and early full product. However, as the platform matures and the developer ecosystem grows, a more open framework may be warranted. This section presents an alternative architecture that trades some control for developer freedom. The constraint is: security isolation and analytics collection remain mandatory. Everything else is negotiable. |
| --- |

### 12.12.1 Philosophy Shift

| Dimension | Controlled Framework (Sections 12.1–12.11) | Open Framework (This Section) |
| --- | --- | --- |
| UI Output | Card, Table, Form, File templates only. Custom Component as escape hatch (iframe-sandboxed, full product). | Plugins output React components directly. Platform provides a component library but plugins can create custom UI. Sandboxed via iframe with postMessage API for security. |
| LLM Access | Built-in connector only (MVP). Declared external connector (full product). Platform mediates all LLM calls. | Plugin chooses its own LLM. Platform provides a built-in connector as a convenience. Plugins can import OpenAI, Anthropic, or any LLM SDK directly. Platform tracks token usage via a lightweight instrumentation wrapper. |
| State Access | Explicit field-level permissions declared in manifest. Gateway rejects undeclared reads. Write sandboxed to own namespace only. | Broader read access: plugins can read any non-sensitive state field without declaring it. Write access to a shared plugin namespace (plugin_shared.*) in addition to own namespace. Core state (agent_state, discovery) remains protected. |
| Plugin-to-Plugin | Read-only access to other plugins' rendered output via Plugin Output Bus. No direct invocation. | Direct invocation allowed via plugin message bus. Plugin A can call Plugin B's execute() with custom parameters. Orchestrated workflows possible. Circular dependency detection enforced. |
| Review Process | Manual code review for all custom plugins before activation. | Automated security scan + manifest audit. Manual review only for plugins that: declare externalNetwork, request shared state write, or include Custom Components. Low-risk plugins auto-approved. |
| Resource Limits | Hard limits enforced by Gateway: 5s timeout, 10 reads, 3 KB queries, 1 LLM call. | Soft limits with burst allowance. Default 5s timeout with ability to request up to 30s for complex plugins (with justification). Higher read/query limits. Multiple LLM calls allowed. |

### 12.12.2 Open Framework Security Model

Even in the open framework, three constraints are ABSOLUTE and cannot be relaxed:

Tenant Data Isolation: A plugin can NEVER access another tenant’s data. This is enforced at the database level (PostgreSQL RLS), the API level (tenant_id scoping on every request), and the execution level (sandboxed context includes only one tenant’s data). This constraint is non-negotiable regardless of framework openness.

Analytics Collection: Every plugin execution MUST generate analytics signals. The platform’s instrumentation wrapper is injected into every execution context. Plugins cannot disable, bypass, or tamper with analytics collection. This ensures Module 16 has complete data for system-wide optimization. A plugin that attempts to suppress analytics is automatically flagged and disabled.

Output Sanitization: Even with custom React components, the output renders in an iframe sandbox. No direct DOM access to the parent application. No cookie/localStorage access. CSP blocks external resource loading. The plugin cannot read or modify anything outside its iframe boundary.

### 12.12.3 Open Framework LLM Access

In the open framework, plugins can bring their own LLM. The platform still provides a built-in connector (context.llm.call()) as the easiest option, but plugins can also:

Import any LLM SDK: Plugin code can import OpenAI, Anthropic, Cohere, or any other LLM SDK. The plugin manages its own API keys (stored encrypted in plugin config, not hardcoded).

Use fine-tuned models: A medical device tenant’s custom plugin can use a healthcare-specific fine-tuned model. An insurance tenant can use a compliance-aware model. The platform doesn’t dictate the model.

Instrumentation wrapper: All LLM calls (built-in or external) are wrapped by the platform’s instrumentation layer, which logs: model used, tokens consumed, latency, cost estimate. This is transparent to the plugin and cannot be bypassed. The wrapper is injected by the Plugin Gateway at execution time.

| Security Note on External LLM External LLM calls mean plugin data leaves the platform. The manifest must declare externalLlm endpoints. The Plugin Gateway whitelists these endpoints. All outbound requests are logged. Tenant admins see which plugins make external calls in their analytics dashboard. Tenants with strict data residency requirements can disable external LLM access entirely via tenant_config. |
| --- |

### 12.12.4 Open Framework State Access

The open framework relaxes read permissions and introduces a shared plugin namespace:

Broader reads: Plugins can read any state field in the customer.*, enrichment.*, and intent_signals.* namespaces without explicitly declaring each field in stateRequirements. The manifest declares namespace-level access (stateRead: ["customer.*"]) rather than field-level. The Plugin Gateway still blocks access to sensitive fields (auth tokens, tenant_config internals, system prompts).

Shared plugin state: In addition to plugin_state.{own_id}.*, plugins can write to plugin_shared.* — a namespace visible to all plugins. Use case: a product selection plugin writes the selected products to plugin_shared.selected_products, and both Email Gen and Quote Gen read from it. Write conflicts are resolved by last-write-wins with timestamps.

Core state remains protected: Even in the open framework, agent_state.*, discovery.*, active_workflow, and recommendations.* are NEVER writable by plugins. These are core platform state that only the Orchestrator and core agents can modify.

### 12.12.5 Migration Path: Controlled to Open

The platform does NOT start with the open framework. The migration is phased:

Phase 1 (MVP): Controlled Framework. All 8 built-in plugins. Card/Table/Form/File templates. Built-in LLM only. Read-only state. No custom plugins. This phase proves the SDK, builds confidence, and establishes baselines.

Phase 2 (Full Product v1): Controlled Framework + custom plugins. Tenant developers can build plugins using the same controlled SDK. Manual review. State Write API (sandboxed). Custom Components (iframe). This phase opens the platform to tenant development while maintaining guard rails.

Phase 3 (Full Product v2): Open Framework. Broader state access. External LLM connectors. Plugin message bus for direct invocation. Automated security scanning. Shared plugin namespace. This phase is triggered when we have 10+ custom plugins in production and understand the real-world usage patterns.

| DECISION: Starting Framework Decided: MVP and early full product use the Controlled Framework. The Open Framework is a Phase 3 evolution, gated on: 10+ custom plugins in production, zero security incidents with the controlled model, and tenant developer feedback requesting more flexibility. Rationale: The controlled framework is safer, simpler to build, and sufficient for MVP. Opening up prematurely creates security surface area without proven demand. The architecture is designed so that the transition from controlled to open is an expansion of permissions, not a rewrite — the Plugin Gateway, manifest system, and analytics collection work identically in both frameworks. |
| --- |

## 12.13 Key Technical Decisions Summary

| DECISION: SDK Is an Interface Contract, Not a Standalone Library Decided: The Plugin SDK is a set of TypeScript types and a test harness. Plugins are functions executed by the Plugin Gateway within the Orchestrator. They are not independent services. Rationale: This keeps deployment simple (no new services per plugin), ensures consistent execution environment, and makes permission enforcement reliable. The Gateway controls everything the plugin can access. |
| --- |

| DECISION: All Built-In Plugins Use the Same SDK as Custom Plugins Decided: Built-in plugins (Email Gen, Competitor Card, etc.) are built on the identical SDK interface as any future custom plugin. No special APIs, no backdoors, no privileged access. Rationale: Dogfooding proves the SDK is robust enough for real use cases. If our team needs a capability that the SDK doesn’t provide, we add it to the SDK (available to everyone) rather than building a backdoor (available only to us). This builds trust with tenant developers. |
| --- |

| DECISION: Plugins Are Domain-Agnostic, Data Is Domain-Specific Decided: One version of each built-in plugin for all industries. Domain behavior comes from tenant configuration (prompt modifiers, KB content, business rules) injected at runtime. Rationale: Eliminates branching, simplifies maintenance, and ensures every tenant benefits from plugin improvements immediately. The alternative — domain-specific plugin versions — would create a combinatorial explosion of plugin x industry variants. |
| --- |

| DECISION: Template-Based UI in MVP, Custom Components in Full Product Decided: Card, Table, Form, File templates for MVP. Custom React components (iframe-sandboxed) for full product. Rationale: Templates cover 95% of use cases for our 8 built-in plugins. They ensure consistent UX, prevent injection attacks, and simplify plugin development. Custom Components are an escape hatch for the 5% of use cases that need rich interactivity, available when the security infrastructure (iframe sandbox, CSP, review process) is mature. |
| --- |

| DECISION: Feedback Flows to Module 16, Not Separate Engines Per Plugin Decided: All plugin feedback signals flow to the unified Feedback Engine (Module 16). No per-plugin feedback processing systems. Rationale: Cross-component pattern detection requires unified data. If reps edit Email Gen subjects, the root cause might be in the core agent’s recommendation framing, not the email template. A unified engine detects these cross-component correlations. Separate engines per plugin would miss them. |
| --- |

| DECISION: Controlled Framework First, Open Framework Later Decided: MVP and early full product use the controlled SDK. Open framework is Phase 3, gated on proven demand and zero security incidents. Rationale: Starting open is tempting but risky: every additional permission is a security surface. Starting controlled and expanding is safer than starting open and trying to restrict. The architecture supports both — the transition is additive (more permissions), not a rewrite. |
| --- |
