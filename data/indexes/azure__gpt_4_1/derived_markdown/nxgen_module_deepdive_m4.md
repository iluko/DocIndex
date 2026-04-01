NxGen AI Sales Platform

Technical Deep-Dive: Module 4

Data Integration Layer

Connectors, Field Mapping, Transform, Sync, Enrichment, and Write-Back

February 2026  |  Internal — Engineering Team

| Document Scope This document covers the complete Data Integration Layer: data taxonomy (what data flows and where), connector framework with interface contract and auth, field mapping engine with auto-suggest, transform and normalize pipeline (normalizer rules, deduplication logic, enrichment computations, aggregations), sync manager (initial load, incremental sync via polling/webhooks, scheduler, DLQ and retry), platform targets (State Engine and KB Engine delivery), outbound write-back service, complete database schemas, MVP CSV import flow with validation, and all architectural decisions with rationale. Gaps identified during the deep dive (data quality scoring, SKU reconciliation, import ordering, account-rep mapping) are filled. |
| --- |

# Table of Contents

4.1  What Data Are We Integrating? — Three inbound categories + one outbound

4.2  Connector Framework — Types, roadmap, interface contract, auth/credential store

4.3  Field Mapping Engine — Canonical schema, mapping process, auto-suggest scoring

4.4  Transform & Normalize — Normalizer rules, deduplication, enricher, aggregations

4.5  Sync Manager — Initial load, incremental sync, scheduler, DLQ and retry

4.6  Platform Targets — What goes to State Engine vs KB Engine

4.7  Outbound Write-Back — Create opportunities, log activities, field mapping, safety

4.8  Complete Data Pipeline Flow — End-to-end with code

4.9  Database Schemas — accounts, contacts, transactions, account_products, account_enrichment

4.10 MVP CSV Import Flow — Import types, UX, validation rules

4.11 Nuances, Risks, and Gaps Filled

4.12 Key Technical Decisions Summary

# Module 4: Data Integration Layer

| Module Purpose The Data Integration Layer is the bridge between a tenant’s existing business systems (CRMs, ERPs, spreadsheets) and our platform. It extracts data, maps foreign schemas to our internal model, normalizes and deduplicates records, enriches them with computed intelligence, and delivers clean data to the State Engine (account context for conversations) and KB Engine (product catalog and purchase history for the Product Graph). In full product, it also writes back: logging AI activities and creating opportunities in the tenant’s CRM when reps accept recommendations. |
| --- |

| MVP vs Full Product — The Deliberate Tradeoff MVP uses CSV/Excel import ONLY. No live CRM connectors. This is not a limitation — it is a deliberate decision that saves months of engineering on OAuth flows, API rate limits, webhook infrastructure, and connector maintenance. The tenant exports their data from Salesforce/HubSpot as CSV, uploads it, and our platform ingests it. CRM connectors (Salesforce, HubSpot) are Phase 2. The architecture is designed so that adding a connector requires zero changes to the processing pipeline — connectors are pluggable sources that feed into the same mapping → transform → sync flow. |
| --- |

## 4.1 What Data Are We Integrating?

Module 4 handles three categories of data flowing INTO the platform, plus one category flowing OUT:

| Category | What It Contains | Source Systems | Destination | Why It Matters |
| --- | --- | --- | --- | --- |
| Account / Customer Data | Company name, industry, segment, revenue, employee count, address, lifecycle stage, account owner (rep assignment), territory | CRM (Salesforce Accounts, HubSpot Companies), CSV upload | State Engine → customer.profile in session state; accounts table in PostgreSQL | Without this, the platform has no idea who the rep is talking about. Agents cannot personalize discovery questions or recommendations. |
| Contact Data | Contact names, titles, roles, email, phone, decision-maker flags, relationship to account | CRM (Salesforce Contacts, HubSpot Contacts), CSV | State Engine → customer.contacts in session state | Agents need to know who the stakeholders are for tailored messaging and email generation. |
| Product / Purchase Data | What products each customer owns, transaction history (who bought what, when, for how much), contract dates, quantities | CRM (Opportunities/Line Items), ERP (invoices), Transaction CSV | KB Engine → products table + Product Graph Layer 1 co-occurrence analysis; State Engine → customer.products | This is the foundation of ALL recommendations. Without it, no cross-sell (don’t know what they have), no upsell (don’t know their tier), no whitespace analysis. |
| KB Content Data | Product catalog (SKUs, descriptions, pricing, features), case studies, playbooks, battle cards | CSV/Excel (product catalog), Document uploads (PDF, Word, Markdown) | KB Engine → Foundry IQ knowledge sources + products table + playbook_index | Module 3 handles KB content ingestion. Module 4’s role is limited to the product catalog CSV parsing and initial field mapping. Other KB documents go directly to Module 3. |
| Outbound: Activity / Opportunity Data (Full Product) | AI-generated activities (conversation summaries, recommendations made), new opportunities created from accepted recommendations | Our platform → CRM | Salesforce Opportunities, HubSpot Deals, CRM Activity logs | Closes the loop: recommendation accepted → opportunity in pipeline → deal progresses → outcome feeds back to Feedback Engine. Without write-back, the CRM has no record of AI-driven pipeline. |

## 4.2 Connector Framework

Each external system connects through a connector — a standardized adapter that handles authentication, API protocols, pagination, rate limiting, and data extraction. Connectors are pluggable: adding a new CRM means writing a new connector, not changing anything downstream.

### 4.2.1 Connector Types and Roadmap

| Connector | Auth Method | Data Pull Method | Incremental Sync | MVP? | Priority |
| --- | --- | --- | --- | --- | --- |
| CSV/Excel Upload | None (file upload) | File parsing (pandas) | Re-upload replaces previous import | YES — MVP | P0 |
| Salesforce | OAuth 2.0 (Web Server Flow) | REST API (query) + Bulk API 2.0 (initial load) | Polling (lastModifiedDate) + Streaming API / Platform Events | No | P1 (Phase 2) |
| HubSpot | OAuth 2.0 | REST API v3 | Polling (updatedAt) + Webhooks (subscription API) | No | P1 (Phase 2) |
| Dynamics 365 | OAuth 2.0 (Azure AD) | OData REST API | Polling (modifiedon) + Webhooks | No | P1 (Phase 2) |
| NetSuite | OAuth 1.0 / Token-Based Auth | SuiteTalk REST API / SuiteQL | Polling (lastModifiedDate) + saved search triggers | No | P1 (Phase 2) |
| SAP | OAuth 2.0 / API Key | OData API / RFC/BAPI | Polling (change pointers) + IDoc listeners | No | P2 (Phase 3) |
| Generic REST API | Configurable (API Key, OAuth, Bearer) | Configurable endpoint mapping | Polling with configurable cursor field | No | P2 (Phase 3) |

### 4.2.2 Connector Interface Contract

Every connector implements a standardized interface regardless of the underlying system:

| class BaseConnector(ABC): """All connectors implement this interface.""" @abstractmethod async def authenticate(self, credentials: dict) -> AuthSession: """Establish authenticated session. Handle OAuth, token refresh, etc.""" @abstractmethod async def initial_load(self, object_types: list[str]) -> AsyncIterator[RawRecord]: """Pull all records for specified object types. Paginated, streaming.""" @abstractmethod async def incremental_sync(self, object_types: list[str], since: datetime) -> AsyncIterator[RawRecord]: """Pull records modified since the given timestamp.""" @abstractmethod async def write_back(self, records: list[OutboundRecord]) -> WriteResult: """Push records back to the source system. Full product only.""" @abstractmethod def get_schema(self) -> SourceSchema: """Return the source system's field schema for field mapping UI.""" class CsvConnector(BaseConnector): """MVP connector. Parses uploaded CSV/Excel files.""" async def authenticate(self, credentials): return NoAuthSession()  # No auth needed for file uploads async def initial_load(self, object_types): # Parse uploaded file with pandas # Auto-detect delimiter, encoding, header row # Yield one RawRecord per row df = pd.read_csv(self.file_path) if self.is_csv else pd.read_excel(self.file_path) for _, row in df.iterrows(): yield RawRecord(source='csv', object_type=self.declared_type, fields=row.to_dict(), external_id=None) async def incremental_sync(self, object_types, since): # CSV has no incremental sync — re-upload replaces previous return self.initial_load(object_types) |
| --- |

The key insight: whether data comes from a CSV upload or a Salesforce API call, it exits the connector as the same RawRecord format. Everything downstream (mapping, transform, sync) is connector-agnostic.

### 4.2.3 Auth and Credential Store

For CRM connectors (full product), OAuth tokens and API credentials must be stored securely per tenant:

OAuth Token Manager: Handles the full OAuth 2.0 flow: redirect to CRM authorization page, exchange code for tokens, store access_token + refresh_token, auto-refresh before expiry. Each tenant has their own OAuth grant — Tenant A’s Salesforce token cannot access Tenant B’s Salesforce org.

Credential Vault: All tokens and API keys are encrypted at rest using Azure Key Vault or application-level AES-256 encryption. Never stored in plaintext in PostgreSQL. The vault supports: OAuth2 tokens (access + refresh + expiry), API keys, Basic auth credentials (username/password), and custom header tokens.

Token refresh lifecycle: A background job checks token expiry every 5 minutes. If a token expires within 10 minutes, it is proactively refreshed. If refresh fails (revoked access), the sync is paused and the admin is notified: ‘Your Salesforce connection needs to be re-authorized.’

| DECISION: MVP Authentication Decided: No OAuth for MVP. CSV upload requires no authentication. Rationale: OAuth2 flows with each CRM require: redirect URI registration, token storage, refresh logic, error handling for revoked access. This is 2-3 weeks of engineering per CRM. CSV upload eliminates all of this. The tenant exports from their CRM, uploads to our Admin Portal, and we ingest. |
| --- |

## 4.3 Field Mapping Engine

| Why Field Mapping Exists Every CRM and every CSV uses different field names. Salesforce calls it ‘Account.Industry’, HubSpot calls it ‘company.industry_type’, a CSV column might say ‘cust_industry’ or ‘Customer Industry’ or ‘INDUSTRY_CODE’. Our platform needs a consistent internal schema. The Field Mapper translates any tenant’s field names to our canonical platform fields. Without this layer, every downstream module would need to handle N different schemas. |
| --- |

### 4.3.1 Platform Canonical Schema

This is our internal data model that ALL connectors map into. Regardless of source, data arrives in this shape:

| Object | Canonical Field | Type | Required? | Description |
| --- | --- | --- | --- | --- |
| Account | external_id | string | Recommended | Original ID in source system (Salesforce ID, HubSpot ID). Used for dedup and sync. |
| Account | company_name | string | YES | Account / company name |
| Account | industry | enum | YES | Normalized industry classification |
| Account | segment | enum | No | smb / mid_market / enterprise. Computed if not provided. |
| Account | annual_revenue | float | No | Annual revenue in USD (or tenant currency) |
| Account | employee_count | int | No | Number of employees |
| Account | website | string | No | Company website URL |
| Account | address | object | No | {street, city, state, zip, country} |
| Account | account_owner_email | string | Recommended | The rep assigned to this account. Used for rep → account mapping. |
| Account | lifecycle_stage | enum | No | customer / prospect / churned / partner |
| Account | created_date | datetime | No | When the account was created in the source system |
| Contact | external_id | string | Recommended | Source system contact ID |
| Contact | account_external_id | string | YES | Links contact to account |
| Contact | first_name | string | YES | Contact first name |
| Contact | last_name | string | YES | Contact last name |
| Contact | email | string | Recommended | Contact email |
| Contact | title | string | No | Job title |
| Contact | role | enum | No | decision_maker / influencer / user / champion / blocker |
| Contact | phone | string | No | Phone number (normalized to E.164) |
| Transaction | external_id | string | Recommended | Source system line item / transaction ID |
| Transaction | account_external_id | string | YES | Links transaction to account |
| Transaction | product_sku | string | YES | Product identifier (must match product catalog SKU) |
| Transaction | product_name | string | Recommended | Product name (used for fuzzy match if SKU missing) |
| Transaction | amount | float | Recommended | Transaction value |
| Transaction | quantity | int | No | Units purchased |
| Transaction | transaction_date | datetime | YES | When the transaction occurred |
| Transaction | transaction_type | enum | No | purchase / renewal / upgrade / return |

### 4.3.2 The Mapping Process

Field mapping happens BEFORE data enters the transform/normalize pipeline. It is the first processing step after raw records leave the connector:

Step 1 — Source schema discovery: The connector provides its field schema. For CSV, this is the column headers. For Salesforce, this is the object metadata API (describe call). The mapping UI shows all available source fields with sample values.

Step 2 — Auto-suggest (full product): The system suggests mappings using: column name similarity (Levenshtein distance between source field name and canonical field name), data pattern matching (a column with email-format values maps to email), sample value analysis (values like ‘Manufacturing’, ‘Healthcare’ suggest industry), and data type inference (numeric columns suggest revenue/amount fields).

Step 3 — Admin review and confirmation: The admin sees a mapping screen: left column shows source fields with sample values, right column shows canonical fields with dropdowns. Auto-suggested mappings are pre-filled. The admin confirms, corrects, or leaves unmapped fields as ‘skip’. Required fields that are unmapped show a red warning.

Step 4 — Mapping persistence: The confirmed mapping is saved as a tenant-scoped configuration in PostgreSQL. It is reused for every subsequent sync from the same source — the admin does not re-map every time.

| -- Field mapping configuration table CREATE TABLE field_mappings ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id), source_type VARCHAR(50) NOT NULL,  -- 'csv_accounts', 'salesforce_account', etc. source_field VARCHAR(255) NOT NULL, canonical_field VARCHAR(255) NOT NULL, transform_rule VARCHAR(100),  -- optional: 'lowercase', 'date_parse', 'enum_map' transform_config JSONB,  -- e.g. {"map": {"Mfg": "manufacturing", "Tech": "technology"}} created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(tenant_id, source_type, source_field) ); -- Example rows for a CSV import: -- ('tenant-1', 'csv_accounts', 'Customer Name',  'company_name', null, null) -- ('tenant-1', 'csv_accounts', 'Industry Code',  'industry', 'enum_map', --  '{"map": {"MFG": "manufacturing", "HC": "healthcare", "FIN": "finance"}}') -- ('tenant-1', 'csv_accounts', 'Annual Rev ($)', 'annual_revenue', 'currency_parse', null) |
| --- |

### 4.3.3 Auto-Suggest — ML-Based Field Matching

Auto-suggest is FULL PRODUCT, not MVP. In MVP, field mapping is manual during onboarding (you configure it for each tenant).

Auto-suggest scoring model:

| Signal | Weight | How It Works | Example |
| --- | --- | --- | --- |
| Column name similarity | 40% | Levenshtein distance / token overlap between source field name and canonical field name | 'Customer_Name' → company_name (high similarity). 'Acct Industry' → industry (token match). |
| Data pattern matching | 25% | Regex patterns on sample values to identify data types | Values matching email regex → maps to email. Values matching phone regex → maps to phone. Values like '$45,000' → maps to amount/revenue. |
| Value set matching | 20% | Compare unique values against known enum sets | Values {Manufacturing, Healthcare, Finance} overlap with our industry enum → maps to industry. Values {SMB, Mid-Market, Enterprise} overlap with segment enum. |
| Positional heuristics | 15% | Common CSV column patterns: first column is often name/ID, last columns are often dates | First text column often maps to company_name. Columns after amount often map to date. |

The auto-suggest confidence threshold is 70%: suggestions above 70% are pre-filled in the mapping UI. Below 70%, the field is shown as ‘unmapped — please review.’ The admin can always override any suggestion.

| DECISION: Auto-Suggest Timing Decided: Full product only. MVP uses manual field mapping configured during white-glove onboarding. Rationale: Auto-suggest needs: a similarity scoring model, sample value analysis, regex pattern library, and a mapping UI with confidence indicators. This is a 2-week feature that helps at scale (100+ tenants self-onboarding) but is unnecessary when you are manually setting up 2-3 tenants. Build it when the manual process becomes a bottleneck. |
| --- |

## 4.4 Transform & Normalize

After field mapping, data enters the transform pipeline. This is where messy, inconsistent real-world data becomes clean, uniform, enriched records ready for the platform.

### 4.4.1 Data Normalizer

The normalizer enforces consistent formats regardless of source system. Every field type has normalization rules:

| Field Type | Input Variations | Normalized Output | Rule |
| --- | --- | --- | --- |
| Dates | '02/28/2026', '2026-02-28', 'Feb 28, 2026', '28-Feb-26', '1709164800' (epoch) | ISO 8601: '2026-02-28T00:00:00Z' | dateutil.parser with fallback patterns. Ambiguous dates (01/02/2026) default to MM/DD/YYYY for US tenants, DD/MM/YYYY for non-US. |
| Currency | '$45,000', '45000', 'USD 45,000', '45K', '₹45,000', '45,000.00' | {amount: 45000.0, currency: 'USD'} | Strip currency symbols, parse multipliers (K/M), remove commas. Currency inferred from tenant config or symbol. |
| Industry enums | 'Manufacturing', 'Mfg', 'MANUFACTURING', 'manufacturing/industrial', 'MFG-001' | 'manufacturing' | Lowercase + tenant-configurable synonym map. If no match, flag as 'other' and alert admin. |
| Segment enums | 'SMB', 'Small Business', 'small', '<100 employees', 'S', 'Tier 3' | 'smb' | Synonym map + fallback to computed segment based on revenue/employee thresholds. |
| Phone numbers | '(555) 123-4567', '555.123.4567', '+15551234567', '555-123-4567 ext 200' | E.164: '+15551234567' | phonenumbers library. Country code from tenant config. Extensions stored separately. |
| Email addresses | 'John@ACME.com', ' john@acme.com ', 'JOHN@acme.com' | 'john@acme.com' | Lowercase, strip whitespace. Basic format validation (contains @, valid domain). |
| Boolean values | 'Yes', 'Y', 'true', '1', 'Active', 'X' | true | Configurable truthy/falsy value sets per tenant. |
| Null handling | '', 'N/A', 'null', '-', 'none', 'n/a', '#N/A', 'NA' | null (Python None) | Configurable null sentinel list. All variants become proper null. |
| Company names | 'Acme Corp.', 'ACME CORPORATION', 'acme corp', 'Acme Corp., Inc.' | 'Acme Corp' (title case, stripped suffixes) | Title case normalization. Optional suffix stripping (Inc., LLC, Corp., Ltd.) for matching purposes. Original preserved for display. |

Normalization rules are tenant-configurable. An insurance tenant might have custom industry enums (Personal Lines, Commercial Lines, Specialty) that differ from a manufacturing distributor’s enums (Automotive, Aerospace, Electronics). The normalizer supports per-tenant synonym maps stored in the field_mappings table’s transform_config column.

### 4.4.2 Deduplication

Deduplication detects when the same real-world entity (account, contact, product) appears multiple times in the import data and merges them into a single record.

When does it happen? During sync, BEFORE records are committed to the platform database. Every incoming record is checked against existing records.

Three-tier matching strategy:

Tier 1 — Exact match on external ID: If the incoming record has the same Salesforce ID or HubSpot ID as an existing record, it is the same entity. This is a guaranteed match — update the existing record. This covers 90%+ of cases for CRM-synced data.

Tier 2 — Deterministic match on business keys: No external ID (common with CSV imports). Match on: exact domain match (acme.com), exact email match for contacts, exact SKU match for products. If any business key matches exactly, treat as the same entity.

Tier 3 — Fuzzy match (configurable): No external ID, no exact business key match. Use fuzzy string matching on company_name (Levenshtein distance < 3 or token overlap > 80%), combined with secondary signals: same city, same phone area code, similar revenue range. Fuzzy matches are flagged as ‘probable duplicate — review’ rather than auto-merged.

Merge strategy when a duplicate is confirmed:

Newer record wins for scalar fields: Name, address, phone, industry — assume the latest data is most accurate.

Arrays merge (union): Products owned, contacts associated — combine both sets without duplicates.

Financial values take latest: Revenue, deal size — most recent value wins.

Platform-generated data is never overwritten: Graph edges, AI-extracted intelligence, enrichment scores — these are our data, not the CRM’s.

Conflict resolution log: Every merge records what changed, from what source, at what time. Audit trail for data lineage.

| async def deduplicate_record(tenant_id, incoming_record, object_type): # Tier 1: Exact external ID match if incoming_record.external_id: existing = await db.find_by_external_id( tenant_id, object_type, incoming_record.external_id) if existing: return MergeAction(type='update', existing_id=existing.id, confidence=1.0) # Tier 2: Business key match if object_type == 'account' and incoming_record.domain: existing = await db.find_by_domain(tenant_id, incoming_record.domain) if existing: return MergeAction(type='update', existing_id=existing.id, confidence=0.95) # Tier 3: Fuzzy match (configurable, can be disabled) if tenant_config.dedup_fuzzy_enabled: candidates = await db.find_fuzzy_matches( tenant_id, object_type, incoming_record.company_name, threshold=tenant_config.dedup_threshold) if candidates: best = candidates[0] if best.similarity > 0.85: return MergeAction(type='update', existing_id=best.id, confidence=best.similarity) else: return MergeAction(type='review', candidates=candidates, confidence=best.similarity) # No match found — new record return MergeAction(type='insert', confidence=1.0) |
| --- |

| DECISION: Fuzzy Dedup in MVP Decided: Disabled by default. MVP uses exact external ID match (Tier 1) and exact business key match (Tier 2) only. Fuzzy matching is configurable post-MVP. Rationale: Fuzzy matching requires tuning (threshold too low = false negatives, too high = false merges). With CSV imports in MVP, the data quality is often cleaner than real-time CRM sync because the tenant curates their export. Enable fuzzy matching when CRM connectors bring in messier, real-time data. |
| --- |

### 4.4.3 Data Enricher

The enricher adds computed fields that do not exist in the source data but are critical for agent intelligence:

| Computed Field | Formula / Logic | Stored On | Used By |
| --- | --- | --- | --- |
| total_lifetime_value | SUM(amount) across all transactions for this account | account_enrichment table | Agents (account sizing), Admin Portal (account tier) |
| products_owned_count | COUNT(DISTINCT product_sku) for this account | account_enrichment | Cross-sell agent (whitespace assessment) |
| categories_owned | DISTINCT product categories from owned products | account_enrichment | Agents (gap analysis — which categories are missing?) |
| whitespace_percentage | (total_categories - categories_owned) / total_categories | account_enrichment | Cross-sell agent (‘This customer only covers 30% of your catalog’) |
| months_since_last_purchase | DATEDIFF(NOW(), MAX(transaction_date)) | account_enrichment | Win-back agent (stale accounts), Account Overview plugin |
| average_deal_size | AVG(amount) for this account | account_enrichment | Agents (setting expectations), Customer Patterns |
| purchase_frequency | COUNT(transactions) / months_active | account_enrichment | Trend analysis, renewal agent |
| yoy_revenue_growth | (this_year_revenue - last_year_revenue) / last_year_revenue | account_enrichment | Win-back (declining accounts), Upsell (growing accounts) |
| computed_segment | CASE: revenue > 10M = enterprise, > 1M = mid_market, else = smb | account_enrichment | Fallback if segment not provided by CRM. Configurable thresholds per tenant. |
| account_health_score | Composite: recency (40%) + frequency (30%) + monetary (30%) | account_enrichment | Account prioritization, rep dashboard, Background Enrichment agent |
| cross_sell_opportunity_count | COUNT of graph edges from owned products → unowned products with confidence > 0.3 | account_enrichment | Cross-sell agent (opportunity sizing), Account Overview |
| contract_expiry_days | DATEDIFF(contract_end_date, NOW()) | account_enrichment | Renewal agent (upcoming expirations) |

Enrichment is recomputed after every sync. It runs as a batch job: pull all accounts for the tenant, compute all fields, upsert into account_enrichment table. The enricher is idempotent — running it twice produces the same results.

### 4.4.4 Aggregations

Aggregations are roll-ups that go beyond individual accounts to produce segment-level and tenant-level intelligence:

Segment-level benchmarks: ‘Manufacturing mid-market customers buy an average of 12 products. This customer has 4 — significantly below average.’ Computed as AVG(products_owned_count) GROUP BY industry, segment. Stored in segment_benchmarks table. Used by agents to contextualize recommendations.

Tenant-level statistics: ‘Across all your customers, average product count is 8, median deal size is $25K, top category is Networking.’ Used for the Admin Portal dashboard and analytics.

Time-series aggregations: Monthly revenue per account, quarterly product additions, seasonal purchase patterns. Stored as time-series data. Used by trend detection (declining spend → win-back trigger) and the Renewal agent (seasonal patterns).

Product popularity rankings: ‘Top 10 most-sold products, fastest-growing categories, products with declining demand.’ Stored in product_analytics table. Used by agents for trending product recommendations.

| -- Segment benchmarks table CREATE TABLE segment_benchmarks ( tenant_id UUID NOT NULL, industry VARCHAR(100) NOT NULL, segment VARCHAR(50) NOT NULL, avg_products_owned FLOAT, avg_lifetime_value FLOAT, avg_deal_size FLOAT, median_purchase_frequency FLOAT, account_count INT, top_product_categories JSONB,  -- [{category, percentage}] computed_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY (tenant_id, industry, segment) ); -- Recomputed after each sync: -- INSERT INTO segment_benchmarks -- SELECT tenant_id, industry, segment, --   AVG(products_owned_count), AVG(total_lifetime_value), ... -- FROM accounts JOIN account_enrichment USING (id) -- GROUP BY tenant_id, industry, segment -- ON CONFLICT (tenant_id, industry, segment) DO UPDATE SET ...; |
| --- |

## 4.5 Sync Manager

### 4.5.1 Initial Load

The first time a tenant connects their data source, an initial load pulls ALL historical data. This is a one-time bulk operation that establishes the baseline:

For CSV: The entire uploaded file is the initial load. There is no ‘history’ beyond what’s in the file.

For CRM connectors (full product): Pull all Accounts, all Contacts, all Opportunities with line items, going back as far as the CRM has data. For a large tenant, this could be 50,000 accounts with 500,000 transaction line items spanning 5 years.

Why full historical data? The Product Graph Layer 1 (co-occurrence analysis) needs complete purchase history to detect patterns like ‘60% of customers who bought Product A also bought Product B.” Three months of data produces unreliable statistics. Five years of data produces robust patterns.

Initial load is: paginated (never pull 50K records in one API call — use LIMIT/OFFSET or cursor-based pagination), resumable (if it fails at record 30,000, resume from 30,000 not from zero), and idempotent (running it twice produces the same state via upsert logic).

Where initial load data is stored:

| Data | Stored In | Read By |
| --- | --- | --- |
| Account profiles | PostgreSQL: accounts table | State Engine (session state → customer.profile) |
| Contact records | PostgreSQL: contacts table | State Engine (session state → customer.contacts) |
| Transaction/purchase history | PostgreSQL: transactions table | KB Engine (Product Graph Layer 1 co-occurrence analysis) |
| Current product ownership | PostgreSQL: account_products table (materialized from transactions) | State Engine (session state → customer.products), Cross-sell agent |
| Raw import archive | Azure Blob Storage: {tenant_id}/imports/{timestamp}/{filename} | Audit trail, re-processing if needed |

Initial load does NOT re-run every time. After the first load, only incremental sync runs. The only exception: if a tenant re-uploads a CSV, that is treated as a new initial load (full replacement) for that data type.

### 4.5.2 Incremental Sync

After the initial load, only changed records need to be pulled. This is incremental sync, and it uses two mechanisms:

Polling (pull-based): Our system asks the CRM: ‘Give me everything modified since timestamp X.’ Salesforce supports lastModifiedDate on every object. HubSpot supports updatedAt. We store a last_sync_cursor (timestamp) per tenant per object type. Each poll requests records modified after that cursor. After processing, the cursor is advanced to the latest record’s modification timestamp.

Webhooks (push-based): The CRM pushes change events to us in near-real-time. Salesforce Platform Events or Change Data Capture. HubSpot subscription API webhooks. We register a webhook endpoint during connector setup. When a record changes, the CRM POSTs the event to our endpoint within seconds. Near-real-time vs polling’s minutes/hours latency.

What triggers a delta:

Account changes: Name updated, industry reclassified, revenue changed, account owner reassigned, lifecycle stage changed.

Contact changes: New contact added, title changed, email updated, contact deactivated.

Transaction changes: New opportunity closed-won (new purchase), deal amount revised, opportunity closed-lost (removed from pipeline).

Product changes: New product added to catalog, price updated, product discontinued.

We track the CRM’s modification timestamp, not specific field changes. If ANYTHING on a record changed, we pull the entire record and let the normalizer handle it. This is simpler and more reliable than tracking individual field-level changes.

| -- Sync cursor tracking CREATE TABLE sync_cursors ( tenant_id UUID NOT NULL, connector_type VARCHAR(50) NOT NULL,  -- 'salesforce', 'hubspot', 'csv' object_type VARCHAR(50) NOT NULL,     -- 'account', 'contact', 'transaction' last_sync_cursor TIMESTAMPTZ, last_sync_status VARCHAR(20),  -- 'success', 'partial_failure', 'failed' records_processed INT DEFAULT 0, records_failed INT DEFAULT 0, last_sync_started_at TIMESTAMPTZ, last_sync_completed_at TIMESTAMPTZ, PRIMARY KEY (tenant_id, connector_type, object_type) ); |
| --- |

### 4.5.3 Scheduler

The scheduler controls WHEN sync operations run. It serves three purposes:

Polling schedule: Webhooks are real-time and need no scheduler. But polling-based sync needs a schedule: ‘Every 15 minutes, check Salesforce for updated accounts.’ ‘Every hour, pull new closed-won opportunities.’ Configurable per tenant per object type. Default: accounts every 30 minutes, transactions every hour.

Batch recomputation schedule: After sync completes, trigger downstream jobs. ‘After the 2 AM sync, re-run Product Graph Layer 1 co-occurrence analysis with new transaction data.’ ‘After any sync, recompute account enrichment fields.’ ‘Every Sunday, recompute segment benchmarks.’

Full reconciliation schedule: Even with incremental sync, drift happens (missed webhooks, API errors). A weekly full reconciliation job compares CRM state against our state and fixes discrepancies. Runs during off-peak hours (e.g., Sunday 3 AM).

| -- Sync schedule configuration CREATE TABLE sync_schedules ( tenant_id UUID NOT NULL, connector_type VARCHAR(50) NOT NULL, object_type VARCHAR(50) NOT NULL, sync_method VARCHAR(20) NOT NULL,  -- 'polling', 'webhook', 'manual' polling_interval_minutes INT,  -- null for webhook/manual cron_expression VARCHAR(100),   -- for batch jobs: '0 2 * * *' = daily at 2 AM enabled BOOLEAN DEFAULT true, last_triggered_at TIMESTAMPTZ, PRIMARY KEY (tenant_id, connector_type, object_type) ); |
| --- |

### 4.5.4 Retry and Error Handling — Dead Letter Queue

Data sync must be resilient. A single bad record must not block 499 good records. The retry strategy uses a Dead Letter Queue (DLQ):

Normal processing: Each record from the connector passes through: mapping → normalization → dedup → enrichment → commit. If all steps succeed, the record is committed to the database.

Record-level failure: If any step fails for a record (malformed date, missing required field, normalization error), the record goes to the Dead Letter Queue instead of blocking the batch. The DLQ entry includes: raw record data, error message, processing step that failed, attempt count, timestamp.

Batch continues: The remaining 499 records continue processing. The batch commits what it can. The sync cursor advances past successfully processed records.

Retry job: A background job runs every 30 minutes. It pulls records from the DLQ and retries processing. Transient errors (API timeout, rate limit) often succeed on retry. Permanent errors (truly malformed data) fail again.

Escalation: After N retry attempts (configurable, default 5), the record is marked permanently_failed. An alert is raised to the admin: ‘3 records from your import couldn’t be processed. Review them in Settings > Data > Failed Records.’ The admin can: fix the source data and re-trigger, manually skip the records, or adjust mapping rules if the error is systemic.

| CREATE TABLE dead_letter_queue ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL, connector_type VARCHAR(50) NOT NULL, object_type VARCHAR(50) NOT NULL, raw_record JSONB NOT NULL,       -- The original record that failed error_message TEXT NOT NULL, error_step VARCHAR(50),           -- 'mapping', 'normalization', 'dedup', 'enrichment' attempt_count INT DEFAULT 1, max_attempts INT DEFAULT 5, status VARCHAR(20) DEFAULT 'pending',  -- 'pending', 'retrying', 'permanently_failed' first_failed_at TIMESTAMPTZ DEFAULT NOW(), last_retried_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ ); |
| --- |

## 4.6 Platform Targets — Where Data Goes

After processing, clean data is delivered to two platform modules:

### 4.6.1 State Engine (Module 1)

The State Engine receives account context that powers every conversation:

| What Gets Delivered | State Engine Location | How Agents Use It |
| --- | --- | --- |
| Account profile (name, industry, segment, revenue, employee count) | customer.profile in session state | Agents contextualize discovery questions: 'As a mid-market manufacturing company...' |
| Contact records (names, titles, roles, decision-maker flags) | customer.contacts in session state | Email generator uses contact names/titles. Agents reference decision-makers. |
| Current product ownership (list of products this customer has now) | customer.products[] in session state | Cross-sell: 'They have a firewall but no monitoring.' Upsell: 'They have Basic tier.' |
| Enrichment data (lifetime value, whitespace %, health score, growth rate) | customer.enrichment in session state | Agents use for sizing: 'This $2M account only covers 30% of your catalog.' |
| Account owner mapping (which rep owns which accounts) | Used during session initialization | When Rep Sarah logs in and selects an account, the system verifies she owns it. |

### 4.6.2 KB Engine (Module 3)

The KB Engine receives product and purchase data:

| What Gets Delivered | KB Engine Location | How It’s Used |
| --- | --- | --- |
| Product catalog (SKUs, names, descriptions, categories, pricing, features) | PostgreSQL: products table + Foundry IQ product-descriptions knowledge source | Direct product lookup by SKU/category. Semantic search for product matching. Agent recommendations reference specific products. |
| Transaction history (full purchase records) | PostgreSQL: transactions table → Product Graph Layer 1 builder | Co-occurrence analysis: 'Products frequently bought together.' Sequential pattern mining. Upgrade detection. This is the raw material for the Product Relationship Graph. |
| Account-product mapping (current ownership per account) | PostgreSQL: account_products materialized view | Graph traversal starts from owned products: 'Given they have Product A, what should they also have?' |

## 4.7 Outbound — Write-Back Service (Full Product)

| Full Product Only Write-back to CRM is NOT in MVP. In MVP, recommendations and conversations exist only within our platform. The rep can manually create opportunities in their CRM based on AI recommendations. Write-back is Phase 2 and depends on having CRM connectors established (which are also Phase 2). |
| --- |

### 4.7.1 What Gets Written Back

| Write-Back Type | When It Triggers | What’s Created in CRM | Fields Populated |
| --- | --- | --- | --- |
| Create Opportunity | Rep accepts a recommendation and clicks 'Create in CRM' | New Opportunity (Salesforce) or Deal (HubSpot) | Account: linked to customer. Product: recommended product. Amount: estimated value from recommendation. Stage: 'Qualification'. Source: 'AI Platform - Cross-Sell'. Notes: discovery context + talking points. |
| Log Activity | Every AI conversation session that produces a recommendation | Activity / Task record linked to the account | Subject: 'AI Sales Conversation - Cross-Sell Discovery'. Description: summary of discovery + recommendations made. Date: session timestamp. Owner: the rep. |
| Update Account Fields | Enrichment computes new values (optional, configurable) | Updated fields on existing Account record | Custom fields like 'AI_Whitespace_Score__c', 'AI_Health_Score__c', 'AI_Last_Conversation__c'. Only if the tenant has configured these custom fields. |
| Create Follow-Up Task | AI suggests a follow-up action and rep confirms | Task record linked to account + contact | Subject: 'Follow up on [product] recommendation'. Due date: AI-suggested timing. Priority: based on opportunity score. Assigned to: the rep. |

### 4.7.2 Outbound Field Mapping

Write-back requires reverse field mapping: our internal fields must be translated back to the CRM’s field names. Salesforce’s Opportunity object has different field names than HubSpot’s Deal object:

| # Outbound field mapping example outbound_mapping = { 'salesforce': { 'opportunity': { 'account_id': 'AccountId', 'product_name': 'Product__c',       # Custom field 'estimated_value': 'Amount', 'stage': 'StageName', 'source': 'LeadSource', 'notes': 'Description', 'ai_recommendation_id': 'AI_Rec_ID__c',  # Custom field for tracking } }, 'hubspot': { 'deal': { 'account_id': 'associations.company', 'product_name': 'dealname',  # Prefixed with product name 'estimated_value': 'amount', 'stage': 'dealstage', 'source': 'hs_analytics_source', 'notes': 'description', } } } |
| --- |

Outbound mapping is configured during CRM connector setup (Phase 2). The admin maps which CRM fields our write-back should populate. Custom fields (like AI_Rec_ID__c) must be created in the CRM first by the tenant’s Salesforce admin.

### 4.7.3 Write-Back Safety

Writing to a tenant’s CRM is a high-stakes operation. Safeguards:

Explicit confirmation required: Write-back never happens automatically. The rep must click ‘Create in CRM’ and confirm. No background auto-creation of opportunities.

Idempotent writes: If the write fails and retries, it must not create duplicate opportunities. Every write-back record includes our internal recommendation_id. Before creating, check if an opportunity with that recommendation_id already exists.

Audit trail: Every write-back is logged: what was written, to which CRM object, by which rep, at what time, success/failure. Stored in write_back_log table.

Rollback capability: If a write-back creates something wrong, the admin can see the write-back log and manually delete the CRM record. Automatic rollback is not supported (CRM APIs don’t support transactions).

## 4.8 Complete Data Pipeline Flow

End-to-end flow from source to platform:

Step 1 — Source: Tenant uploads CSV (MVP) or connector pulls from CRM (full product). Raw records exit the connector as RawRecord objects.

Step 2 — Field Mapping: RawRecord fields are translated from source names to canonical platform names using the tenant’s saved mapping configuration.

Step 3 — Normalization: Dates, currencies, enums, phone numbers, emails, booleans, and nulls are normalized to consistent formats.

Step 4 — Validation: Required fields are checked. Data types are validated. Invalid records are sent to the DLQ.

Step 5 — Deduplication: Each record is checked against existing data. Duplicates are merged; new records are inserted.

Step 6 — Commit: Clean records are written to PostgreSQL (accounts, contacts, transactions, account_products tables).

Step 7 — Enrichment: Computed fields (lifetime value, whitespace, health score, growth) are calculated and stored in account_enrichment.

Step 8 — Aggregation: Segment benchmarks and tenant-level statistics are recomputed.

Step 9 — Downstream triggers: Notify KB Engine to re-run Product Graph Layer 1 if transaction data changed. Invalidate Redis cache for affected accounts. Notify State Engine that account data has been refreshed.

| async def run_sync_pipeline(tenant_id, connector, object_type, sync_mode): mapping = await load_field_mapping(tenant_id, connector.type, object_type) stats = SyncStats() # Choose initial_load or incremental_sync if sync_mode == 'initial': records = connector.initial_load([object_type]) else: cursor = await get_sync_cursor(tenant_id, connector.type, object_type) records = connector.incremental_sync([object_type], since=cursor) async for raw_record in records: try: mapped = apply_field_mapping(raw_record, mapping)        # Step 2 normalized = normalize_record(mapped, tenant_config)      # Step 3 validated = validate_record(normalized, object_type)      # Step 4 dedup_action = await deduplicate_record( tenant_id, validated, object_type)                    # Step 5 await commit_record(tenant_id, validated, dedup_action)   # Step 6 stats.success += 1 except ProcessingError as e: await send_to_dlq(tenant_id, raw_record, e)              # DLQ stats.failed += 1 # Post-sync batch operations await recompute_enrichment(tenant_id)                             # Step 7 await recompute_aggregations(tenant_id)                           # Step 8 await trigger_downstream_jobs(tenant_id, object_type)             # Step 9 await update_sync_cursor(tenant_id, connector.type, object_type)  # Advance cursor return stats |
| --- |

## 4.9 Database Schemas

| -- Core data tables populated by Module 4 CREATE TABLE accounts ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id), external_id VARCHAR(255), company_name VARCHAR(500) NOT NULL, industry VARCHAR(100), segment VARCHAR(50), annual_revenue FLOAT, employee_count INT, website VARCHAR(500), address JSONB, account_owner_email VARCHAR(255), lifecycle_stage VARCHAR(50) DEFAULT 'customer', source_system VARCHAR(50),  -- 'csv', 'salesforce', 'hubspot' raw_data JSONB,  -- Original unmapped record for audit created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(tenant_id, external_id) ); CREATE TABLE contacts ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL, account_id UUID REFERENCES accounts(id), external_id VARCHAR(255), first_name VARCHAR(255) NOT NULL, last_name VARCHAR(255) NOT NULL, email VARCHAR(255), phone VARCHAR(50), title VARCHAR(255), role VARCHAR(50),  -- decision_maker, influencer, user, champion, blocker is_primary BOOLEAN DEFAULT false, source_system VARCHAR(50), created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW() ); CREATE TABLE transactions ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL, account_id UUID REFERENCES accounts(id), external_id VARCHAR(255), product_sku VARCHAR(100) NOT NULL, product_name VARCHAR(500), amount FLOAT, quantity INT DEFAULT 1, transaction_date TIMESTAMPTZ NOT NULL, transaction_type VARCHAR(50) DEFAULT 'purchase', source_system VARCHAR(50), created_at TIMESTAMPTZ DEFAULT NOW() ); CREATE TABLE account_products ( tenant_id UUID NOT NULL, account_id UUID NOT NULL REFERENCES accounts(id), product_sku VARCHAR(100) NOT NULL, product_name VARCHAR(500), first_purchased TIMESTAMPTZ, last_purchased TIMESTAMPTZ, total_spent FLOAT DEFAULT 0, quantity_total INT DEFAULT 0, status VARCHAR(20) DEFAULT 'active',  -- active, expired, churned PRIMARY KEY (tenant_id, account_id, product_sku) ); CREATE TABLE account_enrichment ( account_id UUID PRIMARY KEY REFERENCES accounts(id), tenant_id UUID NOT NULL, total_lifetime_value FLOAT DEFAULT 0, products_owned_count INT DEFAULT 0, categories_owned JSONB DEFAULT '[]', whitespace_percentage FLOAT DEFAULT 0, months_since_last_purchase FLOAT, average_deal_size FLOAT, purchase_frequency FLOAT, yoy_revenue_growth FLOAT, computed_segment VARCHAR(50), account_health_score FLOAT, cross_sell_opportunity_count INT DEFAULT 0, contract_expiry_days INT, enriched_at TIMESTAMPTZ DEFAULT NOW() ); -- Indexes for performance CREATE INDEX idx_accounts_tenant ON accounts(tenant_id); CREATE INDEX idx_accounts_owner ON accounts(tenant_id, account_owner_email); CREATE INDEX idx_transactions_account ON transactions(tenant_id, account_id); CREATE INDEX idx_transactions_date ON transactions(tenant_id, transaction_date DESC); CREATE INDEX idx_account_products_sku ON account_products(tenant_id, product_sku); |
| --- |

## 4.10 MVP Implementation — CSV Import Flow

The MVP experience is deliberately simple. No OAuth, no webhooks, no real-time sync. Just: upload a file, map fields, import.

### 4.10.1 MVP Import Types

| Upload Type | Expected Contents | Destination | Re-Upload Behavior |
| --- | --- | --- | --- |
| Account CSV | Company name, industry, segment, revenue, employee count, rep email, address fields | accounts + contacts tables → State Engine | Full replacement: all previous accounts for this tenant are soft-deleted, new file becomes the source of truth. Enrichment recomputed. |
| Product Catalog CSV | SKU, product name, description, category, price, features | products table → KB Engine (Foundry IQ + Product Graph Layer 2 LLM inference) | Full replacement: previous catalog replaced. Triggers Product Graph Layer 2 re-run. |
| Transaction History CSV | Account identifier, product SKU/name, amount, date, type | transactions table → Product Graph Layer 1 co-occurrence analysis | Full replacement: previous transactions replaced. Triggers Product Graph Layer 1 re-run + enrichment recompute. |

### 4.10.2 MVP Import UX

Step 1: Admin navigates to Settings > Data Import in the Admin Portal.

Step 2: Selects import type (Accounts, Product Catalog, or Transaction History).

Step 3: Drags and drops their CSV/Excel file. System parses header row and shows column preview with sample values.

Step 4: Field mapping screen. Left: detected columns with samples. Right: canonical fields with dropdowns. For first import, admin maps manually. For re-imports, previous mapping is pre-loaded.

Step 5: Click ‘Import.’ System shows progress: ‘Processing... 2,450 / 3,000 records.’

Step 6: Results screen: ‘2,980 records imported successfully. 20 records had issues (view details).’ Failed records shown with error reasons. Admin can download a ‘failed records’ CSV to fix and re-upload.

### 4.10.3 CSV Validation Rules

| Validation | Rule | Error Handling |
| --- | --- | --- |
| File size | Max 50MB per upload | Reject with message: 'File too large. Split into smaller files.' |
| Header row detection | First row must contain column names | If no header detected, prompt: 'Does your file have a header row?' |
| Required fields after mapping | company_name (accounts), product_sku (catalog), account + product_sku + date (transactions) | Show which required fields are unmapped. Block import until resolved. |
| Data type validation | Dates must parse, numbers must be numeric, emails must be valid format | Per-record: send invalid records to DLQ. Per-column: if >50% of a column fails, suggest the mapping is wrong. |
| Duplicate detection within file | Same external_id or same company_name appearing twice in the same CSV | Keep the last occurrence (bottom of file = most recent). Log dedup action. |
| Encoding detection | Auto-detect UTF-8, UTF-16, Latin-1, Windows-1252 | If detection fails, default to UTF-8 and show mojibake warning. |

## 4.11 Nuances, Risks, and Gaps Filled

| Gap Filled: Data Quality Scoring Beyond validation, the import should produce a data quality score per field and per dataset: 'Your account data is 78% complete — industry is missing for 22% of accounts, annual revenue is missing for 45%.' This helps agents make better decisions: if industry is unknown, discovery questions must extract it from conversation. The quality score is visible in the Admin Portal and stored per-import for tracking improvement over time. |
| --- |

| Gap Filled: Account-to-Rep Mapping The account_owner_email field is critical. Without it, the platform cannot determine which accounts a rep can see when they log in. If the CSV lacks rep assignment, ALL accounts are visible to ALL reps (no scoping). In full product with CRM connectors, the Account Owner field maps automatically. For MVP, the CSV template should strongly encourage including the rep assignment column. |
| --- |

| Gap Filled: Product SKU Reconciliation Transaction history references products by SKU. The product catalog defines SKUs. If a transaction references SKU 'FW-5200' but the catalog has 'FW-PA-5200', the join fails silently and that transaction is orphaned. The import pipeline must include a SKU reconciliation step: fuzzy-match transaction SKUs against catalog SKUs, flag mismatches, and allow the admin to create a SKU alias map. |
| --- |

| Gap Filled: Import Ordering Dependency Product catalog MUST be imported BEFORE transaction history. Transactions reference product SKUs which must already exist. Account data MUST be imported BEFORE transactions. Transactions reference accounts. The Admin Portal should enforce or at least warn about this ordering: 'You should import your product catalog first, then accounts, then transaction history.' |
| --- |

| Nuance: CSV Re-Upload Is Destructive Re-uploading a CSV does a full replacement, not an incremental update. If a tenant adds 10 new accounts to their CRM and re-exports, they must export ALL accounts (not just the new 10). This is a limitation of CSV-based integration that CRM connectors solve with incremental sync. The Admin Portal should clearly communicate: 'This will replace all previously imported accounts. Are you sure?' |
| --- |

| Nuance: Rate Limits on CRM APIs (Full Product) Salesforce allows ~100K API calls per 24 hours for Enterprise edition. A large initial load can consume this quickly. The Salesforce connector must use Bulk API 2.0 for initial loads (which doesn’t count against REST API limits) and stay within rate limits for incremental polling. HubSpot has similar limits (100 calls per 10 seconds). The connector framework must implement rate limiting, backoff, and batching. |
| --- |

| Nuance: Multi-Currency Tenants A global tenant may have transactions in USD, EUR, and GBP. The normalizer must handle currency conversion or at minimum normalize to a consistent reporting currency using tenant-configured exchange rates. MVP can assume single currency per tenant. |
| --- |

## 4.12 Key Technical Decisions Summary

| DECISION: CSV-Only for MVP Decided: No live CRM connectors in MVP. CSV/Excel upload only. Rationale: CRM connectors require: OAuth implementation per CRM (2-3 weeks each), webhook infrastructure, API rate limit handling, token refresh lifecycle, and field schema discovery. CSV import is universal (every CRM can export CSV), requires zero authentication code, and can be built in days. This saves 6-8 weeks of engineering. CRM connectors are Phase 2 after MVP validates product-market fit. |
| --- |

| DECISION: Pluggable Connector Architecture Decided: Build the connector interface contract from day one, even though MVP only has the CSV connector. Rationale: The processing pipeline (mapping → transform → sync) must be connector-agnostic from the start. Adding a Salesforce connector in Phase 2 should require zero changes to the mapping engine, normalizer, dedup logic, enrichment, or database schemas. Only the connector itself is new. This prevents a costly re-architecture when CRM connectors arrive. |
| --- |

| DECISION: Full Replacement on CSV Re-Upload Decided: Re-uploading a CSV replaces all previous data of that type for the tenant (not incremental merge). Rationale: Incremental merge with CSV is unreliable: the tenant might have deleted accounts in their CRM that still exist in our system. Full replacement ensures our data matches their current CRM state. The downside (losing platform-generated enrichment) is mitigated by recomputing enrichment after every import. |
| --- |

| DECISION: Enrichment as Post-Sync Batch Job Decided: Compute all enrichment fields as a batch after sync completes, not inline during record processing. Rationale: Enrichment requires aggregation across multiple records (e.g., total_lifetime_value needs all transactions for an account). Computing inline during record-by-record processing would require multiple database queries per record. A batch job after commit is more efficient and ensures consistency. |
| --- |

| DECISION: DLQ with Configurable Retry Decided: Use a Dead Letter Queue with configurable retry count (default 5) rather than fail-fast or skip-silently strategies. Rationale: Fail-fast (stop the entire batch on first error) is too aggressive — one bad record blocks everything. Skip-silently loses data without admin visibility. The DLQ balances both: good records proceed, bad records are captured with full context for debugging, and transient errors get automatic retry. |
| --- |
