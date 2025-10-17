# src/response/handlers.py

import base64
import hashlib
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlencode

import yaml
from flask import Response, g, request
from jinja2.sandbox import SandboxedEnvironment

from response.utils import route_matches_url, safe_ip

logger = logging.getLogger(__name__)


def _md5_filter(value: str) -> str:
    """Jinja2 filter to compute MD5 hash of a string.

    Args:
        value: String to hash

    Returns:
        Lowercase hexadecimal MD5 hash (32 characters)

    Example:
        {{ "hello" | md5 }} -> "5d41402abc4b2a76b9719d911017c592"
    """
    if not isinstance(value, str):
        value = str(value)
    return hashlib.md5(value.encode("utf-8")).hexdigest()


# Create a sandboxed Jinja2 environment for secure template rendering
# This prevents template injection attacks (SSTI) by restricting dangerous operations
_jinja_env = SandboxedEnvironment(
    autoescape=False,  # Don't escape since we're generating various content types (XML, JSON, etc.)
)
# Clear global namespace to prevent access to builtins
_jinja_env.globals.clear()
# Add custom filters
_jinja_env.filters["md5"] = _md5_filter


def set_logger(target_logger: logging.Logger) -> None:
    global logger
    logger = target_logger


def load_responses(file_path: str) -> Optional[Dict[str, Any]]:
    """Load the response configurations from a YAML file."""
    if not os.path.exists(file_path):
        return None

    with open(file_path, "r") as file:
        try:
            result = yaml.safe_load(file)
            return result if isinstance(result, dict) else None
        except yaml.YAMLError as e:
            logger.error("Error loading YAML file: %s", e)
            return None


def get_response_data(
    config: Dict[str, Any], method: str, url: str, path_variables: Dict[str, str]
) -> Optional[Tuple[Dict[str, Any], Dict[str, str]]]:
    """Retrieve the response data configuration for a given method and URL."""
    for route, methods in config.items():
        path_vars = route_matches_url(route, url)
        logger.debug(f"Checking route: {route}, URL: {url}, Matches: {path_vars}")
        if path_vars is not None:
            method_config = methods.get(method)
            if method_config:
                return (method_config, path_vars)
    return None


def generate_response(
    body_template: str,
    context: Dict[str, Any],
    path_variables: Dict[str, str],
    body_obj: Optional[Dict[str, Any]],
    query_obj: Optional[Dict[str, Any]],
    requestdata: str,
) -> str:
    """Generate the response body using Jinja2 sandboxed templating.

    Uses SandboxedEnvironment to prevent SSTI (Server-Side Template Injection) attacks.
    The sandbox restricts access to dangerous Python operations and builtins.
    """
    template = _jinja_env.from_string(body_template)
    rendered: str = template.render(
        request=context,
        matched=path_variables,  # Legacy: kept for backwards compatibility
        path=path_variables,  # Path segment variables extracted from route
        body=body_obj or {},
        query=query_obj or {},
        requestdata=requestdata,
    )
    return rendered


def _parse_request_body() -> Union[Dict[str, Any], str]:
    """Parse request body based on content type and return structured data if possible."""
    content_type = request.headers.get("Content-Type", "").lower().strip()

    # Handle JSON
    if "application/json" in content_type:
        try:
            return request.get_json(silent=True) or {}
        except:
            pass

    # Handle form-encoded data
    if "application/x-www-form-urlencoded" in content_type:
        if request.form:
            return {key: request.form.get(key) for key in request.form.keys()}
        return {}

    # Handle multipart form data
    if "multipart/form-data" in content_type:
        data = {}
        for key, value in request.form.items():
            data[key] = value
        for key, file in request.files.items():
            # For files, base64 encode the content
            file_data = base64.b64encode(file.read()).decode("utf-8")
            data[key] = file_data
            file.seek(0)  # Reset file pointer
        return data

    # Return raw string for other content types
    try:
        return request.data.decode("utf-8") if request.data else ""
    except UnicodeDecodeError:
        return base64.b64encode(request.data).decode("utf-8") if request.data else ""


def _create_requestdata_string(
    body_obj: Optional[Dict[str, Any]], query_obj: Optional[Dict[str, Any]]
) -> str:
    """Create URL-encoded query string from body and query parameters."""
    combined = {}

    # Add query parameters
    if query_obj and isinstance(query_obj, dict):
        combined.update(query_obj)

    # Add body parameters (if it's a dict)
    if body_obj and isinstance(body_obj, dict):
        combined.update(body_obj)

    # If we have data, return as query string with leading ?
    if combined:
        return "?" + urlencode(combined, doseq=True)
    return ""


def handle_request() -> Response:
    """Handle the incoming request and return the appropriate canned response or 204."""
    responses_file = os.getenv("RESPONSES_FILE", "responses.yaml")
    responses_config = load_responses(responses_file)

    if responses_config is None:
        return Response("", status=204)

    url = request.path
    method = request.method
    path_variables = route_matches_url(url, url)
    protocol = request.scheme
    host = request.host
    port = request.environ.get("SERVER_PORT")

    # Get client IP address and convert to safe format
    client_ip = request.remote_addr or "unknown"
    safe_client_ip = safe_ip(client_ip)

    # Get current epoch timestamp
    epoch_timestamp = int(time.time())

    # Get request ID from Flask g context
    request_id = getattr(g, "request_id", "")

    # Parse body to structured format if possible
    parsed_body = _parse_request_body()
    body_obj = parsed_body if isinstance(parsed_body, dict) else None
    body_str = parsed_body if isinstance(parsed_body, str) else ""

    # Get query parameters as dict
    query_obj = request.args.to_dict() if request.args else {}

    # Create encoded requestdata string
    requestdata = _create_requestdata_string(body_obj, query_obj)

    context = {
        "id": request_id,
        "protocol": protocol,
        "host": host,
        "port": port,
        "method": method,
        "path": url,
        "headers": dict(request.headers),
        "query_params": query_obj,
        "body": body_str,
        "safe_ip": safe_client_ip,
        "epoch": epoch_timestamp,
    }

    response_data = get_response_data(responses_config, method, url, path_variables)

    if not response_data:
        logger.debug(f"No matching response configuration for URL: {url} and Method: {method}")
        return Response("", status=204)

    method_config, path_variables = response_data
    body_str = generate_response(
        method_config["body"], context, path_variables, body_obj, query_obj, requestdata
    )

    response_body: Union[str, bytes] = body_str
    if method_config.get("base64"):
        response_body = base64.b64decode(body_str.encode("utf-8"))

    return Response(
        response=response_body,
        status=method_config["responsestatus"],
        mimetype=method_config["mediatype"],
    )
