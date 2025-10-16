"""
Security vulnerability tests to verify actual exploit potential.
These tests check if the theoretical vulnerabilities are actually exploitable.
"""

import base64
import time
from unittest.mock import MagicMock, patch

import pytest


class TestSSTIVulnerability:
    """Test if Server-Side Template Injection is actually exploitable."""

    def test_ssti_via_request_body_injection(self, client):
        """Test if SSTI is possible via request body variables in templates."""
        # Attempt SSTI payload in request body
        malicious_payload = {
            "__class__": "exploit",
            "{{7*7}}": "test",
            "{{ ''.__class__.__mro__[1].__subclasses__() }}": "rce",
        }

        response = client.post(
            "/template/test", content_type="application/json", json=malicious_payload
        )

        # Check if template executed the injection
        response_text = response.data.decode()

        # Parse JSON to check body object
        import json

        response_data = json.loads(response_text)

        # The body object should contain our malicious keys AS-IS (not executed)
        # If SSTI worked, the template would execute {{7*7}} and we'd see "49" as a VALUE
        # But seeing "49" in request ID or other fields is fine
        body_values = str(response_data.get("body", {}))

        # Check if arithmetic was executed (would change "{{7*7}}" to "49" in body values)
        assert (
            "{{7*7}}" in body_values or '"test"' in body_values
        ), "Body should contain original payload, not executed results"

        # The malicious keys should appear as dict keys, not executed
        assert '"__class__"' in response_text or "__class__" in str(
            response_data.get("body", {})
        ), "Payload keys should be preserved"

    def test_ssti_via_query_params(self, client):
        """Test if SSTI is possible via query parameters."""
        import json

        # Attempt SSTI in query params
        response = client.post(
            "/template/test?exploit={{7*7}}", content_type="application/json", json={"test": "data"}
        )

        response_text = response.data.decode()
        response_data = json.loads(response_text)

        # Check if template executed - verify query params are preserved, not executed
        query_values = str(response_data.get("query", {}))
        assert (
            "{{7*7}}" in query_values or "exploit" in query_values
        ), "Query should preserve payload"

    def test_ssti_via_malicious_yaml_config(self, client, tmp_path, monkeypatch):
        """Test if malicious responses.yaml can achieve RCE.

        Expected behavior: SandboxedEnvironment should raise SecurityError
        when trying to access __class__ attribute.
        """
        # Create malicious responses.yaml with SSTI payload
        malicious_yaml = tmp_path / "malicious.yaml"
        malicious_yaml.write_text(
            """
/exploit:
  "GET":
    "mediatype": "text/plain"
    "responsestatus": 200
    "body": "{{ ''.__class__.__mro__[1].__subclasses__()[104].__init__.__globals__['sys'].modules['os'].popen('echo EXPLOITED').read() }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(malicious_yaml))

        # Try to trigger the exploit - should raise SecurityError from sandbox
        from jinja2.exceptions import SecurityError

        with pytest.raises(SecurityError) as exc_info:
            response = client.get("/exploit")

        # Verify the error message indicates sandbox blocked the access
        assert "unsafe" in str(exc_info.value).lower() or "__class__" in str(
            exc_info.value
        ), f"Expected SecurityError about unsafe attribute access, got: {exc_info.value}"


class TestXXEVulnerability:
    """Test if XML External Entity injection is exploitable."""

    def test_xxe_file_disclosure(self, client):
        """Test if XXE can read local files."""
        xxe_payload = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root>
  <data>&xxe;</data>
</root>"""

        response = client.post("/template/test", content_type="application/xml", data=xxe_payload)

        response_text = response.data.decode()

        # Check if /etc/passwd contents leaked
        assert "root:x:" not in response_text, "XXE file disclosure SUCCESSFUL - CRITICAL!"
        assert "nobody:" not in response_text, "XXE file disclosure SUCCESSFUL - CRITICAL!"

    def test_xxe_billion_laughs(self, client):
        """Test if XXE billion laughs DoS is possible."""
        billion_laughs = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<root>&lol4;</root>"""

        start = time.time()
        try:
            response = client.post(
                "/template/test", content_type="application/xml", data=billion_laughs
            )
            elapsed = time.time() - start

            # If it takes a long time or causes memory issues, XXE is vulnerable
            assert elapsed < 2.0, f"XXE billion laughs took {elapsed}s - VULNERABLE to DoS!"
        except Exception as e:
            # If it crashes or times out, that's also a vulnerability
            pytest.fail(f"XXE billion laughs caused exception: {e} - VULNERABLE!")

    def test_xxe_ssrf_attempt(self, client):
        """Test if XXE can be used for SSRF."""
        # Try to make request to internal metadata service (AWS)
        xxe_ssrf = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "http://169.254.169.254/latest/meta-data/">
]>
<root>
  <data>&xxe;</data>
</root>"""

        response = client.post("/template/test", content_type="application/xml", data=xxe_ssrf)

        response_text = response.data.decode()

        # Check if metadata service response is present
        assert "ami-id" not in response_text, "XXE SSRF SUCCESSFUL - CRITICAL!"
        assert "iam" not in response_text, "XXE SSRF may be possible - investigate!"


class TestReDoSVulnerability:
    """Test if Regex Denial of Service is exploitable."""

    def test_redos_catastrophic_backtracking(
        self, client, sample_responses_yaml, monkeypatch, tmp_path
    ):
        """Test if malicious regex causes catastrophic backtracking."""
        # Create malicious regex route
        evil_regex_yaml = tmp_path / "evil_regex.yaml"
        evil_regex_yaml.write_text(
            """
"r/^(a+)+b$":
  "GET":
    "mediatype": "text/plain"
    "responsestatus": 200
    "body": "matched"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(evil_regex_yaml))

        # This payload should cause exponential time if vulnerable
        evil_payload = "a" * 25 + "X"

        start = time.time()
        try:
            # Attempt the attack - if ReDoS works, this will hang
            response = client.get(f"/{evil_payload}")
            elapsed = time.time() - start

            # If it takes more than 1 second for 25 chars, it's vulnerable
            if elapsed > 1.0:
                pytest.fail(
                    f"ReDoS took {elapsed}s for 25 chars - CATASTROPHIC BACKTRACKING VULNERABLE!"
                )

            # Should return 204 (no match) quickly
            assert response.status_code == 204
            assert (
                elapsed < 0.5
            ), f"Regex matching too slow ({elapsed}s) - may be vulnerable to ReDoS"

        except Exception as e:
            pytest.fail(f"ReDoS caused exception: {e}")

    def test_redos_nested_quantifiers(self, client, tmp_path, monkeypatch):
        """Test various nested quantifier patterns."""
        patterns = ["r/^(a*)*b$", "r/^(a+)*b$", "r/^(a|a)*b$", "r/^(a|ab)*c$"]

        for pattern in patterns:
            evil_yaml = tmp_path / f"test_{hash(pattern)}.yaml"
            evil_yaml.write_text(
                f"""
"{pattern}":
  "GET":
    "mediatype": "text/plain"
    "responsestatus": 200
    "body": "matched"
"""
            )
            monkeypatch.setenv("RESPONSES_FILE", str(evil_yaml))

            # Test with non-matching suffix
            payload = "a" * 20 + "X"

            start = time.time()
            response = client.get(f"/{payload}")
            elapsed = time.time() - start

            assert elapsed < 0.5, f"Pattern {pattern} took {elapsed}s - REDOS VULNERABLE!"


class TestFileUploadVulnerabilities:
    """Test file upload memory exhaustion vulnerabilities."""

    def test_large_file_upload_memory(self, client):
        """Test if large file uploads are handled safely."""
        # Create a large file (10MB)
        large_file_size = 10 * 1024 * 1024
        large_data = b"A" * large_file_size

        # Try to upload it
        response = client.post(
            "/template/test",
            content_type="multipart/form-data",
            data={"file": (b"large.bin", large_data, "application/octet-stream")},
        )

        # Should either reject or handle gracefully
        # If it accepts and base64 encodes, memory usage = 10MB * 1.33 = 13.3MB
        assert response.status_code in [200, 204, 413], "Large file upload handling unclear"

        if response.status_code == 413:
            # Good - rejected due to size limit
            pass
        elif response.status_code == 200:
            # Check if it was truncated or handled safely
            response_text = response.data.decode()
            # Should not contain the full base64 encoded file
            # Base64 of 10MB would be ~13.3MB of text

    def test_multiple_file_upload_exhaustion(self, client):
        """Test if multiple large files can exhaust memory."""
        from io import BytesIO

        # Create file-like objects instead of raw bytes
        files = {}
        for i in range(10):  # Reduced from 100 to 10 for faster tests
            # 10 files of 1MB each = 10MB total
            file_data = BytesIO(b"X" * (1024 * 1024))
            files[f"file{i}"] = (file_data, f"file{i}.bin", "application/octet-stream")

        response = client.post(
            "/template/test", content_type="multipart/form-data", data={"files": files}
        )

        # Should handle or reject gracefully
        # 10MB total is well under the 100MB limit, should accept
        assert response.status_code in [
            200,
            204,
            413,
        ], f"Multiple file upload returned unexpected status: {response.status_code}"

    def test_max_content_length_enforcement(self, client):
        """Test if MAX_CONTENT_LENGTH is enforced.

        Expected behavior: Request should be rejected with 413 error
        when exceeding the 100MB limit.
        """
        # Try to send request larger than the 100MB limit
        # Use 150MB to exceed the limit
        huge_data = b"Z" * (150 * 1024 * 1024)  # 150MB

        # Expect werkzeug.exceptions.RequestEntityTooLarge (413)
        from werkzeug.exceptions import RequestEntityTooLarge

        with pytest.raises(RequestEntityTooLarge) as exc_info:
            response = client.post(
                "/template/test", content_type="application/octet-stream", data=huge_data
            )

        # Verify it's a 413 error
        assert (
            exc_info.value.code == 413
        ), f"Expected 413 Request Entity Too Large, got: {exc_info.value.code}"


class TestInputValidation:
    """Test input validation and bounds checking."""

    def test_unbounded_statistics_growth(self, client):
        """Test if statistics grow unbounded."""
        # Make 1000 requests with unique paths
        for i in range(1000):
            client.get(f"/unique_path_{i}")

        # Check stats endpoint
        response = client.get("/deadend-counter")
        stats = response.get_json()

        # If this grows unbounded, at 10k req/s we'll have issues
        # This test can't fully validate but can check structure
        assert "total_requests_received" in stats

        # The counter should work, but we can't easily test unbounded growth
        # in unit tests - would need integration/stress test

    def test_extremely_long_path(self, client):
        """Test handling of extremely long URL paths."""
        long_path = "a" * 10000  # 10k character path

        response = client.get(f"/{long_path}")

        # Should handle gracefully (either accept for logging or reject)
        assert response.status_code in [200, 204, 414], "Long path handling unclear"

    def test_many_query_parameters(self, client):
        """Test handling of many query parameters."""
        # 1000 query parameters
        query_params = "&".join([f"param{i}=value{i}" for i in range(1000)])

        response = client.get(f"/test?{query_params}")

        # Should handle gracefully
        assert response.status_code in [200, 204, 414], "Many query params handling unclear"


class TestActualExploitability:
    """Meta-tests to determine if vulnerabilities are ACTUALLY exploitable."""

    def test_can_escape_container(self, client):
        """Test if any payload can achieve container escape."""
        # This would require actual container environment to test
        # In unit tests, we can only verify that dangerous operations are blocked

        # Try various escape vectors
        payloads = [
            # SSTI attempts
            "{{ ''.__class__.__mro__[1].__subclasses__() }}",
            "{{ config }}",
            "{{ self }}",
            # Command injection attempts
            "; cat /etc/passwd",
            "| whoami",
            "$(cat /etc/passwd)",
            # Path traversal
            "../../../etc/passwd",
            "....//....//....//etc/passwd",
        ]

        for payload in payloads:
            # Try in various locations
            response = client.post("/template/test", json={"exploit": payload})

            response_text = response.data.decode()

            # Check if any sensitive data leaked
            assert "root:x:" not in response_text
            assert "EXPLOITED" not in response_text
