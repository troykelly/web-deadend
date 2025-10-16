#!/bin/sh

# Exit immediately if any command exits with a non-zero status
set -e

# Check if the PORT environment variable is set, if not, default to 3000
PORT=${PORT:-3000}

# Performance tuning for high-throughput honeypot (10k+ req/s target)
# Workers: Scale based on CPU cores (default 32 for 16-core systems)
# Reduce from default if running on smaller instances
WORKERS=${WORKERS:-32}

# Timeout: Reduced from 120s to 30s for faster request cycling
TIMEOUT=${TIMEOUT:-30}

# Worker connections: Max concurrent connections per worker
WORKER_CONNECTIONS=${WORKER_CONNECTIONS:-1000}

# Max requests: Restart worker after N requests to prevent memory leaks
MAX_REQUESTS=${MAX_REQUESTS:-100000}
MAX_REQUESTS_JITTER=${MAX_REQUESTS_JITTER:-10000}

# Log the startup configuration
echo "Starting Gunicorn for high-throughput honeypot:"
echo "  Port: $PORT"
echo "  Workers: $WORKERS"
echo "  Timeout: ${TIMEOUT}s"
echo "  Worker connections: $WORKER_CONNECTIONS"
echo "  Max requests: $MAX_REQUESTS (±$MAX_REQUESTS_JITTER jitter)"

# Bind to both IPv4 and IPv6 addresses on the specified port
BIND_ADDR="[::]:${PORT}"

# Check if the first argument is a flag or a command
if [ "${1#-}" != "$1" ]; then
    # Assume user is passing Gunicorn flags, prepend Gunicorn command
    set -- gunicorn -b ${BIND_ADDR} \
        --timeout ${TIMEOUT} \
        --workers ${WORKERS} \
        --worker-class gevent \
        --worker-connections ${WORKER_CONNECTIONS} \
        --max-requests ${MAX_REQUESTS} \
        --max-requests-jitter ${MAX_REQUESTS_JITTER} \
        src.server:app "$@"
fi

# If the user passes a command (not starting with `gunicorn`), run that instead.
if [ "$1" = "gunicorn" ]; then
    exec gunicorn -b ${BIND_ADDR} \
        --timeout ${TIMEOUT} \
        --workers ${WORKERS} \
        --worker-class gevent \
        --worker-connections ${WORKER_CONNECTIONS} \
        --max-requests ${MAX_REQUESTS} \
        --max-requests-jitter ${MAX_REQUESTS_JITTER} \
        src.server:app "$@"
else
    exec "$@"
fi
