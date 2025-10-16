"""
Tests for utility functions in response/utils.py
"""

from src.response.utils import route_matches_url, safe_ip


class TestSafeIP:
    """Test the safe_ip() function for IP address sanitization."""

    def test_ipv4_conversion(self):
        """Test IPv4 address conversion to filesystem-safe format."""
        assert safe_ip("192.168.1.100") == "192_168_1_100"
        assert safe_ip("10.0.0.1") == "10_0_0_1"
        assert safe_ip("255.255.255.255") == "255_255_255_255"

    def test_ipv6_conversion(self):
        """Test IPv6 address conversion to filesystem-safe format."""
        assert safe_ip("2001:db8::1") == "2001_db8__1"
        assert safe_ip("::1") == "__1"
        assert safe_ip("fe80::1") == "fe80__1"
        assert (
            safe_ip("2001:0db8:0000:0000:0000:0000:0000:0001")
            == "2001_0db8_0000_0000_0000_0000_0000_0001"
        )

    def test_empty_string(self):
        """Test handling of empty string."""
        assert safe_ip("") == "unknown"

    def test_none_value(self):
        """Test handling of None value."""
        assert safe_ip(None) == "unknown"


class TestRouteMatching:
    """Test the route_matches_url() function for various matching strategies."""

    def test_exact_match(self):
        """Test plain text exact URL matching."""
        assert route_matches_url("/test/path", "/test/path") == {}
        assert route_matches_url("/test", "/test") == {}
        assert route_matches_url("/", "/") == {}

    def test_exact_no_match(self):
        """Test that non-matching URLs return None."""
        assert route_matches_url("/test/path", "/other/path") is None
        assert route_matches_url("/test", "/test/extra") is None

    def test_placeholder_match(self):
        """Test placeholder {varname} matching."""
        result = route_matches_url("/user/{id}", "/user/123")
        assert result == {"id": "123"}

        result = route_matches_url("/user/{id}/post/{post_id}", "/user/456/post/789")
        assert result == {"id": "456", "post_id": "789"}

    def test_placeholder_no_match(self):
        """Test placeholder matching with wrong number of segments."""
        assert route_matches_url("/user/{id}", "/user/123/extra") is None
        assert route_matches_url("/user/{id}/post", "/user/123") is None

    def test_percent_wildcard_ip(self):
        """Test %IP% wildcard matching."""
        result = route_matches_url("/reporting/%IP%/data", "/reporting/192_168_1_100/data")
        assert result == {"IP": "192_168_1_100"}

        result = route_matches_url("/reporting/%IP%/data", "/reporting/2001_db8__1/data")
        assert result == {"IP": "2001_db8__1"}

    def test_percent_wildcard_ip_invalid(self):
        """Test %IP% wildcard with invalid IP format."""
        # %IP% should only match alphanumeric and underscores
        assert route_matches_url("/reporting/%IP%/data", "/reporting/invalid.ip/data") is None
        assert (
            route_matches_url("/reporting/%IP%/data", "/reporting/192.168.1.1/data") is None
        )  # dots not allowed

    def test_percent_wildcard_epoch(self):
        """Test %EPOCH% wildcard matching."""
        result = route_matches_url("/logs/%EPOCH%/file", "/logs/1729117800/file")
        assert result == {"EPOCH": "1729117800"}

        result = route_matches_url("/logs/%EPOCH%", "/logs/0")
        assert result == {"EPOCH": "0"}

    def test_percent_wildcard_epoch_invalid(self):
        """Test %EPOCH% wildcard with non-numeric value."""
        assert route_matches_url("/logs/%EPOCH%/file", "/logs/notanumber/file") is None
        assert route_matches_url("/logs/%EPOCH%/file", "/logs/123abc/file") is None

    def test_percent_wildcard_combined(self):
        """Test multiple percent wildcards in one route."""
        result = route_matches_url(
            "/reporting/%IP%/%EPOCH%/ping.txt", "/reporting/192_168_1_100/1729117800/ping.txt"
        )
        assert result == {"IP": "192_168_1_100", "EPOCH": "1729117800"}

    def test_regex_match(self):
        """Test regex pattern matching with r/ prefix."""
        result = route_matches_url(r"r/\/test\/(?P<id>\d+)", "/test/123")
        assert result == {"id": "123"}

        result = route_matches_url(
            r"r/\/api\/(?P<version>v\d+)\/(?P<resource>\w+)", "/api/v1/users"
        )
        assert result == {"version": "v1", "resource": "users"}

    def test_regex_no_match(self):
        """Test regex pattern with no match."""
        assert route_matches_url(r"r/\/test\/(?P<id>\d+)", "/test/abc") is None
        assert route_matches_url(r"r/\/test\/(?P<id>\d+)", "/other/123") is None

    def test_regex_without_named_groups(self):
        """Test regex without named groups returns empty dict."""
        result = route_matches_url(r"r/\/test\/\d+", "/test/123")
        assert result == {}
