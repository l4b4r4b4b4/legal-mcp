# Legal-MCP

[![CI](https://github.com/l4b4r4b4b4/legal-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/l4b4r4b4b4/legal-mcp/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/legal-mcp.svg)](https://pypi.org/project/legal-mcp/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)




[![GHCR](https://img.shields.io/badge/GHCR-legal-mcp-blue?logo=github)](https://ghcr.io/l4b4r4b4b4/legal-mcp)

A comprehensive legal research MCP server built with FastMCP and mcp-refcache, providing AI assistants with structured access to legal information across multiple jurisdictions.

Built with [FastMCP](https://github.com/jlowin/fastmcp) and [mcp-refcache](https://github.com/l4b4r4b4b4/mcp-refcache) for efficient handling of large data in AI agent tools.

## Features

- ✅ **Reference-Based Caching** - Return references instead of large data, reducing context window usage
- ✅ **Preview Generation** - Automatic previews for large results (sample, truncate, paginate strategies)
- ✅ **Pagination** - Navigate large datasets without loading everything at once
- ✅ **Access Control** - Separate user and agent permissions for sensitive data
- ✅ **Private Computation** - Let agents compute with values they cannot see
- ✅ **Docker Ready** - Production-ready containers with Python slim base image
- ✅ **GitHub Actions** - CI/CD with PyPI publishing and GHCR containers

- ✅ **Type-Safe** - Full type hints with Pydantic models
- ✅ **Testing Ready** - pytest with 73% coverage requirement
- ✅ **Pre-commit Hooks** - Ruff formatting and linting

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Installation

```bash
# Clone the repository
git clone https://github.com/l4b4r4b4b4/legal-mcp
cd legal-mcp

# Install dependencies
uv sync

# Run the server (stdio mode for Claude Desktop)
uv run legal-mcp

# Run the server (SSE/HTTP mode for deployment)
uv run legal-mcp --transport sse --port 8000
```

### Install from PyPI

```bash
# Run directly with uvx (no install needed)
uvx legal-mcp stdio

# Or install globally
uv tool install legal-mcp
legal-mcp --help
```

### Docker Deployment

```bash
# Pull and run from GHCR
docker pull ghcr.io/l4b4r4b4b4/legal-mcp:latest
docker run -p 8000:8000 ghcr.io/l4b4r4b4b4/legal-mcp:latest

# Or build locally with Docker Compose
docker compose up

# Build images manually
docker compose --profile build build base
docker compose build
```

### Using with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "legal-mcp": {
      "command": "uv",
      "args": ["run", "legal-mcp"],
      "cwd": "/path/to/legal-mcp"
    }
  }
}
```

### Using with Zed

The project includes `.zed/settings.json` pre-configured for MCP context servers.


## Project Structure

```
legal-mcp/
├── app/                     # Application code
│   ├── __init__.py          # Version export
│   ├── server.py            # Main server with tools
│   ├── tools/               # Tool modules
│   └── __main__.py          # CLI entry point
├── tests/                   # Test suite
│   ├── conftest.py          # Pytest fixtures
│   └── test_server.py       # Server tests
├── docker/
│   ├── Dockerfile.base      # Python slim base image with dependencies
│   ├── Dockerfile           # Production image (extends base)
│   └── Dockerfile.dev       # Development with hot reload
├── .github/
│   └── workflows/
│       ├── ci.yml           # CI pipeline (lint, test, security)
│       ├── publish.yml      # PyPI trusted publisher
│       └── release.yml      # Docker build & publish to GHCR
├── .agent/                  # AI assistant workspace
│   └── goals/
│       └── 00-Template-Goal/  # Goal tracking template
├── pyproject.toml           # Project config
├── docker-compose.yml       # Local development & production
├── flake.nix                # Nix dev shell
└── .rules                   # AI assistant guidelines
```

## Development

### Setup

```bash
# Install dependencies
uv sync

# Install pre-commit and pre-push hooks
uv run pre-commit install --install-hooks
uv run pre-commit install --hook-type pre-push
```

### Running Tests

```bash
uv run pytest
uv run pytest --cov  # With coverage
```

### Linting and Formatting

```bash
uv run ruff check . --fix
uv run ruff format .
```

### Type Checking

```bash
uv run mypy app/
```

### Docker Development

```bash
# Run development container with hot reload
docker compose --profile dev up

# Build base image (for publishing)
docker compose --profile build build base

# Build all images
docker compose build
```

### Using Nix (Optional)

```bash
nix develop  # Enter dev shell with all tools
```

## Configuration


### CLI Commands

```bash
uvx legal-mcp --help

Commands:
  stdio             Start server in stdio mode (for Claude Desktop and local CLI)
  sse               Start server in SSE mode (Server-Sent Events)
  streamable-http   Start server in streamable HTTP mode (recommended for remote/Docker)

# Examples:
uvx legal-mcp stdio                          # Local CLI mode
uvx legal-mcp sse --port 8000                # SSE on port 8000
uvx legal-mcp streamable-http --host 0.0.0.0 # Docker/remote mode
```

## CI/CD Workflow

This project uses a CI-gated workflow to ensure code quality and safe releases:

```
┌─────────────────────────────────────────────────────────────┐
│  Feature Branch → Open PR                                   │
│         ↓                                                    │
│  CI Runs (lint, test, security)                            │
│         ↓                                                    │
│  ✅ CI Must Pass (enforced by branch protection)           │
│         ↓                                                    │
│  Merge to main                                              │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  CI Re-runs on main                                         │
│         ↓                                                    │
│  Release Workflow waits for CI Success                      │
│         ↓                                                    │
│  Docker Images Built & Pushed to GHCR                       │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  Manually Create GitHub Release                             │
│         ↓                                                    │
│  Publish Workflow verifies Release succeeded                │
│         ↓                                                    │
│  Package Published to PyPI                                  │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  CD Workflow deploys (staging/production)                   │
└─────────────────────────────────────────────────────────────┘
```

**Key Safeguards:**
- ✅ Branch protection ensures CI passes before merge
- ✅ Tag pushes verify CI passed before building images
- ✅ Publish workflow verifies Release succeeded before PyPI upload
- ✅ CD workflow only deploys after Release completes

**Manual Gates:**
- 🔒 Creating GitHub Release (allows review before PyPI publish)
- 🔒 Production deployments (requires manual approval)

## Publishing

### PyPI

Configure trusted publisher at [PyPI](https://pypi.org/manage/account/publishing/):
- Project name: `legal-mcp`
- Owner: `l4b4r4b4b4`
- Repository: `legal-mcp`
- Workflow: `publish.yml`
- Environment: `pypi`

### Docker Images

Images are automatically published to GHCR on:
- Push to `main` branch → `latest` tag
- Version tags (`v*.*.*`) → `latest`, `v0.0.1`, `0.0.1`, `0.0` tags

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

## Related Projects

- [mcp-refcache](https://github.com/l4b4r4b4b4/mcp-refcache) - Reference-based caching for MCP servers
- [FastMCP](https://github.com/jlowin/fastmcp) - High-performance MCP server framework
- [Model Context Protocol](https://modelcontextprotocol.io/) - The underlying protocol specification
