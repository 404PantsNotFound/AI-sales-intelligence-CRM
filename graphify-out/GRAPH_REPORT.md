# Graph Report - AI SALES INTELLIGENCE AND CRM SYSTEM  (2026-10-12)

## Corpus Check
- 144 files · ~107,875 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 14 file(s) not represented in the graph (top: .css 7, (none) 5, .example 1)

## Summary
- 2007 nodes · 7381 edges · 71 communities (57 shown, 14 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 371 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ed4a8ab3`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_customers_api.py
- analytics_service.py
- test_google_calendar.py
- customer.js
- agent_action_state.py
- test_agent_actions.py
- test_hitl_tasks.py
- test_ai_intelligence.py
- scheduling_service.py
- test_activity_api.py
- meeting.py
- test_agent_api.py
- registration.js
- User
- test_auth_and_security.py
- schemas/agent.py
- AI Sales CRM
- import_service.py
- api.js
- dashboard.js
- log_database_exception
- activity_service.py
- schemas/__init__.py
- routes/scheduling.py
- SalesAnalyticsInput
- test_intelligence.py
- update_preferences
- hitl_action_registry.py
- APIError
- sqlalchemy_orm
- test_agent_conversations.py
- settings.js
- normalize_utc_datetime
- build_action_proposal_tools
- calendar_sync_service.py
- element
- activity_tools.py
- renderHitlTask
- TestClient
- analytics.js
- GoogleCalendarError
- ai_service.py
- follow_up.py
- test_api.py
- contacts.py
- agent_action_service.py
- test_dashboard_api.py
- update_enquiry
- agent_client
- auth_service.py
- models/__init__.py
- intelligence_service.py
- create_app
- customer_service.py
- reject_explicit_nulls
- create_agent() (LangChain)
- AgentToolContext
- FakeGoogle
- RegistrationInput
- Settings
- typing
- InMemorySlidingWindowRateLimiter
- test_module_endpoint
- sqlalchemy
- api_error_handler

## God Nodes (most connected - your core abstractions)
1. `APIError` - 182 edges
2. `User` - 60 edges
3. `log_database_exception()` - 55 edges
4. `rollback_failed_transaction()` - 51 edges
5. `Meeting` - 51 edges
6. `Customer` - 50 edges
7. `normalize_utc_datetime()` - 50 edges
8. `element()` - 48 edges
9. `Base` - 47 edges
10. `FollowUp` - 42 edges

## Surprising Connections (you probably didn't know these)
- `Shared activity scheduling` --references--> `schedule_call()`  [INFERRED]
  docs/scheduling.md → app/agent/tools/write_tools.py
- `Database setup and migrations` --references--> `get_db()`  [INFERRED]
  README.md → app/database/connection.py
- `Database setup and migrations` --references--> `check_database_connection()`  [INFERRED]
  README.md → app/database/health.py
- `test_scheduling_with_lookups_terminates_within_default_step_limit()` --uses--> `Settings`  [INFERRED]
  tests/test_agent_actions.py → app/core/config.py
- `test_schedule_conflict_has_timezone_aware_suggestions()` --uses--> `APIError`  [INFERRED]
  tests/test_activity_api.py → app/core/exceptions.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **SalesDesk shared nav shell (theme-init + style/theme CSS + nav)** — app_frontend_analytics, app_frontend_customer_registration, app_frontend_customer, app_frontend_index, app_frontend_login [EXTRACTED 1.00]
- **Customer onboarding: register -> profile -> activity** — app_frontend_customer_registration_registration_form, app_frontend_customer_registration_registration_success, app_frontend_customer_customer_detail, app_frontend_customer_activity_dialog [INFERRED 0.75]

## Communities (71 total, 14 thin omitted)

### Community 0 - "test_customers_api.py"
Cohesion: 0.16
Nodes (29): client(), override_get_db(), imported_workbook(), test_customer_list_paginates_and_limits_page_size(), test_customer_search_filters_results(), test_duplicate_company_is_reused_for_another_customer(), test_duplicate_customer_under_company_returns_conflict(), test_engine() (+21 more)

### Community 1 - "analytics_service.py"
Cohesion: 0.09
Nodes (41): _date_range(), get_activities(), get_attention_queue(), get_customers(), get_enquiries(), get_followups(), get_overview(), get_pipeline() (+33 more)

### Community 2 - "test_google_calendar.py"
Cohesion: 0.30
Nodes (17): CalendarEventLink, event_id_for(), _connect(), _create(), google(), _links(), _status(), test_activities_sync_with_timezone_details_and_reminder_preference() (+9 more)

### Community 3 - "customer.js"
Cohesion: 0.04
Nodes (58): activityDialog, activityDialogTitle, activityFields, activityForm, activityFormError, activityFormPayload(), addFormField(), agentActionLabels (+50 more)

### Community 4 - "agent_action_state.py"
Cohesion: 0.10
Nodes (31): HitlActionProposal, HitlApprovalDecision, HitlExecutionAudit, HitlTask, action_fingerprint(), _expire_incomplete_tasks(), _expire_pending(), _is_agent_action() (+23 more)

### Community 5 - "test_agent_actions.py"
Cohesion: 0.14
Nodes (55): _add_history(), _chat_model(), _configure_action_policy(), _count(), _post_chat(), _post_meeting_clarification(), _post_meeting_proposal(), _schedule_args() (+47 more)

### Community 6 - "test_hitl_tasks.py"
Cohesion: 0.10
Nodes (28): hash_password(), ActionPolicyStore, _is_policy_action(), _is_policy_mode(), set_action_policy(), suggest_actions(), _user_must_provide(), _create_second_user() (+20 more)

### Community 7 - "test_ai_intelligence.py"
Cohesion: 0.21
Nodes (18): _patch_model(), StructuredFakeModel, test_chat_model_factory_uses_pydantic_secret_key(), test_customer_summary_customer_not_found(), test_customer_summary_malformed_ai_output(), test_customer_summary_missing_llm_configuration(), test_customer_summary_model_failure(), test_customer_summary_no_activity_history() (+10 more)

### Community 8 - "scheduling_service.py"
Cohesion: 0.23
Nodes (18): AmbiguousLocalTimeError, NonexistentLocalTimeError, resolve_local_datetime(), timezone_from_name(), _as_utc(), _busy_slots(), current_time(), inspect_availability() (+10 more)

### Community 9 - "test_activity_api.py"
Cohesion: 0.07
Nodes (42): _workspace_schedule_lock_statement(), activity_client(), override_get_db(), activity_engine(), activity_sessions(), test_activity_timeline_combines_sorts_and_filters_records(), test_activity_timeline_empty_date_validation_and_overview(), test_availability_api_returns_conflict_details() (+34 more)

### Community 10 - "meeting.py"
Cohesion: 0.15
Nodes (9): create_meeting(), get_meeting(), list_customer_meetings(), test_meetings(), update_meeting(), MeetingCreate, MeetingInput, MeetingResponse (+1 more)

### Community 11 - "test_agent_api.py"
Cohesion: 0.13
Nodes (19): create_compatible_agent(), _fake_tool_call(), test_all_read_only_tools_return_structured_data(), test_chat_agent_uses_precalculated_analytics_tool(), test_chat_endpoint_handles_missing_openai_configuration(), test_chat_endpoint_rejects_malformed_request(), test_chat_endpoint_rejects_missing_customer(), test_chat_endpoint_reports_safe_tool_failure() (+11 more)

### Community 12 - "registration.js"
Cohesion: 0.15
Nodes (11): buildRegistrationPayload(), customerLink, emailError, emailInput, errorBox, form, formValue(), submitButton (+3 more)

### Community 13 - "User"
Cohesion: 0.12
Nodes (37): cancel_action(), cancel_agent_task(), chat(), confirm_action(), create_agent_task(), customer_summary(), latest_conversation(), list_agent_policies() (+29 more)

### Community 14 - "test_auth_and_security.py"
Cohesion: 0.16
Nodes (23): create_access_token(), authorize_test_client(), test_cross_customer_entity_references_are_rejected(), test_duplicate_email_registration_returns_409(), test_expired_and_invalid_jwt_tokens_return_401(), test_find_customer_cannot_unlock_writes_for_another_customer_when_locked(), test_frontend_auth_and_customer_switch_isolation_assets(), test_global_chat_without_customer_id_discovers_customers_but_blocks_cross_entity_mixing() (+15 more)

### Community 15 - "schemas/agent.py"
Cohesion: 0.13
Nodes (21): AgentChatRequest, AgentChatResponse, AgentConversationMessageResponse, AgentConversationResponse, AgentPolicyUpdateRequest, AgentTaskFormChoice, AgentTaskFormField, AgentTaskOperationInput (+13 more)

### Community 16 - "AI Sales CRM"
Cohesion: 0.14
Nodes (13): graphify Knowledge Graph (project rules), AI Sales CRM, Confirmed CRM actions, CRM assistant, Customer activity API, Customer registration API, Database setup and migrations, Frontend (+5 more)

### Community 17 - "import_service.py"
Cohesion: 0.16
Nodes (12): import_workbook(), _datetime(), _decimal(), import_crm_workbook(), _optional(), _require_columns(), _rows(), _schedule_duration() (+4 more)

### Community 18 - "api.js"
Cohesion: 0.09
Nodes (42): cancelAgentAction(), cancelAgentTask(), chatWithAgent(), checkSchedulingAvailability(), clearAuthSession(), confirmAgentAction(), connectGoogleCalendar(), createAgentTask() (+34 more)

### Community 19 - "dashboard.js"
Cohesion: 0.08
Nodes (47): getAuthenticatedUser(), activities, activityLocalDay(), ATTENTION_REASONS, attentionError, attentionList, attentionRetry, attentionRules (+39 more)

### Community 20 - "log_database_exception"
Cohesion: 0.17
Nodes (25): _integrity_error_metadata(), log_database_exception(), atomic_transaction(), rollback_failed_transaction(), _aware_utc(), move_activity_to_date(), validate_customer_references(), _execute_action() (+17 more)

### Community 21 - "activity_service.py"
Cohesion: 0.12
Nodes (11): get_customer_activity(), get_customer_overview(), ActivityFilter, ActivityResponse, ActivityTimelineResponse, CustomerOverviewResponse, _coerce_activity_date(), get_customer_activity() (+3 more)

### Community 22 - "schemas/__init__.py"
Cohesion: 0.15
Nodes (21): get_customer(), get_customers(), register_customer(), test_customers(), update_customer(), CompanyCreate, CompanyResponse, CompanyUpdate (+13 more)

### Community 23 - "routes/scheduling.py"
Cohesion: 0.13
Nodes (14): check_availability(), list_workspace_activities(), move_workspace_activity(), DashboardActivityListResponse, DashboardActivityRange, DashboardActivityResponse, ActivityMoveRequest, ActivityMoveResponse (+6 more)

### Community 25 - "test_intelligence.py"
Cohesion: 0.20
Nodes (30): _as_utc(), _by_scope(), _earliest(), get_attention_queue(), facts(), _latest(), _call(), _codes() (+22 more)

### Community 26 - "update_preferences"
Cohesion: 0.15
Nodes (12): connect(), _cookie_path(), disconnect(), get_status(), oauth_callback(), retry_failed(), update_preferences(), CalendarConnectResponse (+4 more)

### Community 27 - "hitl_action_registry.py"
Cohesion: 0.16
Nodes (12): ActionInput, ApplyEnrichmentProposal, CompleteFollowupProposal, CreateFollowupProposal, CreateMeetingProposal, CreateMeetingTaskInput, RecordCallResultProposal, ScheduleCallProposal (+4 more)

### Community 28 - "APIError"
Cohesion: 0.22
Nodes (16): APIError, require_customer(), validate_contact_reassignment(), validate_enquiry_reassignment(), _clear_primary_contacts(), create_contact(), get_contact(), list_customer_contacts() (+8 more)

### Community 29 - "sqlalchemy_orm"
Cohesion: 0.09
Nodes (10): create_call(), get_call(), list_customer_calls(), test_calls(), create_followup(), get_followup(), list_customer_followups(), test_followups() (+2 more)

### Community 30 - "test_agent_conversations.py"
Cohesion: 0.12
Nodes (35): ground_meeting_time(), _in_business_hours(), _label(), _mentions(), unverified_recall_reply(), _values(), _book(), _chat() (+27 more)

### Community 31 - "settings.js"
Cohesion: 0.06
Nodes (50): getAuthToken(), logoutUser(), applyPreferences(), brand(), formatDate(), getPreference(), icon(), ICONS (+42 more)

### Community 32 - "normalize_utc_datetime"
Cohesion: 0.10
Nodes (8): update_call(), CallCreate, CallInput, CallResponse, CallUpdate, normalize_utc_datetime(), timezone_name_from_datetime(), validate_timezone_name()

### Community 33 - "build_action_proposal_tools"
Cohesion: 0.25
Nodes (15): build_action_proposal_tools(), apply_enrichment(), _availability_message(), complete_followup(), create_followup(), create_meeting(), _past_message(), _prepare() (+7 more)

### Community 34 - "calendar_sync_service.py"
Cohesion: 0.14
Nodes (24): decode_access_token(), _get_jwt_secret(), GoogleCalendarConnection, _access_token(), complete_authorization(), _decrypt(), _digest(), disconnect() (+16 more)

### Community 35 - "element"
Cohesion: 0.11
Nodes (47): displayValue(), element(), humanize(), initials(), aiErrorMessage(), appendActivities(), appendActivityToolbar(), appendAgentChat() (+39 more)

### Community 36 - "activity_tools.py"
Cohesion: 0.39
Nodes (3): CustomerActivityInput, CustomerIdInput, FindCustomerInput

### Community 37 - "renderHitlTask"
Cohesion: 0.39
Nodes (12): getAgentTasks(), cancelHitlTask(), decideHitlAction(), refreshHitlTasks(), refreshOneHitlTask(), renderHitlTask(), renderHitlTasks(), sendAgentChatMessage() (+4 more)

### Community 38 - "TestClient"
Cohesion: 0.13
Nodes (17): _install_fake_chat_model(), test_ai_provider_quota_vs_generic_error_mapping_and_safe_logging(), test_ai_rate_limiting_blocks_excess_requests_and_returns_429(), test_boundary_monetary_value_is_accepted(), test_confirming_action_whose_underlying_record_became_invalid_marks_action_failed(), test_contacts_enquiries_and_customer_update_crud_endpoints(), test_customer_snapshot_does_not_execute_duplicate_queries_and_preserves_500_on_db_error(), test_database_unique_constraints_enforce_company_and_customer_uniqueness() (+9 more)

### Community 39 - "analytics.js"
Cohesion: 0.07
Nodes (49): Analytics Page, Analytics Filters Form, Analytics KPI Panel, Activity Volume Over Time Chart, Customers by Sales Stage Chart, Enquiries by Priority Chart, Enquiries by Status Chart, Follow-up Status Chart (+41 more)

### Community 40 - "GoogleCalendarError"
Cohesion: 0.19
Nodes (4): _client_secret(), GoogleCalendarClient, GoogleCalendarError, _json()

### Community 41 - "ai_service.py"
Cohesion: 0.14
Nodes (21): invoke_structured_output(), _parse_json_content(), _text_content(), _validate_payload(), _build_activity_timeline_from_records(), _call_crm_tool(), _clean_text(), _customer_snapshot() (+13 more)

### Community 42 - "follow_up.py"
Cohesion: 0.19
Nodes (5): update_followup(), FollowUpCreate, FollowUpInput, FollowUpResponse, FollowUpUpdate

### Community 44 - "contacts.py"
Cohesion: 0.15
Nodes (6): create_contact(), get_contact(), list_customer_contacts(), test_contacts(), update_contact(), ContactUpdate

### Community 45 - "agent_action_service.py"
Cohesion: 0.08
Nodes (28): build_sales_agent(), create_chat_model(), is_quota_or_rate_limit_error(), map_ai_provider_exception(), _configured_secrets(), contains_sensitive_material(), redact_sensitive_text(), AgentActionResult (+20 more)

### Community 46 - "test_dashboard_api.py"
Cohesion: 0.36
Nodes (7): test_move_uses_calendar_timezone_wall_clock_to_select_target_day(), test_moving_activity_rejects_conflicts_and_preserves_database_time(), test_moving_activity_rejects_nonexistent_local_dst_time(), test_moving_calls_and_followups_updates_their_saved_schedule(), test_moving_meeting_persists_new_day_and_keeps_saved_timezone(), test_workspace_activities_are_authenticated_and_shared(), test_workspace_activities_reject_invalid_ranges_and_require_auth()

### Community 47 - "update_enquiry"
Cohesion: 0.22
Nodes (5): create_enquiry(), get_enquiry(), list_customer_enquiries(), test_enquiries(), update_enquiry()

### Community 48 - "agent_client"
Cohesion: 0.24
Nodes (6): agent_client(), override_get_db(), agent_sessions(), ensure_jwt_test_secret(), freeze_scheduling_clock(), override_get_db()

### Community 49 - "auth_service.py"
Cohesion: 0.18
Nodes (11): get_current_user_route(), login_user_route(), register_user_route(), verify_password(), TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse (+3 more)

### Community 50 - "models/__init__.py"
Cohesion: 0.16
Nodes (24): Base, AgentConversation, AgentConversationMessage, Call, Company, Contact, Customer, FollowUp (+16 more)

### Community 51 - "intelligence_service.py"
Cohesion: 0.36
Nodes (4): AttentionItem, AttentionQueue, AttentionReason, SuggestedAction

### Community 52 - "create_app"
Cohesion: 0.20
Nodes (3): check_database_connection(), create_app(), readiness()

### Community 53 - "customer_service.py"
Cohesion: 0.27
Nodes (7): create_customer(), get_customer(), _is_duplicate_customer_integrity_error(), list_customers(), _normalized_name(), update_customer(), verify_email()

### Community 55 - "create_agent() (LangChain)"
Cohesion: 0.18
Nodes (11): AgentMiddleware hooks, create_agent() (LangChain), interrupt_before / checkpointer (user confirmation), LangGraph StateGraph (compiled agent graph), Structured output strategies (ToolStrategy/ProviderStrategy/AutoStrategy), ToolNode, argon2-cffi / PyJWT (auth), langchain / langgraph / langchain-google-genai (+3 more)

### Community 56 - "AgentToolContext"
Cohesion: 0.06
Nodes (37): build_activity_tools(), get_customer_activity(), get_customer_calls(), get_customer_followups(), get_customer_meetings(), get_session(), build_analytics_tools(), get_sales_analytics() (+29 more)

### Community 64 - "RegistrationInput"
Cohesion: 0.25
Nodes (4): InitialSalesEnquiryInput, PrimaryContactInput, RegistrationCompany, RegistrationInput

### Community 65 - "Settings"
Cohesion: 0.25
Nodes (3): Settings, test_production_settings_reject_weak_jwt_and_unsafe_public_registration(), test_docs_and_redoc_disabled_in_production_and_analytics_html_served()

### Community 69 - "sqlalchemy"
Cohesion: 0.06
Nodes (13): upgrade(), upgrade(), _ensure_no_duplicate_records(), upgrade(), upgrade(), upgrade(), upgrade(), downgrade() (+5 more)

## Ambiguous Edges - Review These
- `Frontend` → `argon2-cffi / PyJWT (auth)`  [AMBIGUOUS]
  requirements.txt · relation: conceptually_related_to

## Knowledge Gaps
- **10 isolated node(s):** `LangGraph StateGraph (compiled agent graph)`, `ToolNode`, `AgentMiddleware hooks`, `interrupt_before / checkpointer (user confirmation)`, `Structured output strategies (ToolStrategy/ProviderStrategy/AutoStrategy)` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 414 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `APIError` connect `APIError` to `analytics_service.py`, `agent_action_state.py`, `test_agent_actions.py`, `test_hitl_tasks.py`, `scheduling_service.py`, `test_activity_api.py`, `User`, `schemas/agent.py`, `import_service.py`, `log_database_exception`, `activity_service.py`, `routes/scheduling.py`, `update_preferences`, `hitl_action_registry.py`, `sqlalchemy_orm`, `calendar_sync_service.py`, `activity_tools.py`, `TestClient`, `ai_service.py`, `test_api.py`, `agent_action_service.py`, `auth_service.py`, `models/__init__.py`, `create_app`, `customer_service.py`, `AgentToolContext`, `InMemorySlidingWindowRateLimiter`, `api_error_handler`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Are the 27 inferred relationships involving `APIError` (e.g. with `build_activity_tools()` and `error_output()`) actually correct?**
  _`APIError` has 27 INFERRED edges - model-reasoned connections that need verification._
- **What connects `LangGraph StateGraph (compiled agent graph)`, `ToolNode`, `AgentMiddleware hooks` to the rest of the system?**
  _10 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `analytics_service.py` be split into smaller, more focused modules?**
  _Cohesion score 0.09249786871270248 - nodes in this community are weakly interconnected._
- **Why does `User` connect `User` to `calendar_sync_service.py`, `agent_action_state.py`, `test_agent_actions.py`, `test_hitl_tasks.py`, `meeting.py`, `agent_action_service.py`, `test_auth_and_security.py`, `test_dashboard_api.py`, `auth_service.py`, `models/__init__.py`, `update_preferences`, `sqlalchemy_orm`, `test_agent_conversations.py`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **Should `customer.js` be split into smaller, more focused modules?**
  _Cohesion score 0.04180790960451977 - nodes in this community are weakly interconnected._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `Frontend` and `argon2-cffi / PyJWT (auth)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._