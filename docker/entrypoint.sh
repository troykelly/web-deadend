#!/bin/sh

# Exit immediately if any command exits with a non-zero status
set -e

# Check if the PORT environment variable is set, if not, default to 3000
PORT=${PORT:-3000}

# Performance tuning for high-throughput honeypot (10k+ req/s target)
# Workers: Auto-calculate based on CPU cores
# Formula for gevent workers (I/O-bound): (2 × CPU_cores) + 1
# This provides good concurrency while maintaining isolation between workers
if [ -z "$WORKERS" ]; then
    # Detect CPU count (works on Linux and most Unix systems)
    CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
    WORKERS=$((2 * CPU_COUNT + 1))
    echo "Auto-detected $CPU_COUNT CPU cores, setting WORKERS=$WORKERS"
fi

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
