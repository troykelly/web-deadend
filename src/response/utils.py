# src/response/utils.py

import re
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

def set_logger(target_logger: logging.Logger) -> None:
    global logger
    logger = target_logger

def safe_ip(ip_address: str) -> str:
    """
    Convert an IP address to a filesystem-safe format.

    IPv4: 192.168.1.100 -> 192_168_1_100
    IPv6: 2001:db8::1 -> 2001_db8__1

    Args:
        ip_address: The IP address string to convert

    Returns:
        A safe string with dots and colons replaced by underscores
    """
    if not ip_address:
        return "unknown"

    # Replace dots (IPv4) and colons (IPv6) with underscores
    safe = ip_address.replace('.', '_').replace(':', '_')

    # Handle IPv6 double colons (::) which become __
    # This is intentional for unique representation

    return safe

def route_matches_url(route: str, url: str) -> Optional[Dict[str, str]]:
    """
    Check if the route matches the given URL and extract variables.
    Supports plain text, placeholders, percent-wildcards, and regex matches.

    Supported wildcards:
    - {varname} - Placeholder syntax for path variables
    - %IP% - Matches safe IP format (e.g., 192_168_1_100, 2001_db8__1)
    - %EPOCH% - Matches Unix epoch timestamp (numeric digits)
    """
    logger.debug(f"Matching route: {route} with URL: {url}")

    # Plain text or wildcard match
    if route == url:
        return {}

    # Percent wildcard match (%IP%, %EPOCH%)
    if '%IP%' in route or '%EPOCH%' in route:
        parts = route.split('/')
        url_parts = url.split('/')
        if len(parts) != len(url_parts):
            return None
        path_variables = {}
        for p, u in zip(parts, url_parts):
            if p == '%IP%':
                # %IP% matches safe IP format: underscores and alphanumeric
                # Valid: 192_168_1_100, 2001_db8__1
                if re.match(r'^[a-fA-F0-9_]+$', u):
                    path_variables['IP'] = u
                else:
                    return None
            elif p == '%EPOCH%':
                # %EPOCH% matches numeric timestamp
                if re.match(r'^\d+$', u):
                    path_variables['EPOCH'] = u
                else:
                    return None
            elif p != u:
                return None
        return path_variables

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
