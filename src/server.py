"""
Server module for handling incoming requests using Flask.
"""

import atexit
import base64
import ipaddress
import json
import logging
import os
import queue
import signal
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union
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


class BoundedCounter:
    """Thread-safe counter with maximum size limit using LRU eviction.

    Prevents unbounded memory growth from unique keys (e.g., random paths in attacks).
    When maxsize is reached, the least recently accessed item is removed.
    """

    def __init__(self, maxsize: int = 10000):
        self.maxsize = maxsize
        self.data: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def __setitem__(self, key: str, value: int) -> None:
        with self.lock:
            if key in self.data:
                # Move to end (mark as recently used)
                self.data.move_to_end(key)
            elif len(self.data) >= self.maxsize:
                # Remove oldest item (LRU eviction)
                self.data.popitem(last=False)
            self.data[key] = value

    def __getitem__(self, key: str) -> int:
        with self.lock:
            return self.data.get(key, 0)

    def increment(self, key: str, amount: int = 1) -> None:
        """Increment counter for key by amount."""
        with self.lock:
            current = self.data.get(key, 0)
            if key in self.data:
                self.data.move_to_end(key)
            elif len(self.data) >= self.maxsize:
                self.data.popitem(last=False)
            self.data[key] = current + amount

    def most_common(self, n: int) -> List[tuple]:
        """Return top n items by count."""
        with self.lock:
            return sorted(self.data.items(), key=lambda x: x[1], reverse=True)[:n]

    def __len__(self) -> int:
        with self.lock:
            return len(self.data)


class Server:
    """Server class to handle incoming requests and configuration."""

    def __init__(self):
        self.app = Flask(__name__)
        # Set maximum request size to prevent memory exhaustion (100MB)
        self.app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

        # Maximum URL length for honeypot (64KB - captures attacks while preventing memory issues)
        # Most web servers limit to 8KB, we allow 64KB to see long attack payloads
        self.max_url_length = int(os.getenv("MAX_URL_LENGTH", 65536))

        # Shutdown tracking
        self._shutdown_in_progress: bool = False

        self.logger = create_logger(self.app)
        self.request_counter: Counter[str] = Counter()
        # Use bounded deque instead of unbounded list to prevent memory exhaustion at scale
        # At 10k req/s, keeping last 100k requests = last 10 seconds of traffic
        self.request_details: deque = deque(maxlen=100000)

        # Async GELF logging queue for non-blocking I/O at 10k req/s
        self.gelf_queue: Optional[queue.Queue] = None
        self.gelf_worker_thread: Optional[threading.Thread] = None
        self.gelf_drops: int = 0  # Track dropped GELF logs due to queue saturation

        # Stats logging thread
        self.stats_worker_thread: Optional[threading.Thread] = None
        self.stats_shutdown_event = threading.Event()

        # Stats tracking for periodic logging (bounded to prevent memory exhaustion)
        # Keep last 50k unique IPs and 10k paths max
        self.unique_ips: deque = deque(maxlen=50000)  # Bounded set (FIFO eviction)
        self.unique_ips_set: Set[str] = set()  # For O(1) lookup
        self.path_counter = BoundedCounter(maxsize=10000)  # Bounded counter (LRU eviction)
        self.error_counter: Counter[int] = Counter()  # Status codes are naturally bounded
        self.total_bytes_received: int = 0
        self.total_bytes_sent: int = 0
        self.last_stats: Dict[str, Any] = {}
        self.last_heartbeat_time: float = time.time()

        # Proxy configuration warning flag (one-time warning to prevent log spam)
        self.warned_missing_xff: bool = False
        self.proxyfix_enabled: bool = False

        self._setup_logging()
        self._setup_proxy_fix()
        self._setup_gelf_handler()
        self._setup_healthcheck_allowed()
        self._setup_stats_worker()
        self._setup_signal_handlers()

        # Flask app configuration
        self.app.before_request(self.before_request)
        self.app.after_request(self.after_request)
        self.app.route("/deadend-status", methods=["GET"])(self.deadend_status)
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
        Configures the ProxyFix middleware based on trusted proxy networks.

        SECURITY: By default, ProxyFix is NOT enabled to prevent X-Forwarded-* header spoofing.
        Set TRUSTED_PROXIES to enable proxy header processing when behind a load balancer.

        Examples:
            TRUSTED_PROXIES="10.0.0.1" - Trust single IP (auto-converted to 10.0.0.1/32)
            TRUSTED_PROXIES="10.0.0.0/24" - Trust CIDR range
            TRUSTED_PROXIES="0.0.0.0/0,::/0" - Trust all IPs, use PROXY_DEPTH for hop count
            PROXY_DEPTH=1 - Number of proxy hops (default: 1)
            TRUST_ALL_PROXIES=true - Trust all proxies (DANGEROUS - use only in dev)
        """
        import ipaddress

        trust_all = bool(os.getenv("TRUST_ALL_PROXIES", "false").lower() in ["true", "1", "yes"])
        trusted_proxies_str = os.getenv("TRUSTED_PROXIES", "")
        proxy_depth_str = os.getenv("PROXY_DEPTH", "")

        self.logger.info(
            f"[PROXY_DEBUG] Starting proxy configuration: "
            f"TRUST_ALL_PROXIES={os.getenv('TRUST_ALL_PROXIES', 'not set')}, "
            f"TRUSTED_PROXIES={trusted_proxies_str or 'not set'}"
        )

        # Parse CIDR ranges from TRUSTED_PROXIES
        trusted_networks = []
        if trusted_proxies_str:
            for proxy in trusted_proxies_str.split(","):
                proxy = proxy.strip()
                if not proxy:
                    continue
                try:
                    # If no CIDR notation, add /32 for IPv4 or /128 for IPv6
                    if "/" not in proxy:
                        # Determine if IPv4 or IPv6 and add appropriate suffix
                        try:
                            addr = ipaddress.ip_address(proxy)
                            if isinstance(addr, ipaddress.IPv4Address):
                                proxy = f"{proxy}/32"
                                self.logger.info(
                                    f"[PROXY_DEBUG] Auto-appended /32 to IPv4: {proxy}"
                                )
                            else:
                                proxy = f"{proxy}/128"
                                self.logger.info(
                                    f"[PROXY_DEBUG] Auto-appended /128 to IPv6: {proxy}"
                                )
                        except ValueError:
                            self.logger.error(f"Invalid IP address in TRUSTED_PROXIES: {proxy}")
                            continue

                    network = ipaddress.ip_network(proxy, strict=False)
                    trusted_networks.append(network)
                    self.logger.info(f"[PROXY_DEBUG] Parsed network: {network}")
                except ValueError as e:
                    self.logger.error(f"Invalid CIDR range in TRUSTED_PROXIES: {proxy} - {e}")
                    continue

        self.logger.info(f"[PROXY_DEBUG] Total parsed networks: {len(trusted_networks)}")
        if trusted_networks:
            self.logger.info(f"[PROXY_DEBUG] Networks: {[str(net) for net in trusted_networks]}")

        # Determine proxy depth (number of hops to traverse in X-Forwarded-For chain)
        proxy_depth = 1  # Default to 1 hop (most common: single load balancer)

        # Parse PROXY_DEPTH if provided
        if proxy_depth_str:
            try:
                proxy_depth = int(proxy_depth_str)
                if proxy_depth < 1 or proxy_depth > 100:
                    self.logger.error(
                        f"Invalid PROXY_DEPTH={proxy_depth_str}. Must be 1-100. Using default: 1"
                    )
                    proxy_depth = 1
                else:
                    self.logger.info(f"[PROXY_DEBUG] Using explicit PROXY_DEPTH={proxy_depth}")
            except ValueError:
                self.logger.error(
                    f"Invalid PROXY_DEPTH={proxy_depth_str}. Must be an integer. Using default: 1"
                )
                proxy_depth = 1

        # Check if trust-all ranges are present (0.0.0.0/0 or ::/0)
        if trusted_networks and any(str(net) in ["0.0.0.0/0", "::/0"] for net in trusted_networks):
            self.logger.info(
                f"[PROXY_DEBUG] Detected trust-all CIDR ranges in TRUSTED_PROXIES ({trusted_proxies_str}). "
                f"Using PROXY_DEPTH={proxy_depth} for X-Forwarded-For processing."
            )
            self.app.wsgi_app = ProxyFix(
                self.app.wsgi_app, x_for=proxy_depth, x_proto=1, x_host=1, x_port=1, x_prefix=1
            )
            self.proxyfix_enabled = True
            self.logger.info(
                f"[PROXY_DEBUG] ProxyFix middleware installed with x_for={proxy_depth}"
            )
        elif trust_all:
            # Legacy TRUST_ALL_PROXIES=true (use explicit depth or default to 1)
            self.logger.warning(
                f"[PROXY_DEBUG] TRUST_ALL_PROXIES=true - Using PROXY_DEPTH={proxy_depth}. "
                "This should ONLY be used when behind trusted infrastructure."
            )
            self.app.wsgi_app = ProxyFix(
                self.app.wsgi_app, x_for=proxy_depth, x_proto=1, x_host=1, x_port=1, x_prefix=1
            )
            self.proxyfix_enabled = True
            self.logger.info(
                f"[PROXY_DEBUG] ProxyFix middleware installed with x_for={proxy_depth}"
            )
        elif trusted_networks:
            # Store trusted networks for validation and enable ProxyFix with correct depth
            self.trusted_proxy_networks = trusted_networks

            # Use explicit PROXY_DEPTH if provided, otherwise default to 1
            if not proxy_depth_str:
                # No explicit depth - use default of 1 (single proxy/load balancer)
                proxy_depth = 1
                self.logger.info(
                    f"ProxyFix enabled: Trusting network(s) {[str(net) for net in trusted_networks]} "
                    f"with default PROXY_DEPTH={proxy_depth}. Set PROXY_DEPTH env var if you have multiple proxies."
                )
            else:
                self.logger.info(
                    f"ProxyFix enabled: Trusting network(s) {[str(net) for net in trusted_networks]} "
                    f"with PROXY_DEPTH={proxy_depth}"
                )

            self.app.wsgi_app = ProxyFix(
                self.app.wsgi_app,
                x_for=proxy_depth,
                x_proto=1,
                x_host=1,
                x_port=1,
                x_prefix=1,
            )
            self.proxyfix_enabled = True
        else:
            # SECURITY: No ProxyFix by default - prevents header spoofing
            self.trusted_proxy_networks = []
            self.logger.warning(
                "ProxyFix NOT enabled - X-Forwarded-* headers will be ignored. "
                "If running behind a load balancer, IP-based features (filtering, stats) "
                "will not work correctly. "
                "Set TRUSTED_PROXIES environment variable to enable proxy support. "
                "Examples: TRUSTED_PROXIES='10.0.0.1' or TRUSTED_PROXIES='10.0.0.0/24,192.168.0.0/16'"
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
            # ALWAYS daemon=True - background loggers should never block shutdown
            # Graceful shutdown is handled by sending None to queue in _graceful_shutdown()
            self.gelf_worker_thread = threading.Thread(
                target=self._gelf_worker,
                daemon=True,
                name="gelf-logger",
            )
            self.gelf_worker_thread.start()
            self.logger.info("Async GELF logging enabled with queue size 10000 (daemon=True)")
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

    def _setup_healthcheck_allowed(self) -> None:
        """Parse HEALTHCHECK_ALLOWED environment variable for IP filtering."""
        allowed_str = os.getenv("HEALTHCHECK_ALLOWED", "0.0.0.0/0,::/0")
        self.healthcheck_allowed_networks: List[
            Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
        ] = []

        for network_str in allowed_str.split(","):
            network_str = network_str.strip()
            if not network_str:
                continue
            try:
                network = ipaddress.ip_network(network_str, strict=False)
                self.healthcheck_allowed_networks.append(network)
            except ValueError as e:
                self.logger.error(f"Invalid network in HEALTHCHECK_ALLOWED: {network_str} - {e}")

        if not self.healthcheck_allowed_networks:
            # Default to allow all if no valid networks
            self.healthcheck_allowed_networks = [
                ipaddress.ip_network("0.0.0.0/0"),
                ipaddress.ip_network("::/0"),
            ]
            self.logger.warning("No valid networks in HEALTHCHECK_ALLOWED, defaulting to allow all")

    def _is_healthcheck_allowed(self, ip_str: str) -> bool:
        """Check if an IP address is allowed to access healthcheck endpoint."""
        try:
            ip = ipaddress.ip_address(ip_str)
            for network in self.healthcheck_allowed_networks:
                if ip in network:
                    return True
            return False
        except ValueError:
            self.logger.warning(f"Invalid IP address for healthcheck: {ip_str}")
            return False

    def _setup_stats_worker(self) -> None:
        """Start the background stats logging thread.

        Skip in test mode to prevent flooding test output with errors.
        """
        # Skip stats worker entirely in test mode
        # The worker causes TypeError spam when time.time() is mocked
        if os.getenv("TESTING") or self.app.config.get("TESTING"):
            self.logger.debug("Skipping stats worker thread in test mode")
            self.stats_worker_thread = None
            # Set defaults for tests that check these attributes
            self.log_format = "json"
            self.log_stats_interval = 60
            self.log_heartbeat_interval = 3600
            return

        self.log_format = os.getenv("LOG_FORMAT", "json").lower()
        self.log_stats_interval = int(os.getenv("LOG_STATS_INTERVAL", "60"))
        self.log_heartbeat_interval = int(os.getenv("LOG_HEARTBEAT_INTERVAL", "3600"))

        if self.log_format not in ["json", "text"]:
            self.logger.warning(f"Invalid LOG_FORMAT '{self.log_format}', defaulting to 'json'")
            self.log_format = "json"

        # ALWAYS daemon=True - background loggers should never block shutdown
        # Graceful shutdown is handled by sending None to stats_queue in _graceful_shutdown()
        self.stats_worker_thread = threading.Thread(
            target=self._stats_worker, daemon=True, name="stats-logger"
        )
        self.stats_worker_thread.start()
        self.logger.info(
            f"Stats logging enabled (format={self.log_format}, "
            f"interval={self.log_stats_interval}s, heartbeat={self.log_heartbeat_interval}s, daemon=True)"
        )

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown.

        Only register signal handlers when NOT running in test mode.
        Tests need to manage their own lifecycle without signal interference.
        """
        # Skip signal handlers in test mode (TESTING env var or Flask TESTING config)
        if os.getenv("TESTING") or self.app.config.get("TESTING"):
            self.logger.debug("Skipping signal handlers in test mode")
            return

        # Register shutdown handler for SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Also register atexit handler as backup (but only in production)
        # In tests, atexit can interfere with test teardown - daemon threads auto-terminate
        # Don't register atexit in ANY test environment (TESTING, pytest, etc.)
        # NOTE: We skip this entirely - atexit handlers cause 5s+ delays per test teardown
        # since they call _graceful_shutdown() which waits for worker threads

        self.logger.info("Signal handlers registered for graceful shutdown")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals (SIGTERM, SIGINT)."""
        sig_name = signal.Signals(signum).name
        self.logger.info(f"Received {sig_name}, initiating graceful shutdown...")
        self._graceful_shutdown()
        # Exit after graceful shutdown
        import sys

        sys.exit(0)

    def _graceful_shutdown(self) -> None:
        """Gracefully shutdown background threads and flush queues."""
        # Prevent multiple shutdown attempts
        if hasattr(self, "_shutdown_in_progress") and self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True

        self.logger.info("Starting graceful shutdown...")

        # Stop stats worker
        if self.stats_worker_thread and self.stats_worker_thread.is_alive():
            self.logger.info("Stopping stats worker thread...")
            self.stats_shutdown_event.set()
            self.stats_worker_thread.join(timeout=5)
            if self.stats_worker_thread.is_alive():
                self.logger.warning("Stats worker did not stop cleanly")
            else:
                self.logger.info("Stats worker stopped")

        # Flush GELF queue
        if self.gelf_queue:
            queue_size = self.gelf_queue.qsize()
            if queue_size > 0:
                self.logger.info(f"Flushing {queue_size} GELF log entries...")
                # Signal worker to stop after processing remaining items
                self.gelf_queue.put(None)  # Shutdown signal

                # Wait for queue to drain (max 30 seconds)
                start_time = time.time()
                while not self.gelf_queue.empty() and (time.time() - start_time) < 30:
                    time.sleep(0.1)

                remaining = self.gelf_queue.qsize()
                if remaining > 0:
                    self.logger.warning(f"Timed out flushing GELF queue, {remaining} entries lost")
                else:
                    self.logger.info("GELF queue flushed successfully")

        # Wait for GELF worker to finish
        if self.gelf_worker_thread and self.gelf_worker_thread.is_alive():
            self.logger.info("Stopping GELF worker thread...")
            self.gelf_worker_thread.join(timeout=5)
            if self.gelf_worker_thread.is_alive():
                self.logger.warning("GELF worker did not stop cleanly")
            else:
                self.logger.info("GELF worker stopped")

        self.logger.info(f"Graceful shutdown complete. Total GELF drops: {self.gelf_drops}")

    def _stats_worker(self) -> None:
        """Background worker that logs stats periodically."""
        while not self.stats_shutdown_event.is_set():
            try:
                # Wait for the stats interval
                self.stats_shutdown_event.wait(timeout=self.log_stats_interval)

                if self.stats_shutdown_event.is_set():
                    break

                # Calculate stats
                current_stats = self._calculate_stats()
                current_time = time.time()

                # Check if stats changed or if it's time for heartbeat
                # Exclude timestamp from comparison since it always changes
                stats_without_timestamp = {
                    k: v for k, v in current_stats.items() if k != "timestamp"
                }
                # Handle empty last_stats on first run
                if self.last_stats:
                    last_stats_without_timestamp = {
                        k: v for k, v in self.last_stats.items() if k != "timestamp"
                    }
                    stats_changed = stats_without_timestamp != last_stats_without_timestamp
                else:
                    stats_changed = True  # First run, always log

                # Check heartbeat timing (skip if time is mocked in tests)
                try:
                    heartbeat_due = (
                        current_time - self.last_heartbeat_time
                    ) >= self.log_heartbeat_interval
                except TypeError:
                    # time.time() is mocked (tests), skip heartbeat check
                    heartbeat_due = False

                if stats_changed or heartbeat_due:
                    self._log_stats(current_stats, heartbeat=heartbeat_due and not stats_changed)
                    self.last_stats = current_stats.copy()

                    if heartbeat_due:
                        self.last_heartbeat_time = current_time

            except Exception as e:
                self.logger.error(f"Error in stats worker thread: {e}")
                continue

    def _calculate_stats(self) -> Dict[str, Any]:
        """Calculate current statistics."""
        now = time.time()

        # Calculate requests per minute from last 60 seconds of data
        # Handle mocked time in tests gracefully
        try:
            recent_requests = [
                req for req in self.request_details if now - req.get("timestamp", 0) <= 60
            ]
        except TypeError:
            # time.time() is mocked (tests), use all requests
            recent_requests = list(self.request_details)
        requests_per_minute = len(recent_requests)

        # Get top paths
        top_paths = dict(self.path_counter.most_common(10))

        # Get error breakdown
        errors = dict(self.error_counter)

        return {
            "requests_per_minute": requests_per_minute,
            "total_requests": len(self.request_details),
            "unique_ips": len(self.unique_ips),
            "top_paths": top_paths,
            "bytes_received": self.total_bytes_received,
            "bytes_sent": self.total_bytes_sent,
            "errors": errors,
            "timestamp": now,
        }

    def _log_stats(self, stats: Dict[str, Any], heartbeat: bool = False) -> None:
        """Log statistics in the configured format.

        Skip logging in test mode to prevent flooding test output.
        """
        # Skip stats logging in test mode to avoid spamming test output
        if os.getenv("TESTING") or self.app.config.get("TESTING"):
            return

        if self.log_format == "json":
            stats_copy = stats.copy()
            stats_copy["heartbeat"] = heartbeat
            stats_copy["service"] = "web-deadend"
            stats_copy["version"] = VERSION
            self.logger.info(json.dumps(stats_copy))
        else:  # text format
            prefix = "[HEARTBEAT] " if heartbeat else "[STATS] "
            msg = (
                f"{prefix}Requests/min: {stats['requests_per_minute']}, "
                f"Unique IPs: {stats['unique_ips']}, "
                f"Total requests: {stats['total_requests']}, "
                f"Traffic: {stats['bytes_received']}↓ / {stats['bytes_sent']}↑ bytes"
            )
            if stats["errors"]:
                msg += f", Errors: {stats['errors']}"
            self.logger.info(msg)

    def before_request(self) -> None:
        """Store the start time and generate request ID before processing the request.

        Also validates URL length for honeypot purposes - we want to capture attacks
        but prevent memory exhaustion from extremely long URLs.
        """
        # Check URL length (path + query string)
        url_length = len(request.full_path)
        if url_length > self.max_url_length:
            self.logger.warning(
                f"URL length {url_length} exceeds limit {self.max_url_length}, "
                f"truncating for logging. Path: {request.path[:100]}..."
            )
            # Log the oversized URL attempt but still process it (it's a honeypot!)
            # We'll truncate in logging, but still return 414 to match HTTP spec

        g.start_time = time.time()
        # Generate UUIDv7 for request tracking (RFC 9562 compliant, time-sortable)
        g.request_id = str(uuid_utils.uuid7())

        # Check for missing X-Forwarded-For when ProxyFix is enabled (one-time warning)
        # NOTE: We check the environ dict because ProxyFix consumes and removes X-Forwarded-For
        # from request.headers after processing it. The raw header is in request.environ.
        if self.proxyfix_enabled and not self.warned_missing_xff:
            # ProxyFix stores original headers in environ with HTTP_ prefix
            xff_header = request.environ.get("HTTP_X_FORWARDED_FOR")
            if not xff_header:
                self.logger.error(
                    "PROXY CONFIGURATION ERROR: ProxyFix is enabled but no X-Forwarded-For header "
                    "received! This means your load balancer/ingress is NOT sending the real client IP. "
                    f"You will see Docker/proxy IPs (like {request.remote_addr}) instead of real clients. "
                    "Configure your load balancer to add X-Forwarded-For headers. "
                    "Examples: nginx 'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' "
                    "or Traefik '--entryPoints.web.forwardedHeaders.insecure=true'"
                )
                self.warned_missing_xff = True  # Only warn once to prevent log spam

        # Return 414 URI Too Long if URL exceeds limit (after logging above)
        if url_length > self.max_url_length:
            from flask import abort

            abort(414)

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

        # Track stats for periodic logging
        remote_ip = request.remote_addr or "unknown"

        # Track unique IPs with bounded deque (FIFO eviction when full)
        if remote_ip not in self.unique_ips_set:
            self.unique_ips.append(remote_ip)
            self.unique_ips_set.add(remote_ip)
            # Clean up set when deque evicts old entries
            if len(self.unique_ips_set) > len(self.unique_ips):
                # Rebuild set from current deque
                self.unique_ips_set = set(self.unique_ips)

        # Track paths with bounded counter (LRU eviction when full)
        self.path_counter.increment(request.path)

        # Track errors
        if response.status_code >= 400:
            self.error_counter[response.status_code] += 1

        # Track traffic
        request_size = (
            request.content_length if request.content_length is not None else len(request.data)
        )
        response_size = response.calculate_content_length() or 0
        self.total_bytes_received += request_size
        self.total_bytes_sent += response_size

        self.request_details.append(
            {
                "method": request.method,
                "path": request.path,
                "query_params": request.args.to_dict(),
                "domain": request.host,
                "timestamp": time.time(),
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
                    # Track drops and alert at intervals to avoid log spam
                    self.gelf_drops += 1

                    # Log error every 100 drops to indicate saturation attack or GELF server issue
                    if self.gelf_drops % 100 == 0:
                        self.logger.error(
                            f"GELF QUEUE SATURATION: {self.gelf_drops} total logs dropped! "
                            f"Queue size: {self.gelf_queue.qsize()}/{self.gelf_queue.maxsize}. "
                            "Possible attack in progress or GELF server is slow/down. "
                            "Consider increasing queue size (GELF_QUEUE_SIZE) or investigating "
                            "GELF server performance."
                        )
                    elif self.gelf_drops == 1:
                        # First drop is always logged as warning
                        self.logger.warning(
                            "GELF queue full, starting to drop log entries. "
                            "Will report every 100 drops to avoid log spam."
                        )
            except Exception as e:
                self.logger.error("Error queueing GELF data: %s", e)

    def deadend_status(self):
        """Endpoint to return service status with IP filtering."""
        remote_ip = request.remote_addr or "unknown"

        if not self._is_healthcheck_allowed(remote_ip):
            self.logger.warning(f"Healthcheck denied from {remote_ip}")
            # Return 204 No Content to avoid revealing endpoint existence
            return "", 204

        return jsonify({"service": "ok"}), 200

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
