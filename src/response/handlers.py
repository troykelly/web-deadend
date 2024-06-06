# src/response/handlers.py

import os
import yaml
import base64
from typing import Any, Dict, Optional
from flask import request, Response
from jinja2 import Template
from response.utils import route_matches_url

def load_responses(file_path: str) -> Optional[Dict[str, Any]]:
    """Load the response configurations from a YAML file."""
    if not os.path.exists(file_path):
        return None
    
    with open(file_path, 'r') as file:
        try:
            return yaml.safe_load(file)
        except yaml.YAMLError:
            return None

def get_response_data(config: Dict[str, Any], method: str, url: str, path_variables: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Retrieve the response data configuration for a given method and URL."""
    for route, methods in config.items():
        path_vars = route_matches_url(route, url)
        print(f"Checking route: {route}, URL: {url}, Matches: {path_vars}")
        if path_vars is not None:
            method_config = methods.get(method)
            if method_config:
                return method_config, path_vars
    return None

def generate_response(body_template: str, data: Dict[str, Any], path_variables: Dict[str, str]) -> str:
    """Generate the response body using Jinja2 templating."""
    template = Template(body_template)
    return template.render(request=data, matched=path_variables)

def handle_request() -> Response:
    """Handle the incoming request and return the appropriate canned response or 204."""
    responses_file = os.getenv("RESPONSES_FILE", "responses.yaml")
    responses_config = load_responses(responses_file)
    
    if responses_config is None:
        return Response("", status=204)

    url = request.path
    method = request.method
    path_variables = route_matches_url(url, url)

    response_data = get_response_data(responses_config, method, url, path_variables)
    
    if not response_data:
        print(f"No matching response configuration for URL: {url} and Method: {method}")
        return Response("", status=204)

    method_config, path_variables = response_data
    body = generate_response(method_config["body"], request, path_variables)

    if method_config.get("base64"):
        body = base64.b64decode(body.encode("utf-8"))

    return Response(
        response=body,
        status=method_config["responsestatus"],
        mimetype=method_config["mediatype"]
    )