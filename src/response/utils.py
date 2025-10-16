# src/response/utils.py

import logging
import re
import signal
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Regex timeout in seconds to prevent ReDoS attacks
REGEX_TIMEOUT_SECONDS = 1


class RegexTimeoutError(Exception):
    """Raised when regex matching exceeds timeout."""

    pass


def _timeout_handler(signum, frame):
    """Signal handler for regex timeout."""
    raise RegexTimeoutError("Regex matching exceeded timeout")


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
        if re.search(danger_pattern, pattern):
            logger.warning(f"Regex pattern contains nested quantifiers (ReDoS risk): {pattern}")
            return False

    # Try to compile the pattern to ensure it's valid
    try:
        re.compile(pattern)
    except re.error as e:
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
    - %IP% - Matches safe IP format (e.g., 192_168_1_100, 2001_db8__1)
    - %EPOCH% - Matches Unix epoch timestamp (numeric digits)
    """
    logger.debug(f"Matching route: {route} with URL: {url}")

    # Plain text or wildcard match
    if route == url:
        return {}

    # Percent wildcard match (%IP%, %EPOCH%)
    if "%IP%" in route or "%EPOCH%" in route:
        parts = route.split("/")
        url_parts = url.split("/")
        if len(parts) != len(url_parts):
            return None
        path_variables = {}
        for p, u in zip(parts, url_parts):
            if p == "%IP%":
                # %IP% matches safe IP format: underscores and alphanumeric
                # Valid: 192_168_1_100, 2001_db8__1
                if re.match(r"^[a-fA-F0-9_]+$", u):
                    path_variables["IP"] = u
                else:
                    return None
            elif p == "%EPOCH%":
                # %EPOCH% matches numeric timestamp
                if re.match(r"^\d+$", u):
                    path_variables["EPOCH"] = u
                else:
                    return None
            elif p != u:
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

        # Set up timeout to prevent ReDoS attacks
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(REGEX_TIMEOUT_SECONDS)

        try:
            match = re.match(pattern, url)
            signal.alarm(0)  # Cancel alarm
            if match:
                return match.groupdict()
        except RegexTimeoutError:
            logger.error(f"Regex timeout for pattern: {pattern} with URL: {url}")
            return None
        except Exception as e:
            logger.error(f"Regex error for pattern: {pattern}: {e}")
            return None
        finally:
            signal.alarm(0)  # Ensure alarm is cancelled
            signal.signal(signal.SIGALRM, old_handler)  # Restore old handler

    return None
