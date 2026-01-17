"""
Pytest plugin to generate rerun filters for failed tests.

This plugin automatically captures failed tests and generates
a pytest command to rerun only those tests.

Usage:
    Add to conftest.py:
        pytest_plugins = ["testing.pytest_failed_filter"]
    
    Or use --failed-filter flag:
        pytest tests/ --failed-filter
"""

import pytest
from pathlib import Path


def pytest_addoption(parser):
    """Add command line options."""
    parser.addoption(
        "--failed-filter",
        action="store_true",
        default=False,
        help="Generate pytest filter for failed tests",
    )


def pytest_configure(config):
    """Configure plugin."""
    config.failed_tests = []
    config.addinivalue_line("markers", "failed_filter: mark test for failure tracking")


def pytest_runtest_logreport(report):
    """Capture failed test names."""
    if report.failed and report.when == "call":
        config = report.config
        if hasattr(config, 'failed_tests'):
            config.failed_tests.append(report.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print failed test filter at the end."""
    if not hasattr(config, 'failed_tests') or not config.failed_tests:
        return
    
    failed_tests = config.failed_tests
    
    if failed_tests:
        terminalreporter.write_sep("=", "Failed Test Rerun Filter", bold=True, yellow=True)
        terminalreporter.write_line("")
        terminalreporter.write_line(f"To rerun {len(failed_tests)} failed test(s):", yellow=True)
        terminalreporter.write_line("")
        
        # Generate direct test path filter
        test_paths = " ".join(failed_tests)
        terminalreporter.write_line(f"  pytest {test_paths}", blue=True, bold=True)
        terminalreporter.write_line("")
        
        # Generate -k filter for pattern matching (if reasonable number of tests)
        if len(failed_tests) <= 10:
            test_names = [nodeid.split("::")[-1] for nodeid in failed_tests]
            k_filter = " or ".join(test_names)
            terminalreporter.write_line("Or using -k filter:", yellow=True)
            terminalreporter.write_line(f'  pytest -k "{k_filter}"', blue=True, bold=True)
            terminalreporter.write_line("")
        
        # Save to file
        failed_file = Path("/tmp/pytest-failed-tests.txt")
        with open(failed_file, "w") as f:
            f.write("\n".join(failed_tests))
        
        terminalreporter.write_line(f"Failed tests saved to: {failed_file}", yellow=True)
        terminalreporter.write_line("")
