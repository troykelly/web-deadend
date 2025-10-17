"""
Server module for handling incoming requests using Flask.
"""

import base64

# import ipaddress  # noqa: F401
import json
import logging
import os
import queue
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse  # urlencode unused

import defusedxml.ElementTree as ET
import graypy
import uuid_utils
from flask import Flask, Response, g, jsonify, request
from flask.logging import create_logger
from werkzeug.middleware.proxy_fix import ProxyFix

from response.handlers import handle_request

# Define a constant for the maximum Graylog payload size (e.g., 1MB)
MAX_GELF_PAYLOAD_SIZE = 1024 * 1024  # 1MB
VERSION = "__VERSION__"  # <-- This will be replaced during the release process


class Server:
    """Server class to handle incoming requests and configuration."""

    def __init__(self):
        self.app = Flask(__name__)
        # Set maximum request size to prevent memory exhaustion (100MB)
        self.app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
        self.logger = create_logger(self.app)
        self.request_counter: Counter[str] = Counter()
        # Use bounded deque instead of unbounded list to prevent memory exhaustion at scale
        # At 10k req/s, keeping last 100k requests = last 10 seconds of traffic
        self.request_details: deque = deque(maxlen=100000)

        # Async GELF logging queue for non-blocking I/O at 10k req/s
        self.gelf_queue: Optional[queue.Queue] = None
        self.gelf_worker_thread: Optional[threading.Thread] = None

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
        trusted_proxies = [
            proxy.strip() for proxy in os.getenv("TRUSTED_PROXIES", "").split(",") if proxy
        ]
        num_proxies = 1  # Default to trust only the immediate upstream proxy

        if trust_all:
            # Trust all proxies by setting x_for and other parameters to a high value
            self.app.wsgi_app = ProxyFix(
                self.app.wsgi_app, x_for=100, x_proto=1, x_host=1, x_port=1, x_prefix=1
            )
        elif trusted_proxies:
            # Apply ProxyFix with the number of trusted proxies specified
            self.app.wsgi_app = ProxyFix(
                self.app.wsgi_app, x_for=num_proxies, x_proto=1, x_host=1, x_port=1, x_prefix=1
            )
        else:
            self.logger.warning(
                "No trusted proxies specified and TRUST_ALL_PROXIES not set; "
                "ProxyFix not configured."
            )

    def _setup_gelf_handler(self) -> None:
        """Configures async GELF logging for high-throughput non-blocking I/O.

        Uses a background thread with a queue to prevent GELF logging from
        blocking request processing at 10k+ req/s.
        """
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

            # Create async logging queue (bounded to prevent memory exhaustion)
            # At 10k req/s, a 10k queue = 1 second buffer
            self.gelf_queue = queue.Queue(maxsize=10000)

            # Start background worker thread for async GELF logging
            self.gelf_worker_thread = threading.Thread(
                target=self._gelf_worker,
                daemon=True,  # Daemon thread exits when main program exits
                name="gelf-logger",
            )
            self.gelf_worker_thread.start()
            self.logger.info("Async GELF logging enabled with queue size 10000")
        else:
            self.logger.warning("No GELF server specified; GELF handler not set up")
            self.gelf_logger = None

    def _gelf_worker(self) -> None:
        """Background worker thread that processes GELF logging queue.

        This runs in a separate thread to prevent network I/O from blocking
        request processing. Logs are queued and processed asynchronously.
        """
        while True:
            try:
                # Block until item available (with timeout for graceful shutdown)
                log_item = self.gelf_queue.get(timeout=1.0)

                if log_item is None:  # Shutdown signal
                    break

                message, extra_data = log_item

                # Send to GELF server (network I/O happens in background thread)
                self.gelf_logger.info(message, extra=extra_data)

                self.gelf_queue.task_done()

            except queue.Empty:
                # Timeout - continue loop (allows graceful shutdown check)
                continue
            except Exception as e:
                # Log errors but don't crash the worker thread
                self.logger.error(f"Error in GELF worker thread: {e}")
                continue

    def before_request(self) -> None:
        """Store the start time and generate request ID before processing the request."""
        g.start_time = time.time()
        # Generate UUIDv7 for request tracking (RFC 9562 compliant, time-sortable)
        g.request_id = str(uuid_utils.uuid7())

    def after_request(self, response: Response) -> Response:
        """Log ending information and calculate request duration."""
        if self._should_skip_logging(request):
            return response

        # Add request ID to response headers (X-Request-ID is the standard header)
        response.headers["X-Request-ID"] = g.request_id

        request_duration = time.time() - g.start_time
        request_data = self._gather_request_data(response, request_duration)
        self.logger.debug(json.dumps(request_data))
        self.request_counter.update([request.path])
        self.request_details.append(
            {
                "method": request.method,
                "path": request.path,
                "query_params": request.args.to_dict(),
                "domain": request.host,
            }
        )

        self._send_to_gelf(request_data)
        return response

    def _should_skip_logging(self, request) -> bool:
        """Determine if logging should be skipped for the current request."""
        skip: bool = request.path == "/deadend-status" and request.remote_addr == "127.0.0.1"
        return skip

    def _gather_request_data(self, response: Response, request_duration: float) -> Dict[str, Any]:
        """Gathers detailed data about the request and response."""
        response_size = response.calculate_content_length()
        # Calculate request size from Content-Length header if available, otherwise use request.data
        request_size = (
            request.content_length if request.content_length is not None else len(request.data)
        )
        return {
            "request_id": g.request_id,
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

    def _xml_to_dict(self, element) -> Dict[str, Any]:
        """Convert XML ElementTree to dictionary for logging.

        This is a simple converter that creates a dict representation of XML.
        Does not use xmltodict to avoid XXE vulnerabilities.
        """
        result = {}
        # Add attributes
        if element.attrib:
            result["@attributes"] = dict(element.attrib)

        # Add text content
        if element.text and element.text.strip():
            result["#text"] = element.text.strip()

        # Add children
        for child in element:
            child_data = self._xml_to_dict(child)
            if child.tag in result:
                # Handle multiple children with same tag
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]  # type: ignore[assignment]
                result[child.tag].append(child_data)  # type: ignore[attr-defined]
            else:
                result[child.tag] = child_data

        return result if result else element.text

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
                # Use defusedxml to prevent XXE attacks
                if request.data:
                    try:
                        tree = ET.fromstring(request.data.decode("utf-8"))
                        # Convert XML to dict-like structure for logging
                        return self._xml_to_dict(tree)
                    except ET.ParseError as e:
                        self.logger.warning(f"XML parse error: {e}")
                        return {
                            "_xml_parse_error": str(e),
                            "_raw": request.data.decode("utf-8", errors="replace"),
                        }
                return {}
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
            self.logger.error(
                "Error processing request body for content type %s: %s", content_type, e
            )
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
            # Check file size BEFORE reading to prevent memory exhaustion
            # file.content_length may be None, so seek to end to get size
            file.seek(0, 2)  # Seek to end
            file_size = file.tell()
            file.seek(0)  # Reset to beginning

            # For files > 10MB, just log metadata without base64 encoding
            if file_size > 10 * 1024 * 1024:
                data[key] = (
                    f"<large_file size={file_size} name={file.filename} type={file.content_type}>"
                )
            else:
                file_data = base64.b64encode(file.read()).decode("utf-8")
                if len(file_data) > MAX_GELF_PAYLOAD_SIZE:
                    data[key] = f"<file_too_large_for_gelf size={len(file_data)}>"
                else:
                    data[key] = file_data
        return data

    def _send_to_gelf(self, data: Dict[str, Any]) -> None:
        """Send data to the GELF server asynchronously via queue.

        Uses a non-blocking queue to prevent GELF logging from blocking
        request processing at 10k+ req/s. The background worker thread
        handles actual network I/O.
        """
        if self.gelf_queue:
            try:
                message = (
                    f"{data['method']} {data['path']} "
                    f"{data['response_status']} {data['duration_ms']}ms"
                )

                # Create a copy of data for GELF logging
                gelf_data = data.copy()

                # If body is a dict, flatten it into separate fields with 'body_' prefix
                # Keep the original 'body' field as JSON for structured logging
                if isinstance(gelf_data.get("body"), dict):
                    body_dict = gelf_data["body"]
                    # Store original body as JSON string
                    gelf_data["body_json"] = json.dumps(body_dict)
                    # Also flatten for easy searching
                    for key, value in body_dict.items():
                        # Add body fields with 'body_' prefix for easy filtering in Graylog
                        gelf_data[f"body_{key}"] = value

                # If query_params is a dict, flatten it into separate fields with 'query_' prefix
                # Keep the original 'query_params' field as JSON for structured logging
                if isinstance(gelf_data.get("query_params"), dict) and gelf_data["query_params"]:
                    query_dict = gelf_data["query_params"]
                    # Store original query_params as JSON string
                    gelf_data["query_params_json"] = json.dumps(query_dict)
                    # Also flatten for easy searching
                    for key, value in query_dict.items():
                        # Add query fields with 'query_' prefix for easy filtering in Graylog
                        gelf_data[f"query_{key}"] = value

                # Check payload size
                log_entry = json.dumps(gelf_data).encode("utf-8")
                if len(log_entry) > MAX_GELF_PAYLOAD_SIZE:
                    self.logger.error(
                        "GELF payload size exceeds the limit; removing body and query fields."
                    )
                    # Remove all body_ and query_ fields if payload is too large
                    gelf_data = {
                        k: v
                        for k, v in gelf_data.items()
                        if not (k.startswith("body_") or k.startswith("query_"))
                    }
                    gelf_data["body"] = (
                        "Request body too large, removed to prevent payload overflow"
                    )
                    gelf_data["query_params"] = {}

                # Queue the log entry for async processing (non-blocking)
                try:
                    self.gelf_queue.put_nowait((message, gelf_data))
                    self.logger.debug(
                        f"Queued GELF log entry (queue size: ~{self.gelf_queue.qsize()})"
                    )
                except queue.Full:
                    # Queue full - drop the log entry to prevent blocking
                    self.logger.warning("GELF queue full, dropping log entry")
            except Exception as e:
                self.logger.error("Error queueing GELF data: %s", e)

    def deadend_status(self):
        """Endpoint to return service status."""
        return jsonify({"service": "ok"}), 200

    def deadend_counter(self):
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
