# The Web Dead End

## Overview

**The Web Dead End** is a Python-based HTTP server that logs all inbound web traffic. It provides GELF (Graylog Extended Log Format) logging support for integrating with centralized logging systems.

## Features

- **Request Logging**: Logs details about every HTTP request received.
- **GELF Support**: Integrates with GELF logging systems if configured.
- **Request Statistics**: Provides endpoints to fetch statistics about traffic.
- **Docker Support**: Can be easily deployed using Docker.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
  - [Prerequisites](#prerequisites)
  - [Setup](#setup)
- [Usage](#usage)
  - [Running Server](#running-server)
  - [Endpoints](#endpoints)
- [Development](#development)
  - [Dev Container](#dev-container)
  - [GitHub Actions Workflows](#github-actions-workflows)
- [Contributing](#contributing)
- [License](#license)

## Installation

### Prerequisites

- Python 3.12 or later.
- Docker (for containerised deployment).
- Optional: Graylog server for GELF logging.

### Setup

1. Clone repository:

```bash
git clone https://github.com/troykelly/web-deadend.git
cd web-deadend
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Running Server

You can run the server directly with Python or using Docker.

#### Running with Python

```bash
export PORT=3000  # Optionally set the port (default: 3000)
export GELF_SERVER="udp://graylog.example.com:12201"  # Optional for GELF logging
python src/server.py
```

#### Running with Docker

Build and run the Docker image:

```bash
docker build -t web-deadend .
docker run -p 3000:3000 -e PORT=3000 -e GELF_SERVER="udp://graylog.example.com:12201" web-deadend
```

##### Or

```bash
docker run -p 3000:3000 -e PORT=3000 -e GELF_SERVER="udp://graylog.example.com:12201" ghcr.io/troykelly/web-deadend:edge
```

### Endpoints

- `GET /deadend-status`: Returns service status.
- `GET /deadend-counter`: Returns request statistics.
- `GET /, POST /, PUT /, DELETE /, etc.`: Catch-all endpoint that logs details of the request.

## Development

### Dev Container

This project includes a dev container configuration for VSCode. To use it, ensure you have the Remote - Containers extension installed.

1. Open the project in VSCode.
2. When prompted, reopen in container.

### GitHub Actions Workflows

The repository includes workflows to automate Dependabot PRs and publish Docker images:

- **dependabot-release.yml**: Automatically approves and merges Dependabot PRs, creating a draft release.
- **build-and-publish.yml**: Builds and publishes Docker images to the GitHub Container Registry on pushes to main and when releases are published.

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Commit your changes.
4. Push the branch and create a pull request.

## License

This project is licensed under the Apache 2.0 License.