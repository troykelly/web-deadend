"""Integration tests for ProxyFix middleware to verify real client IP extraction."""

import os
from unittest.mock import patch

import pytest
from flask import Flask

from src.server import Server


class TestProxyFixIntegration:
    """Test that ProxyFix correctly extracts real client IPs from X-Forwarded-For headers."""

    @patch.dict(os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0,::/0"})
    def test_trust_all_with_single_proxy_extracts_real_ip(self):
        """Test TRUSTED_PROXIES=0.0.0.0/0,::/0 with PROXY_DEPTH=1 (default) extracts real IP."""
        server = Server()
        client = server.app.test_client()

        # Simulate Traefik forwarding request: direct connection from 172.21.9.172 (Traefik)
        # with X-Forwarded-For containing real client IP 223.96.43.68
        response = client.get(
            "/test",
            environ_base={
                "REMOTE_ADDR": "172.21.9.172",
                "HTTP_X_FORWARDED_FOR": "223.96.43.68",
            },
        )

        assert response.status_code == 204

        # Verify that the request was logged with the REAL client IP, not the proxy IP
        # We need to check the GELF queue or logs to verify this
        # For now, we verify that ProxyFix middleware is installed correctly
        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 1  # Should be 1, not 100

    @patch.dict(
        os.environ,
        {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0,::/0", "PROXY_DEPTH": "2"},
    )
    def test_trust_all_with_explicit_depth_2(self):
        """Test PROXY_DEPTH=2 for 2-hop proxy chain (e.g., Cloudflare -> Traefik)."""
        server = Server()
        client = server.app.test_client()

        # Simulate 2-hop chain: Cloudflare -> Traefik -> web-deadend
        # Direct connection from 172.21.9.172 (Traefik)
        # XFF: 1.2.3.4 (real client), 104.16.1.1 (Cloudflare proxy)
        response = client.get(
            "/test",
            environ_base={
                "REMOTE_ADDR": "172.21.9.172",
                "HTTP_X_FORWARDED_FOR": "1.2.3.4, 104.16.1.1",
            },
        )

        assert response.status_code == 204
        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 2

    @patch.dict(os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "10.0.0.1"})
    def test_single_ip_defaults_to_depth_1(self):
        """Test single IP in TRUSTED_PROXIES defaults to PROXY_DEPTH=1."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 1

    @patch.dict(os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "10.0.0.0/24,192.168.1.0/24"})
    def test_multiple_cidr_ranges_defaults_to_depth_1(self):
        """Test multiple CIDR ranges default to PROXY_DEPTH=1."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 1

    @patch.dict(
        os.environ,
        {
            "TESTING": "true",
            "TRUSTED_PROXIES": "10.0.0.0/24,192.168.1.0/24",
            "PROXY_DEPTH": "3",
        },
    )
    def test_explicit_proxy_depth_overrides_default(self):
        """Test explicit PROXY_DEPTH overrides default of 1."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 3

    @patch.dict(os.environ, {"TESTING": "true", "TRUST_ALL_PROXIES": "true"})
    def test_legacy_trust_all_uses_default_depth_1(self):
        """Test legacy TRUST_ALL_PROXIES=true now defaults to PROXY_DEPTH=1."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 1  # Changed from 100 to 1

    @patch.dict(
        os.environ,
        {"TESTING": "true", "TRUST_ALL_PROXIES": "true", "PROXY_DEPTH": "5"},
    )
    def test_legacy_trust_all_with_explicit_depth(self):
        """Test TRUST_ALL_PROXIES=true with explicit PROXY_DEPTH."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 5

    @patch.dict(
        os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0", "PROXY_DEPTH": "abc"}
    )
    def test_invalid_proxy_depth_falls_back_to_1(self):
        """Test invalid PROXY_DEPTH falls back to default of 1."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 1

    @patch.dict(
        os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0", "PROXY_DEPTH": "999"}
    )
    def test_proxy_depth_out_of_range_falls_back_to_1(self):
        """Test PROXY_DEPTH out of range (1-100) falls back to 1."""
        server = Server()

        assert hasattr(server.app.wsgi_app, "x_for")
        assert server.app.wsgi_app.x_for == 1

    @patch.dict(os.environ, {"TESTING": "true"})
    def test_no_proxy_config_disables_proxyfix(self):
        """Test that ProxyFix is NOT enabled when TRUSTED_PROXIES is not set."""
        server = Server()

        # ProxyFix should not be installed (app.wsgi_app should be the base app)
        assert not hasattr(server.app.wsgi_app, "x_for")


class TestProxyFixActualIPExtraction:
    """Test that ProxyFix actually extracts the correct IP from X-Forwarded-For."""

    @patch.dict(os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0,::/0"})
    def test_single_proxy_extracts_client_ip_not_proxy_ip(self):
        """CRITICAL: Verify ProxyFix extracts 223.96.43.68, NOT 172.21.9.172."""
        server = Server()

        # Create a route that returns the remote_addr
        @server.app.route("/check-ip")
        def check_ip():
            from flask import request

            return request.remote_addr

        client = server.app.test_client()

        # Simulate production scenario: Traefik (172.21.9.172) forwarding real client (223.96.43.68)
        response = client.get(
            "/check-ip",
            environ_base={
                "REMOTE_ADDR": "172.21.9.172",
                "HTTP_X_FORWARDED_FOR": "223.96.43.68",
            },
        )

        # THIS IS THE CRITICAL TEST: Should return real client IP, NOT proxy IP
        assert response.data.decode() == "223.96.43.68"
        assert response.data.decode() != "172.21.9.172"

    @patch.dict(
        os.environ,
        {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0,::/0", "PROXY_DEPTH": "2"},
    )
    def test_two_proxies_extracts_client_ip(self):
        """Test 2-hop chain extracts real client, not intermediate proxy."""
        server = Server()

        @server.app.route("/check-ip")
        def check_ip():
            from flask import request

            return request.remote_addr

        client = server.app.test_client()

        # Cloudflare (104.16.1.1) -> Traefik (172.21.9.172) -> web-deadend
        # XFF: real_client, cloudflare_ip
        response = client.get(
            "/check-ip",
            environ_base={
                "REMOTE_ADDR": "172.21.9.172",
                "HTTP_X_FORWARDED_FOR": "1.2.3.4, 104.16.1.1",
            },
        )

        # Should extract the REAL client IP from the beginning of XFF chain
        assert response.data.decode() == "1.2.3.4"
        assert response.data.decode() != "104.16.1.1"
        assert response.data.decode() != "172.21.9.172"

    @patch.dict(os.environ, {"TESTING": "true", "TRUSTED_PROXIES": "0.0.0.0/0,::/0"})
    def test_ipv6_client_extraction(self):
        """Test ProxyFix correctly extracts IPv6 client addresses."""
        server = Server()

        @server.app.route("/check-ip")
        def check_ip():
            from flask import request

            return request.remote_addr

        client = server.app.test_client()

        # IPv6 client through IPv4 proxy
        response = client.get(
            "/check-ip",
            environ_base={
                "REMOTE_ADDR": "172.21.9.172",
                "HTTP_X_FORWARDED_FOR": "2001:db8::1",
            },
        )

        assert response.data.decode() == "2001:db8::1"

    @patch.dict(os.environ, {"TESTING": "true"})
    def test_no_proxyfix_shows_direct_connection_ip(self):
        """Test without ProxyFix, remote_addr shows direct connection (proxy IP)."""
        server = Server()

        @server.app.route("/check-ip")
        def check_ip():
            from flask import request

            return request.remote_addr

        client = server.app.test_client()

        # Without ProxyFix, should show the direct connection IP (proxy), ignore XFF
        response = client.get(
            "/check-ip",
            environ_base={
                "REMOTE_ADDR": "172.21.9.172",
                "HTTP_X_FORWARDED_FOR": "223.96.43.68",
            },
        )

        # Without ProxyFix, remote_addr is the direct connection (proxy IP)
        assert response.data.decode() == "172.21.9.172"
