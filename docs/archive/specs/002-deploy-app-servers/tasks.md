# Tasks: Application Services Deployment

**Input**: Design documents from `/specs/002-deploy-app-servers/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4, US5)
- Include exact file paths in descriptions

## Path Conventions
- Infrastructure: `provision/ansible/roles/`, `provision/pct/`
- Testing: `test-infrastructure.sh`, `docs/testing.md`
- Documentation: `QUICKSTART.md`, `docs/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and directory structure for new Ansible roles

- [x] T001 [P] Create `provision/ansible/roles/nginx/` directory structure (tasks/, templates/, handlers/, vars/)
- [x] T002 [P] Create `provision/ansible/roles/app_deployer/` directory structure (tasks/, templates/, handlers/)
- [x] T003 [P] Create `provision/ansible/roles/secrets/` directory structure (tasks/, vars/)
- [x] T004 [P] Create `provision/ansible/group_vars/` directory for centralized configuration
- [x] T005 [P] Create documentation structure in `specs/002-deploy-app-servers/` (verified complete)

**Checkpoint**: Directory structure ready for role implementation

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core configuration management and validation infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Configuration Schema & Validation

- [x] T006 [Foundational] Create `provision/ansible/group_vars/all.yml` with global variables (domain: ai.localhost, SSL email, network config)
- [x] T007 [Foundational] Create `provision/ansible/group_vars/apps.yml` schema template with application definition fields (name, github_repo, container, port, deploy_path, health_endpoint, routes[], secrets[], env{})
- [x] T008 [Foundational] Implement configuration validation script in `provision/ansible/roles/app_deployer/tasks/validate.yml` (check required fields, duplicate route detection, secret existence validation per FR-006a)

### Secrets Management Infrastructure

- [x] T009 [P] [Foundational] Create Ansible vault structure in `provision/ansible/roles/secrets/vars/vault.yml` with encryption enabled
- [x] T010 [P] [Foundational] Create secrets deployment template in `provision/ansible/roles/secrets/templates/app.env.j2` (environment variable generation from vault)
- [x] T011 [Foundational] Implement secrets validation in `provision/ansible/roles/secrets/tasks/main.yml` (verify all required secrets exist per FR-008)

### Deploywatch Extension Framework

- [x] T012 [P] [Foundational] Create deploywatch app template in `provision/ansible/roles/app_deployer/templates/deploywatch-app.sh.j2` (GitHub release checking, download, deploy, health check, rollback logic per FR-035)
- [x] T013 [P] [Foundational] Create deploywatch orchestrator update in `/srv/deploywatch/deploywatch.sh` (loop over `/srv/deploywatch/apps/*.sh`)
- [x] T014 [Foundational] Implement rollback mechanism in deploywatch template (preserve previous version, restore on failure per clarification)
- [x] T015 [Foundational] Implement database migration detection and execution in deploywatch template (auto-detect migrations, run, rollback on failure per FR-036, FR-037)

**Checkpoint**: Configuration management, secrets, and deploywatch foundations complete - user stories can now proceed

---

## Phase 3: User Story 1 - Agent Server Operational (Priority: P1) 🎯 MVP

**Goal**: Deploy agent-server from GitHub releases via deploywatch, verify internal-only access and connectivity to backend services

**Independent Test**: Deploy agent-server from GitHub releases, verify health endpoints respond, confirm it can connect to PostgreSQL, Milvus, MinIO, and Redis, and validate that it's only accessible from the internal network (not publicly exposed)

### Implementation for User Story 1

- [x] T016 [P] [US1] Add agent-server definition to `provision/ansible/group_vars/apps.yml` (name: agent-server, github_repo: jazzmind/agent-server, container: agent-lxc, IP: 10.96.200.30, port: 8000, routes: [] for internal-only per FR-002)
- [x] T017 [P] [US1] Add agent-server secrets to `provision/ansible/roles/secrets/vars/vault.yml` (database_url, minio_access_key, minio_secret_key, redis_url per FR-005)
- [x] T018 [US1] Implement app_deployer role main task in `provision/ansible/roles/app_deployer/tasks/main.yml` (read apps.yml, loop over applications, generate deploywatch scripts, create .env files)
- [x] T019 [US1] Create deploywatch scripts directory and generate agent-server script in `/srv/deploywatch/apps/agent-server.sh` via Ansible template
- [x] T020 [US1] Deploy agent-server .env file to `/srv/agent/.env` with secrets from vault (owner: root, mode: 0600 per FR-008)
- [x] T021 [US1] Verify agent-server internal-only access by confirming no NGINX routes generated (routes: [] in apps.yml per FR-002)
- [ ] T022 [US1] Test agent-server deployment: trigger deploywatch manually, verify health endpoint at http://10.96.200.30:8000/health
- [ ] T023 [US1] Verify agent-server connectivity to PostgreSQL (10.96.200.26), Milvus (10.96.200.27), MinIO (10.96.200.28), Redis (10.96.200.29) per FR-003

**Checkpoint**: Agent-server deployed and operational, accessible internally only, connected to all backend services

---

## Phase 4: User Story 2 - Centralized Configuration Management (Priority: P1)

**Goal**: Enable adding new applications via configuration file updates without manual SSH access

**Independent Test**: Create a configuration file listing GitHub repos, modify it to add a new service, run deployment process, and verify the new service is deployed without manual container access

### Implementation for User Story 2

- [x] T024 [P] [US2] Document apps.yml schema in `provision/ansible/roles/app_deployer/README.md` (all fields, validation rules, examples per data-model.md)
- [x] T025 [US2] Implement automatic secret distribution in `provision/ansible/roles/secrets/tasks/main.yml` (create /etc/busibox/secrets/ directory, deploy encrypted secrets per FR-007, FR-009)
- [x] T026 [US2] Implement configuration change detection in `provision/ansible/roles/app_deployer/tasks/main.yml` (detect apps.yml changes, mark affected apps for redeployment per FR-010)
- [x] T027 [US2] Add handler for application restart on secret change in `provision/ansible/roles/app_deployer/handlers/main.yml` (PM2 restart per FR-010 clarification)
- [x] T028 [US2] Update `provision/ansible/site.yml` to include app_deployer and secrets roles
- [x] T029 [US2] Update `provision/ansible/Makefile` with deploy-apps target (runs app_deployer + secrets roles)
- [ ] T030 [US2] Test adding a new application: add test app to apps.yml, run `make deploy-apps`, verify deployment without SSH
- [ ] T031 [US2] Test secret rotation: update secret in vault.yml, run deployment, verify application restarted with new secret

**Checkpoint**: Configuration management operational, new apps deployable via config file, secrets securely managed

---

## Phase 5: User Story 3 - Web Access via NGINX Proxy (Priority: P2)

**Goal**: Configure NGINX reverse proxy with SSL for subdomain and path-based routing to applications

**Independent Test**: Configure NGINX for one subdomain (e.g., agents.ai.localhost), deploy an app, access it via browser, and verify SSL certificate is valid and traffic routes correctly

### Implementation for User Story 3

- [x] T032 [P] [US3] Implement NGINX installation in `provision/ansible/roles/nginx/tasks/main.yml` (apt install nginx, systemd enable per FR-012)
- [x] T033 [P] [US3] Implement SSL certificate mode detection in `provision/ansible/roles/nginx/tasks/main.yml` (check ssl_mode variable: letsencrypt, provisioned, selfsigned per FR-013a)
- [x] T034 [US3] Implement Let's Encrypt certificate acquisition in `provision/ansible/roles/nginx/tasks/letsencrypt.yml` (install certbot, DNS plugin, obtain wildcard cert for *.ai.localhost per FR-013b)
- [x] T035 [US3] Implement certbot auto-renewal configuration in `provision/ansible/roles/nginx/tasks/letsencrypt.yml` (systemd timer, reload hook, alert 7 days before expiry per FR-013b)
- [x] T036 [US3] Implement pre-provisioned certificate deployment in `provision/ansible/roles/nginx/tasks/provisioned.yml` (copy cert files from secrets vault)
- [x] T037 [US3] Implement self-signed certificate generation in `provision/ansible/roles/nginx/tasks/selfsigned.yml` (openssl generate for development)
- [x] T038 [P] [US3] Create main NGINX config template in `provision/ansible/roles/nginx/templates/nginx.conf.j2` (worker processes, logging, include sites-enabled/*)
- [x] T039 [P] [US3] Create HTTP to HTTPS redirect template in `provision/ansible/roles/nginx/templates/redirect-https.conf.j2` (listen 80, return 301 per FR-016)
- [x] T040 [P] [US3] Create subdomain virtual host template in `provision/ansible/roles/nginx/templates/vhost-subdomain.conf.j2` (server block, SSL config, proxy_pass, security headers per FR-014)
- [x] T041 [P] [US3] Create path routing location block template in `provision/ansible/roles/nginx/templates/location-path.conf.j2` (location directive, optional path stripping, proxy settings per FR-015)
- [x] T042 [US3] Implement NGINX configuration generation in `provision/ansible/roles/nginx/tasks/configure.yml` (loop over apps.yml routes, generate vhosts and locations per app)
- [x] T043 [US3] Implement NGINX configuration validation in `provision/ansible/roles/nginx/tasks/configure.yml` (nginx -t before reload per FR-017)
- [x] T044 [US3] Create NGINX reload handler in `provision/ansible/roles/nginx/handlers/main.yml` (graceful reload without dropping connections per FR-017)
- [x] T045 [P] [US3] Create error page templates in `provision/ansible/roles/nginx/templates/errors/` (404.html, 502.html, 503.html per FR-018)
- [x] T046 [US3] Add WebSocket support template in `provision/ansible/roles/nginx/templates/websocket.conf.j2` (upgrade headers, connection upgrade for apps with websocket: true)
- [x] T047 [US3] Update `provision/ansible/site.yml` to include nginx role for openwebui-lxc container
- [ ] T048 [US3] Add NGINX verification to test-infrastructure.sh (SSL cert valid, HTTP redirects to HTTPS, subdomain routing works)

**Checkpoint**: NGINX operational with SSL, subdomain and path routing configured, ready for application traffic

---

## Phase 6: User Story 4 - Main Portal and Authentication (Priority: P2)

**Goal**: Deploy busibox portal at root domain with authentication, accessible via multiple URLs

**Independent Test**: Deploy the busibox portal, log in as a test user, verify the home page displays available applications, and confirm that authentication state persists when navigating to other applications

### Implementation for User Story 4

- [ ] T049 [P] [US4] Add busibox portal definition to `provision/ansible/group_vars/apps.yml` (name: busibox-portal, github_repo: jazzmind/busibox, container: apps-lxc, port: 3000, routes: [domain: ai.localhost/www.ai.localhost, path: /home] per FR-020)
- [ ] T050 [P] [US4] Add busibox secrets to `provision/ansible/roles/secrets/vars/vault.yml` (session_secret, oauth_client_id, oauth_client_secret, database_url, jwt_secret per FR-021, FR-023)
- [ ] T051 [US4] Configure busibox NGINX routes in apps.yml (multiple domains + path routing per FR-020)
- [ ] T052 [US4] Deploy busibox portal via deploywatch: run `make deploy-apps`, verify busibox deployed to /srv/apps/busibox
- [ ] T053 [US4] Test busibox domain routing: access ai.localhost, www.ai.localhost, ai.localhost/home - verify all serve same portal
- [ ] T054 [US4] Test busibox authentication: verify login screen appears, test login flow, verify session persists
- [ ] T055 [US4] Verify JWT cookie configuration: check cookie domain=.ai.localhost, httpOnly=true, secure=true for cross-app auth per FR-023
- [ ] T056 [US4] Test busibox logout: verify session termination, redirect to login per FR-024

**Checkpoint**: Main portal operational at all configured URLs with working authentication and session management

---

## Phase 7: User Story 5 - Agent Administration Interface (Priority: P3)

**Goal**: Deploy agent-manager for agent administration, accessible via subdomain and path routing

**Independent Test**: Deploy agent-manager from GitHub, configure it to connect to the agent-server API, access it via agents.ai.localhost, and verify you can view agent configurations and system status

### Implementation for User Story 5

- [ ] T057 [P] [US5] Add agent-manager definition to `provision/ansible/group_vars/apps.yml` (name: agent-manager, github_repo: jazzmind/agent-manager, container: apps-lxc, port: 3001, routes: [subdomain: agents, path: /agents], websocket: true per FR-026)
- [ ] T058 [P] [US5] Add agent-manager secrets to `provision/ansible/roles/secrets/vars/vault.yml` (agent_api_key, jwt_secret shared with portal per FR-029)
- [ ] T059 [P] [US5] Add agent-manager environment variables to apps.yml (AGENT_API_URL: http://10.96.200.30:8000 per FR-027)
- [ ] T060 [US5] Deploy agent-manager via deploywatch: run `make deploy-apps`, verify agent-manager deployed to /srv/apps/agent-manager
- [ ] T061 [US5] Test agent-manager subdomain routing: access agents.ai.localhost, verify agent-manager loads per FR-026
- [ ] T062 [US5] Test agent-manager path routing: access ai.localhost/agents, verify same agent-manager loads
- [ ] T063 [US5] Test agent-manager WebSocket: verify real-time updates work (WebSocket connection upgrade headers in NGINX)
- [ ] T064 [US5] Test agent-manager connectivity to agent-server: verify dashboard shows agent status, can view workflows per FR-028
- [ ] T065 [US5] Test agent-manager authentication: verify portal session recognized or own auth works per FR-029

**Checkpoint**: Agent-client operational, accessible via subdomain and path, connected to agent-server with WebSocket support

---

## Phase 8: Integration & Validation

**Purpose**: Cross-story validation and end-to-end testing

- [ ] T066 [P] [Integration] Test full deployment flow: add new app to apps.yml, add secrets, run `make deploy-apps`, verify app deploys successfully
- [ ] T067 [Integration] Test deployment failure rollback: deploy malformed release, verify rollback to previous version and alert sent per FR-035
- [ ] T068 [Integration] Test configuration validation: add duplicate routes, verify validation fails with clear error per clarification
- [ ] T069 [Integration] Test missing secrets: reference non-existent secret, verify validation fails listing missing secrets per clarification
- [ ] T070 [Integration] Test database migration: deploy app with schema change, verify migration runs and rolls back on failure per FR-036, FR-037
- [ ] T071 [P] [Integration] Test SSL certificate renewal: trigger certbot renewal (dry-run), verify NGINX reloads gracefully per FR-013b
- [ ] T072 [P] [Integration] Test cross-app authentication: login to portal, navigate to agent-manager, verify no re-authentication required per SC-009
- [ ] T073 [Integration] Test concurrent users: simulate 100 concurrent users across apps, verify no performance degradation per SC-014
- [ ] T074 [Integration] Test application crash recovery: kill app process, verify PM2 restarts within 10 seconds per SC-015
- [ ] T075 [Integration] Update `test-infrastructure.sh` with application deployment tests (all scenarios from quickstart.md)
- [ ] T076 [Integration] Test incremental provisioning: add one container to existing stack, verify correct provisioning (from test-infrastructure.sh)

**Checkpoint**: All user stories integrated and tested end-to-end

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories and production readiness

### Documentation

- [ ] T077 [P] [Polish] Update `QUICKSTART.md` with final deployment instructions (verify against actual implementation)
- [ ] T078 [P] [Polish] Create `provision/ansible/roles/nginx/README.md` documenting SSL modes, routing configuration, troubleshooting
- [ ] T079 [P] [Polish] Create `provision/ansible/roles/app_deployer/README.md` documenting apps.yml schema, adding applications, secret management
- [ ] T080 [P] [Polish] Update `docs/architecture.md` with application deployment layer, NGINX routing, deploywatch extension

### Operational Readiness

- [ ] T081 [P] [Polish] Implement deployment alerting in deploywatch scripts (send alerts on failure to operators per FR-035)
- [ ] T082 [P] [Polish] Implement SSL certificate expiration monitoring (certbot renewal status, alert 7 days before expiry per FR-013b)
- [ ] T083 [P] [Polish] Add application health monitoring to deploywatch (periodic health checks, alert on failure per FR-031)
- [ ] T084 [Polish] Create deployment dashboard script in `tools/deployment-status.sh` (show all apps, versions, health, last deployment time)

### Security Hardening

- [ ] T085 [P] [Polish] Review and harden NGINX security headers (CSP, HSTS, X-Frame-Options per research.md)
- [ ] T086 [P] [Polish] Audit secrets vault permissions (ensure only Ansible can read, no secrets in logs per FR-008)
- [ ] T087 [P] [Polish] Implement rate limiting in NGINX (protect against abuse)
- [ ] T088 [Polish] Test external access blocking: verify agent-server inaccessible from outside 10.96.200.0/21 per SC-003

### Performance Optimization

- [ ] T089 [P] [Polish] Optimize NGINX caching for static assets (reduce backend load)
- [ ] T090 [P] [Polish] Tune PM2 configuration (cluster mode for apps if needed)
- [ ] T091 [Polish] Test and optimize deployment time (target: under 5 minutes per SC-004)

### Test Infrastructure

- [ ] T092 [Polish] Extend test-infrastructure.sh with full test and production modes (test uses TEST- prefix containers per existing implementation)
- [ ] T093 [Polish] Add idempotency tests for all Ansible roles (re-run should be safe)
- [ ] T094 [Polish] Create smoke test script in `tests/smoke-test-apps.sh` (quick health check all apps after deployment)

**Checkpoint**: Production-ready application deployment system with monitoring, alerts, and comprehensive testing

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Foundational completion - No dependencies on other stories
- **User Story 2 (Phase 4)**: Depends on Foundational completion - No dependencies on other stories (but enhances US1)
- **User Story 3 (Phase 5)**: Depends on Foundational completion - Required for public web access to apps
- **User Story 4 (Phase 6)**: Depends on US3 (NGINX) - Needs web routing to be accessible
- **User Story 5 (Phase 7)**: Depends on US1 (agent-server) and US3 (NGINX) - Connects to agent-server via web
- **Integration (Phase 8)**: Depends on desired user stories being complete
- **Polish (Phase 9)**: Depends on all user stories being complete

### User Story Dependencies

```
Foundational (Phase 2) - MUST complete first
    ├── US1: Agent Server (P1) - Independent
    ├── US2: Config Management (P1) - Independent (enhances all others)
    └── US3: NGINX Proxy (P2) - Independent
            ├── US4: Main Portal (P2) - Requires US3
            └── US5: Agent Client (P3) - Requires US1 + US3
```

### Within Each User Story

- Configuration before deployment
- Secrets before application start
- NGINX config before application routing
- Validation before deployment execution
- Health check before marking deployment successful

### Parallel Opportunities

**Setup (Phase 1)**: All 5 tasks can run in parallel (different directories)

**Foundational (Phase 2)**: 
- T006-T008 (configuration) in parallel
- T009-T011 (secrets) in parallel  
- T012-T015 (deploywatch) sequential (depend on each other)

**User Story 1 (Phase 3)**:
- T016-T017 (config + secrets) in parallel
- T018-T023 sequential (deployment process)

**User Story 3 (Phase 5)**:
- T032-T033 in parallel (NGINX install + SSL detection)
- T038-T041 in parallel (all templates)
- T045-T046 in parallel (error pages + WebSocket)

**User Story 4 & 5**: Config and secrets tasks in parallel (T049-T050, T057-T059)

**Polish (Phase 9)**: Most documentation and monitoring tasks can run in parallel (all [P] marked)

---

## Parallel Example: Foundational Phase

```bash
# Can run these configuration tasks together:
Task: "Create provision/ansible/group_vars/all.yml with global variables"
Task: "Create provision/ansible/group_vars/apps.yml schema template"
Task: "Implement configuration validation script"

# Can run these secrets tasks together:
Task: "Create Ansible vault structure"
Task: "Create secrets deployment template"
```

## Parallel Example: User Story 3 (NGINX)

```bash
# Can run all template creation tasks together:
Task: "Create main NGINX config template"
Task: "Create HTTP to HTTPS redirect template"
Task: "Create subdomain virtual host template"
Task: "Create path routing location block template"
Task: "Create error page templates"
Task: "Add WebSocket support template"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2 Only)

1. Complete Phase 1: Setup (5 tasks)
2. Complete Phase 2: Foundational (10 tasks) - **CRITICAL BLOCKER**
3. Complete Phase 3: User Story 1 - Agent Server (8 tasks)
4. Complete Phase 4: User Story 2 - Config Management (8 tasks)
5. **STOP and VALIDATE**: Test US1 + US2 independently
   - Agent-server deployed and operational
   - New apps can be added via config file
6. Deploy/demo if ready (internal backend services operational)

### Incremental Delivery

1. **Foundation** (Phases 1-2): 15 tasks → Configuration and deployment infrastructure ready
2. **+ User Story 1** (Phase 3): 8 tasks → Agent-server deployed (MVP backend!)
3. **+ User Story 2** (Phase 4): 8 tasks → Config management operational
4. **+ User Story 3** (Phase 5): 17 tasks → NGINX with SSL, web routing active
5. **+ User Story 4** (Phase 6): 8 tasks → Main portal with authentication
6. **+ User Story 5** (Phase 7): 9 tasks → Agent admin interface
7. **Integration** (Phase 8): 11 tasks → End-to-end validation
8. **Polish** (Phase 9): 18 tasks → Production-ready

Each increment is independently testable and deployable.

### Parallel Team Strategy

With multiple developers:

1. **Together**: Complete Setup + Foundational (Phases 1-2) - 15 tasks
2. **After Foundational**:
   - Developer A: User Story 1 (agent-server) - 8 tasks
   - Developer B: User Story 2 (config management) - 8 tasks
   - Developer C: Start User Story 3 (NGINX templates) - can do T032-T041 in parallel
3. **After US1 + US2**:
   - Developer A: Continue User Story 3 (NGINX deployment) - T042-T048
   - Developer B: Start User Story 4 (main portal) - T049-T056
   - Developer C: Start User Story 5 (agent-manager) - T057-T065 (requires US1 complete)
4. **Final**: Integration and Polish together

---

## Summary

**Total Tasks**: 94
- Phase 1 (Setup): 5 tasks
- Phase 2 (Foundational): 10 tasks ⚠️ **BLOCKING**
- Phase 3 (US1 - Agent Server): 8 tasks 🎯 **P1**
- Phase 4 (US2 - Config Management): 8 tasks 🎯 **P1**
- Phase 5 (US3 - NGINX Proxy): 17 tasks **P2**
- Phase 6 (US4 - Main Portal): 8 tasks **P2**
- Phase 7 (US5 - Agent Client): 9 tasks **P3**
- Phase 8 (Integration): 11 tasks
- Phase 9 (Polish): 18 tasks

**Parallel Opportunities**: 
- 38 tasks marked [P] can run in parallel within their phases
- User stories 1-2 can run in parallel after Foundational
- User stories 4-5 can run in parallel after US3 (with US1 for US5)

**MVP Scope**: Phases 1-4 (31 tasks) = Backend services deployed with config management

**Independent Tests**:
- US1: Agent-server health check from internal network
- US2: Add test app via config file, verify deployment
- US3: Access test app via HTTPS subdomain, verify SSL valid
- US4: Login to portal, verify app list displayed
- US5: Access agent-manager, verify agent-server connectivity

**Critical Path**: Setup → Foundational → US1/US2 → US3 → US4/US5 → Integration → Polish

---

## Notes

- [P] tasks = different files, no dependencies, can run in parallel
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Foundational phase (Phase 2) is **BLOCKING** - must complete before any user story work
- User Stories 1-2 (P1) are highest priority and can be parallelized
- User Stories 4-5 depend on US3 (NGINX) for web access
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Tests are integrated into implementation (not separate TDD phase as this is infrastructure)

