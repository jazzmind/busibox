# Code Analysis Report: Maigent Apps Ecosystem

**Created**: 2026-01-16
**Last Updated**: 2026-01-16
**Status**: Active
**Category**: Reference

## Executive Summary

| Repository | Type | Primary Lang | LOC (Source) | Files | Tests | Docs | AI-Ready |
|------------|------|--------------|--------------|-------|-------|------|----------|
| **busibox** | Infrastructure/Backend | Python/Bash/YAML | ~100K | 391 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **ai-portal** | Frontend/App | TypeScript | ~48K | 286 | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **busibox-app** | Shared Library | TypeScript | ~22K | 105 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **agent-manager** | Frontend/App | TypeScript | ~21K | 126 | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

---

## 1. Busibox (Infrastructure Platform)

### Overview
The largest and most mature repository. A comprehensive LLM infrastructure platform providing secure file storage, document processing, embeddings, RAG, and AI agent operations on isolated LXC containers.

### Code Metrics

| Metric | Value |
|--------|-------|
| **Python Files** | 391 (excluding venv/pycache) |
| **Python LOC** | ~99,554 |
| **Shell Scripts** | 119 files (~28,108 lines) |
| **Ansible YAML** | 117 playbooks + 51 templates |
| **Documentation** | 178 markdown files (~107K lines) |
| **Specifications** | 51 spec files |
| **OpenAPI Specs** | 4 API definitions |

### Service Breakdown

| Service | Python Files | Description |
|---------|--------------|-------------|
| `srv/agent/` | 227 | FastAPI agent API |
| `srv/ingest/` | 90 | Document processing worker |
| `srv/authz/` | 646 | Authorization service |
| `srv/search/` | 30 | Search API |

### Test Coverage: ⭐⭐⭐⭐

| Category | Count | Coverage |
|----------|-------|----------|
| Python Test Files (srv/) | 89 | Good |
| Integration Tests (tests/) | 12 | Security focus |
| Ansible Tests | Via Makefile | `make test-*` |
| **Total Test Files** | **101** | **26% ratio** |

**Test Infrastructure**: Mature testing system with:
- `make test-ingest`, `test-search`, `test-agent`, `test-apps`
- Extraction strategy tests (simple, LLM, marker, colpali)
- Coverage targets defined
- Interactive test menu (`make test-menu`)

**What's Good**:
- ✅ All services have test files
- ✅ Makefile-based test runners
- ✅ Coverage reporting available
- ✅ Integration test suite

**Path to 5 Stars**:
1. Increase test-to-source ratio to 30%+ (add ~20 more test files)
2. Add coverage thresholds to CI/CD (target 80%)
3. Add E2E tests for critical workflows
4. Document coverage metrics in TESTING.md

### Documentation Quality: ⭐⭐⭐⭐⭐

**Outstanding documentation**:
- **CLAUDE.md**: 353 lines - comprehensive AI guidance
- **TESTING.md**: 262 lines - complete testing strategy
- **docs/**: 178 files organized by category

### AI-Readiness: ⭐⭐⭐⭐⭐

| Feature | Status | Details |
|---------|--------|---------|
| **CLAUDE.md** | ✅ | 353 lines |
| **.cursor/rules/** | ✅ | 6 rule files |
| **.cursor/commands/** | ✅ | 8 speckit commands |
| **MCP Server** | ✅ | Custom Cursor MCP |
| **Structured Docs** | ✅ | Category-based |

---

## 2. AI-Portal (Main Frontend Application)

### Overview
Next.js 15 application providing unified interface for AI app management, document ingestion, and deployment. The primary user-facing dashboard.

### Code Metrics

| Metric | Value |
|--------|-------|
| **TypeScript/TSX Files** | 286 |
| **Source LOC** | ~48,225 |
| **API Routes** | 123 |
| **Components** | 41 |
| **Lib Files** | 53 |
| **Documentation** | 47 markdown files |

### Test Coverage: ⭐⭐

| Category | Count | Notes |
|----------|-------|-------|
| Test Files | 12 | Very low for 286 source files |
| Test Framework | Vitest | Modern, fast |
| Coverage Tool | @vitest/coverage-v8 | Available |
| **Test Ratio** | **4%** | **Needs significant improvement** |

**Current Test Files**:
```
src/app/api/chat/__tests__/
  - conversation-detail.test.ts
  - messages.test.ts
  - conversations.test.ts

src/lib/chat/__tests__/
  - messages.test.ts
  - conversations.test.ts

src/lib/videos/__tests__/
  - upload.test.ts
  - access-control.test.ts
  - expiration.test.ts

src/lib/__tests__/
  - chat-infrastructure.test.ts
  - email-validation.test.ts
  - totp.test.ts

src/lib/ai/__tests__/
  - dual-model-router.test.ts
```

**What's Missing**:
- ❌ 123 API routes with only 3 tested (2% coverage)
- ❌ 41 components with 0 component tests
- ❌ 53 lib files with only 9 tested (17% coverage)
- ❌ No E2E tests
- ❌ No auth flow tests

**Path to 5 Stars**:
1. **Add API route tests** (Priority: High)
   - Target: 30+ API route tests
   - Focus on: admin/*, auth/*, documents/*
   
2. **Add component tests** (Priority: Medium)
   - Target: 20+ component tests
   - Focus on: admin components, auth forms, chat UI
   
3. **Add lib utility tests** (Priority: Medium)
   - Target: 25+ lib tests
   - Focus on: auth.ts, db.ts, api clients
   
4. **Add E2E tests** (Priority: Low)
   - Target: 5-10 E2E tests with Playwright
   - Focus on: login flow, document upload, chat

5. **Set coverage threshold**
   - Target: 60% overall coverage
   - Add to CI/CD pipeline

### Documentation Quality: ⭐⭐⭐⭐⭐

**Excellent documentation** with category-based organization:
- **CLAUDE.md**: 494 lines
- **docs/README.md**: Documentation index
- **docs/**: 47 files in categories

### AI-Readiness: ⭐⭐⭐⭐⭐

| Feature | Status | Details |
|---------|--------|---------|
| **CLAUDE.md** | ✅ | 494 lines |
| **.cursor/rules/** | ✅ | 5 files |
| **.cursor/commands/** | ✅ | 8 speckit commands |
| **Structured Docs** | ✅ | Category-based |

---

## 3. Busibox-App (Shared Library)

### Overview
TypeScript library package providing shared components, contexts, and service clients for the Busibox ecosystem. Published as npm package.

### Code Metrics

| Metric | Value |
|--------|-------|
| **TypeScript/TSX Files** | 105 |
| **Source LOC** | ~21,543 |
| **Components** | 56 |
| **Lib Files** | 27 |
| **Contexts** | 3 |

### Test Coverage: ⭐⭐⭐

| Category | Count | Notes |
|----------|-------|-------|
| Test Files | 8 | Integration tests only |
| Test Framework | Jest | Standard |
| Focus | Service clients | Real API calls |
| **Test Ratio** | **8%** | **Good for integration, missing unit** |

**Current Test Files**:
```
tests/
  - audit.test.ts         # AuditClient
  - chat-client.test.ts   # AgentClient
  - embeddings.test.ts    # EmbeddingsClient
  - ingest.test.ts        # IngestClient
  - insights-client.test.ts
  - rbac.test.ts          # RBACClient
  - search.test.ts        # SearchClient
  - search-client.test.ts
```

**What's Good**:
- ✅ All service clients tested
- ✅ Real API integration tests
- ✅ 80% coverage target documented
- ✅ Comprehensive tests/README.md

**What's Missing**:
- ❌ 56 components with 0 tests
- ❌ 3 contexts with 0 tests
- ❌ No unit tests for utilities
- ❌ No component rendering tests

**Path to 5 Stars**:
1. **Add component tests** (Priority: High)
   - Target: 20+ component tests
   - Focus on: Header, Footer, ChatMessage, DocumentCard
   - Use: React Testing Library + Jest
   
2. **Add context tests** (Priority: Medium)
   - Target: 3 context tests
   - Focus on: ThemeProvider, CustomizationProvider
   
3. **Add utility unit tests** (Priority: Medium)
   - Target: 10+ utility tests
   - Focus on: formatters, validators, helpers
   
4. **Set up coverage reporting**
   - Target: 60% overall, 80% for lib/
   - Add coverage badge to README

### Documentation Quality: ⭐⭐⭐⭐⭐

**Well-organized documentation**:
- **CLAUDE.md**: Comprehensive
- **docs/README.md**: Index
- **tests/README.md**: Testing guide

### AI-Readiness: ⭐⭐⭐⭐⭐

| Feature | Status | Details |
|---------|--------|---------|
| **CLAUDE.md** | ✅ | Library guidance |
| **.cursor/rules/** | ✅ | 3 files |
| **.cursor/commands/** | ✅ | 8 speckit commands |
| **Structured Docs** | ✅ | Category-based |

---

## 4. Agent-Manager (Agent Management UI)

### Overview
Next.js application for managing AI agents, workflows, and tool configurations. Provides agent simulation and conversation management.

### Code Metrics

| Metric | Value |
|--------|-------|
| **TypeScript/TSX Files** | 126 |
| **Source LOC** | ~20,664 |
| **API Routes** | 38 |
| **Components** | 39 |
| **Lib Files** | 13 |

### Test Coverage: ⭐

| Category | Count | Notes |
|----------|-------|-------|
| Test Files | 3 | **Critical gap** |
| Test Framework | Vitest | Modern |
| **Test Ratio** | **2%** | **Needs urgent attention** |

**Current Test Files**:
```
app/page.test.tsx                           # Home page
components/admin/ClientManagement.test.tsx  # Admin component
lib/admin-client.test.ts                    # Admin API client
```

**What's Missing**:
- ❌ 38 API routes with 0 tested
- ❌ 39 components with only 1 tested (3%)
- ❌ 13 lib files with only 1 tested (8%)
- ❌ No hook tests (useChatMessages, useRunStream)
- ❌ No workflow builder tests
- ❌ No E2E tests

**Path to 5 Stars**:
1. **Add API route tests** (Priority: Critical)
   - Target: 15+ API route tests
   - Focus on: agents/*, conversations/*, runs/*, workflows/*
   
2. **Add component tests** (Priority: High)
   - Target: 15+ component tests
   - Focus on: chat components, workflow builder, agent forms
   
3. **Add hook tests** (Priority: High)
   - Target: 2 hook tests
   - Focus on: useChatMessages, useRunStream
   
4. **Add lib tests** (Priority: Medium)
   - Target: 5+ lib tests
   - Focus on: API clients, utilities
   
5. **Add workflow builder tests** (Priority: Medium)
   - Target: 5 workflow tests
   - Focus on: node creation, execution, validation

6. **Set coverage threshold**
   - Target: 40% initially, grow to 60%
   - Add to CI/CD pipeline

### Documentation Quality: ⭐⭐⭐⭐⭐

**Comprehensive documentation**:
- **CLAUDE.md**: Frontend-only architecture
- **docs/README.md**: Index
- **docs/**: 33 files in categories

### AI-Readiness: ⭐⭐⭐⭐⭐

| Feature | Status | Details |
|---------|--------|---------|
| **CLAUDE.md** | ✅ | Architecture |
| **.cursor/rules/** | ✅ | 4 files |
| **.cursor/commands/** | ✅ | 9 speckit commands |
| **Structured Docs** | ✅ | Category-based |

---

## Test Coverage Analysis

### Current State

```
┌─────────────────┬──────────┬───────────┬─────────┬─────────┐
│ Repository      │ Source   │ Test      │ Ratio   │ Grade   │
│                 │ Files    │ Files     │         │         │
├─────────────────┼──────────┼───────────┼─────────┼─────────┤
│ busibox         │ 391      │ 101       │ 26%     │ ⭐⭐⭐⭐   │
│ busibox-app     │ 105      │ 8         │ 8%      │ ⭐⭐⭐    │
│ ai-portal       │ 286      │ 12        │ 4%      │ ⭐⭐     │
│ agent-manager   │ 126      │ 3         │ 2%      │ ⭐      │
└─────────────────┴──────────┴───────────┴─────────┴─────────┘
```

### Target State for 5 Stars

| Repository | Current | Target | New Tests Needed | Priority |
|------------|---------|--------|------------------|----------|
| busibox | 101 | 120+ | ~20 | Low |
| busibox-app | 8 | 35+ | ~27 | Medium |
| ai-portal | 12 | 75+ | ~63 | High |
| agent-manager | 3 | 40+ | ~37 | **Critical** |

### Test Type Distribution Needed

| Repo | Unit | Integration | Component | E2E | Hook |
|------|------|-------------|-----------|-----|------|
| busibox | ✅ | ✅ | N/A | ⚠️ | N/A |
| ai-portal | ⚠️ | ✅ | ❌ | ❌ | N/A |
| busibox-app | ❌ | ✅ | ❌ | N/A | N/A |
| agent-manager | ❌ | ❌ | ❌ | ❌ | ❌ |

### Priority Test Files to Create

#### Agent-Manager (Critical - 37 new tests)
```
1. app/api/agents/route.test.ts
2. app/api/conversations/route.test.ts
3. app/api/runs/route.test.ts
4. app/api/workflows/route.test.ts
5. app/api/tools/route.test.ts
6. components/chat/ChatMessage.test.tsx
7. components/chat/ChatInput.test.tsx
8. components/chat/ChatContainer.test.tsx
9. components/workflow/WorkflowCanvas.test.tsx
10. components/workflow/WorkflowNode.test.tsx
11. components/tools/ToolForm.test.tsx
12. components/agents/AgentForm.test.tsx
13. hooks/useChatMessages.test.ts
14. hooks/useRunStream.test.ts
15. lib/api-client.test.ts
```

#### AI-Portal (High - 63 new tests)
```
1. app/api/admin/users/route.test.ts
2. app/api/admin/roles/route.test.ts
3. app/api/admin/apps/route.test.ts
4. app/api/auth/session/route.test.ts
5. app/api/documents/upload/route.test.ts
6. components/admin/UserManagement.test.tsx
7. components/admin/RoleManagement.test.tsx
8. components/admin/AppCard.test.tsx
9. components/auth/LoginForm.test.tsx
10. lib/auth.test.ts
11. lib/db.test.ts
12. lib/encryption.test.ts
13. lib/api-url.test.ts
14. e2e/login.spec.ts
15. e2e/document-upload.spec.ts
```

#### Busibox-App (Medium - 27 new tests)
```
1. components/layout/Header.test.tsx
2. components/layout/Footer.test.tsx
3. components/chat/ChatMessage.test.tsx
4. components/chat/ChatInput.test.tsx
5. components/documents/DocumentCard.test.tsx
6. contexts/ThemeProvider.test.tsx
7. contexts/CustomizationProvider.test.tsx
8. lib/formatters.test.ts
9. lib/validators.test.ts
10. lib/utils.test.ts
```

---

## Comparative Analysis

### Code Complexity by Lines

```
┌─────────────────┬────────────┬────────────┬────────────┐
│ Repository      │ Source LOC │ Doc LOC    │ Total      │
├─────────────────┼────────────┼────────────┼────────────┤
│ busibox         │   ~128K    │   ~107K    │   ~235K    │
│ ai-portal       │    ~48K    │    ~36K    │    ~84K    │
│ busibox-app     │    ~22K    │    ~3K     │    ~25K    │
│ agent-manager   │    ~21K    │    ~18K    │    ~39K    │
└─────────────────┴────────────┴────────────┴────────────┘
```

### AI-Readiness Scorecard

| Feature | busibox | ai-portal | busibox-app | agent-manager |
|---------|---------|-----------|-------------|---------------|
| CLAUDE.md | ✅ 353 lines | ✅ 494 lines | ✅ | ✅ |
| .cursor/rules | ✅ 6 files | ✅ 5 files | ✅ 3 files | ✅ 4 files |
| .cursor/commands | ✅ 8 files | ✅ 8 files | ✅ 8 files | ✅ 9 files |
| Custom MCP | ✅ | ❌ | ❌ | ❌ |
| OpenAPI/Specs | ✅ 4 APIs | ✅ 48 specs | ❌ | ✅ 8 specs |
| Structured Docs | ✅ | ✅ | ✅ | ✅ |
| docs/README.md | ✅ | ✅ | ✅ | ✅ |

### Overall Ratings

| Category | busibox | ai-portal | busibox-app | agent-manager |
|----------|---------|-----------|-------------|---------------|
| **Test Coverage** | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐ |
| **Documentation** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **AI-Readiness** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

---

## Recommendations

### Critical Priority (Test Coverage)

#### 1. Agent-Manager: From ⭐ to ⭐⭐⭐⭐⭐

**Current**: 3 test files (2% coverage)
**Target**: 40+ test files (30%+ coverage)

| Phase | Action | Tests to Add | Timeline |
|-------|--------|--------------|----------|
| 1 | API route tests | 10 | Week 1 |
| 2 | Component tests | 12 | Week 2 |
| 3 | Hook tests | 2 | Week 2 |
| 4 | Lib tests | 8 | Week 3 |
| 5 | E2E tests | 5 | Week 4 |

```bash
# Create test structure
mkdir -p app/api/__tests__
mkdir -p components/__tests__
mkdir -p hooks/__tests__
mkdir -p lib/__tests__
mkdir -p e2e
```

#### 2. AI-Portal: From ⭐⭐ to ⭐⭐⭐⭐⭐

**Current**: 12 test files (4% coverage)
**Target**: 75+ test files (25%+ coverage)

| Phase | Action | Tests to Add | Timeline |
|-------|--------|--------------|----------|
| 1 | Admin API tests | 15 | Week 1-2 |
| 2 | Auth API tests | 8 | Week 2 |
| 3 | Component tests | 20 | Week 3-4 |
| 4 | Lib unit tests | 15 | Week 4-5 |
| 5 | E2E tests | 5 | Week 6 |

#### 3. Busibox-App: From ⭐⭐⭐ to ⭐⭐⭐⭐⭐

**Current**: 8 test files (8% coverage)
**Target**: 35+ test files (30%+ coverage)

| Phase | Action | Tests to Add | Timeline |
|-------|--------|--------------|----------|
| 1 | Component tests | 15 | Week 1-2 |
| 2 | Context tests | 3 | Week 2 |
| 3 | Utility tests | 9 | Week 3 |

#### 4. Busibox: From ⭐⭐⭐⭐ to ⭐⭐⭐⭐⭐

**Current**: 101 test files (26% coverage)
**Target**: 120+ test files (30%+ coverage)

| Phase | Action | Tests to Add | Timeline |
|-------|--------|--------------|----------|
| 1 | E2E workflow tests | 10 | Week 1-2 |
| 2 | Coverage enforcement | N/A | Week 2 |
| 3 | CI/CD integration | N/A | Week 3 |

### Medium Priority

5. **Set up coverage reporting for all repos**
   - Add coverage badges to READMEs
   - Configure CI to fail below thresholds

6. **Standardize testing frameworks**
   - Consider migrating busibox-app from Jest to Vitest

### Low Priority

7. **Add MCP servers for other repos**
8. **Add OpenAPI specs where missing**

---

## Improvements Made (2026-01-16)

### CLAUDE.md Files Added
- ✅ **agent-manager/CLAUDE.md** - 8.5 KB
- ✅ **busibox-app/CLAUDE.md** - 8.7 KB

### Cursor Rules Added/Enhanced
- ✅ **ai-portal/.cursor/rules/** - 5 files
- ✅ **busibox-app/.cursor/rules/** - 3 files
- ✅ **agent-manager/.cursor/rules/** - 4 files

### Documentation Restructured
- ✅ All repos now have category-based docs/
- ✅ All repos have docs/README.md index

---

## Conclusion

### Documentation & AI-Readiness: All 5-Star ✅

All repositories now meet 5-star standards for documentation and AI readiness.

### Test Coverage: Work Needed

| Repo | Current | Target | Status |
|------|---------|--------|--------|
| busibox | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Near target |
| busibox-app | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Add component tests |
| ai-portal | ⭐⭐ | ⭐⭐⭐⭐⭐ | Major effort needed |
| agent-manager | ⭐ | ⭐⭐⭐⭐⭐ | **Critical priority** |

**Total New Tests Needed**: ~147 test files across all repos

**Total Ecosystem**:
- **~383K lines of code** (source + docs)
- **~500+ files** across all repos
- **~124 current test files** → Target: ~270 test files
- **~267+ documentation files**
- **~20+ cursor rule files**
- **~33+ speckit command files**

---

## Related Documentation

- [Architecture Overview](../architecture/architecture.md)
- [Testing Strategy](../../TESTING.md)
- [Cursor Rules](../../.cursor/rules/README.md)
