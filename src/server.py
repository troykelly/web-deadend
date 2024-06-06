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

VERSION = "__VERSION__"  # <-- This will be replaced during the release process

app = Flask(__name__)
logger = create_logger(app)

# Set logging level from environment variable, default to DEBUG if not set
debug_level = os.getenv("DEBUG_LEVEL", "INFO").upper()
if debug_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
    debug_level = "DEBUG"
logger.setLevel(getattr(logging, debug_level))

# GELF configuration
gelf_server = os.getenv("GELF_SERVER")
gelf_handler = None

# Setup GELF handler if GELF_SERVER environment variable is set
if gelf_server:
    parsed_url = urlparse(gelf_server)
    gelf_host = parsed_url.hostname
    gelf_port = parsed_url.port

    if parsed_url.scheme == "udp":
        gelf_handler = graypy.GELFUDPHandler(gelf_host, gelf_port)
        logger.info(f"Setting up UDP GELF handler for {gelf_host}:{gelf_port}")
    elif parsed_url.scheme == "tcp":
        gelf_handler = graypy.GELFTCPHandler(gelf_host, gelf_port)
        logger.info(f"Setting up TCP GELF handler for {gelf_host}:{gelf_port}")
    else:
        raise ValueError(f"Unsupported GELF scheme: {parsed_url.scheme}")

# Counter for requests statistics
request_counter: Counter[str] = Counter()
request_details: List[Dict[str, Union[str, int]]] = []

def get_request_body() -> Union[Dict[str, Any], str]:
    """Extract and return the request body based on its content type."""
    content_type = request.headers.get("Content-Type", "").lower()
    
    if content_type == "application/json":
        return request.get_json(silent=True) or {}
    
    elif content_type == "application/xml":
        try:
            return xmltodict.parse(request.data.decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse XML body: {e}")
            return {}
    
    elif content_type == "text/plain":
        return request.data.decode("utf-8")
    
    elif content_type == "application/x-www-form-urlencoded":
        return parse_qs(request.data.decode("utf-8"))
    
    elif content_type.startswith("multipart/form-data"):
        data = {}
        for key, value in request.form.items():
            data[key] = value
        for key, file in request.files.items():
            data[key] = base64.b64encode(file.read()).decode('utf-8')
        return data
    
    else:
        logger.warning(f"Unhandled content type: {content_type}")
        return request.data.decode("utf-8")


def send_to_gelf(data: Dict[str, Any]) -> None:
    """Send data to the GELF server if configured."""
    if gelf_handler:
        logger.info("Sending payload to GELF", extra=data)
        gelf_handler.emit(logging.makeLogRecord({"msg": "Payload Data", "extra": data}))

@app.before_request
def before_request() -> None:
    """Store the start time before processing the request."""
    g.start_time = time.time()

@app.after_request
def after_request(response: Response) -> Response:
    """Log ending information and calculate request duration."""
    request_duration = time.time() - g.start_time
    request_size = len(request.data)
    response_size = response.calculate_content_length()
    
    request_data: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": VERSION if VERSION != "__VERSION__" else "dev",
        "remote_addr": request.remote_addr,
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "query_params": request.args.to_dict(),  # Add query parameters here
        "body": get_request_body(),
        "request_size": request_size,
        "response_status": response.status_code,
        "response_size": response_size if response_size is not None else 0,
        "duration_ms": int(request_duration * 1000),  # Convert to milliseconds
    }
    
    # Log the request to STDERR in JSON format
    logger.debug(json.dumps(request_data))
    
    # Update counters and details for statistics
    request_counter.update([request.path])
    request_details.append(
        {"method": request.method, "path": request.path, "query_params": request.args.to_dict(), "domain": request.host}
    )
    
    # Send payload to GELF if configured
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
    return "", 204

if __name__ == "__main__":
    # Get port from the environment variable or default to 3000
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)