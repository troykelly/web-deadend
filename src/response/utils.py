# src/response/utils.py

import logging
from typing import Dict, Optional

try:
    import regex  # Use regex module with proper timeout support for gevent

    REGEX_MODULE = "regex"
except ImportError:
    import re as regex  # Fallback to standard re module

    REGEX_MODULE = "re"
    logging.warning(
        "regex module not installed, falling back to 're' module. "
        "Install 'regex' for proper ReDoS protection in multi-threaded environments: "
        "pip install regex"
    )

logger = logging.getLogger(__name__)

# Regex timeout in seconds to prevent ReDoS attacks
REGEX_TIMEOUT_SECONDS = 1


def validate_regex_pattern(pattern: str) -> bool:
    """Validate regex pattern to reject dangerous patterns that could cause ReDoS.

    Returns True if pattern is safe, False if it should be rejected.
    """
    # Maximum pattern length
    if len(pattern) > 500:
        logger.warning(f"Regex pattern too long ({len(pattern)} chars): {pattern[:100]}...")
        return False

    # Check for nested quantifiers (e.g., (a+)+, (a*)*) - major ReDoS indicator
    nested_quantifier_patterns = [
        r"\([^)]*[*+]\)[*+]",  # (something+)+ or (something*)*
        r"\([^)]*[*+]\)\{",  # (something+){n,m}
        r"\{[^}]+\}[*+]",  # {n,m}+ or {n,m}*
    ]

    for danger_pattern in nested_quantifier_patterns:
        if regex.search(danger_pattern, pattern):
            logger.warning(f"Regex pattern contains nested quantifiers (ReDoS risk): {pattern}")
            return False

    # Try to compile the pattern to ensure it's valid
    try:
        regex.compile(pattern)
    except regex.error as e:
        logger.warning(f"Invalid regex pattern: {pattern}, error: {e}")
        return False

    return True


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
    safe = ip_address.replace(".", "_").replace(":", "_")

    # Handle IPv6 double colons (::) which become __
    # This is intentional for unique representation

    return safe


def route_matches_url(route: str, url: str) -> Optional[Dict[str, str]]:
    """
    Check if the route matches the given URL and extract variables.
    Supports plain text, placeholders, percent-wildcards, and regex matches.

    Supported wildcards:
    - {varname} - Placeholder syntax for path variables
    - %WILDCARD% - Generic wildcard that matches any path segment
      Examples: %IP%, %EPOCH%, %ORIGINALREQUESTID%, %FILENAME%, etc.
    """
    logger.debug(f"Matching route: {route} with URL: {url}")

    # Plain text or wildcard match
    if route == url:
        return {}

    # Percent wildcard match (%ANYTHING%)
    # Match any %WILDCARD% pattern
    if "%" in route:
        parts = route.split("/")
        url_parts = url.split("/")
        if len(parts) != len(url_parts):
            return None
        path_variables = {}
        for p, u in zip(parts, url_parts):
            # Check if this part is a %WILDCARD% pattern
            if p.startswith("%") and p.endswith("%") and len(p) > 2:
                # Extract the wildcard name (e.g., %IP% -> IP)
                wildcard_name = p[1:-1]
                # Store the matched value with the wildcard name as key
                path_variables[wildcard_name] = u
            elif p != u:
                # Not a wildcard and doesn't match exactly
                return None
        return path_variables

    # Placeholder {varname} match
    if "{" in route and "}" in route:
        parts = route.split("/")
        url_parts = url.split("/")
        if len(parts) != len(url_parts):
            return None
        path_variables = {}
        for p, u in zip(parts, url_parts):
            if p.startswith("{") and p.endswith("}"):
                var_name = p[1:-1]
                path_variables[var_name] = u
            elif p != u:
                return None
        return path_variables

    # Regex pattern match with timeout protection
    if route.startswith("r/"):
        pattern = route[2:]

        # Validate pattern for ReDoS safety
        if not validate_regex_pattern(pattern):
            logger.error(f"Dangerous regex pattern rejected: {pattern}")
            return None

        try:
            # Use regex module with built-in timeout (works properly with gevent/threading)
            compiled = regex.compile(pattern)
            if REGEX_MODULE == "regex":
                # regex module supports native timeout parameter on match()
                match = compiled.match(url, timeout=REGEX_TIMEOUT_SECONDS)
            else:
                # Fallback to standard re (no timeout protection)
                logger.warning(
                    "Using standard 're' module without timeout protection. "
                    "Install 'regex' module for proper ReDoS prevention."
                )
                match = compiled.match(url)

            if match:
                return match.groupdict()
        except TimeoutError:
            logger.error(f"Regex timeout for pattern: {pattern} with URL: {url}")
            return None
        except regex.error as e:
            logger.error(f"Regex compilation error for pattern: {pattern}: {e}")
            return None
        except Exception as e:
            logger.error(f"Regex error for pattern: {pattern}: {e}")
            return None

    return None
