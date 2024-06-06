#!/bin/sh

# Exit immediately if any command exits with a non-zero status
set -e

# Check if PORT environment variable is set, if not, default to 3000
PORT=${PORT:-3000}

# Log the start-up configuration
echo "Starting Gunicorn on port $PORT"

# Check if the first argument is a flag or a command
if [ "${1:0:1}" = '-' ]; then
  # Assume user is passing Gunicorn flags, prepend Gunicorn command
  set -- gunicorn -b 0.0.0.0:${PORT} "$@"
fi

# If the user passes a command (not starting with `gunicorn`), run that instead.
if [ "$1" != "gunicorn" ]; then
  exec "$@"
else
  # Execute Gunicorn server with expanded environment variables
  exec gunicorn -b 0.0.0.0:${PORT} "$@"
fi
