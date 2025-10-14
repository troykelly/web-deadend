FROM python:3.12-slim

# Create a group and user with a specific uid and gid
RUN groupadd -g 1000 webdeadend && useradd -u 1000 -g webdeadend -d /app -s /bin/bash webdeadend

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and default configuration with proper ownership
COPY --chown=webdeadend:webdeadend src /app/src
COPY --chown=webdeadend:webdeadend responses.yaml /etc/webdeadend/responses.yaml
COPY docker/entrypoint.sh /entrypoint.sh

# Ensure the working directory has correct ownership
RUN chown -R webdeadend:webdeadend /app && \
    chmod +x /entrypoint.sh

# Metadata labels for best practices
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION
ARG REPO_URL

LABEL maintainer="troy@troykelly.com" \
    org.opencontainers.image.title="The Web Dead End" \
    org.opencontainers.image.description="Logs all inbound web traffic" \
    org.opencontainers.image.authors="Troy Kelly <troy@troykellycom>" \
    org.opencontainers.image.vendor="Troy Kelly" \
    org.opencontainers.image.licenses="Apache 2.0" \
    org.opencontainers.image.url="${REPO_URL}" \
    org.opencontainers.image.source="${REPO_URL}" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.created="${BUILD_DATE}"

ENV RESPONSES_FILE=/etc/webdeadend/responses.yaml
ENV PYTHONPATH="/app/src"

# Health check configuration
ENV PORT=3000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c 'import os, requests; port = os.getenv("PORT", 3000); url = f"http://localhost:{port}/deadend-status"; exit(1) if requests.get(url).status_code != 200 else exit(0)'

# Switch to non-root user
USER webdeadend

# Use the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Default CMD to run the application
CMD ["gunicorn"]
