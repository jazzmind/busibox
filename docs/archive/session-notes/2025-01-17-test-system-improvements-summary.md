---
created: 2025-01-17
updated: 2025-01-17
status: completed
category: testing
tags: [testing, pytest, makefile, menu, state-tracking, improvements]
---

# Test System Improvements Summary - 2025-01-17

## Overview

This document summarizes the test system improvements made and those still pending.

## Completed Tasks ✅

### 1. Fixed Pytest Plugin AttributeError

**Problem**: Plugin was accessing `report.config` which doesn't exist in newer pytest versions.

**Solution**: Used module-level variable `_failed_tests` instead.

**File**: `srv/shared/testing/pytest_failed_filter.py`

```python
# Module-level storage (works across pytest versions)
_failed_tests = []

def pytest_runtest_logreport(report):
    if report.failed and report.when == "call":
        global _failed_tests
        _failed_tests.append(report.nodeid)
```

### 2. Added Test Result State Tracking

**File**: `scripts/lib/state.sh`

Added functions to track test pass/fail status:
- `save_test_result(service, result)` - Save "passed" or "failed"
- `get_test_result(service)` - Get result for a service
- `get_failed_services()` - List all failed services
- `get_passed_services()` - List all passed services
- `clear_test_results()` - Reset all test results
- `has_failed_tests()` - Check if any tests failed

### 3. Updated test.sh to Store Results

**File**: `scripts/make/test.sh`

Modified all test execution blocks to save results:

```bash
if ssh "root@${ingest_ip}" "...pytest..."; then
    success "Ingest tests passed!"
    save_test_result "ingest" "passed"
else
    error "Ingest tests failed"
    save_test_result "ingest" "failed"
    return 1
fi
```

Updated for: authz, ingest, search, agent

### 4. Enhanced Test Summary

Modified "all" tests case to show passed and failed services:

```bash
# Show passed services
local passed_services=($(get_passed_services))
if [[ ${#passed_services[@]} -gt 0 ]]; then
    success "Passed services: ${passed_services[*]}"
fi

# Show failed services
if [[ ${#failed_services[@]} -eq 0 ]]; then
    success "All service tests passed!"
else
    error "Failed services: ${failed_services[*]}"
    warn "Or use 'Run Failed Tests' option to rerun only failed services"
fi
```

## Completed Tasks (Continued) ✅

### 5. Created Test Submenu Structure

**File**: `scripts/make/menu.sh`

Implemented hierarchical test menu:

```
Test Menu
├── 1. PVT Tests
│   ├── Run All PVT
│   └── Back
├── 2. Service Tests
│   ├── Run All Services
│   ├── Run Failed Services (dynamic - only shows if failures exist)
│   ├── AuthZ
│   ├── Ingest
│   ├── Search
│   ├── Agent
│   ├── Clear Test Results
│   └── Back
├── 3. App Tests
│   ├── Run All Apps
│   ├── AI Portal
│   ├── Agent Manager
│   └── Back
└── 4. Back to Main Menu
```

**Functions Created**:
- `show_test_main_menu()` - Top-level test menu with status summary
- `handle_test_pvt()` - PVT test submenu
- `handle_test_services()` - Service test submenu with dynamic failed tests option
- `handle_test_apps()` - App test submenu (placeholder for future)
- Updated `handle_test()` - Now loops through test menu

### 6. Implemented 'Run Failed Tests' Functionality

**Location**: `handle_test_services()` in menu.sh

Features:
- Dynamically adds "Run Failed Services" option when failures exist
- Shows which services failed in the option text
- Iterates through failed services and reruns them
- Works for both Docker and Proxmox backends
- Updates test results after rerun

```bash
# Dynamic menu building
if [[ ${#failed_services[@]} -gt 0 ]]; then
    menu_items+=("Run Failed Services (${failed_services[*]})")
fi

# Execution
for svc in "${failed_services[@]}"; do
    bash "${SCRIPT_DIR}/test.sh" services "$svc"
done
```

### 7. Updated Menu Flow to Return to Test Submenu

**Implementation**: All test menu functions now use `while true` loops

**New Flow**:
```
Main Menu → Test Menu → Service Tests → Run Tests → Service Tests Menu
                                                    ↑                 ↓
                                                    └─────────────────┘
                                                    (stays in submenu)
```

**Features**:
- Tests complete and pause for review
- User returns to the submenu they were in
- Can run more tests or go back
- Only returns to main menu when explicitly selected

## Usage Examples

### Current Usage

```bash
# Run tests and check results
make test-docker SERVICE=all

# Check what failed
grep "TEST_RESULT_" .busibox-state

# Manually rerun failed service
make test-docker SERVICE=ingest
```

### New Usage (All Features Implemented)

```bash
# Interactive menu
make

# Navigate: Test → Service Tests
# Menu shows:
#   - Current test status (passed/failed)
#   - "Run Failed Services" option (if any failed)
#   - Individual service test options
#   - Clear test results option
# After running tests, returns to Service Tests menu
# Can immediately rerun failed tests or try other services
```

## State File Format

Test results are stored in `.busibox-state`:

```
TEST_RESULT_authz=passed
TEST_TIME_authz=2026-01-17T10:30:00Z
TEST_RESULT_ingest=failed
TEST_TIME_ingest=2026-01-17T10:31:00Z
TEST_RESULT_search=passed
TEST_TIME_search=2026-01-17T10:32:00Z
TEST_RESULT_agent=failed
TEST_TIME_agent=2026-01-17T10:33:00Z
```

## Benefits (All Completed) ✅

- ✅ Pytest plugin works across all pytest versions
- ✅ Test results are tracked persistently in state file
- ✅ Easy to see which tests passed/failed in menu
- ✅ Test summary shows both passed and failed services
- ✅ Hierarchical test organization (PVT, Services, Apps)
- ✅ Quick rerun of only failed tests with one menu option
- ✅ Stay in test menu after running tests
- ✅ Significantly improved test workflow efficiency
- ✅ Clear test results option to start fresh
- ✅ Dynamic menu adapts to test status

## Files Modified (All Completed) ✅

- `srv/shared/testing/pytest_failed_filter.py` - Fixed AttributeError with module-level variable
- `scripts/lib/state.sh` - Added comprehensive test result tracking functions
- `scripts/make/test.sh` - Save test results, enhanced summary with passed/failed lists
- `scripts/make/menu.sh` - Complete test submenu system with dynamic options

## Testing the New System

### Try It Out

```bash
# Start the menu
make

# Select "Test" from main menu
# You'll see the new test menu with:
# - Test status summary at the top
# - Three categories: PVT, Services, Apps

# Navigate to Service Tests
# You'll see:
# - Current pass/fail status
# - "Run Failed Services" option (if any failed)
# - Individual service options
# - Clear results option

# Run a test, it will:
# 1. Execute the test
# 2. Show output with pytest filter for failures
# 3. Pause for review
# 4. Return to Service Tests menu (not main menu!)
# 5. Update status display

# Run failed tests:
# - Select "Run Failed Services"
# - Only failed services will be retested
# - Results update automatically
```

### Menu Navigation Example

```
Main Menu
  ↓ (select Test)
Test Menu [shows: Failed: ingest, agent | Passed: authz, search]
  ↓ (select Service Tests)
Service Tests Menu
  1. Run All Services
  2. Run Failed Services (ingest, agent)  ← Dynamic option!
  3. Test AuthZ Service
  4. Test Ingest Service
  5. Test Search Service
  6. Test Agent Service
  7. Clear Test Results
  8. Back to Test Menu
  ↓ (select Run Failed Services)
[Tests run, pause for review]
  ↓ (press Enter)
Service Tests Menu [updated status shown]
  ↓ (select Back)
Test Menu
  ↓ (select Back)
Main Menu
```

## Related Documentation

- Pytest plugin: `srv/shared/testing/pytest_failed_filter.py`
- State management: `scripts/lib/state.sh`
- Test execution: `scripts/make/test.sh`
- Menu system: `scripts/make/menu.sh`

## Rules Applied

Following `.cursorrules`:
- Documentation in `docs/development/session-notes/`
- Used kebab-case for filename
- Included metadata header with tags
- No breaking changes to existing functionality
