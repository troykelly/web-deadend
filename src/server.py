"""
Server module for handling incoming requests using Flask.
"""
import os
import json
import logging
import time
import ipaddress
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, Union, List, Optional
from urllib.parse import urlparse, parse_qs
import base64
import xmltodict
from flask import Flask, request, jsonify, Response, g
from flask.logging import create_logger
import graypy
from response.handlers import handle_request

# Define a constant for the maximum Graylog payload size (e.g., 1MB)
MAX_GELF_PAYLOAD_SIZE = 1024 * 1024  # 1MB
VERSION = "__VERSION__"  # <-- This will be replaced during the release process

class Server:
    """Server class to handle incoming requests and configuration."""

    def __init__(self):
        self.app = Flask(__name__)
        self.logger = create_logger(self.app)
        self.request_counter: Counter[str] = Counter()
        self.request_details: List[Dict[str, Union[str, int]]] = []
        self._setup_logging()
        self._setup_proxy_fix()
        self._setup_gelf_handler()

        # Flask app configuration
        self.app.before_request(self.before_request)
        self.app.after_request(self.after_request)
        self.app.route("/deadend-status", methods=["GET"])(self.deadend_status)
        self.app.route("/deadend-counter", methods=["GET"])(self.deadend_counter)
        self.app.route("/", defaults={"path": ""}, methods=self.all_methods())(self.catch_all)
        self.app.route("/<path:path>", methods=self.all_methods())(self.catch_all)

    def _setup_logging(self) -> None:
        """Configures the logging for the application."""
        debug_level = os.getenv("DEBUG_LEVEL", "INFO").upper()
        valid_debug_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if debug_level not in valid_debug_levels:
            debug_level = "DEBUG"
        self.logger.setLevel(getattr(logging, debug_level))

    def _setup_proxy_fix(self) -> None:
        """
        Configures the ProxyFix middleware based on the depth of trusted proxies.
        """
        trust_all = bool(os.getenv("TRUST_ALL_PROXIES", "false").lower() in ["true", "1", "yes"])
        trusted_proxies = [proxy.strip() for proxy in os.getenv("TRUSTED_PROXIES", "").split(",") if proxy]
        num_proxies = 1  # Default to trust only the immediate upstream proxy

        if trust_all:
            # Trust all proxies by setting x_for and other parameters to a high value
            self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_for=100, x_proto=1, x_host=1, x_port=1, x_prefix=1)
        elif trusted_proxies:
            # Apply ProxyFix with the number of trusted proxies specified
            self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_for=num_proxies, x_proto=1, x_host=1, x_port=1, x_prefix=1)
        else:
            self.logger.warning("No trusted proxies specified and TRUST_ALL_PROXIES not set; ProxyFix not configured.")
    
    def _setup_gelf_handler(self) -> None:
        """Configures the GELF logging handler if specified in the environment variables."""
        gelf_server = os.getenv("GELF_SERVER")
        if gelf_server:
            parsed_url = urlparse(gelf_server)
            gelf_host = parsed_url.hostname
            gelf_port = parsed_url.port

            if parsed_url.scheme == "udp":
                gelf_handler = graypy.GELFUDPHandler(gelf_host, gelf_port)
            elif parsed_url.scheme == "tcp":
                gelf_handler = graypy.GELFTCPHandler(gelf_host, gelf_port)
            else:
                self.logger.error("Unsupported GELF scheme: %s", parsed_url.scheme)
                return

            gelf_logger = logging.getLogger("gelf")
            gelf_logger.setLevel(logging.INFO)
            gelf_logger.addHandler(gelf_handler)
            self.gelf_logger = gelf_logger
        else:
            self.logger.warning("No GELF server specified; GELF handler not set up")
            self.gelf_logger = None

    def before_request(self) -> None:
        """Store the start time before processing the request."""
        g.start_time = time.time()

    def after_request(self, response: Response) -> Response:
        """Log ending information and calculate request duration."""
        if self._should_skip_logging(request):
            return response

        request_duration = time.time() - g.start_time
        request_data = self._gather_request_data(response, request_duration)
        self.logger.debug(json.dumps(request_data))
        self.request_counter.update([request.path])
        self.request_details.append({
            "method": request.method,
            "path": request.path,
            "query_params": request.args.to_dict(),
            "domain": request.host,
        })

        self._send_to_gelf(request_data)
        return response

    def _should_skip_logging(self, request) -> bool:
        """Determine if logging should be skipped for the current request."""
        return request.path == "/deadend-status" and request.remote_addr == "127.0.0.1"

    def _gather_request_data(self, response: Response, request_duration: float) -> Dict[str, Any]:
        """Gathers detailed data about the request and response."""
        response_size = response.calculate_content_length()
        # Calculate request size from Content-Length header if available, otherwise use request.data
        request_size = request.content_length if request.content_length is not None else len(request.data)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": VERSION if VERSION != "__VERSION__" else "dev",
            "hostname": request.host,
            "remote_addr": request.remote_addr,
            "method": request.method,
            "path": request.path,
            "headers": dict(request.headers),
            "query_params": request.args.to_dict(),
            "body": self._get_request_body(),
            "request_size": request_size,
            "response_status": response.status_code,
            "response_size": response_size if response_size is not None else 0,
            "duration_ms": int(request_duration * 1000),
        }

    def _get_request_body(self) -> Union[Dict[str, Any], str]:
        """Extract and return the request body based on its content type."""
        content_type = request.headers.get("Content-Type", "").lower().strip()
        if not content_type:
            # Try to decode as text if no content type specified
            try:
                return request.data.decode("utf-8") if request.data else ""
            except UnicodeDecodeError:
                return base64.b64encode(request.data).decode("utf-8") if request.data else ""

        try:
            if content_type == "application/json":
                return request.get_json(silent=True) or {}
            if content_type == "application/xml":
                return xmltodict.parse(request.data.decode("utf-8")) if request.data else {}
            if content_type == "text/plain":
                return request.data.decode("utf-8") if request.data else ""
            if content_type == "application/x-www-form-urlencoded":
                # Use request.form which properly handles form-encoded data
                # Convert ImmutableMultiDict to regular dict, keeping only first value for each key
                if request.form:
                    return {key: request.form.get(key) for key in request.form.keys()}
                # Fallback to parsing raw data if form is empty
                if request.data:
                    return parse_qs(request.data.decode("utf-8"))
                return {}

            if content_type.startswith("multipart/form-data"):
                return self._handle_multipart_form_data()
        except Exception as e:
            self.logger.error("Error processing request body for content type %s: %s", content_type, e)
            return {}

        self.logger.warning("Unhandled content type: %s", content_type)
        try:
            return request.data.decode("utf-8") if request.data else ""
        except UnicodeDecodeError:
            return base64.b64encode(request.data).decode("utf-8") if request.data else ""

    def _handle_multipart_form_data(self) -> Dict[str, Any]:
        """Handles multipart form data content type."""
        data = {}
        for key, value in request.form.items():
            data[key] = value
        for key, file in request.files.items():
            file_data = base64.b64encode(file.read()).decode("utf-8")
            if len(file_data) > MAX_GELF_PAYLOAD_SIZE:
                data[key] = "File too large, removed to prevent payload overflow"
            else:
                data[key] = file_data
        return data

    def _send_to_gelf(self, data: Dict[str, Any]) -> None:
        """Send data to the GELF server if configured."""
        if self.gelf_logger:
            try:
                message = f"{data['method']} {data['path']} {data['response_status']} {data['duration_ms']}ms"
                log_entry = json.dumps(data).encode("utf-8")
                if len(log_entry) > MAX_GELF_PAYLOAD_SIZE:
                    self.logger.error("GELF payload size exceeds the limit; reducing body size.")
                    data["body"] = "Request body too large, removed to prevent payload overflow"
                self.gelf_logger.info(message, extra=data)
                self.logger.debug(f"Sent data to GELF server at {self.gelf_logger.handlers[0].host}")
            except Exception as e:
                self.logger.error("Error sending data to GELF: %s", e)

    def deadend_status(self) -> Response:
        """Endpoint to return service status."""
        return jsonify({"service": "ok"}), 200

    def deadend_counter(self) -> Response:
        """Endpoint to return request statistics."""
        top_domains = self.request_counter.most_common(10)
        request_breakdown = Counter(req["method"] for req in self.request_details)

        response_data = {
            "total_requests_received": sum(self.request_counter.values()),
            "top_10_domains_urls": top_domains,
            "request_type_breakdown": request_breakdown,
        }
        return jsonify(response_data), 200

    def catch_all(self, path: str) -> Response:
        """Catch-all endpoint to handle all requests that are not explicitly defined."""
        return handle_request()

    @staticmethod
    def all_methods() -> List[str]:
        """Return all HTTP methods allowed for catch-all routes."""
        return ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"]

    def run(self) -> None:
        """Run the Flask application."""
        port = int(os.getenv("PORT", 3000))
        self.app.run(host="0.0.0.0", port=port)


app = Server().app

if __name__ == "__main__":
    server = Server()
    server.run()