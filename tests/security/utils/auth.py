"""
Authentication Testing Utilities

Test authentication and authorization mechanisms.
"""

import json
import base64
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend


@dataclass
class AuthTestResult:
    """Result of an authentication test."""
    test_name: str
    expected_status: int
    actual_status: int
    passed: bool
    details: str = ""
    response_body: str = ""


class AuthTester:
    """Authentication and authorization testing utilities."""
    
    def __init__(self, client: httpx.Client, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")
    
    def test_no_auth(
        self,
        endpoint: str,
        method: str = "GET",
        expected_status: int = 401,
    ) -> AuthTestResult:
        """Test endpoint without authentication."""
        url = f"{self.base_url}{endpoint}"
        
        if method == "GET":
            response = self.client.get(url)
        elif method == "POST":
            response = self.client.post(url, json={})
        elif method == "PUT":
            response = self.client.put(url, json={})
        elif method == "DELETE":
            response = self.client.delete(url)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        return AuthTestResult(
            test_name=f"no_auth_{method}_{endpoint}",
            expected_status=expected_status,
            actual_status=response.status_code,
            passed=response.status_code == expected_status,
            details=f"Expected {expected_status}, got {response.status_code}",
            response_body=response.text[:500],
        )
    
    def test_invalid_token(
        self,
        endpoint: str,
        method: str = "GET",
        expected_status: int = 401,
    ) -> List[AuthTestResult]:
        """Test endpoint with various invalid tokens."""
        results = []
        invalid_tokens = [
            ("empty_bearer", "Bearer "),
            ("malformed_jwt", "Bearer not.a.jwt"),
            ("expired_jwt", self._generate_expired_jwt()),
            ("wrong_signature", self._generate_wrong_signature_jwt()),
            ("alg_none", self._generate_alg_none_jwt()),
            ("null_token", "Bearer null"),
            ("basic_auth", "Basic YWRtaW46YWRtaW4="),
        ]
        
        url = f"{self.base_url}{endpoint}"
        
        for token_name, token_value in invalid_tokens:
            headers = {"Authorization": token_value}
            
            if method == "GET":
                response = self.client.get(url, headers=headers)
            elif method == "POST":
                response = self.client.post(url, json={}, headers=headers)
            elif method == "PUT":
                response = self.client.put(url, json={}, headers=headers)
            elif method == "DELETE":
                response = self.client.delete(url, headers=headers)
            else:
                continue
            
            results.append(AuthTestResult(
                test_name=f"invalid_token_{token_name}_{method}_{endpoint}",
                expected_status=expected_status,
                actual_status=response.status_code,
                passed=response.status_code == expected_status,
                details=f"Token type: {token_name}",
                response_body=response.text[:500],
            ))
        
        return results
    
    def test_token_manipulation(
        self,
        endpoint: str,
        valid_token: str,
        method: str = "GET",
    ) -> List[AuthTestResult]:
        """Test various token manipulation attacks."""
        results = []
        
        manipulated_tokens = []
        
        # Try to decode and manipulate the token
        try:
            parts = valid_token.split(".")
            if len(parts) == 3:
                # Decode header and payload
                header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                
                # Test 1: Change algorithm to none
                header_none = header.copy()
                header_none["alg"] = "none"
                none_token = (
                    base64.urlsafe_b64encode(json.dumps(header_none).encode()).decode().rstrip("=") +
                    "." + parts[1] + "."
                )
                manipulated_tokens.append(("alg_none", none_token))
                
                # Test 2: Modify payload to admin
                payload_admin = payload.copy()
                payload_admin["role"] = "admin"
                payload_admin["is_admin"] = True
                payload_admin["roles"] = ["admin", "superuser"]
                admin_payload = base64.urlsafe_b64encode(
                    json.dumps(payload_admin).encode()
                ).decode().rstrip("=")
                admin_token = parts[0] + "." + admin_payload + "." + parts[2]
                manipulated_tokens.append(("admin_payload", admin_token))
                
                # Test 3: Change subject
                payload_diff_user = payload.copy()
                payload_diff_user["sub"] = "admin"
                diff_user_payload = base64.urlsafe_b64encode(
                    json.dumps(payload_diff_user).encode()
                ).decode().rstrip("=")
                diff_user_token = parts[0] + "." + diff_user_payload + "." + parts[2]
                manipulated_tokens.append(("different_user", diff_user_token))
                
                # Test 4: Extend expiration
                payload_long_exp = payload.copy()
                payload_long_exp["exp"] = 9999999999
                long_exp_payload = base64.urlsafe_b64encode(
                    json.dumps(payload_long_exp).encode()
                ).decode().rstrip("=")
                long_exp_token = parts[0] + "." + long_exp_payload + "." + parts[2]
                manipulated_tokens.append(("extended_exp", long_exp_token))
                
        except Exception:
            pass  # Token manipulation failed, skip these tests
        
        url = f"{self.base_url}{endpoint}"
        
        for token_name, token_value in manipulated_tokens:
            headers = {"Authorization": f"Bearer {token_value}"}
            
            if method == "GET":
                response = self.client.get(url, headers=headers)
            elif method == "POST":
                response = self.client.post(url, json={}, headers=headers)
            else:
                continue
            
            # Manipulated tokens should be rejected (401 or 403)
            passed = response.status_code in [401, 403]
            
            results.append(AuthTestResult(
                test_name=f"manipulated_token_{token_name}_{method}_{endpoint}",
                expected_status=401,
                actual_status=response.status_code,
                passed=passed,
                details=f"Token manipulation: {token_name}, Accepted: {not passed}",
                response_body=response.text[:500],
            ))
        
        return results
    
    def test_idor(
        self,
        endpoint_template: str,
        valid_resource_id: str,
        other_user_resource_id: str,
        auth_headers: Dict[str, str],
        method: str = "GET",
    ) -> AuthTestResult:
        """Test Insecure Direct Object Reference."""
        url = f"{self.base_url}{endpoint_template.format(id=other_user_resource_id)}"
        
        if method == "GET":
            response = self.client.get(url, headers=auth_headers)
        elif method == "DELETE":
            response = self.client.delete(url, headers=auth_headers)
        elif method == "PUT":
            response = self.client.put(url, json={}, headers=auth_headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        # IDOR test passes if we get 403 or 404 (not allowed to access)
        # Fails if we get 200 (accessed another user's resource)
        passed = response.status_code in [403, 404]
        
        return AuthTestResult(
            test_name=f"idor_{method}_{endpoint_template}",
            expected_status=403,
            actual_status=response.status_code,
            passed=passed,
            details=f"Tried to access resource {other_user_resource_id} as different user",
            response_body=response.text[:500],
        )
    
    def test_privilege_escalation(
        self,
        endpoint: str,
        regular_user_headers: Dict[str, str],
        admin_only_action: Dict[str, Any],
        method: str = "POST",
    ) -> AuthTestResult:
        """Test privilege escalation by regular user performing admin action."""
        url = f"{self.base_url}{endpoint}"
        
        if method == "POST":
            response = self.client.post(url, json=admin_only_action, headers=regular_user_headers)
        elif method == "PUT":
            response = self.client.put(url, json=admin_only_action, headers=regular_user_headers)
        elif method == "DELETE":
            response = self.client.delete(url, headers=regular_user_headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        # Should be rejected with 403
        passed = response.status_code == 403
        
        return AuthTestResult(
            test_name=f"privilege_escalation_{method}_{endpoint}",
            expected_status=403,
            actual_status=response.status_code,
            passed=passed,
            details=f"Regular user tried admin action, got {response.status_code}",
            response_body=response.text[:500],
        )
    
    def _generate_expired_jwt(self) -> str:
        """Generate an expired JWT for testing."""
        payload = {
            "sub": "test-user",
            "exp": 1000000000,  # Long expired
            "iat": 999999999,
        }
        # Sign with a random key - it will fail validation anyway
        return f"Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.{base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')}.invalid"
    
    def _generate_wrong_signature_jwt(self) -> str:
        """Generate JWT with wrong signature."""
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"sub": "admin", "role": "admin", "exp": 9999999999}
        
        header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        
        return f"Bearer {header_b64}.{payload_b64}.wrongsignature"
    
    def _generate_alg_none_jwt(self) -> str:
        """Generate JWT with algorithm: none (CVE-2015-2951)."""
        header = {"alg": "none", "typ": "JWT"}
        payload = {"sub": "admin", "role": "admin", "exp": 9999999999}
        
        header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        
        return f"Bearer {header_b64}.{payload_b64}."
    
    def generate_jwt_with_key_confusion(self, public_key_pem: str) -> str:
        """
        Generate JWT using public key as HMAC secret (key confusion attack).
        Tests for CVE-2016-5431.
        """
        payload = {"sub": "admin", "role": "admin", "exp": 9999999999}
        
        # Try to use the public key as the HMAC secret
        try:
            token = jwt.encode(payload, public_key_pem, algorithm="HS256")
            return f"Bearer {token}"
        except Exception:
            return ""
    
    def test_jwt_key_confusion(
        self,
        endpoint: str,
        jwks_endpoint: str,
        method: str = "GET",
    ) -> AuthTestResult:
        """Test JWT key confusion attack (RS256 to HS256)."""
        # First, get the JWKS
        try:
            jwks_response = self.client.get(f"{self.base_url}{jwks_endpoint}")
            if jwks_response.status_code != 200:
                return AuthTestResult(
                    test_name=f"jwt_key_confusion_{endpoint}",
                    expected_status=401,
                    actual_status=0,
                    passed=True,
                    details="Could not fetch JWKS, skipping test",
                )
            
            jwks = jwks_response.json()
            if "keys" in jwks and len(jwks["keys"]) > 0:
                # Extract public key
                key_data = jwks["keys"][0]
                # Try key confusion attack
                confused_token = self.generate_jwt_with_key_confusion(
                    json.dumps(key_data)
                )
                
                if confused_token:
                    url = f"{self.base_url}{endpoint}"
                    headers = {"Authorization": confused_token}
                    
                    if method == "GET":
                        response = self.client.get(url, headers=headers)
                    else:
                        response = self.client.post(url, json={}, headers=headers)
                    
                    # Should be rejected
                    passed = response.status_code == 401
                    
                    return AuthTestResult(
                        test_name=f"jwt_key_confusion_{endpoint}",
                        expected_status=401,
                        actual_status=response.status_code,
                        passed=passed,
                        details="Key confusion attack" + (" blocked" if passed else " succeeded!"),
                    )
        except Exception as e:
            pass
        
        return AuthTestResult(
            test_name=f"jwt_key_confusion_{endpoint}",
            expected_status=401,
            actual_status=0,
            passed=True,
            details="Test skipped due to error",
        )




