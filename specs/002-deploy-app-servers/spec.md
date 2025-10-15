# Feature Specification: Application Services Deployment

**Feature Branch**: `002-deploy-app-servers`  
**Created**: 2025-10-15  
**Status**: Draft  
**Input**: User description: "a new feature. This focuses on ensuring all our app servers/services are running within the containers."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent Server Operational (Priority: P1)

As a system operator, I need the agent-server deployed and running so that the file ingestion workflow can process documents and serve AI agent requests from applications.

**Why this priority**: The agent-server is the core backend service that enables all AI functionality. Without it, no AI features work, and the ingestion pipeline cannot process files. This is the foundation for all other application services.

**Independent Test**: Deploy agent-server from GitHub releases, verify health endpoints respond, confirm it can connect to PostgreSQL, Milvus, MinIO, and Redis, and validate that it's only accessible from the internal network (not publicly exposed).

**Acceptance Scenarios**:

1. **Given** the infrastructure is provisioned, **When** deploywatch runs its check cycle, **Then** it detects the latest agent-server release from github.com/jazzmind/agent-server and deploys it to the agent-lxc container
2. **Given** the agent-server is deployed, **When** an app server makes a health check request to the agent API, **Then** the API responds with status 200 and service health information
3. **Given** an external client attempts to access the agent-server, **When** the request comes from outside the internal network, **Then** the connection is refused (agent-server is not publicly exposed)
4. **Given** the agent-server is running, **When** a file is uploaded to MinIO, **Then** the webhook triggers the agent API ingestion endpoint and a job is queued in Redis

---

### User Story 2 - Centralized Configuration Management (Priority: P1)

As a system administrator, I need a centralized way to configure which applications are deployed and manage their secrets, so that adding new services or updating credentials doesn't require manual SSH into containers.

**Why this priority**: Without centralized config management, every deployment requires manual intervention and credentials are scattered. This creates security risks and operational overhead. It's essential infrastructure that all other services depend on.

**Independent Test**: Create a configuration file listing GitHub repos, modify it to add a new service, run deployment process, and verify the new service is deployed without manual container access.

**Acceptance Scenarios**:

1. **Given** a configuration file exists with app repository definitions, **When** deploywatch runs, **Then** it deploys all listed applications to their designated containers
2. **Given** I need to add a new application, **When** I add the GitHub repo and configuration to the apps config file, **Then** the next deploywatch cycle deploys it automatically
3. **Given** an application requires secrets (API keys, database passwords), **When** I define secrets in the secure configuration, **Then** they are made available to the application as environment variables without being exposed in logs or version control
4. **Given** I update a secret value, **When** the configuration is reloaded, **Then** affected applications are restarted with the new secret values

---

### User Story 3 - Web Access via NGINX Proxy (Priority: P2)

As an end user, I need to access applications through friendly URLs with SSL encryption, so that I can use the system securely without remembering port numbers or internal IPs.

**Why this priority**: User-facing web access is essential for usability but builds on the foundation of deployed services (P1). Without SSL and proper routing, services cannot be used in production, but the services themselves must exist first.

**Independent Test**: Configure NGINX for one subdomain (e.g., agents.ai.jaycashman.com), deploy an app, access it via browser, and verify SSL certificate is valid and traffic routes correctly.

**Acceptance Scenarios**:

1. **Given** NGINX is configured with SSL certificates, **When** a user navigates to agents.ai.jaycashman.com, **Then** they are served the agent-client app over HTTPS with a valid certificate
2. **Given** subdomain routing is configured, **When** a user accesses docintel.ai.jaycashman.com, **Then** they are routed to the docintel application
3. **Given** sub-directory routing is configured, **When** a user accesses ai.jaycashman.com/agents, **Then** they are routed to the agent-client application
4. **Given** a user accesses an undefined subdomain or path, **When** NGINX receives the request, **Then** the user is shown an appropriate error page or redirected to the main site

---

### User Story 4 - Main Portal and Authentication (Priority: P2)

As a user, I need a central landing page with authentication that provides access to all available applications, so that I have a single entry point to the system and can manage my session across applications.

**Why this priority**: The main portal provides the user experience layer but depends on having deployable applications (P1) and web routing (P2). It's the user-facing front door but not technically blocking for individual app functionality.

**Independent Test**: Deploy the cashman portal, log in as a test user, verify the home page displays available applications, and confirm that authentication state persists when navigating to other applications.

**Acceptance Scenarios**:

1. **Given** a user navigates to ai.jaycashman.com (naked domain), **When** the page loads, **Then** they see the main portal landing page
2. **Given** a user navigates to www.ai.jaycashman.com or ai.jaycashman.com/home, **When** the page loads, **Then** they see the same main portal landing page
3. **Given** a user is not authenticated, **When** they attempt to access the portal, **Then** they are presented with a login screen
4. **Given** a user successfully authenticates, **When** they access the portal home page, **Then** they see a list of available applications they have permission to access
5. **Given** a user is authenticated in the portal, **When** they navigate to a sub-application, **Then** their authentication state is recognized and they don't need to log in again

---

### User Story 5 - Agent Administration Interface (Priority: P3)

As a system administrator, I need access to the agent-client application to manage agents, workflows, and monitor the agent-server, so that I can configure and troubleshoot the AI services.

**Why this priority**: The admin interface is important for operations but the agent-server itself (P1) can function without it. This is a quality-of-life feature for administrators rather than a system requirement.

**Independent Test**: Deploy agent-client from GitHub, configure it to connect to the agent-server API, access it via agents.ai.jaycashman.com, and verify you can view agent configurations and system status.

**Acceptance Scenarios**:

1. **Given** the agent-client is deployed, **When** an administrator navigates to agents.ai.jaycashman.com, **Then** they can access the agent administration interface
2. **Given** an administrator navigates to ai.jaycashman.com/agents, **When** the page loads, **Then** they can access the same agent administration interface
3. **Given** an authenticated administrator is using the agent-client, **When** they view the dashboard, **Then** they can see the status of all configured agents and workflows
4. **Given** an administrator needs to troubleshoot, **When** they access the agent-client, **Then** they can view logs, metrics, and recent activity from the agent-server

---

### Edge Cases

- What happens when a GitHub repository release is malformed or the deployment fails?
- How does the system handle SSL certificate expiration?
- What occurs when an application crashes or becomes unresponsive after deployment?
- How are conflicting subdomain/path routes resolved (e.g., if two apps claim the same path)?
- What happens when secrets are missing or invalid for an application?
- How does the system behave when the agent-server is unreachable from an application?
- What occurs when deploywatch attempts to deploy during an active user session?
- How are database migrations handled when deploying new application versions?
- What happens when NGINX configuration errors occur during reload?
- How does authentication work across applications if the portal is down?

## Requirements *(mandatory)*

### Functional Requirements

#### Agent Server Deployment (P1)

- **FR-001**: System MUST deploy the agent-server from the latest GitHub release at github.com/jazzmind/agent-server using the deploywatch mechanism
- **FR-002**: Agent-server MUST only be accessible from internal network addresses (10.96.200.0/21 range) and not exposed to public internet
- **FR-003**: Agent-server MUST have connectivity to PostgreSQL (10.96.200.26), Milvus (10.96.200.27), MinIO (10.96.200.28), and Redis (10.96.200.29)
- **FR-004**: System MUST verify agent-server health by checking HTTP health endpoints before marking deployment as successful
- **FR-005**: Agent-server MUST receive secrets and configuration via environment variables provided by the deployment system

#### Configuration Management (P1)

- **FR-006**: System MUST maintain a configuration file that defines all deployable applications with their GitHub repository URLs and deployment parameters
- **FR-007**: System MUST support secure secret management for all applications and services
- **FR-008**: Secrets MUST be stored encrypted at rest and MUST NOT appear in application logs or version control
- **FR-009**: System MUST make secrets available to applications as environment variables during runtime
- **FR-010**: Configuration changes MUST trigger automatic redeployment of affected applications via deploywatch
- **FR-011**: System MUST support specifying which container (apps-lxc, openwebui-lxc, etc.) each application deploys to

#### NGINX Proxy Configuration (P2)

- **FR-012**: System MUST configure NGINX as a reverse proxy to route HTTP/HTTPS traffic to backend applications
- **FR-013**: NGINX MUST serve all traffic over HTTPS with valid SSL certificates for the ai.jaycashman.com domain and subdomains
- **FR-014**: System MUST support subdomain-based virtual hosts (e.g., agents.ai.jaycashman.com, docintel.ai.jaycashman.com)
- **FR-015**: System MUST support sub-directory routing (e.g., ai.jaycashman.com/agents, ai.jaycashman.com/docintel)
- **FR-016**: NGINX MUST redirect HTTP requests to HTTPS automatically
- **FR-017**: System MUST handle NGINX configuration reloads without dropping active connections
- **FR-018**: NGINX MUST provide appropriate error pages for undefined routes or backend failures

#### Main Portal Deployment (P2)

- **FR-019**: System MUST deploy the main portal application from github.com/jazzmind/cashman to the apps-lxc container
- **FR-020**: Main portal MUST be accessible at the naked domain (ai.jaycashman.com), www subdomain (www.ai.jaycashman.com), and /home path (ai.jaycashman.com/home)
- **FR-021**: Main portal MUST provide user authentication functionality
- **FR-022**: Main portal MUST display a home page listing available applications after successful authentication
- **FR-023**: Main portal MUST maintain user session state that is recognized by other applications
- **FR-024**: Main portal MUST handle user logout and session termination

#### Agent Client Deployment (P3)

- **FR-025**: System MUST deploy the agent-client application from github.com/jazzmind/agent-client to the apps-lxc container
- **FR-026**: Agent-client MUST be accessible at agents.ai.jaycashman.com and ai.jaycashman.com/agents
- **FR-027**: Agent-client MUST connect to the agent-server API endpoint for all agent management operations
- **FR-028**: Agent-client MUST provide interfaces for viewing agent configurations, workflows, and system status
- **FR-029**: Agent-client MUST require authentication (via main portal session or its own mechanism)

#### General Application Deployment

- **FR-030**: System MUST support adding new applications by updating the configuration file without code changes
- **FR-031**: System MUST monitor deployed applications for health and availability
- **FR-032**: System MUST support automatic restarts of failed applications
- **FR-033**: System MUST log all deployment activities including successes, failures, and errors
- **FR-034**: System MUST preserve application data during redeployments (stateful data must persist)

### Key Entities

- **Application Definition**: Represents a deployable service with GitHub repository URL, target container, routing configuration (subdomain/path), required secrets, and health check endpoints
- **Deployment Configuration**: Central file listing all applications and their deployment parameters; contains references to secret stores
- **Secret**: Sensitive configuration value (API key, password, token) associated with an application; stored encrypted with access controls
- **SSL Certificate**: TLS/SSL certificate for the ai.jaycashman.com domain and wildcards for subdomains; includes private key and certificate chain
- **NGINX Virtual Host**: Configuration defining routing rules for a subdomain or path, including backend application address and SSL settings
- **Service Route**: Mapping between public URL (subdomain or path) and internal application endpoint (container IP:port)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent-server is deployed from GitHub releases and responds to health checks within 60 seconds of a new release being published
- **SC-002**: Internal applications can successfully make requests to the agent-server with 99% success rate
- **SC-003**: External network requests to the agent-server are blocked 100% of the time
- **SC-004**: New applications can be added to the system by updating configuration and are fully deployed within 5 minutes without manual intervention
- **SC-005**: All application secrets are stored encrypted and zero secrets appear in application logs or git history
- **SC-006**: Users can access applications via HTTPS with valid SSL certificates (no browser warnings) 100% of the time
- **SC-007**: Subdomain routing works correctly for all configured applications with no cross-application traffic leakage
- **SC-008**: Sub-directory routing works correctly with proper URL handling (no broken relative paths or assets)
- **SC-009**: Users can authenticate once via the main portal and access all applications without re-authenticating during the same session
- **SC-010**: Main portal is accessible at all three configured URLs (naked domain, www, /home) with identical functionality
- **SC-011**: Administrators can view agent status and configurations via agent-client interface within 2 seconds of loading the page
- **SC-012**: NGINX configuration reloads complete without dropping active user connections (zero connection failures during reload)
- **SC-013**: Failed application deployments are detected and logged within 30 seconds with clear error messages
- **SC-014**: System handles 100 concurrent users across all applications without performance degradation
- **SC-015**: All deployed applications automatically restart within 10 seconds if they crash or become unresponsive

## Assumptions

- SSL certificates for ai.jaycashman.com and *.ai.jaycashman.com will be obtained via Let's Encrypt or provided by the system administrator
- All applications follow standard Node.js deployment patterns (PM2 compatible) or containerized deployments
- Applications expose standard HTTP health check endpoints (e.g., /health or /api/health)
- The main portal (cashman) implements standard session-based authentication that can be shared across applications via cookies or headers
- GitHub repositories use semantic versioning and GitHub Releases for version management
- Applications can be configured via environment variables (twelve-factor app methodology)
- The apps-lxc container has Node.js and PM2 already installed (from Phase 3 node_common role)
- Network firewall rules prevent external access to internal service IPs (10.96.200.0/21)
- DNS records for ai.jaycashman.com and *.ai.jaycashman.com point to the NGINX proxy IP address
