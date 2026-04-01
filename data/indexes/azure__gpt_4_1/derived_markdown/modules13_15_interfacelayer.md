Sales Intelligence Platform

Technical Deep-Dive: Modules 13–15

Interface Layer — Conversation UI, CRM Widget, Admin Portal

Layout Architecture, WebSocket Protocol, State-Driven Rendering, CRM Embedding, Auto-Context Detection, Write-Back, Admin Portal, KB Management, Business Rules & Configuration

March 2026  |  Internal — Engineering Team

| Document Scope This document covers the complete Interface Layer of the Sales Intelligence Platform: Module 13 Conversation UI (six-component layout architecture, WebSocket real-time protocol with full message type specifications, state-driven rendering model, UX principles mapped to implementation, session lifecycle), Module 14 CRM Embedded Widget (Salesforce Lightning and HubSpot sidebar integration, auto-context detection mechanisms, compact widget components, CRM write-back operations with permission model, feature parity matrix vs full UI), Module 15 Admin Portal (KB management with upload pipeline and visual relationship editor, integration setup wizard, visual business rule builder with test/preview, agent configuration, user management with RBAC, discovery framework customization, usage analytics), shared backend services (Platform API endpoint catalog, WebSocket service scaling, Auth service flows per module), cross-module interaction map showing every M1–M20 backend dependency, and key technical decisions. |
| --- |

# Table of Contents

# Module 13: Conversation UI

Module 13 is the primary interface that sales reps interact with during every customer engagement. It is a real-time, state-driven application that reflects the current session’s context, active agent, discovery progress, and recommendations. Every visual element on screen is a function of the session state held in Module 1 (State Engine) and pushed to the UI via WebSocket.

## 13.1 Layout Architecture

The Conversation UI is organized into six distinct components, each occupying a defined region of the screen. The layout is not a simple chat window — it is a multi-panel workspace designed for guided selling. The rep sees everything they need without switching tabs or opening separate tools.

| Component | Position | Purpose | Primary Data Source |
| --- | --- | --- | --- |
| Chat Panel | Center (dominant) | Conversation thread between rep and AI assistant | M1 State Engine (conversation history), M2 Orchestrator (active agent responses) |
| Customer Context Panel | Left sidebar | Account info, current products, key metrics, health score | M4 Data Integration (CRM/ERP data), M1 State Engine (session context) |
| Discovery Progress | Top bar or right panel | Visual completion indicator across multiple discovery dimensions | M1 State Engine (discovery_progress layer), M5 Multi-Tenancy (dimension config) |
| Recommendations Panel | Right panel or inline | Product/solution cards with reasoning and evidence | M2 Orchestrator (agent outputs), M3 KB Engine (product data), M16 Feedback Engine (confidence scores) |
| Quick Actions Bar | Bottom or floating bar | Dynamic buttons that appear/disappear based on conversation state | M12 Plugin SDK (triggered plugins), M1 State Engine (trigger conditions) |
| Agent Indicator | Top bar badge | Shows which workflow agent is active, suggests switches | M2 Orchestrator (active agent), M1 State Engine (agent transitions) |

### 13.1.1 Chat Panel — The Conversation Thread

The Chat Panel is the central element. It renders the ongoing dialogue between the rep and the AI assistant. Messages flow in chronological order with clear visual distinction between rep messages (right-aligned, brand color) and AI responses (left-aligned, neutral background). The AI responses are not free-form text — they are structured outputs from whichever agent is currently active in Module 2.

Each AI message can contain embedded elements: inline product mentions that link to the Recommendations Panel, discovery questions that update the Discovery Progress when answered, and action suggestions that map to Quick Action buttons. The Chat Panel subscribes to the conversation_history layer of the session state in M1. When the State Engine broadcasts a state_changed event with new conversation entries, the Chat Panel appends them in real time.

| Message Rendering Pipeline Rep types message → POST /api/sessions/{id}/messages → M1 appends to conversation_history → M2 Orchestrator routes to active agent → Agent generates structured response → M1 appends agent response to conversation_history → WebSocket pushes state_changed event → Chat Panel renders new message. The entire round-trip targets sub-2-second latency. For longer agent processing (e.g., KB-heavy Full Solution agent), the Chat Panel shows a typing indicator streamed via WebSocket. |
| --- |

### 13.1.2 Customer Context Panel

The left sidebar displays everything the platform knows about the customer before and during the conversation. This panel is populated at session start when M1 assembles the initial context by calling M4 (Data Integration) for CRM/ERP data. It is not editable by the rep during the session — it is a read-only reference surface.

| Section | Data Points | Source Module | Update Frequency |
| --- | --- | --- | --- |
| Account Header | Company name, industry, segment, account owner | M4 (CRM sync) | Session start |
| Current Products | Active subscriptions, contract dates, usage levels | M4 (ERP sync) | Session start |
| Key Metrics | ARR, growth rate, NPS score, support ticket volume | M4 (CRM + ERP) | Session start |
| Health Score | Composite score from usage, support, payment signals | M4 + M16 (computed) | Real-time (if CRM signals update mid-session) |
| Recent Activity | Last 5 touchpoints: calls, emails, meetings, support cases | M4 (CRM activity log) | Session start |
| Relationship Map | Key contacts, roles, decision-making authority | M4 (CRM contacts) | Session start |

| Why Read-Only? The Customer Context Panel intentionally does not allow the rep to edit CRM data from within the Conversation UI. Allowing inline edits would create a sync conflict problem: the rep edits a field here, another rep edits it in the CRM, and the platform has to resolve the conflict. Instead, CRM write-back happens exclusively through Module 14 (CRM Widget) where the context is clear and the CRM is the system of record. The Conversation UI is for selling, not data entry. |
| --- |

### 13.1.3 Discovery Progress

The Discovery Progress component visualizes how much the rep has uncovered about the customer’s needs across multiple dimensions. In the full product, discovery dimensions are fully customizable per tenant via Module 15 (Admin Portal), which writes to M5 (Multi-Tenancy) tenant configuration. In the MVP, a default set of dimensions is used.

Each dimension is a named category (e.g., “Business Challenges,” “Current Infrastructure,” “Decision Timeline,” “Budget Authority”) with a progress percentage from 0–100%. Progress updates happen automatically: when the rep’s conversation reveals information that maps to a dimension, the active agent in M2 calls M1’s updateState() to increment the relevant dimension’s progress. The UI receives this via WebSocket and animates the progress bar.

| Aspect | MVP | Full Product |
| --- | --- | --- |
| Dimensions | Fixed set of 5 standard dimensions | Tenant-configurable via M15 Admin Portal (stored in M5) |
| Weights | Equal weighting across all dimensions | Custom weights per dimension (some matter more than others) |
| Progress Calculation | Binary: question answered = progress increment | Graduated: depth of answer quality affects increment amount |
| Visual Format | Simple progress bars with percentages | Multi-dimensional radar/spider chart with color coding |
| Completion Threshold | 80% across all dimensions = “discovery complete” | Configurable per tenant, per agent workflow |

### 13.1.4 Recommendations Panel

The Recommendations Panel is where the platform’s intelligence becomes visible. When an agent in M2 determines that a product or solution should be recommended, it emits a structured recommendation object. This object flows through M1 (which stores it in the recommendations layer of session state) and arrives at the UI via WebSocket.

Each recommendation card displays: the product or solution name, a confidence score (how strongly the platform believes this is a good fit), the reasoning (a natural-language explanation of why this product matches the customer’s discovered needs), supporting evidence (specific data points from the KB or CRM that back the recommendation), and action buttons (Accept, Dismiss, Learn More). When a rep clicks Accept, the product moves to the Accepted Products Tray (a sub-component that tracks the rep’s selected products for the session). When they click Dismiss, a feedback signal is sent to M16 (Feedback Engine).

| Accepted Products Tray The Accepted Products Tray is a persistent sub-component within the Recommendations Panel (or docked at the bottom of the screen, depending on layout configuration). It holds the products the rep has accepted during this session. These accepted products influence subsequent agent behavior: if the rep accepts a UCaaS product, the Cross-Sell agent in M6 will pivot to recommending complementary products (e.g., SD-WAN, security) rather than competing ones. The Tray contents are stored in M1’s accepted_products state layer and are read by all agents via their state access. |
| --- |

### 13.1.5 Quick Actions Bar

The Quick Actions Bar renders the Plugin SDK (M12) outputs as interactive buttons. This bar is not static — buttons appear and disappear dynamically based on the conversation state. The bar subscribes to M12’s plugin trigger evaluations: when a plugin’s triggerConditions are met (as defined in its manifest), the button appears. When conditions are no longer met, the button fades or is removed.

When a rep clicks a Quick Action button, the UI sends an execute request to M12’s Plugin Gateway. The plugin runs in its sandboxed environment, calls Platform Access APIs (State Read, KB Query, History Read), generates its output using the UI Rendering Contract (Card, Table, Form, or Custom Component template), and the result is rendered in the Side Panel. The rep never leaves the main screen.

| Button State | Condition | Visual Treatment |
| --- | --- | --- |
| Visible + Active | Plugin triggerConditions met, plugin enabled for tenant and not hidden by rep | Full color, clickable, optional pulse animation for high-priority suggestions |
| Visible + Disabled | Plugin triggerConditions partially met (e.g., needs more discovery data) | Greyed out with tooltip explaining what’s needed |
| Hidden | Plugin triggerConditions not met, or plugin disabled by tenant/rep | Not rendered at all — no empty placeholder |
| Loading | Rep clicked button, plugin executing | Spinner overlay on button, other buttons remain clickable |
| Error | Plugin execution failed (after circuit breaker threshold) | Red border, error icon, tooltip with retry option |

### 13.1.6 Agent Indicator

The Agent Indicator is a small but critical UI element that shows which workflow agent (M6–M11) is currently active. It displays as a badge in the top bar with the agent’s name and icon (e.g., “Cross-Sell” with a branching icon, “Renewal” with a refresh icon). When the Orchestrator in M2 detects that a different agent should take over (based on trigger conditions and conversation flow), the Agent Indicator shows a subtle animation and a “Suggested switch” tooltip. The rep can accept or dismiss the switch suggestion.

This element reinforces the “rep in control” principle: the platform never silently switches agents. The rep always sees what’s happening and can override. The agent transition is logged in M1’s state as an agent_transition event, which M16 (Feedback Engine) uses to track whether agent switches correlate with better outcomes.

## 13.2 Real-Time Communication — WebSocket Protocol

Module 13 is the only interface module that communicates primarily via WebSocket rather than REST API. This is a deliberate architectural choice: the Conversation UI needs sub-second updates for typing indicators, state changes, recommendation arrivals, and agent transitions. REST polling would introduce unacceptable latency and unnecessary load.

### 13.2.1 Connection Lifecycle

| Phase | Action | Technical Detail |
| --- | --- | --- |
| Connect | Rep opens Conversation UI or starts a new session | WebSocket upgrade request to wss://api.platform.com/ws with JWT token in Authorization header. M18 (Auth) validates token and extracts tenant_id + user_id. |
| Authenticate | Server validates JWT and binds socket to session | Socket is bound to session_id. All subsequent messages are scoped to this session. If the rep has an existing session for this account, the server resumes it. |
| Subscribe | Client subscribes to state channels | Client sends { type: “subscribe”, channels: [“conversation”, “state”, “recommendations”, “plugins”, “agent”] }. Server confirms subscription. |
| Active | Bidirectional message flow | Client sends rep messages and action events. Server pushes state updates, agent responses, recommendations, plugin triggers. |
| Heartbeat | Keep-alive every 30 seconds | Client sends ping, server responds with pong. Three missed heartbeats = connection considered dead, server-side cleanup. |
| Disconnect | Rep closes tab, navigates away, or session times out | Server persists current state to M1 warm storage (PostgreSQL). Session remains resumable for 24 hours. |

### 13.2.2 Message Types — Server to Client

| Message Type | Payload | UI Action |
| --- | --- | --- |
| state_changed | { layer: string, delta: object, version: number } | Update the relevant panel (Context, Discovery, etc.) with the delta. Version number enables optimistic UI conflict resolution. |
| message_appended | { role: “assistant”, content: string, metadata: object } | Append new message to Chat Panel. Metadata includes embedded_actions[], product_mentions[], discovery_updates[]. |
| recommendation_surfaced | { recommendation_id, product, confidence, reasoning, evidence[] } | Add card to Recommendations Panel with animation. Sort by confidence descending. |
| plugin_triggered | { plugin_id, name, icon, priority } | Show button in Quick Actions Bar at the specified priority position. |
| plugin_result | { plugin_id, template_type, rendered_output } | Open Side Panel and render the plugin output using the appropriate template (Card/Table/Form/Custom). |
| agent_transition | { from_agent, to_agent, reason, suggested: boolean } | Update Agent Indicator badge. If suggested=true, show tooltip for rep to accept/dismiss. |
| typing_indicator | { active: boolean } | Show/hide typing dots in Chat Panel while agent is processing. |
| discovery_updated | { dimension, old_value, new_value, trigger } | Animate progress bar for the specified dimension. Highlight the discovery question that caused the update. |
| error | { code, message, recoverable: boolean } | Show toast notification. If recoverable=false, offer to reload session. |

### 13.2.3 Message Types — Client to Server

| Message Type | Payload | Backend Action |
| --- | --- | --- |
| send_message | { content: string } | M1 appends to conversation_history → M2 routes to active agent → agent processes → response pushed back via message_appended |
| accept_recommendation | { recommendation_id } | M1 moves recommendation to accepted_products layer → M16 records accept signal → all agents notified of new accepted product |
| dismiss_recommendation | { recommendation_id, reason?: string } | M16 records dismiss signal with optional reason → recommendation hidden from panel → agents adjust future recommendations |
| execute_plugin | { plugin_id } | M12 Plugin Gateway executes plugin in sandbox → result pushed via plugin_result |
| accept_agent_switch | { to_agent } | M2 Orchestrator transitions active agent → M1 logs transition → Agent Indicator updates |
| dismiss_agent_switch | { to_agent } | M2 suppresses suggestion for this session → M16 records dismissal signal |
| feedback_signal | { target_type, target_id, signal_type, metadata } | M16 records feedback (thumbs up/down on response, edit signal on email, etc.) |

| Why Not REST for Everything? The Conversation UI handles 10–20 state updates per minute during an active session. REST polling at this frequency would mean 10–20 HTTP requests per minute per active session, each with TCP handshake overhead, authentication header parsing, and response serialization. With WebSocket, the connection is established once and all updates flow as lightweight JSON frames over the persistent connection. For a deployment with 500 concurrent reps, this is the difference between 10,000 HTTP requests/minute and 500 persistent connections. The WebSocket approach also enables true real-time push: the server sends updates the instant they happen, rather than waiting for the client’s next poll interval. |
| --- |

## 13.3 State-Driven UI Rendering

Every visual element in Module 13 is a function of session state. The UI does not maintain its own state independently — it is a projection of M1’s session state. This is a critical architectural principle: if the rep refreshes the page, the entire UI reconstructs from M1’s persisted state. Nothing is lost.

| UI Component | State Layer in M1 | State Fields Consumed |
| --- | --- | --- |
| Chat Panel | conversation_history | messages[], current_turn, pending_response |
| Customer Context Panel | customer_context | account, products, metrics, health_score, activity_log, contacts |
| Discovery Progress | discovery_progress | dimensions[], overall_completion, last_updated_dimension |
| Recommendations Panel | recommendations | active_recommendations[], accepted_products[], dismissed_ids[] |
| Quick Actions Bar | plugin_state | triggered_plugins[], active_plugin_id, plugin_results{} |
| Agent Indicator | agent_state | active_agent_id, suggested_transition, transition_history[] |

When the UI connects via WebSocket, it first performs a full state sync: GET /api/sessions/{id}/state returns the complete session state object. The UI renders all components from this snapshot. After initial sync, the UI only receives deltas via WebSocket — incremental updates to specific state layers. This minimizes bandwidth and keeps the UI responsive.

| Optimistic UI and Conflict Resolution When the rep sends a message, the Chat Panel immediately renders it (optimistic update) without waiting for the server to confirm. The message is marked as “pending” with a subtle visual indicator. When the server confirms (via state_changed with the new version number), the pending marker is removed. If the server rejects the message (e.g., session expired), the UI rolls back the optimistic update and shows an error. The version number on every state_changed message enables this: the UI tracks the expected next version. If it receives a version it didn’t expect, it knows something is out of sync and performs a full state re-sync. |
| --- |

## 13.4 UX Principles — How They Translate to Code

The mermaid diagram defines four UX principles. Here is exactly how each principle manifests in the technical implementation:

| Principle | What It Means | Technical Implementation |
| --- | --- | --- |
| Dynamic | Actions appear/disappear based on state | Quick Actions Bar subscribes to M12 trigger evaluations. Buttons are rendered conditionally: if (plugin.triggerConditions.every(c => evaluate(c, sessionState))) renderButton(plugin). When state changes invalidate a trigger, the button is removed via DOM transition. |
| Rep in Control | AI suggests, rep decides | Every recommendation has Accept/Dismiss. Agent switches are suggested, never forced. Plugin outputs appear in Side Panel for review, never auto-applied. No action happens without rep click. M16 tracks every accept/dismiss to learn rep preferences. |
| Progressive Disclosure | Surface info as relevant | Customer Context Panel loads with essential data at session start. Detailed product specs, competitive info, and historical analysis are loaded on-demand when the rep clicks “Learn More” or when the agent’s context assembly in M1 determines they’re relevant. This reduces cognitive load. |
| Workflow Aware | UI reflects active agent | Agent Indicator badge changes color and icon per agent. Chat Panel’s AI response styling adapts subtly per agent (e.g., Renewal agent responses have a different accent color). Quick Actions Bar filters to show only plugins relevant to the active agent’s workflow. Discovery dimensions may reweight based on active agent. |

## 13.5 Session Lifecycle from the UI Perspective

Understanding the full lifecycle of a session as experienced through the Conversation UI:

| Phase | What Happens in the UI | Backend Modules Involved |
| --- | --- | --- |
| 1. Session Start | Rep selects an account from their account list or clicks “New Session” from the CRM widget. The UI shows a loading screen while the backend assembles context. | M1 creates session, M4 fetches CRM/ERP data, M5 loads tenant config, M2 evaluates initial agent triggers |
| 2. Context Assembly | Customer Context Panel populates. Discovery Progress initializes at 0%. Agent Indicator shows the initially triggered agent (usually Net-New or Renewal based on account signals). | M1 stores assembled context, M2 selects initial agent based on trigger engine evaluation |
| 3. Opening Move | AI sends the first message — a contextual greeting that references the customer’s situation. Quick Actions with autoSurface triggers (Meeting Prep Brief, Account Overview) appear. | Active agent generates opening, M12 evaluates autoSurface plugins |
| 4. Active Conversation | Rep and AI exchange messages. Discovery Progress updates. Recommendations surface. Quick Actions appear/disappear. Agent transitions may be suggested. | M1 accumulates state, M2 manages agent, M3 serves KB queries, M12 triggers plugins, M16 captures signals |
| 5. Recommendation Phase | One or more product recommendations appear in the Recommendations Panel. Rep reviews, accepts, or dismisses each. | Active agent emits recommendations, M3 provides product data, M16 records feedback signals |
| 6. Action Phase | Rep uses Quick Actions (Generate Email, Build Quote, etc.) to create deliverables based on the conversation and accepted products. | M12 executes plugins with full context, plugins use State Read + KB Query APIs |
| 7. Session End | Rep closes the conversation or navigates away. State is persisted. Session is resumable. | M1 persists to warm storage, M16 finalizes signal capture, M19 logs session audit trail |

## 13.6 Module Access Map — What Module 13 Touches

| Backend Module | How M13 Accesses It | Communication | Data Direction |
| --- | --- | --- | --- |
| M1 State Engine | WebSocket subscription + REST for initial sync | WebSocket (primary), REST (initial load) | Bidirectional: reads state, sends rep actions that mutate state |
| M2 Orchestrator | Indirect via M1 (agent responses flow through state) | Through M1 WebSocket events | Read: receives agent responses and transition suggestions |
| M3 KB Engine | Indirect via agents and plugins (never called directly by UI) | N/A — accessed by agents/plugins on backend | N/A |
| M4 Data Integration | Indirect via M1 context assembly at session start | N/A — M1 calls M4 during context assembly | Read: customer data populates Context Panel |
| M5 Multi-Tenancy | Indirect via M1 (tenant config loaded at session start) | N/A — config loaded by M1 | Read: discovery dimensions, agent configs, UI preferences |
| M12 Plugin SDK | REST API for plugin execution, WebSocket for trigger events | REST (execute), WebSocket (triggers/results) | Bidirectional: sends execute requests, receives rendered outputs |
| M16 Feedback Engine | REST API for feedback signals | REST POST /api/feedback/signals | Write: sends accept/dismiss/edit signals |
| M18 Auth & Roles | JWT validation on WebSocket connect | REST (token refresh), WebSocket (initial auth) | Read: validates session, enforces RBAC |
| M19 Compliance | Indirect — M19 audits all state mutations | N/A — M19 subscribes to M1 events | N/A — passive audit |
| M20 API Gateway | All REST calls route through API Gateway | REST (all HTTP requests) | Bidirectional: rate limiting, request routing |

# Module 14: CRM Embedded Widget

Module 14 takes the core capabilities of the Conversation UI and embeds them inside the rep’s CRM system — the application they already live in. The widget is not a miniaturized copy of Module 13; it is a purpose-built compact interface optimized for the CRM sidebar form factor. The goal: the rep should never need to leave their CRM to access platform intelligence.

## 14.1 Supported CRM Platforms

| Platform | Integration Type | Rendering Surface | Technical Framework |
| --- | --- | --- | --- |
| Salesforce | Lightning Web Component (LWC) | Utility Bar or Record Page sidebar | LWC with Shadow DOM, communicates via Platform API over REST. Deployed as a managed package. |
| HubSpot | CRM Sidebar App | CRM record sidebar | React-based iframe app registered via HubSpot App Marketplace. Communicates via Platform API over REST. |

| Why Not WebSocket for the CRM Widget? Module 14 uses REST API (Platform API) instead of WebSocket for a practical reason: CRM sidebar components are constrained environments. Salesforce Lightning Components have strict CSP (Content Security Policy) rules that complicate WebSocket connections. HubSpot iframe apps have cross-origin restrictions. REST with short polling (every 2–3 seconds during active sessions) provides acceptable latency for the compact widget use case, where update frequency is lower than the full Conversation UI. In the full product, we evaluate Server-Sent Events (SSE) as a middle ground — unidirectional push over HTTP that CRM CSP policies generally allow. |
| --- |

## 14.2 Auto-Context Detection

The most important capability of the CRM Widget is automatic context detection. When the rep navigates to a CRM record (Account, Opportunity, Contact), the widget automatically detects which record they’re viewing and loads the relevant session context — without the rep typing anything.

### 14.2.1 Detection Mechanism per Platform

| Platform | How Context Is Detected | Data Extracted | Fallback |
| --- | --- | --- | --- |
| Salesforce LWC | Lightning Message Channel — the LWC subscribes to record page events and reads the recordId from the page context | Account ID, Opportunity ID, Contact ID, record type | If no record context available (e.g., on a dashboard), widget shows account search/select UI |
| HubSpot Sidebar | HubSpot CRM Extension SDK — provides context.crm.objectId and context.crm.objectType on sidebar load | Object ID, Object Type (contact, company, deal), associated company ID | Same fallback: manual account selection |

Once the widget detects the CRM record, it calls the Platform API: POST /api/sessions/resolve with { crm_platform, crm_object_type, crm_object_id, tenant_id }. The Platform API does three things: maps the CRM record ID to the platform’s internal account ID (using field mappings configured in M4), checks if there’s an existing active session for this account and this rep, and either resumes the existing session or creates a new one. The widget then loads the session state and renders.

## 14.3 Widget Interface Components

The CRM Widget renders a compact subset of Module 13’s components, optimized for a narrow sidebar (typically 350–400px wide):

| Component | Full UI (M13) Equivalent | Widget Adaptation | What’s Reduced/Changed |
| --- | --- | --- | --- |
| Mini Chat | Chat Panel | Compact message list with shorter max-width, collapsible AI responses | No inline product mention links (space constraints), no rich text formatting |
| Inline Recommendations | Recommendations Panel | Stacked cards below the chat, one card visible at a time with swipe/scroll | No side-by-side comparison, simplified evidence display (1–2 bullet points vs full evidence panel) |
| Quick Action Chips | Quick Actions Bar | Horizontal scrollable chip row above the chat input | Maximum 4 visible at once, overflow in a “More” menu |
| Context Summary | Customer Context Panel | Collapsible header showing account name, health score, and top 3 metrics | No full relationship map, no activity timeline (rep can see these in the CRM itself) |
| Agent Badge | Agent Indicator | Small icon + label in the widget header bar | No transition animation, switch suggestions appear as a chat message instead of tooltip |

## 14.4 CRM Write-Back

A key differentiator of the CRM Widget over the standalone Conversation UI is bidirectional CRM integration. The widget can log platform activities back into CRM records, so managers and other team members can see what happened without accessing the platform.

| Write-Back Action | CRM Record Updated | Data Written | Trigger |
| --- | --- | --- | --- |
| Log Session Activity | Activity/Task on Account | Session summary, duration, topics discussed, recommendations made | Automatic on session end (configurable: auto or rep-confirms) |
| Create Follow-Up Task | Task on Account or Opportunity | Task title, description (from AI suggestion), due date, assigned to rep | Rep clicks “Create Task” from a recommendation or plugin output |
| Update Opportunity Stage | Opportunity record | New stage value based on conversation progression | Rep confirms stage change suggestion from the Renewal or Upsell agent |
| Attach Generated Document | Note/Attachment on Account | Email draft, quote PDF, competitive comparison from plugin outputs | Rep clicks “Save to CRM” on a plugin output |
| Log Recommendation Outcome | Custom field or Activity | Which products were recommended, which were accepted/dismissed | Automatic on session end |

| Write-Back Permission Model CRM write-back requires explicit OAuth scopes granted during M4 (Data Integration) setup. The platform never writes to CRM records that the rep doesn’t have edit access to in the CRM’s own permission model. Before executing a write-back, the Platform API calls M4’s permission check: M4 validates the rep’s CRM user has write access to the target record. If not, the write-back fails gracefully with a message: “You don’t have permission to update this record in [CRM Name].” This prevents the platform from accidentally bypassing CRM security. |
| --- |

## 14.5 Feature Parity Matrix — Module 13 vs Module 14

| Feature | M13 (Conversation UI) | M14 (CRM Widget) | Reason for Difference |
| --- | --- | --- | --- |
| Full conversation history | ✓ Complete scrollable thread | ✓ Last 20 messages + “Load more” | Sidebar height constraint |
| Multi-dimensional discovery | ✓ Radar/spider chart (full product) | ✓ Simplified progress bar per dimension | Width constraint — radar chart unusable below 300px |
| Side Panel for plugin outputs | ✓ Dedicated side panel slides in | ✗ Plugin outputs render inline below chat | No space for a second panel in sidebar |
| Product comparison tables | ✓ Full table with all columns | ✓ Condensed 3-column max | Table overflow in narrow sidebar |
| Agent transition animation | ✓ Smooth badge animation | ✗ Static badge swap | Reduces JS bundle size for CRM embedding |
| Real-time WebSocket updates | ✓ WebSocket | ✗ REST polling (2–3s) | CRM CSP restrictions |
| CRM write-back | ✗ Not available | ✓ Full write-back capabilities | Module 13 is CRM-agnostic; M14 knows the CRM context |
| Keyboard shortcuts | ✓ Full shortcut set | ✗ Minimal (Enter to send only) | CRM has its own shortcut namespace |
| Offline capability | ✗ Requires connection | ✗ Requires connection | Both require real-time backend access |

## 14.6 Module Access Map — What Module 14 Touches

| Backend Module | How M14 Accesses It | Communication | Notes |
| --- | --- | --- | --- |
| M1 State Engine | Platform API (REST) for state read/write | REST with polling | Same session state as M13 — if rep switches between M13 and M14, state is continuous |
| M2 Orchestrator | Indirect via Platform API (agent responses in session state) | Through M1 | Same agent behavior regardless of UI surface |
| M4 Data Integration | Platform API for session resolution (maps CRM record ID to platform account) | REST | Also used for write-back permission validation |
| M5 Multi-Tenancy | Indirect via Platform API (tenant config in session state) | Through M1 | Widget appearance can be tenant-branded (logo, colors) |
| M12 Plugin SDK | Platform API for plugin execution | REST | Plugin outputs render inline in widget instead of Side Panel |
| M16 Feedback Engine | Platform API for feedback signals | REST | Same signals as M13, plus CRM-specific signals (write-back completed) |
| M18 Auth & Roles | OAuth flow within CRM iframe, JWT for Platform API calls | REST | CRM SSO may chain to Platform SSO for seamless auth |
| M20 API Gateway | All REST calls route through API Gateway | REST | Widget requests are rate-limited separately from M13 requests |

# Module 15: Admin Portal

Module 15 is the administrative interface used by Sales Ops teams and platform administrators to configure, manage, and monitor the Sales Intelligence Platform. Unlike Modules 13 and 14 (which serve sales reps during customer conversations), Module 15 is a traditional web application with forms, tables, editors, and dashboards. It communicates exclusively via REST API (Platform API) and has no real-time requirements.

| Who Uses Module 15? The Admin Portal serves two personas. Sales Ops / Admin: the primary user, responsible for configuring business rules, managing the knowledge base, setting up integrations, and monitoring adoption. They access all seven admin sections. Platform Super Admin: an internal role for cross-tenant support, used by the platform vendor’s customer success team. They can view tenant configurations, run diagnostics, and assist with onboarding. This role is enforced by M18 (Auth & Roles). |
| --- |

## 15.1 Admin Sections Overview

| Section | Purpose | Primary Backend Module | CRUD Operations |
| --- | --- | --- | --- |
| KB Management | Upload, browse, edit, and delete knowledge base documents. Visual relationship editor for product connections. | M3 KB Engine | Create (upload docs), Read (browse/search), Update (edit metadata, relationships), Delete (remove docs with cascade check) |
| Integration Setup | Connect CRM and ERP systems. Configure field mappings and sync schedules. | M4 Data Integration | Create (new connection), Read (connection status), Update (field mappings, sync config), Delete (disconnect with data retention options) |
| Business Rules | Visual rule builder for tenant-specific logic. Test and preview rules against sample data. | M5 Multi-Tenancy (business_rules layer) | Create (new rule), Read (rule list + execution logs), Update (edit conditions/actions), Delete (deactivate or remove) |
| Agent Config | Enable/disable workflow agents (M6–M11). Set trigger thresholds and priority weights. | M2 Orchestrator + M5 Multi-Tenancy | Read (current agent config), Update (enable/disable, thresholds, priorities). No Create/Delete — agents are platform-defined. |
| User Management | Add/remove users. Assign roles (rep, manager, admin, executive). | M18 Auth & Roles | Create (invite user), Read (user list + activity), Update (role change, deactivate), Delete (remove user, reassign accounts) |
| Discovery Framework | Customize discovery dimensions and progress weights per tenant. | M5 Multi-Tenancy (discovery_config layer) | Create (new dimension), Read (current framework), Update (weights, labels, completion thresholds), Delete (remove dimension with impact analysis) |
| Usage Analytics | Admin-level metrics: adoption rates, feature usage, data quality scores. | M17 Analytics Dashboard (admin view) | Read only — no create/update/delete. Displays aggregated metrics with filters. |

## 15.2 KB Management — Deep Dive

The Knowledge Base Management section is the most complex admin surface. It is the interface through which tenant administrators populate M3 (KB Engine) with product information, competitive intelligence, pricing rules, and sales collateral that agents and plugins consume.

### 15.2.1 Document Upload Pipeline

| Step | Admin Action | Backend Processing | UI Feedback |
| --- | --- | --- | --- |
| 1. Upload | Admin drags files or clicks upload. Supported: PDF, DOCX, XLSX, CSV, Markdown. | File is sent to Platform API → M3 ingestion pipeline. File is validated (type, size, malware scan). | Progress bar with file name, size, and estimated processing time. |
| 2. Processing | Admin sees processing status per document. | M3 chunks document, generates embeddings, extracts metadata (title, product mentions, categories). Vector + keyword indexes are updated. | Status transitions: Uploading → Processing → Indexing → Complete. Error state with specific failure reason if applicable. |
| 3. Review | Admin reviews extracted metadata, corrects if needed. Assigns product tags and categories. | Admin edits are saved to M3’s document metadata. If product tags change, graph relationships are recalculated. | Editable form with auto-suggested tags based on content analysis. |
| 4. Publish | Admin clicks “Publish” to make the document available to agents and plugins. | M3 marks document as active. It becomes retrievable via KB Query API. All agents and plugins can now access it. | Green checkmark, document appears in the published list. |

### 15.2.2 Visual Relationship Editor

The Visual Relationship Editor is a graph-based UI that lets admins define how products relate to each other. These relationships are stored in M3’s knowledge graph and directly influence which products agents recommend together. For example, an admin can define that “SD-WAN” is a “complements” relationship with “UCaaS,” which tells the Cross-Sell agent (M6) to recommend SD-WAN when the customer has UCaaS.

| Relationship Type | Meaning | Agent Impact | Example |
| --- | --- | --- | --- |
| complements | Products that work well together | Cross-Sell agent (M6) uses this to recommend the complementary product | SD-WAN complements UCaaS |
| upgrades_to | A product is a higher tier of another | Upsell agent (M7) uses this to suggest tier upgrades | UCaaS Pro upgrades_to UCaaS Enterprise |
| replaces | A product replaces a discontinued one | Renewal agent (M9) uses this during contract renewals to suggest the replacement | Legacy PBX replaces_with Cloud Voice |
| bundles_with | Products commonly sold as a package | Full Solution agent (M8) uses this to construct complete solution bundles | SD-WAN bundles_with Security bundles_with UCaaS |
| competes_with | Products from competitors | All agents use this for competitive positioning; Competitor Analysis plugin (M12) surfaces comparison cards | Our UCaaS competes_with RingCentral MVP |

## 15.3 Integration Setup

The Integration Setup section is where admins connect the platform to external systems (CRM, ERP) via Module 4 (Data Integration). The admin portal provides a guided wizard that walks through each step of the integration process.

### 15.3.1 Connection Wizard Flow

| Step | Admin Action | Backend Processing |
| --- | --- | --- |
| 1. Select Platform | Admin chooses CRM/ERP from supported list (Salesforce, HubSpot, SAP, Oracle, etc.) | M4 loads the connector template for the selected platform (OAuth config, API endpoints, required scopes) |
| 2. Authenticate | Admin clicks “Connect” → OAuth flow opens in popup → admin logs into CRM/ERP and grants permissions | M4 stores encrypted OAuth tokens (refresh + access) in M5 tenant vault. M19 logs the authorization event. |
| 3. Field Mapping | Admin maps CRM/ERP fields to platform fields (e.g., CRM “Annual Revenue” → Platform “ARR”) | M4 stores field mapping configuration in M5 tenant config. Default mappings are pre-populated for common CRM platforms. |
| 4. Sync Configuration | Admin sets sync frequency (real-time webhooks vs. batch intervals) and selects which objects to sync | M4 registers webhook endpoints with the CRM (if real-time) or schedules batch sync jobs (if interval-based) |
| 5. Test Connection | Admin clicks “Test” → platform pulls a sample record and displays it | M4 executes a single API call to the CRM, retrieves one record, normalizes it through the field mapping, and returns the result for admin review |
| 6. Activate | Admin clicks “Activate” → initial full sync begins | M4 triggers the initial sync: all relevant records are pulled, normalized, and stored. Progress is streamed to the admin UI. |

| Integration Health Monitoring Once connected, the Integration Setup section shows a health dashboard for each integration: last sync time, records synced, error count, average sync latency. If an integration becomes unhealthy (OAuth token expired, API rate limited, field mapping broken), the admin sees an amber/red status with specific remediation steps. M4 also sends proactive alerts via email when integration health degrades, so admins don’t have to check the portal constantly. |
| --- |

## 15.4 Business Rules — Visual Rule Builder

The Business Rules section lets admins define tenant-specific logic without writing code. Rules are stored in M5’s business_rules layer and are evaluated by agents during conversations. The visual builder provides a drag-and-drop interface with condition blocks and action blocks.

| Rule Component | Description | Example |
| --- | --- | --- |
| Condition Block | An IF statement that evaluates session data, customer context, or product attributes | IF customer.industry = “Healthcare” AND customer.arr > 100000 |
| Action Block | A THEN statement that modifies agent behavior, filters recommendations, or triggers alerts | THEN require_approval_for = [“HIPAA-compliant products only”] AND notify = “compliance_team@tenant.com” |
| Exception Block | An UNLESS clause that overrides the rule under specific conditions | UNLESS customer.existing_product includes “HIPAA Cloud Suite” (already compliant, no restriction needed) |
| Priority | Numeric priority when multiple rules apply | Priority 1 rules execute first. Conflicting actions from lower-priority rules are discarded. |
| Scope | Which agents and workflows the rule applies to | Apply to: M6 (Cross-Sell), M7 (Upsell). Exclude: M11 (Net-New). |

### 15.4.1 Test and Preview

Before activating a rule, admins can test it against sample scenarios. The admin selects a test customer (from real CRM data, anonymized if needed) and a test agent workflow. The platform runs the rule evaluation and shows: which conditions matched, which actions would fire, which recommendations would be filtered/modified, and what the rep would see differently. This prevents admins from deploying rules that accidentally block all recommendations or create contradictory constraints.

## 15.5 Agent Configuration

Admins cannot create or delete agents (agents are platform-defined: M6–M11). But they can configure how agents behave within their tenant:

| Configuration | What It Controls | Example | Stored In |
| --- | --- | --- | --- |
| Enable/Disable | Whether an agent is available for this tenant | Disable Win-Back (M10) if the tenant has no churned customers to recover | M5 tenant_config.agent_config.enabled_agents[] |
| Trigger Thresholds | The sensitivity of agent trigger conditions | Cross-Sell (M6) triggers when opportunity confidence > 60% (default: 70%) | M5 tenant_config.agent_config.trigger_overrides{} |
| Priority Weights | Which agent takes precedence when multiple could trigger | Renewal (M9) should always win over Cross-Sell (M6) when a renewal is within 90 days | M5 tenant_config.agent_config.priority_weights{} |
| Quick Action Set | Which plugins (M12) are available for each agent workflow | During Renewal conversations, show Contract Review and Generate Quote but hide Competitor Analysis | M5 tenant_config.agent_config.quick_action_ids{} per agent |
| Discovery Dimensions | Which discovery dimensions an agent focuses on | Upsell (M7) focuses on “Usage Patterns” and “Growth Plans”; ignores “Competitive Landscape” | M5 tenant_config.discovery_config.agent_dimensions{} |

## 15.6 User Management

The User Management section handles platform access and role assignment. It does not replace the CRM’s own user management — it manages platform-specific permissions enforced by M18 (Auth & Roles).

| Role | Platform Access | Module Access | Data Scope |
| --- | --- | --- | --- |
| Sales Rep | M13 (Conversation UI), M14 (CRM Widget) | Full conversation capabilities, all enabled plugins, own session history | Own assigned accounts only (enforced by M18 data scoping) |
| Sales Manager | M13, M14, M17 (Analytics Dashboard — team view) | Same as rep + view team members’ sessions (read-only) + team performance analytics | Team’s accounts and sessions (based on CRM team hierarchy synced via M4) |
| Sales Ops / Admin | M15 (Admin Portal), M17 (Analytics — ops view) | Full admin portal access: KB management, rules, integrations, user management, agent config | All accounts and sessions within the tenant (no cross-tenant access) |
| Executive | M17 (Analytics Dashboard — executive view) | ROI dashboard, revenue impact, strategic insights. No conversation access. | Aggregated tenant-wide metrics only (no individual account/session drill-down) |
| Platform Super Admin | M15 (Admin Portal — cross-tenant view) | View any tenant’s configuration, run diagnostics, assist with onboarding. Cannot modify tenant data without tenant admin approval. | Cross-tenant (managed by M5 tenant isolation with explicit break-glass audit in M19) |

### 15.6.1 User Onboarding Flow

When an admin invites a new user: admin enters the user’s email and selects a role. The Platform API creates a pending user record in M18. An invitation email is sent with a one-time setup link. The user clicks the link and authenticates via the tenant’s SSO provider (configured in M18). On first login, M4 syncs the user’s CRM profile to determine account assignments. The user’s data scope is automatically set based on CRM assignments. The admin can override account assignments manually if needed.

## 15.7 Discovery Framework Configuration

This section lets admins customize the discovery dimensions that drive the Discovery Progress component in Module 13. Discovery dimensions define “what the rep needs to learn about the customer.” Different tenants in different industries need different discovery frameworks.

| Configuration | Default Value | What Admins Can Change | Impact |
| --- | --- | --- | --- |
| Dimension Names | Business Challenges, Current Infrastructure, Decision Timeline, Budget Authority, Stakeholder Map | Rename, add new dimensions, remove irrelevant ones | Changes what the Discovery Progress component shows in M13 |
| Dimension Weights | Equal (20% each for 5 dimensions) | Assign different weights (e.g., Budget Authority = 30%, Stakeholder Map = 10%) | Changes the overall completion percentage calculation |
| Completion Thresholds | 80% per dimension = complete | Set per-dimension thresholds (e.g., Budget Authority needs 90%, Current Infrastructure needs only 60%) | Changes when the agent considers discovery “complete” and shifts to recommendation mode |
| Agent-Specific Dimensions | All dimensions apply to all agents | Assign specific dimensions to specific agents (e.g., Renewal agent only cares about “Contract Satisfaction” and “Renewal Timeline”) | Agents focus their discovery questions on the dimensions assigned to them |
| Dimension Questions | Platform-generated discovery questions | Customize or add specific questions per dimension | Agents use these questions to guide conversation and determine progress |

## 15.8 Usage Analytics (Admin View)

The Usage Analytics section provides admin-level metrics that are distinct from the executive-facing Analytics Dashboard (M17). While M17 focuses on ROI and revenue impact, this section focuses on platform health, adoption, and data quality.

| Metric Category | Metrics | Data Source | Action Triggers |
| --- | --- | --- | --- |
| Adoption | Daily/Weekly Active Reps, sessions per rep per week, time spent in platform, feature adoption heatmap | M16 (Feedback Engine) session signals, M1 (State Engine) session metadata | Low adoption → admin sends training reminders, adjusts agent config, reviews rep feedback |
| Data Quality | KB freshness (% of documents updated in last 90 days), CRM sync error rate, field mapping completeness | M3 (KB Engine) document metadata, M4 (Data Integration) sync logs | Stale KB → prompt to update documents. High error rate → review integration config. |
| Agent Effectiveness | Recommendation accept rate per agent, average discovery completion per agent, agent switch frequency | M16 (Feedback Engine) aggregated signals, M1 (State Engine) agent transition logs | Low accept rate → review agent trigger thresholds, KB content quality, business rules |
| Plugin Usage | Execution count per plugin, average execution time, error rate per plugin, most/least used plugins | M12 (Plugin SDK) execution logs | Unused plugins → consider disabling to reduce clutter. High error rate → investigate plugin health. |
| Compliance | Audit log volume, PII detection events, data access anomalies | M19 (Compliance & Audit) | PII events → review which documents contain PII, update masking rules |

## 15.9 Module Access Map — What Module 15 Touches

| Backend Module | Admin Section(s) That Access It | Operations | Communication |
| --- | --- | --- | --- |
| M2 Orchestrator | Agent Config | Read agent registry, update trigger thresholds and priorities | REST via Platform API |
| M3 KB Engine | KB Management | Full CRUD on documents, relationship editing, search/browse | REST via Platform API (multipart upload for documents) |
| M4 Data Integration | Integration Setup | Create/manage CRM/ERP connections, field mappings, sync config | REST via Platform API + OAuth callback handlers |
| M5 Multi-Tenancy | Business Rules, Agent Config, Discovery Framework | Read/write tenant configuration layers: business_rules, agent_config, discovery_config | REST via Platform API |
| M16 Feedback Engine | Usage Analytics | Read aggregated feedback signals and adoption metrics | REST via Platform API (read-only) |
| M17 Analytics Dashboard | Usage Analytics | Read admin-level analytics views | REST via Platform API (read-only) |
| M18 Auth & Roles | User Management | CRUD users, assign roles, manage SSO config | REST via Platform API |
| M19 Compliance | Usage Analytics (Compliance tab) | Read audit logs, PII events, access logs | REST via Platform API (read-only) |
| M20 API Gateway | All sections | All requests route through gateway for rate limiting and auth | REST |

# Shared Backend Services

All three interface modules communicate with the backend through a shared service layer. Understanding these services clarifies how M13, M14, and M15 connect to the rest of the platform.

## Shared.1 Platform API

The Platform API is the unified REST API that serves all interface modules. It is implemented behind Module 20 (API Gateway), which handles rate limiting, authentication token validation, request routing, and response caching. Every HTTP request from M13, M14, or M15 passes through M20 before reaching the target backend module.

| API Domain | Consumers | Key Endpoints | Auth Requirement |
| --- | --- | --- | --- |
| Sessions | M13, M14 | POST /sessions (create), GET /sessions/{id}/state (full sync), POST /sessions/{id}/messages (send message), POST /sessions/resolve (CRM context resolution) | JWT (rep-scoped) |
| Plugins | M13, M14 | POST /plugins/{id}/execute (run plugin), GET /plugins (list available for session) | JWT (rep-scoped, plugin access validated against tenant config) |
| Feedback | M13, M14 | POST /feedback/signals (record signal) | JWT (rep-scoped) |
| Knowledge Base | M15 | POST /kb/documents (upload), GET /kb/documents (list/search), PUT /kb/documents/{id} (update), DELETE /kb/documents/{id}, PUT /kb/relationships (graph edit) | JWT (admin-scoped) |
| Integrations | M15 | POST /integrations (create connection), GET /integrations (list), PUT /integrations/{id} (update config), POST /integrations/{id}/test (test connection) | JWT (admin-scoped) |
| Tenant Config | M15 | GET /config (read), PUT /config/rules (business rules), PUT /config/agents (agent config), PUT /config/discovery (discovery framework) | JWT (admin-scoped) |
| Users | M15 | POST /users (invite), GET /users (list), PUT /users/{id} (update role), DELETE /users/{id} (remove) | JWT (admin-scoped) |
| Analytics | M15, M17 | GET /analytics/adoption, GET /analytics/effectiveness, GET /analytics/plugins, GET /analytics/compliance | JWT (admin or executive-scoped) |

## Shared.2 WebSocket Service

The WebSocket Service is a dedicated real-time communication layer used exclusively by Module 13 (Conversation UI). It maintains persistent connections with active rep sessions and pushes state updates from M1 (State Engine) to the UI. The service subscribes to M1’s event bus: whenever M1’s broadcastChange() fires, the WebSocket Service routes the event to the correct client connection based on session_id.

| Concern | Implementation |
| --- | --- |
| Scaling | WebSocket connections are stateful, so horizontal scaling requires sticky sessions (route same session to same server). We use Redis-backed session affinity. If a WebSocket server goes down, clients automatically reconnect and perform a full state sync. |
| Authentication | JWT validated on initial WebSocket upgrade. Token refresh is handled via a separate REST endpoint; the client sends the refreshed token via a special WebSocket message type. If the token expires without refresh, the connection is closed with a 4001 (auth_expired) code. |
| Message Ordering | Every message includes a monotonically increasing sequence number per session. If the client detects a gap in sequence numbers, it requests a full state re-sync to recover missed events. This handles network glitches gracefully. |
| Backpressure | If the client is slow to acknowledge messages (e.g., tab in background), the server buffers up to 100 messages. Beyond that, it drops intermediate state_changed events and sends a single full-state snapshot when the client catches up. |
| Multi-Tab | If a rep opens the same session in two browser tabs, both tabs receive the same WebSocket events. Writes from one tab are visible in the other via the shared session state in M1. This is intentional — the rep might have one tab for the conversation and another for reference. |

## Shared.3 Auth Service

The Auth Service wraps Module 18 (Auth & Roles) and provides authentication and authorization for all interface modules. Each module authenticates differently but all result in a JWT that the Platform API validates:

| Module | Auth Flow | Token Contents | Session Duration |
| --- | --- | --- | --- |
| M13 (Conversation UI) | Standard SSO login: rep visits platform URL → redirected to tenant’s IdP (Okta, Azure AD, etc.) → SAML/OIDC callback → JWT issued | tenant_id, user_id, role (rep/manager), account_scope[] | 8 hours (typical work day), refresh token for seamless extension |
| M14 (CRM Widget) | Chained SSO: rep is already authenticated in CRM → widget loads → silent token exchange using CRM session context → JWT issued. If silent exchange fails, widget shows a one-click login button. | tenant_id, user_id, role, crm_platform, crm_user_id | Matches CRM session duration (typically until CRM logout) |
| M15 (Admin Portal) | Standard SSO login with MFA enforcement (full product). Admin role requires step-up authentication. | tenant_id, user_id, role (admin/super_admin), permissions[] | 4 hours (shorter for security), mandatory re-auth for destructive operations |

# Cross-Module Interaction Summary

The following table provides a complete view of how every backend module (M1–M20) is consumed by the three interface modules. This is the definitive reference for understanding which UI surfaces access which backend capabilities.

| Backend Module | M13 (Conversation UI) | M14 (CRM Widget) | M15 (Admin Portal) |
| --- | --- | --- | --- |
| M1 State Engine | Primary consumer via WebSocket (real-time state sync) | Consumer via REST polling (same state, different transport) | No direct access |
| M2 Orchestrator | Indirect (agent responses via M1 state events) | Indirect (same path as M13) | Agent Config section (read/update trigger thresholds) |
| M3 KB Engine | Indirect (agents/plugins query KB on backend) | Indirect (same as M13) | KB Management section (full CRUD) |
| M4 Data Integration | Indirect (M1 assembles CRM data at session start) | Direct (session resolution, write-back, permission checks) | Integration Setup section (full CRUD) |
| M5 Multi-Tenancy | Indirect (tenant config loaded via M1) | Indirect (same as M13) | Business Rules, Agent Config, Discovery sections (read/write) |
| M6–M11 Agents | Indirect (agent outputs via M1) | Indirect (same as M13) | Indirect (configured via M2/M5 settings in Agent Config) |
| M12 Plugin SDK | Direct (execute plugins, receive outputs) | Direct (same endpoints, different rendering) | Indirect (plugin enable/disable via M5 config) |
| M16 Feedback Engine | Direct (sends feedback signals via REST) | Direct (same endpoints) | Read-only (Usage Analytics section) |
| M17 Analytics Dashboard | No access | No access | Usage Analytics section (read-only admin view) |
| M18 Auth & Roles | JWT validation on WebSocket connect | JWT validation on REST calls, chained SSO | User Management section (full CRUD) + step-up auth |
| M19 Compliance | Passive (M19 audits via M1 events) | Passive (same) | Compliance tab in Usage Analytics (read-only) |
| M20 API Gateway | All REST calls routed through gateway | All REST calls routed through gateway | All REST calls routed through gateway |

# Key Technical Decisions Summary

| Decision | What We Chose | Why | Alternative Considered |
| --- | --- | --- | --- |
| M13 transport | WebSocket for real-time, REST for initial sync | Sub-second updates required for conversation flow | REST polling (rejected: too much latency and load) |
| M14 transport | REST with short polling (2–3s) | CRM CSP restrictions prevent reliable WebSocket connections | WebSocket (rejected: Salesforce LWC CSP issues), SSE (considered for full product) |
| M15 transport | REST only | Admin operations are request-response, no real-time needed | N/A — REST is the obvious choice |
| M14 context detection | Platform-specific SDKs (Lightning Message Channel, HubSpot CRM SDK) | Native CRM APIs provide the most reliable record context | URL parsing (rejected: fragile, breaks with CRM URL changes) |
| M13 state management | Server-authoritative via M1, UI is a projection | Enables session resumability, multi-tab support, and prevents state drift | Client-side state with sync (rejected: conflict resolution complexity) |
| M14 write-back | Permission-checked via M4 before every write | Never bypass CRM’s own security model | Trust platform permissions only (rejected: could create CRM audit issues) |
| M15 rule testing | Test against real (anonymized) customer data | Realistic results prevent admin errors | Synthetic test data (rejected: too different from production scenarios) |
| Shared API gateway | Single gateway (M20) for all interface modules | Consistent rate limiting, auth, and observability | Per-module gateways (rejected: operational overhead, inconsistent policies) |
