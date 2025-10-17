"""
Tests for MD5 filter in Jinja2 templates.
"""

import hashlib
import json


class TestMD5Filter:
    """Test the md5 filter for template rendering."""

    def test_md5_filter_basic(self, client, tmp_path, monkeypatch):
        """Test basic MD5 hash generation in template."""
        # Create test responses.yaml with MD5 in response
        test_yaml = tmp_path / "test_responses.yaml"
        test_yaml.write_text(
            """
"/reporting/%IP%/%EPOCH%/ping.txt":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "hello - request {{ request.id }} - md5: {{ ('hello - request ' + request.id) | md5 }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/reporting/192_168_1_100/1729117800/ping.txt")

        assert response.status_code == 200
        data = response.data.decode()

        # Response should contain both the request ID and its MD5
        assert "hello - request" in data
        assert "md5:" in data

        # Extract request ID from response
        # Format: "hello - request {uuid} - md5: {hash}"
        parts = data.split(" - md5: ")
        assert len(parts) == 2

        request_part = parts[0]
        md5_hash = parts[1].strip()

        # Verify MD5 is valid (32 hex characters)
        assert len(md5_hash) == 32
        assert all(c in "0123456789abcdef" for c in md5_hash)

        # Compute expected MD5
        expected_md5 = hashlib.md5(request_part.encode("utf-8")).hexdigest()
        assert md5_hash == expected_md5

    def test_md5_filter_static_value(self, client, tmp_path, monkeypatch):
        """Test MD5 filter with known static value."""
        # Create test responses.yaml with static MD5 test
        test_yaml = tmp_path / "test_md5.yaml"
        test_yaml.write_text(
            """
"/test/md5":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "{{ 'hello' | md5 }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/test/md5")

        assert response.status_code == 200
        # MD5 of "hello" is known value
        expected = hashlib.md5(b"hello").hexdigest()
        assert response.data.decode() == expected
        assert response.data.decode() == "5d41402abc4b2a76b9719d911017c592"

    def test_md5_filter_with_variables(self, client, tmp_path, monkeypatch):
        """Test MD5 filter with template variable concatenation."""
        test_yaml = tmp_path / "test_md5_vars.yaml"
        test_yaml.write_text(
            """
"/test/md5/{value}":
  "GET":
    "mediatype": "application/json"
    "base64": False
    "responsestatus": 200
    "body": |
      {
        "value": "{{ matched.value }}",
        "md5": "{{ matched.value | md5 }}"
      }
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/test/md5/testing123")

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data["value"] == "testing123"
        expected_md5 = hashlib.md5(b"testing123").hexdigest()
        assert data["md5"] == expected_md5
        # Verify format - should be 32 hex characters
        assert len(data["md5"]) == 32
        assert all(c in "0123456789abcdef" for c in data["md5"])

    def test_md5_filter_in_xml_response(self, client, tmp_path, monkeypatch):
        """Test MD5 filter in XML response (OTA use case)."""
        test_yaml = tmp_path / "test_md5_xml.yaml"
        test_yaml.write_text(
            """
"/ota/update":
  "POST":
    "mediatype": "application/xml"
    "base64": False
    "responsestatus": 200
    "body": |
      <?xml version="1.0" encoding="UTF-8"?>
      <root>
        <url>http://example.com/update.zip</url>
        <md5>{{ 'update.zip content' | md5 }}</md5>
      </root>
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.post("/ota/update", content_type="application/json", data="{}")

        assert response.status_code == 200
        assert b"<md5>" in response.data
        assert b"</md5>" in response.data

        # Extract MD5 from XML
        xml_data = response.data.decode()
        import re

        md5_match = re.search(r"<md5>(.*?)</md5>", xml_data)
        assert md5_match is not None
        md5_value = md5_match.group(1)

        # Verify it's a valid MD5
        assert len(md5_value) == 32
        expected = hashlib.md5(b"update.zip content").hexdigest()
        assert md5_value == expected

    def test_md5_filter_empty_string(self, client, tmp_path, monkeypatch):
        """Test MD5 filter with empty string."""
        test_yaml = tmp_path / "test_md5_empty.yaml"
        test_yaml.write_text(
            """
"/test/empty":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "{{ '' | md5 }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/test/empty")

        assert response.status_code == 200
        # MD5 of empty string
        expected = hashlib.md5(b"").hexdigest()
        assert response.data.decode() == expected
        assert response.data.decode() == "d41d8cd98f00b204e9800998ecf8427e"

    def test_md5_filter_non_string_coercion(self, client, tmp_path, monkeypatch):
        """Test MD5 filter coerces non-string values to string."""
        test_yaml = tmp_path / "test_md5_number.yaml"
        test_yaml.write_text(
            """
"/test/number":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "{{ request.epoch | md5 }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/test/number")

        assert response.status_code == 200
        md5_value = response.data.decode()

        # Should be valid MD5 hash
        assert len(md5_value) == 32
        assert all(c in "0123456789abcdef" for c in md5_value)
