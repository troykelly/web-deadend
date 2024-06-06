# src/response/utils.py

import re
from typing import Dict, Optional

def route_matches_url(route: str, url: str) -> Optional[Dict[str, str]]:
    """
    Check if the route matches the given URL and extract variables.
    Supports plain text, placeholders, and regex matches.
    """
    print(f"Matching route: {route} with URL: {url}")

    # Plain text or wildcard match
    if route == url:
        return {}

    # Placeholder {varname} match
    if '{' in route and '}' in route:
        parts = route.split('/')
        url_parts = url.split('/')
        if len(parts) != len(url_parts):
            return None
        path_variables = {}
        for p, u in zip(parts, url_parts):
            if p.startswith('{') and p.endswith('}'):
                var_name = p[1:-1]
                path_variables[var_name] = u
            elif p != u:
                return None
        return path_variables

    # Regex pattern match
    if route.startswith('r/'):
        pattern = route[2:]
        match = re.match(pattern, url)
        if match:
            return match.groupdict()

    return None