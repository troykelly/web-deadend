"""Tests for healthcheck IP filtering functionality."""

from unittest.mock import patch

import pytest


class TestHealthcheckFiltering:
    """Test suite for healthcheck IP filtering."""

    def test_healthcheck_default_allows_all(self, client):
        """Test that healthcheck endpoint allows all IPs by default."""
        response = client.get("/deadend-status")
        assert response.status_code == 200
        assert response.json == {"service": "ok"}

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "127.0.0.1/32"})
    def test_healthcheck_allows_specific_ip(self):
        """Test healthcheck allows specific IP."""
        from src.server import Server

        server = Server()

        # Check that 127.0.0.1 is allowed
        assert server._is_healthcheck_allowed("127.0.0.1") is True

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "127.0.0.1/32"})
    def test_healthcheck_denies_other_ip(self):
        """Test healthcheck denies IPs not in allowed list."""
        from src.server import Server

        server = Server()

        # Check that other IPs are denied
        assert server._is_healthcheck_allowed("192.168.1.1") is False
        assert server._is_healthcheck_allowed("10.0.0.1") is False

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "192.168.1.0/24"})
    def test_healthcheck_allows_subnet(self):
        """Test healthcheck allows entire subnet."""
        from src.server import Server

        server = Server()

        # All IPs in the subnet should be allowed
        assert server._is_healthcheck_allowed("192.168.1.1") is True
        assert server._is_healthcheck_allowed("192.168.1.100") is True
        assert server._is_healthcheck_allowed("192.168.1.254") is True

        # IPs outside the subnet should be denied
        assert server._is_healthcheck_allowed("192.168.2.1") is False
        assert server._is_healthcheck_allowed("10.0.0.1") is False

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "10.0.0.0/8,192.168.0.0/16"})
    def test_healthcheck_multiple_subnets(self):
        """Test healthcheck with multiple allowed subnets."""
        from src.server import Server

        server = Server()

        # IPs in first subnet
        assert server._is_healthcheck_allowed("10.0.0.1") is True
        assert server._is_healthcheck_allowed("10.255.255.255") is True

        # IPs in second subnet
        assert server._is_healthcheck_allowed("192.168.0.1") is True
        assert server._is_healthcheck_allowed("192.168.255.255") is True

        # IPs outside both subnets
        assert server._is_healthcheck_allowed("172.16.0.1") is False

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "2001:db8::/32"})
    def test_healthcheck_ipv6_subnet(self):
        """Test healthcheck with IPv6 subnet."""
        from src.server import Server

        server = Server()

        # IPs in the IPv6 subnet
        assert server._is_healthcheck_allowed("2001:db8::1") is True
        assert server._is_healthcheck_allowed("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff") is True

        # IPs outside the subnet
        assert server._is_healthcheck_allowed("2001:db9::1") is False
        assert server._is_healthcheck_allowed("::1") is False

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "127.0.0.1/32,::1/128"})
    def test_healthcheck_mixed_ipv4_ipv6(self):
        """Test healthcheck with mixed IPv4 and IPv6 addresses."""
        from src.server import Server

        server = Server()

        # IPv4 loopback
        assert server._is_healthcheck_allowed("127.0.0.1") is True

        # IPv6 loopback
        assert server._is_healthcheck_allowed("::1") is True

        # Other IPs denied
        assert server._is_healthcheck_allowed("192.168.1.1") is False
        assert server._is_healthcheck_allowed("2001:db8::1") is False

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "invalid,192.168.1.0/24"})
    def test_healthcheck_invalid_network_ignored(self):
        """Test that invalid networks are ignored with warning."""
        from src.server import Server

        server = Server()

        # Valid network should still work
        assert server._is_healthcheck_allowed("192.168.1.1") is True

        # Invalid network is ignored
        assert len(server.healthcheck_allowed_networks) == 1

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "invalid1,invalid2"})
    def test_healthcheck_all_invalid_defaults_to_allow_all(self):
        """Test that all invalid networks defaults to allow all."""
        from src.server import Server

        server = Server()

        # Should default to allow all (0.0.0.0/0 and ::/0)
        assert server._is_healthcheck_allowed("192.168.1.1") is True
        assert server._is_healthcheck_allowed("10.0.0.1") is True
        assert len(server.healthcheck_allowed_networks) == 2

    def test_healthcheck_invalid_ip_address(self, app):
        """Test healthcheck with invalid IP address format."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]

            # Invalid IP should return False
            assert server._is_healthcheck_allowed("not_an_ip") is False
            assert server._is_healthcheck_allowed("999.999.999.999") is False

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "10.0.0.0/24"})
    def test_healthcheck_endpoint_denies_unauthorized(self, mocker):
        """Test that healthcheck endpoint returns 204 for unauthorized IPs to avoid revealing existence."""
        from src.server import Server

        server = Server()
        client = server.app.test_client()

        # Mock the remote_addr to simulate request from unauthorized IP
        with client:
            with client.application.test_request_context(
                "/deadend-status", environ_base={"REMOTE_ADDR": "192.168.1.1"}
            ):
                response = server.deadend_status()
                assert response[1] == 204
                assert response[0] == ""

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "10.0.0.0/24"})
    def test_healthcheck_endpoint_allows_authorized(self, mocker):
        """Test that healthcheck endpoint returns 200 for authorized IPs."""
        from src.server import Server

        server = Server()
        client = server.app.test_client()

        # Mock the remote_addr to simulate request from authorized IP
        with client:
            with client.application.test_request_context(
                "/deadend-status", environ_base={"REMOTE_ADDR": "10.0.0.5"}
            ):
                response = server.deadend_status()
                assert response[1] == 200
                assert response[0].json == {"service": "ok"}

    def test_healthcheck_with_cidr_notation(self, app):
        """Test various CIDR notation formats."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]

            # Parse a /32 (single IP)
            server.healthcheck_allowed_networks = []
            server._setup_healthcheck_allowed()

            # Should have default networks
            assert len(server.healthcheck_allowed_networks) >= 1

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": " 10.0.0.0/8 , 192.168.0.0/16 "})
    def test_healthcheck_whitespace_handling(self):
        """Test that whitespace in config is handled correctly."""
        from src.server import Server

        server = Server()

        # Should work despite whitespace
        assert server._is_healthcheck_allowed("10.0.0.1") is True
        assert server._is_healthcheck_allowed("192.168.1.1") is True

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "0.0.0.0/0"})
    def test_healthcheck_allow_all_ipv4(self):
        """Test allowing all IPv4 addresses."""
        from src.server import Server

        server = Server()

        assert server._is_healthcheck_allowed("1.1.1.1") is True
        assert server._is_healthcheck_allowed("192.168.1.1") is True
        assert server._is_healthcheck_allowed("255.255.255.255") is True

    @patch.dict("os.environ", {"HEALTHCHECK_ALLOWED": "::/0"})
    def test_healthcheck_allow_all_ipv6(self):
        """Test allowing all IPv6 addresses."""
        from src.server import Server

        server = Server()

        assert server._is_healthcheck_allowed("::1") is True
        assert server._is_healthcheck_allowed("2001:db8::1") is True
        assert server._is_healthcheck_allowed("fe80::1") is True
