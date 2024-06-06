import os
import json
import logging
import time
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, Union, List
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

app = Flask(__name__)
logger = create_logger(app)

debug_level = os.getenv("DEBUG_LEVEL", "INFO").upper()
valid_debug_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
if debug_level not in valid_debug_levels:
    debug_level = "DEBUG"
logger.setLevel(getattr(logging, debug_level))

gelf_server = os.getenv("GELF_SERVER")
gelf_handler = None

if gelf_server:
    parsed_url = urlparse(gelf_server)
    gelf_host = parsed_url.hostname
    gelf_port = parsed_url.port

    if parsed_url.scheme == "udp":
        gelf_handler = graypy.GELFUDPHandler(gelf_host, gelf_port)
    elif parsed_url.scheme == "tcp":
        gelf_handler = graypy.GELFTCPHandler(gelf_host, gelf_port)
    else:
        raise ValueError(f"Unsupported GELF scheme: {parsed_url.scheme}")

    gelf_logger = logging.getLogger("gelf")
    gelf_logger.setLevel(logging.INFO)
    gelf_logger.addHandler(gelf_handler)
else:
    gelf_logger = None
    logger.warning("No GELF server specified; GELF handler not set up")

request_counter: Counter[str] = Counter()
request_details: List[Dict[str, Union[str, int]]] = []

def get_logger() -> logging.Logger:
    return logger

def get_request_body() -> Union[Dict[str, Any], str]:
    """Extract and return the request body based on its content type."""
    content_type = request.headers.get("Content-Type", "").lower().strip()
    
    if not content_type:
        return request.data.decode("utf-8")

    try:
        if content_type == "application/json":
            return request.get_json(silent=True) or {}

        if content_type == "application/xml":
            return xmltodict.parse(request.data.decode("utf-8"))

        if content_type == "text/plain":
            return request.data.decode("utf-8")

        if content_type == "application/x-www-form-urlencoded":
            return parse_qs(request.data.decode("utf-8"))

        if content_type.startswith("multipart/form-data"):
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

        logger.warning("Unhandled content type: %s", content_type)
    except Exception as e:
        logger.error("Error processing request body for content type %s: %s", content_type, e)
        return {}
    
    return request.data.decode("utf-8")

def send_to_gelf(data: Dict[str, Any]) -> None:
    """Send data to the GELF server if configured."""
    if gelf_logger:
        try:
            message = f"{data['method']} {data['path']} {data['response_status']} {data['duration_ms']}ms"
            log_entry = json.dumps(data).encode('utf-8')
            if len(log_entry) > MAX_GELF_PAYLOAD_SIZE:
                logger.error("GELF payload size exceeds the limit, reducing body size.")
                data["body"] = "Request body too large, removed to prevent payload overflow"
            gelf_logger.info(message, extra=data)
            logger.debug(f"Sent data to {gelf_host}")
        except Exception as e:
            logger.error("Error sending data to GELF: %s", e)

@app.before_request
def before_request() -> None:
    """Store the start time before processing the request."""
    g.start_time = time.time()

@app.after_request
def after_request(response: Response) -> Response:
    """
    Log ending information and calculate request duration. Skip logging
    for health check requests from localhost.
    """
    if request.path == "/deadend-status" and request.remote_addr == "127.0.0.1":
        return response

    request_duration = time.time() - g.start_time
    request_size = len(request.data)
    response_size = response.calculate_content_length()

    request_data: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": VERSION if VERSION != "__VERSION__" else "dev",
        "hostname": request.host,
        "remote_addr": request.remote_addr,
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "query_params": request.args.to_dict(),
        "body": get_request_body(),
        "request_size": request_size,
        "response_status": response.status_code,
        "response_size": response_size if response_size is not None else 0,
        "duration_ms": int(request_duration * 1000),  # Convert to milliseconds
    }

    logger.debug(json.dumps(request_data))

    request_counter.update([request.path])
    request_details.append({
        "method": request.method,
        "path": request.path,
        "query_params": request.args.to_dict(),
        "domain": request.host,
    })

    send_to_gelf(request_data)

    return response

@app.route("/deadend-status", methods=["GET"])
def deadend_status() -> Response:
    """Endpoint to return service status."""
    return jsonify({"service": "ok"}), 200

@app.route("/deadend-counter", methods=["GET"])
def deadend_counter() -> Response:
    """Endpoint to return request statistics."""
    top_domains = request_counter.most_common(10)
    request_breakdown = Counter([req["method"] for req in request_details])

    response_data = {
        "total_requests_received": sum(request_counter.values()),
        "top_10_domains_urls": top_domains,
        "request_type_breakdown": request_breakdown,
    }
    return jsonify(response_data), 200

@app.route(
    "/",
    defaults={"path": ""},
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"],
)
@app.route(
    "/<path:path>",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"],
)
def catch_all(path: str) -> Response:
    """Catch-all endpoint to handle requests."""
    return handle_request()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)