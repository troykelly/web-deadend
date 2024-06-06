#!/bin/sh

# Exit immediately if any command exits with a non-zero status
set -e

# Check if the PORT environment variable is set, if not, default to 3000
PORT=${PORT:-3000}

# Log the startup configuration
echo "Starting Gunicorn on port $PORT"

# Bind to both IPv4 and IPv6 addresses on the specified port
BIND_ADDR="[::]:${PORT}"

# Check if the first argument is a flag or a command
if [ "${1#-}" != "$1" ]; then
    # Assume user is passing Gunicorn flags, prepend Gunicorn command
    set -- gunicorn -b ${BIND_ADDR} src.server:app "$@"
fi

# If the user passes a command (not starting with `gunicorn`), run that instead.
if [ "$1" = "gunicorn" ]; then
    exec gunicorn -b ${BIND_ADDR} src.server:app "$@"
else
    exec "$@"
fi
