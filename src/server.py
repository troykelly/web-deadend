import os
import json
import logging
from datetime import datetime
from collections import Counter
from typing import Any, Dict, Union, List
from urllib.parse import urlparse

import xmltodict
from flask import Flask, request, jsonify, Response
from flask.logging import create_logger
import graypy

app = Flask(__name__)
logger = create_logger(app)
logger.setLevel(logging.DEBUG)

# Setup GELF logging if GELF_SERVER environment variable is set
gelf_server = os.getenv("GELF_SERVER")
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

    logger.addHandler(gelf_handler)

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

    else:
        logger.warning(f"Unhandled content type: {content_type}")
        return request.data.decode("utf-8")

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
    """Catch-all endpoint to log request details."""
    request_body = get_request_body()
    request_data: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "remote_addr": request.remote_addr,
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "body": request_body,
    }

    # Log the request to STDERR in JSON format
    logger.debug(json.dumps(request_data))

    # Update counters and details for statistics
    request_counter.update([request.path])
    request_details.append(
        {"method": request.method, "path": request.path, "domain": request.host}
    )

    # Optionally log to GELF if configured
    if gelf_server:
        logger.info("Request logged to GELF", extra=request_data)

    # Return empty response
    return "", 204


if __name__ == "__main__":
    # Get port from the environment variable or default to 3000
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
