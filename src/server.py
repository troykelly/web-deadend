import os
import json
import logging
from collections import Counter
from typing import Any, Dict

from flask import Flask, request, jsonify, Response
from flask.logging import create_logger

app = Flask(__name__)
logger = create_logger(app)
logger.setLevel(logging.DEBUG)

# Counter for requests statistics
request_counter = Counter()
request_details = []


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
    request_data = {
        "remote_addr": request.remote_addr,
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "body": request.get_json(silent=True) or request.data.decode("utf-8"),
    }

    # Log the request to STDERR
    logger.debug(json.dumps(request_data, indent=2))

    # Update counters and details for statistics
    request_counter.update([request.path])
    request_details.append(
        {"method": request.method, "path": request.path, "domain": request.host}
    )

    # Return empty response
    return "", 204


if __name__ == "__main__":
    # Get port from the environment variable or default to 5000
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
