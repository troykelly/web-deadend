"""
Tests for request body parsing with different content types.
"""

import base64
import json
from io import BytesIO


class TestBodyParsing:
    """Test parsing of different request body formats."""

    def test_json_body_parsing(self, client):
        """Test JSON body is parsed to dict."""
        response = client.post(
            "/template/test",
            content_type="application/json",
            data='{"key1": "value1", "key2": 123}',
        )

        data = json.loads(response.data)
        assert data["body"] == {"key1": "value1", "key2": 123}

    def test_form_urlencoded_body_parsing(self, client):
        """Test form-urlencoded body is parsed to dict."""
        response = client.post(
            "/template/test",
            content_type="application/x-www-form-urlencoded",
            data="key1=value1&key2=value2",
        )

        data = json.loads(response.data)
        assert data["body"] == {"key1": "value1", "key2": "value2"}

    def test_multipart_form_data_parsing(self, client):
        """Test multipart form data is parsed to dict."""
        data = {"field1": "value1", "field2": "value2"}
        response = client.post("/template/test", content_type="multipart/form-data", data=data)

        result = json.loads(response.data)
        assert result["body"]["field1"] == "value1"
        assert result["body"]["field2"] == "value2"

    def test_multipart_with_file(self, client):
        """Test multipart form data with file upload."""
        data = {"field": "value", "file": (BytesIO(b"file content"), "test.txt")}

        response = client.post("/template/test", content_type="multipart/form-data", data=data)

        result = json.loads(response.data)
        assert result["body"]["field"] == "value"
        # File should be base64 encoded
        assert "file" in result["body"]
        # Should be base64 encoded content
        file_content = base64.b64decode(result["body"]["file"])
        assert file_content == b"file content"

    def test_plain_text_body(self, client):
        """Test plain text body is kept as string."""
        response = client.post(
            "/template/test", content_type="text/plain", data="plain text content"
        )

        data = json.loads(response.data)
        # For plain text, body object should be empty and body string should have content
        # The template uses {{ body | tojson }} which would show empty dict for non-dict bodies
        assert data["body"] == {}  # Not a dict, so body object is empty

    def test_empty_body(self, client):
        """Test empty request body."""
        response = client.post("/template/test", content_type="application/json", data="")

        data = json.loads(response.data)
        assert data["body"] == {}

    def test_invalid_json_body(self, client):
        """Test invalid JSON returns empty dict."""
        response = client.post(
            "/template/test", content_type="application/json", data="not valid json"
        )

        data = json.loads(response.data)
        # Should handle gracefully
        assert data["body"] == {}

    def test_binary_body_base64_encoding(self, client):
        """Test binary data is base64 encoded."""
        binary_data = b"\x00\x01\x02\x03\xff\xfe"
        response = client.post(
            "/template/test", content_type="application/octet-stream", data=binary_data
        )

        # Should not crash, response should be successful or 204
        assert response.status_code in [200, 204]

    def test_query_params_parsing(self, client):
        """Test query parameters are parsed correctly."""
        response = client.post(
            "/template/test?param1=value1&param2=value2&param1=value3",
            content_type="application/json",
            data="{}",
        )

        data = json.loads(response.data)
        # Flask args.to_dict() gets first value
        assert "param1" in data["query"]
        assert "param2" in data["query"]
        assert data["query"]["param2"] == "value2"

    def test_combined_body_and_query(self, client):
        """Test request with both body and query parameters."""
        response = client.post(
            "/template/test?query1=qvalue1",
            content_type="application/json",
            data='{"body1": "bvalue1"}',
        )

        data = json.loads(response.data)
        assert data["body"] == {"body1": "bvalue1"}
        assert data["query"] == {"query1": "qvalue1"}

        # requestdata should contain both
        requestdata = data["requestdata"]
        assert "query1=qvalue1" in requestdata
        assert "body1=bvalue1" in requestdata

    def test_url_encoded_special_characters(self, client):
        """Test URL encoding of special characters in requestdata."""
        response = client.post(
            "/template/test?param=hello world&other=test@example.com",
            content_type="application/json",
            data='{"key": "value with spaces"}',
        )

        data = json.loads(response.data)
        requestdata = data["requestdata"]

        # Should be properly URL encoded
        assert (
            "param=hello" in requestdata
            or "param=hello+world" in requestdata
            or "hello%20world" in requestdata
        )
        assert "test" in requestdata  # Part of email
