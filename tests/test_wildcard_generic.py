"""
Tests for generic %WILDCARD% pattern matching.
"""


class TestGenericWildcards:
    """Test generic %WILDCARD% pattern matching in routes."""

    def test_single_wildcard_uppercase(self, client, tmp_path, monkeypatch):
        """Test %ORIGINALREQUESTID% wildcard."""
        test_yaml = tmp_path / "test_wildcard.yaml"
        test_yaml.write_text(
            """
"/reporting/%ORIGINALREQUESTID%/ping.txt":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "hello - request {{ path.ORIGINALREQUESTID }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        # Test with various request ID formats
        response = client.get("/reporting/abc123def456/ping.txt")

        assert response.status_code == 200
        assert response.data.decode() == "hello - request abc123def456"

    def test_multiple_wildcards(self, client, tmp_path, monkeypatch):
        """Test multiple wildcards in same route."""
        test_yaml = tmp_path / "test_multi_wildcard.yaml"
        test_yaml.write_text(
            """
"/files/%USERID%/%FILENAME%/download":
  "GET":
    "mediatype": "application/json"
    "base64": False
    "responsestatus": 200
    "body": |
      {
        "user": "{{ path.USERID }}",
        "file": "{{ path.FILENAME }}"
      }
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/files/user123/document.pdf/download")

        assert response.status_code == 200
        import json

        data = json.loads(response.data)
        assert data["user"] == "user123"
        assert data["file"] == "document.pdf"

    def test_wildcard_with_uuid_format(self, client, tmp_path, monkeypatch):
        """Test wildcard matching UUIDs."""
        test_yaml = tmp_path / "test_uuid_wildcard.yaml"
        test_yaml.write_text(
            """
"/api/%REQUESTID%/status":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "Status for {{ path.REQUESTID }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        uuid = "550e8400-e29b-41d4-a716-446655440000"
        response = client.get(f"/api/{uuid}/status")

        assert response.status_code == 200
        assert response.data.decode() == f"Status for {uuid}"

    def test_wildcard_with_special_chars(self, client, tmp_path, monkeypatch):
        """Test wildcard matching values with special characters."""
        test_yaml = tmp_path / "test_special_wildcard.yaml"
        test_yaml.write_text(
            """
"/data/%VALUE%/info":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "Value: {{ path.VALUE }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        # Test with dashes, underscores, numbers
        response = client.get("/data/test-value_123/info")

        assert response.status_code == 200
        assert response.data.decode() == "Value: test-value_123"

    def test_backwards_compatibility_with_matched(self, client, tmp_path, monkeypatch):
        """Test that 'matched' still works (backwards compatibility)."""
        test_yaml = tmp_path / "test_matched_compat.yaml"
        test_yaml.write_text(
            """
"/legacy/%ID%/test":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "ID via matched: {{ matched.ID }}, ID via path: {{ path.ID }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/legacy/test123/test")

        assert response.status_code == 200
        assert response.data.decode() == "ID via matched: test123, ID via path: test123"

    def test_wildcard_ip_format(self, client, tmp_path, monkeypatch):
        """Test %IP% wildcard with safe IP format."""
        test_yaml = tmp_path / "test_ip_wildcard.yaml"
        test_yaml.write_text(
            """
"/reporting/%IP%/%EPOCH%/ping.txt":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "IP: {{ path.IP }}, Epoch: {{ path.EPOCH }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/reporting/192_168_1_100/1729117800/ping.txt")

        assert response.status_code == 200
        assert response.data.decode() == "IP: 192_168_1_100, Epoch: 1729117800"

    def test_wildcard_no_match_different_segments(self, client, tmp_path, monkeypatch):
        """Test wildcard doesn't match when segment count differs."""
        test_yaml = tmp_path / "test_no_match.yaml"
        test_yaml.write_text(
            """
"/api/%ID%/data":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "Found"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        # Too many segments
        response = client.get("/api/123/data/extra")
        assert response.status_code == 204  # No match

        # Too few segments
        response = client.get("/api/123")
        assert response.status_code == 204  # No match

    def test_wildcard_with_md5_filter(self, client, tmp_path, monkeypatch):
        """Test wildcard values can be used with MD5 filter."""
        test_yaml = tmp_path / "test_wildcard_md5.yaml"
        test_yaml.write_text(
            """
"/hash/%VALUE%":
  "GET":
    "mediatype": "application/json"
    "base64": False
    "responsestatus": 200
    "body": |
      {
        "original": "{{ path.VALUE }}",
        "hash": "{{ path.VALUE | md5 }}"
      }
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        response = client.get("/hash/hello")

        assert response.status_code == 200
        import hashlib
        import json

        data = json.loads(response.data)
        assert data["original"] == "hello"
        assert data["hash"] == hashlib.md5(b"hello").hexdigest()

    def test_wildcard_empty_segment(self, client, tmp_path, monkeypatch):
        """Test wildcard with empty segment."""
        test_yaml = tmp_path / "test_empty_wildcard.yaml"
        test_yaml.write_text(
            """
"/test/%VALUE%/end":
  "GET":
    "mediatype": "text/plain"
    "base64": False
    "responsestatus": 200
    "body": "Value: {{ path.VALUE }}"
"""
        )

        monkeypatch.setenv("RESPONSES_FILE", str(test_yaml))

        # Empty segment should still match (empty string)
        response = client.get("/test//end")

        assert response.status_code == 200
        assert response.data.decode() == "Value: "
