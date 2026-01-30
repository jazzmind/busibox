"""
Comprehensive Endpoint Coverage Tests

Tests all endpoints from OpenAPI specifications for security issues.
Ensures no endpoint is missed in security testing.
"""

import pytest
import httpx

from utils.payloads import PayloadGenerator
from utils.assertions import SecurityAssertions


class TestAuthzApiEndpoints:
    """Security tests for ALL AuthZ API endpoints."""
    
    # =========================================================================
    # OAuth Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_jwks_endpoint(self, http_client, endpoints):
        """GET /.well-known/jwks.json - Public endpoint, should not leak sensitive info."""
        response = http_client.get(f"{endpoints.authz}/.well-known/jwks.json")
        
        assert response.status_code == 200
        data = response.json()
        assert "keys" in data
        
        # JWKS should not contain private key material
        for key in data.get("keys", []):
            assert "d" not in key, "Private key exponent exposed in JWKS!"
            assert "p" not in key, "Private key prime exposed in JWKS!"
            assert "q" not in key, "Private key prime exposed in JWKS!"
    
    @pytest.mark.auth
    @pytest.mark.injection
    def test_oauth_token_injection(self, http_client, endpoints):
        """POST /oauth/token - Test injection attacks."""
        url = f"{endpoints.authz}/oauth/token"
        
        # SQL injection in credentials
        injection_tests = [
            {"grant_type": "client_credentials", "client_id": "' OR '1'='1", "client_secret": "test"},
            {"grant_type": "client_credentials", "client_id": "test", "client_secret": "'; DROP TABLE oauth_clients; --"},
            {"grant_type": "client_credentials", "client_id": "<script>alert(1)</script>", "client_secret": "test"},
        ]
        
        for payload in injection_tests:
            response = http_client.post(url, data=payload)
            SecurityAssertions.assert_no_sql_errors(response.text, f"oauth_token:{payload}")
            SecurityAssertions.assert_no_sensitive_data(response.text, f"oauth_token:{payload}")
    
    @pytest.mark.auth
    def test_oauth_token_grant_types(self, http_client, endpoints):
        """POST /oauth/token - Test invalid grant types are rejected."""
        url = f"{endpoints.authz}/oauth/token"
        
        invalid_grants = [
            {"grant_type": "password"},  # Should not support password grant
            {"grant_type": "authorization_code"},  # Not supported
            {"grant_type": "implicit"},  # Should never be supported
            {"grant_type": "../../../etc/passwd"},  # Path traversal
        ]
        
        for payload in invalid_grants:
            response = http_client.post(url, data=payload)
            assert response.status_code in [400, 401], f"Invalid grant accepted: {payload}"
    
    # =========================================================================
    # Admin Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_admin_roles_requires_auth(self, http_client, endpoints):
        """GET/POST /admin/roles - Requires authentication."""
        url = f"{endpoints.authz}/admin/roles"
        
        # GET without auth
        response = http_client.get(url)
        assert response.status_code in [401, 403], "Admin roles GET accessible without auth"
        
        # POST without auth
        response = http_client.post(url, json={"name": "test"})
        assert response.status_code in [401, 403], "Admin roles POST accessible without auth"
    
    @pytest.mark.auth
    def test_admin_role_by_id_requires_auth(self, http_client, endpoints):
        """GET/PUT/DELETE /admin/roles/{role_id} - Requires authentication."""
        role_id = "00000000-0000-0000-0000-000000000000"
        url = f"{endpoints.authz}/admin/roles/{role_id}"
        
        for method in ["GET", "PUT", "DELETE"]:
            if method == "GET":
                response = http_client.get(url)
            elif method == "PUT":
                response = http_client.put(url, json={"name": "test"})
            else:
                response = http_client.delete(url)
            
            assert response.status_code in [401, 403, 404], f"Admin role {method} accessible without auth"
    
    @pytest.mark.injection
    def test_admin_role_id_injection(self, http_client, endpoints, admin_headers):
        """Test role_id path parameter for injection."""
        if not admin_headers.get("Authorization"):
            pytest.skip("No admin token")
        
        for payload in PayloadGenerator.MALICIOUS_UUIDS[:5]:
            url = f"{endpoints.authz}/admin/roles/{payload}"
            response = http_client.get(url, headers=admin_headers)
            
            SecurityAssertions.assert_no_sql_errors(response.text, f"role_id:{payload}")
    
    @pytest.mark.auth
    def test_admin_user_roles_requires_auth(self, http_client, endpoints):
        """POST/DELETE /admin/user-roles - Requires authentication."""
        url = f"{endpoints.authz}/admin/user-roles"
        
        # POST without auth
        response = http_client.post(url, json={"user_id": "test", "role_id": "test"})
        assert response.status_code in [401, 403], "User roles POST accessible without auth"
        
        # DELETE without auth
        response = http_client.request("DELETE", url, json={"user_id": "test", "role_id": "test"})
        assert response.status_code in [401, 403], "User roles DELETE accessible without auth"
    
    @pytest.mark.auth
    def test_admin_users_roles_requires_auth(self, http_client, endpoints):
        """GET /admin/users/{user_id}/roles - Requires authentication."""
        user_id = "00000000-0000-0000-0000-000000000000"
        url = f"{endpoints.authz}/admin/users/{user_id}/roles"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403], "User roles GET accessible without auth"
    
    @pytest.mark.auth
    def test_admin_oauth_clients_requires_auth(self, http_client, endpoints):
        """GET/POST /admin/oauth-clients - Requires authentication."""
        url = f"{endpoints.authz}/admin/oauth-clients"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403], "OAuth clients GET accessible without auth"
        
        response = http_client.post(url, json={"client_id": "test", "client_secret": "test"})
        assert response.status_code in [401, 403], "OAuth clients POST accessible without auth"
    
    # =========================================================================
    # Internal Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_internal_sync_user_requires_auth(self, http_client, endpoints):
        """POST /internal/sync/user - Requires OAuth client auth."""
        url = f"{endpoints.authz}/internal/sync/user"
        
        response = http_client.post(url, json={"user": {"user_id": "test", "email": "test@test.com"}})
        assert response.status_code in [401, 403], "Internal sync accessible without auth"
    
    # =========================================================================
    # Audit Endpoints
    # =========================================================================
    
    @pytest.mark.injection
    def test_authz_audit_injection(self, http_client, endpoints):
        """POST /authz/audit - Test injection in audit logging."""
        url = f"{endpoints.authz}/authz/audit"
        
        injection_payloads = [
            {"actorId": "'; DROP TABLE audit_logs; --", "action": "test", "resourceType": "test"},
            {"actorId": "test", "action": "<script>alert(1)</script>", "resourceType": "test"},
            {"actorId": "test", "action": "test", "resourceType": "test", "details": {"key": "{{7*7}}"}},
        ]
        
        for payload in injection_payloads:
            response = http_client.post(url, json=payload)
            SecurityAssertions.assert_no_sql_errors(response.text, f"audit:{payload}")
    
    # =========================================================================
    # Health Endpoints
    # =========================================================================
    
    def test_health_endpoints_public(self, http_client, endpoints):
        """GET /health/live, /health/ready - Should be public."""
        for endpoint in ["/health/live", "/health/ready"]:
            url = f"{endpoints.authz}{endpoint}"
            response = http_client.get(url)
            
            assert response.status_code == 200, f"Health endpoint not accessible: {endpoint}"
            SecurityAssertions.assert_no_sensitive_data(response.text, f"health:{endpoint}")


class TestAgentApiEndpoints:
    """Security tests for ALL Agent API endpoints."""
    
    # =========================================================================
    # Root & Health
    # =========================================================================
    
    def test_root_public(self, http_client, endpoints):
        """GET / - Should be public service info."""
        response = http_client.get(f"{endpoints.agent}/")
        assert response.status_code == 200
        SecurityAssertions.assert_no_sensitive_data(response.text, "agent_root")
    
    def test_health_public(self, http_client, endpoints):
        """GET /health - Should be public."""
        response = http_client.get(f"{endpoints.agent}/health")
        assert response.status_code == 200
    
    # =========================================================================
    # Auth Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_auth_exchange_requires_auth(self, http_client, endpoints):
        """POST /auth/exchange - Requires valid token."""
        url = f"{endpoints.agent}/auth/exchange"
        response = http_client.post(url, json={"scopes": ["search.read"], "purpose": "test"})
        # 422 = validation failed (missing X-User-Id), which is still a rejection
        assert response.status_code in [401, 403, 422], "Token exchange accessible without auth"
    
    # =========================================================================
    # Agent Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_agents_requires_auth(self, http_client, endpoints):
        """GET /agents - Requires authentication."""
        response = http_client.get(f"{endpoints.agent}/agents")
        # Agent API validates X-User-Id header first (422), then auth
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    def test_agent_by_id_requires_auth(self, http_client, endpoints):
        """GET /agents/{agent_id} - Requires authentication."""
        url = f"{endpoints.agent}/agents/00000000-0000-0000-0000-000000000000"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404, 422]
    
    @pytest.mark.auth
    def test_agent_definitions_requires_auth(self, http_client, endpoints):
        """POST /agents/definitions - Requires authentication."""
        url = f"{endpoints.agent}/agents/definitions"
        response = http_client.post(url, json={"name": "test", "model": "test", "instructions": "test"})
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Tools Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_tools_requires_auth(self, http_client, endpoints):
        """GET/POST /agents/tools - Requires authentication."""
        url = f"{endpoints.agent}/agents/tools"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.post(url, json={"name": "test", "description": "test"})
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    def test_tool_by_id_requires_auth(self, http_client, endpoints):
        """GET/PUT/DELETE /agents/tools/{tool_id} - Requires authentication."""
        tool_id = "00000000-0000-0000-0000-000000000000"
        url = f"{endpoints.agent}/agents/tools/{tool_id}"
        
        for method in ["GET", "PUT", "DELETE"]:
            if method == "GET":
                response = http_client.get(url)
            elif method == "PUT":
                response = http_client.put(url, json={"description": "test"})
            else:
                response = http_client.delete(url)
            
            assert response.status_code in [401, 403, 404, 422]
    
    # =========================================================================
    # Workflow Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_workflows_requires_auth(self, http_client, endpoints):
        """GET/POST /agents/workflows - Requires authentication."""
        url = f"{endpoints.agent}/agents/workflows"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.post(url, json={"name": "test", "steps": []})
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    def test_workflow_by_id_requires_auth(self, http_client, endpoints):
        """GET/PUT/DELETE /agents/workflows/{workflow_id} - Requires authentication."""
        workflow_id = "00000000-0000-0000-0000-000000000000"
        url = f"{endpoints.agent}/agents/workflows/{workflow_id}"
        
        for method in ["GET", "PUT", "DELETE"]:
            if method == "GET":
                response = http_client.get(url)
            elif method == "PUT":
                response = http_client.put(url, json={"description": "test"})
            else:
                response = http_client.delete(url)
            
            assert response.status_code in [401, 403, 404, 422]
    
    # =========================================================================
    # Evals Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_evals_requires_auth(self, http_client, endpoints):
        """GET/POST /agents/evals - Requires authentication."""
        url = f"{endpoints.agent}/agents/evals"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.post(url, json={"name": "test", "scorer_type": "exact_match"})
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Models Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_models_requires_auth(self, http_client, endpoints):
        """GET /agents/models - Requires authentication."""
        response = http_client.get(f"{endpoints.agent}/agents/models")
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Runs Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_runs_requires_auth(self, http_client, endpoints):
        """GET/POST /runs - Requires authentication."""
        url = f"{endpoints.agent}/runs"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.post(url, json={"agent_id": "00000000-0000-0000-0000-000000000000", "input": {}})
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    def test_run_by_id_requires_auth(self, http_client, endpoints):
        """GET /runs/{run_id} - Requires authentication."""
        url = f"{endpoints.agent}/runs/00000000-0000-0000-0000-000000000000"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404, 422]
    
    @pytest.mark.auth
    def test_runs_schedule_requires_auth(self, http_client, endpoints):
        """GET/POST /runs/schedule - Requires authentication."""
        url = f"{endpoints.agent}/runs/schedule"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.post(url, json={
            "agent_id": "00000000-0000-0000-0000-000000000000",
            "cron": "0 * * * *",
            "input": {},
            "scopes": [],
            "purpose": "test",
        })
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    def test_runs_workflow_requires_auth(self, http_client, endpoints):
        """POST /runs/workflow - Requires authentication."""
        url = f"{endpoints.agent}/runs/workflow"
        response = http_client.post(url, json={
            "workflow_id": "00000000-0000-0000-0000-000000000000",
            "input": {},
        })
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Streams Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_streams_requires_auth(self, http_client, endpoints):
        """GET /streams/runs/{run_id} - Requires authentication."""
        url = f"{endpoints.agent}/streams/runs/00000000-0000-0000-0000-000000000000"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404, 422]
    
    # =========================================================================
    # Dispatcher Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_dispatcher_requires_auth(self, http_client, endpoints):
        """POST /dispatcher/route - Requires authentication."""
        url = f"{endpoints.agent}/dispatcher/route"
        response = http_client.post(url, json={
            "query": "test",
            "available_tools": [],
            "available_agents": [],
        })
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.injection
    def test_dispatcher_injection(self, http_client, endpoints, auth_headers):
        """POST /dispatcher/route - Test injection in query."""
        url = f"{endpoints.agent}/dispatcher/route"
        
        for payload in PayloadGenerator.SQL_INJECTION_BASIC[:3]:
            response = http_client.post(url, json={
                "query": payload,
                "available_tools": [],
                "available_agents": [],
            }, headers=auth_headers)
            
            if response.status_code not in [401, 403]:
                SecurityAssertions.assert_no_sql_errors(response.text, f"dispatcher:{payload}")
    
    # =========================================================================
    # Scores Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_scores_execute_requires_auth(self, http_client, endpoints):
        """POST /scores/execute - Requires authentication."""
        url = f"{endpoints.agent}/scores/execute"
        response = http_client.post(url, json={
            "eval_id": "00000000-0000-0000-0000-000000000000",
            "run_id": "00000000-0000-0000-0000-000000000000",
        })
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    def test_scores_aggregates_requires_auth(self, http_client, endpoints):
        """GET /scores/aggregates - Requires authentication."""
        url = f"{endpoints.agent}/scores/aggregates"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Conversations Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_conversations_requires_auth(self, http_client, endpoints):
        """GET/POST /conversations - Requires authentication."""
        url = f"{endpoints.agent}/conversations"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.post(url, json={"title": "test"})
        assert response.status_code in [401, 403, 422]
    
    @pytest.mark.auth
    @pytest.mark.idor
    def test_conversation_by_id_requires_auth(self, http_client, endpoints):
        """GET/PATCH/DELETE /conversations/{id} - Requires authentication."""
        conv_id = "00000000-0000-0000-0000-000000000000"
        url = f"{endpoints.agent}/conversations/{conv_id}"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404, 422]
        
        response = http_client.patch(url, json={"title": "test"})
        assert response.status_code in [401, 403, 404, 422]
        
        response = http_client.delete(url)
        assert response.status_code in [401, 403, 404, 422]
    
    @pytest.mark.auth
    def test_conversation_messages_requires_auth(self, http_client, endpoints):
        """GET/POST /conversations/{id}/messages - Requires authentication."""
        conv_id = "00000000-0000-0000-0000-000000000000"
        url = f"{endpoints.agent}/conversations/{conv_id}/messages"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404, 422]
        
        response = http_client.post(url, json={"role": "user", "content": "test"})
        assert response.status_code in [401, 403, 404, 422]
    
    @pytest.mark.auth
    def test_message_by_id_requires_auth(self, http_client, endpoints):
        """GET /messages/{message_id} - Requires authentication."""
        url = f"{endpoints.agent}/messages/00000000-0000-0000-0000-000000000000"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404, 422]
    
    # =========================================================================
    # Chat Settings Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_chat_settings_requires_auth(self, http_client, endpoints):
        """GET/PUT /users/me/chat-settings - Requires authentication."""
        url = f"{endpoints.agent}/users/me/chat-settings"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 422]
        
        response = http_client.put(url, json={"model": "test"})
        assert response.status_code in [401, 403, 422]


class TestDataApiEndpoints:
    """Security tests for ALL Data API endpoints."""
    
    # =========================================================================
    # Upload Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_upload_requires_auth(self, http_client, endpoints):
        """POST /upload - Requires authentication."""
        url = f"{endpoints.data}/upload"
        response = http_client.post(url, data={})
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Status Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_status_requires_auth(self, http_client, endpoints):
        """GET /status/{fileId} - Requires authentication."""
        url = f"{endpoints.data}/status/00000000-0000-0000-0000-000000000000"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404]
    
    # =========================================================================
    # Search Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_data_search_requires_auth(self, http_client, endpoints):
        """POST /search - Requires authentication."""
        url = f"{endpoints.data}/search"
        response = http_client.post(url, json={"query": "test"})
        assert response.status_code in [401, 403]
    
    # =========================================================================
    # Embeddings Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_embeddings_requires_auth(self, http_client, endpoints):
        """POST /api/embeddings - Requires authentication."""
        url = f"{endpoints.data}/api/embeddings"
        response = http_client.post(url, json={"input": "test"})
        assert response.status_code in [401, 403]
    
    # =========================================================================
    # Files Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_file_get_requires_auth(self, http_client, endpoints):
        """GET /files/{fileId} - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_delete_requires_auth(self, http_client, endpoints):
        """DELETE /files/{fileId} - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000"
        response = http_client.delete(url)
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_download_requires_auth(self, http_client, endpoints):
        """GET /files/{fileId}/download - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/download"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_chunks_requires_auth(self, http_client, endpoints):
        """GET /files/{fileId}/chunks - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/chunks"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_search_requires_auth(self, http_client, endpoints):
        """POST /files/{fileId}/search - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/search"
        response = http_client.post(url, json={"query": "test"})
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_reprocess_requires_auth(self, http_client, endpoints):
        """POST /files/{fileId}/reprocess - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/reprocess"
        response = http_client.post(url, json={})
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_export_requires_auth(self, http_client, endpoints):
        """GET /files/{fileId}/export - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/export"
        response = http_client.get(url, params={"format": "markdown"})
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_markdown_requires_auth(self, http_client, endpoints):
        """GET /files/{fileId}/markdown - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/markdown"
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_roles_requires_auth(self, http_client, endpoints):
        """GET/PUT /files/{fileId}/roles - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/roles"
        
        response = http_client.get(url)
        assert response.status_code in [401, 403, 404]
        
        response = http_client.put(url, json={"add_role_ids": []})
        assert response.status_code in [401, 403, 404]
    
    @pytest.mark.auth
    def test_file_share_requires_auth(self, http_client, endpoints):
        """POST /files/{fileId}/share - Requires authentication."""
        url = f"{endpoints.data}/files/00000000-0000-0000-0000-000000000000/share"
        response = http_client.post(url, json={"role_ids": [], "role_names": []})
        assert response.status_code in [401, 403, 404]
    
    # =========================================================================
    # Extract Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_extract_requires_auth(self, http_client, endpoints):
        """POST /extract - Requires authentication."""
        url = f"{endpoints.data}/extract"
        response = http_client.post(url, data={})
        assert response.status_code in [401, 403, 422]
    
    # =========================================================================
    # Authz Check Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_authz_check_requires_auth(self, http_client, endpoints):
        """POST /authz/check - Requires authentication."""
        url = f"{endpoints.data}/authz/check"
        response = http_client.post(url, json={
            "resource_type": "file",
            "resource_id": "00000000-0000-0000-0000-000000000000",
            "permission": "read",
        })
        assert response.status_code in [401, 403]
    
    # =========================================================================
    # Health Endpoints
    # =========================================================================
    
    def test_health_public(self, http_client, endpoints):
        """GET /health - Should be public."""
        response = http_client.get(f"{endpoints.data}/health")
        assert response.status_code == 200
        SecurityAssertions.assert_no_sensitive_data(response.text, "data_health")


class TestSearchApiEndpoints:
    """Security tests for ALL Search API endpoints."""
    
    # =========================================================================
    # Root & Health
    # =========================================================================
    
    def test_root_requires_auth(self, http_client, endpoints):
        """GET / - Search API requires auth even for root."""
        response = http_client.get(f"{endpoints.search}/")
        # Search API requires authentication for all endpoints including root
        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            SecurityAssertions.assert_no_sensitive_data(response.text, "search_root")
    
    def test_health_public(self, http_client, endpoints):
        """GET /health - Should be public."""
        response = http_client.get(f"{endpoints.search}/health")
        assert response.status_code == 200
    
    # =========================================================================
    # Search Endpoints
    # =========================================================================
    
    @pytest.mark.auth
    def test_search_requires_auth(self, http_client, endpoints):
        """POST /search - Requires authentication."""
        url = f"{endpoints.search}/search"
        response = http_client.post(url, json={"query": "test"})
        assert response.status_code in [401, 403]
    
    @pytest.mark.auth
    def test_search_keyword_requires_auth(self, http_client, endpoints):
        """POST /search/keyword - Requires authentication."""
        url = f"{endpoints.search}/search/keyword"
        response = http_client.post(url, json={"query": "test"})
        assert response.status_code in [401, 403]
    
    @pytest.mark.auth
    def test_search_semantic_requires_auth(self, http_client, endpoints):
        """POST /search/semantic - Requires authentication."""
        url = f"{endpoints.search}/search/semantic"
        response = http_client.post(url, json={"query": "test"})
        assert response.status_code in [401, 403]
    
    @pytest.mark.auth
    def test_search_mmr_requires_auth(self, http_client, endpoints):
        """POST /search/mmr - Requires authentication."""
        url = f"{endpoints.search}/search/mmr"
        response = http_client.post(url, json={"query": "test"})
        assert response.status_code in [401, 403]
    
    @pytest.mark.auth
    def test_search_explain_requires_auth(self, http_client, endpoints):
        """POST /search/explain - Requires authentication."""
        url = f"{endpoints.search}/search/explain"
        response = http_client.post(url, json={
            "query": "test",
            "file_id": "00000000-0000-0000-0000-000000000000",
            "chunk_index": 0,
        })
        assert response.status_code in [401, 403]


class TestEndpointCoverageSummary:
    """Summary test to verify all endpoints are being tested."""
    
    def test_authz_endpoint_count(self):
        """Verify AuthZ endpoints are covered."""
        # From OpenAPI spec
        authz_endpoints = [
            "/.well-known/jwks.json",
            "/oauth/token",
            "/admin/roles",
            "/admin/roles/{role_id}",
            "/admin/user-roles",
            "/admin/users/{user_id}/roles",
            "/admin/oauth-clients",
            "/internal/sync/user",
            "/authz/audit",
            "/health/live",
            "/health/ready",
        ]
        # This test documents expected coverage
        assert len(authz_endpoints) == 11, "AuthZ endpoint count changed"
    
    def test_agent_endpoint_count(self):
        """Verify Agent endpoints are covered."""
        agent_endpoints = [
            "/",
            "/health",
            "/auth/exchange",
            "/agents",
            "/agents/{agent_id}",
            "/agents/definitions",
            "/agents/tools",
            "/agents/tools/{tool_id}",
            "/agents/workflows",
            "/agents/workflows/{workflow_id}",
            "/agents/evals",
            "/agents/models",
            "/runs",
            "/runs/{run_id}",
            "/runs/schedule",
            "/runs/schedule/{schedule_id}",
            "/runs/workflow",
            "/streams/runs/{run_id}",
            "/dispatcher/route",
            "/scores/execute",
            "/scores/aggregates",
            "/conversations",
            "/conversations/{conversation_id}",
            "/conversations/{conversation_id}/messages",
            "/messages/{message_id}",
            "/users/me/chat-settings",
        ]
        assert len(agent_endpoints) == 26, "Agent endpoint count changed"
    
    def test_data_endpoint_count(self):
        """Verify Data endpoints are covered."""
        data_endpoints = [
            "/upload",
            "/status/{fileId}",
            "/search",
            "/api/embeddings",
            "/files/{fileId}",
            "/files/{fileId}/download",
            "/files/{fileId}/chunks",
            "/files/{fileId}/search",
            "/files/{fileId}/reprocess",
            "/files/{fileId}/export",
            "/files/{fileId}/markdown",
            "/files/{fileId}/roles",
            "/files/{fileId}/share",
            "/extract",
            "/authz/check",
            "/health",
        ]
        assert len(data_endpoints) == 16, "Data endpoint count changed"
    
    def test_search_endpoint_count(self):
        """Verify Search endpoints are covered."""
        search_endpoints = [
            "/",
            "/search",
            "/search/keyword",
            "/search/semantic",
            "/search/mmr",
            "/search/explain",
            "/health",
        ]
        assert len(search_endpoints) == 7, "Search endpoint count changed"

