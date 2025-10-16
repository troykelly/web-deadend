"""
Tests for template context variables and Jinja2 rendering.
"""

import json


class TestTemplateContext:
    """Test that all template variables are available and work correctly."""

    def test_request_id_in_template(self, client):
        """Test request.id is available in templates."""
        response = client.post(
            "/template/test", content_type="application/json", data='{"test": "data"}'
        )

        data = json.loads(response.data)
        assert "id" in data
        assert len(data["id"]) == 36  # UUID format

    def test_safe_ip_in_template(self, client):
        """Test request.safe_ip converts IP to safe format."""
        response = client.post(
            "/template/test",
            content_type="application/json",
            data='{"test": "data"}',
            environ_base={"REMOTE_ADDR": "192.168.1.100"},
        )

        data = json.loads(response.data)
        assert data["safe_ip"] == "192_168_1_100"

    def test_epoch_in_template(self, client):
        """Test request.epoch provides Unix timestamp."""
        response = client.post(
            "/template/test", content_type="application/json", data='{"test": "data"}'
        )

        data = json.loads(response.data)
        assert "epoch" in data
        assert isinstance(data["epoch"], int)
        assert data["epoch"] > 1700000000  # Reasonable timestamp

    def test_body_object_in_template(self, client):
        """Test body object is available for structured data."""
        response = client.post(
            "/template/test",
            content_type="application/json",
            data='{"key1": "value1", "key2": "value2"}',
        )

        data = json.loads(response.data)
        assert data["body"] == {"key1": "value1", "key2": "value2"}

    def test_query_object_in_template(self, client):
        """Test query object is available from query parameters."""
        response = client.post(
            "/template/test?param1=value1&param2=value2", content_type="application/json", data="{}"
        )

        data = json.loads(response.data)
        assert data["query"] == {"param1": "value1", "param2": "value2"}

    def test_requestdata_encoding(self, client):
        """Test requestdata provides URL-encoded query string."""
        response = client.post(
            "/template/test?param1=value1&param2=value2",
            content_type="application/json",
            data='{"key1": "data1", "key2": "data2"}',
        )

        data = json.loads(response.data)
        requestdata = data["requestdata"]

        # Should start with ?
        assert requestdata.startswith("?")
        # Should contain all params (order may vary)
        assert "param1=value1" in requestdata
        assert "param2=value2" in requestdata
        assert "key1=data1" in requestdata
        assert "key2=data2" in requestdata

    def test_matched_variables_placeholder(self, client):
        """Test matched.* variables from placeholder routes."""
        response = client.post("/test/myvalue", content_type="application/json", data="{}")

        data = json.loads(response.data)
        assert data["param"] == "myvalue"

    def test_matched_variables_wildcard(self, client):
        """Test matched.* variables from percent wildcards."""
        response = client.get("/test/192_168_1_100/1729117800")

        assert response.status_code == 200
        assert b"IP: 192_168_1_100" in response.data
        assert b"Epoch: 1729117800" in response.data

    def test_matched_variables_regex(self, client):
        """Test matched.* variables from regex routes."""
        response = client.get("/regex/testvalue/test")

        assert response.status_code == 200
        assert b"Regex value: testvalue" in response.data

    def test_empty_body_object(self, client):
        """Test body object is empty dict when no body."""
        response = client.post("/template/test", content_type="application/json", data="")

        data = json.loads(response.data)
        assert data["body"] == {}

    def test_empty_query_object(self, client):
        """Test query object is empty dict when no query params."""
        response = client.post("/template/test", content_type="application/json", data="{}")

        data = json.loads(response.data)
        assert data["query"] == {}

    def test_requestdata_empty_when_no_params(self, client):
        """Test requestdata is empty string when no body or query params."""
        response = client.post("/template/test", content_type="application/json", data="")

        data = json.loads(response.data)
        assert data["requestdata"] == ""
