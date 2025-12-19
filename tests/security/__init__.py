"""
Busibox Security Test Suite

Comprehensive security testing for all Busibox API endpoints.
Covers OWASP API Security Top 10 and common attack vectors.

Test Categories:
- Authentication bypass
- Authorization (IDOR, privilege escalation)
- Injection (SQL, NoSQL, command, LDAP)
- Input validation (XSS, path traversal)
- Rate limiting
- Information disclosure
- Mass assignment
- Fuzzing

Usage:
    # Run all security tests
    pytest tests/security/ -v
    
    # Run specific test category
    pytest tests/security/ -v -m auth
    pytest tests/security/ -v -m injection
    pytest tests/security/ -v -m fuzz
    
    # Run against specific environment
    SECURITY_TEST_ENV=local pytest tests/security/ -v
    SECURITY_TEST_ENV=test pytest tests/security/ -v
"""

__version__ = "1.0.0"




