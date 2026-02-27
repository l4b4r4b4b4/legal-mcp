"""CLI entry point for Legal-MCP.

Usage:
    uvx legal-mcp stdio           # Local CLI mode (Claude Desktop)
    uvx legal-mcp sse             # SSE server mode (deprecated)
    uvx legal-mcp streamable-http # Streamable HTTP (recommended for remote)
    uvx legal-mcp warmup          # Ingest pre-downloaded HTML corpus into ChromaDB

Environment Variables:
    FASTMCP_PORT: Server port for HTTP modes (default: 9685)
    FASTMCP_HOST: Server host for HTTP modes (default: 0.0.0.0)
    CACHE_BACKEND: Cache backend - memory, sqlite, redis (default: auto)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    LANGFUSE_PUBLIC_KEY: Langfuse public key (optional)
    LANGFUSE_SECRET_KEY: Langfuse secret key (optional)
    WARMUP_ON_STARTUP: Enable automatic corpus warm-up (default: false)
    WARMUP_MAX_LAWS: Max laws to ingest during warm-up (default: all)
    WARMUP_HTML_ROOT: Path to pre-downloaded HTML corpus (default: data/html)
"""

import os
import sys

import typer

app = typer.Typer(
    name="legal-mcp",
    help="A comprehensive legal research MCP server built with FastMCP and mcp-refcache, providing AI assistants with structured access to legal information across multiple jurisdictions.",
    add_completion=False,
)


def _get_host() -> str:
    """Get server host from environment."""
    return os.environ.get("FASTMCP_HOST", "0.0.0.0")  # nosec B104 - intentional for Docker


def _get_port() -> int:
    """Get server port from environment."""
    return int(os.environ.get("FASTMCP_PORT", "9685"))


def _print_startup_info(transport: str) -> None:
    """Print startup information."""
    from .tracing import is_langfuse_enabled

    typer.echo(f"Transport: {transport}")
    typer.echo(
        f"Langfuse tracing: {'enabled' if is_langfuse_enabled() else 'disabled'}"
    )
    typer.echo("Context propagation: enabled (user_id, session_id, metadata)")


def _maybe_start_warmup() -> None:
    """Start background corpus warm-up if configured.

    Reads WARMUP_ON_STARTUP from settings. If enabled, starts a background
    thread that ingests the pre-downloaded HTML corpus into ChromaDB.
    The server remains fully available while warm-up runs.
    """
    from .config import get_settings

    settings = get_settings()

    if not settings.warmup_on_startup:
        return

    from .warmup import start_background_warmup

    typer.echo("Corpus warm-up: starting in background...")
    started = start_background_warmup(
        html_root=settings.warmup_html_root,
        max_laws=settings.warmup_max_laws,
        batch_size=settings.warmup_batch_size,
        max_workers=settings.warmup_max_workers,
    )
    if started:
        typer.echo(
            f"Corpus warm-up: ingesting from {settings.warmup_html_root} "
            f"(max_laws={settings.warmup_max_laws or 'all'})"
        )
    else:
        typer.echo("Corpus warm-up: already running or skipped")


def _handle_shutdown() -> None:
    """Handle graceful shutdown."""
    from .tracing import flush_traces

    typer.echo("\nShutting down server...")
    flush_traces()
    typer.echo("Service stopped.")


@app.command()
def stdio() -> None:
    """Start server in stdio mode (for Claude Desktop and local CLI).

    This is the recommended mode for local usage with Claude Desktop
    or other MCP clients that communicate via stdin/stdout.

    Cache backend defaults to SQLite for persistence across sessions.
    """
    from .server import mcp

    _print_startup_info("stdio")
    _maybe_start_warmup()

    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        pass
    except Exception as error:
        typer.echo(f"\nError: {error}", err=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        _handle_shutdown()


@app.command()
def sse(
    host: str = typer.Option(None, "--host", "-h", help="Server host"),
    port: int = typer.Option(None, "--port", "-p", help="Server port"),
) -> None:
    """Start server in SSE mode (Server-Sent Events).

    Note: SSE transport is deprecated. Use streamable-http for new deployments.

    Cache backend defaults to Redis for distributed deployments.
    """
    from .server import mcp

    server_host = host or _get_host()
    server_port = port or _get_port()

    _print_startup_info("sse")
    _maybe_start_warmup()
    typer.echo(f"Server: http://{server_host}:{server_port}/sse")
    typer.secho(
        "Warning: SSE transport is deprecated. Use streamable-http instead.",
        fg=typer.colors.YELLOW,
    )

    try:
        mcp.run(transport="sse", host=server_host, port=server_port)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        typer.echo(f"\nError: {error}", err=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        _handle_shutdown()


@app.command("streamable-http")
def streamable_http(
    host: str = typer.Option(None, "--host", "-h", help="Server host"),
    port: int = typer.Option(None, "--port", "-p", help="Server port"),
) -> None:
    """Start server in streamable HTTP mode (recommended for remote).

    This is the recommended mode for remote deployments, Docker containers,
    and any scenario where the client connects over HTTP.

    Cache backend defaults to Redis for distributed deployments.
    """
    from .server import mcp

    server_host = host or _get_host()
    server_port = port or _get_port()

    _print_startup_info("streamable-http")
    _maybe_start_warmup()
    typer.echo(f"Server: http://{server_host}:{server_port}/mcp")

    try:
        mcp.run(transport="streamable-http", host=server_host, port=server_port)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        typer.echo(f"\nError: {error}", err=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        _handle_shutdown()


@app.command()
def warmup(
    max_laws: int = typer.Option(
        None,
        "--max-laws",
        "-n",
        help="Maximum laws to ingest (default: all ~6400). Use 10-50 for testing.",
    ),
    html_root: str = typer.Option(
        None,
        "--html-root",
        "-r",
        help="Path to pre-downloaded HTML corpus (default: from config or data/html).",
    ),
    batch_size: int = typer.Option(
        256,
        "--batch-size",
        "-b",
        help="Documents per embedding batch.",
    ),
    max_workers: int = typer.Option(
        8,
        "--max-workers",
        "-w",
        help="Concurrent HTML parsing workers.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force re-ingestion even if corpus is already populated.",
    ),
    status_only: bool = typer.Option(
        False,
        "--status",
        "-s",
        help="Only check corpus status, don't ingest.",
    ),
) -> None:
    """Ingest pre-downloaded HTML corpus into ChromaDB.

    Reads the HTML files from data/html/ (or --html-root) and embeds them
    into ChromaDB using the configured embedding backend (TEI or local).

    This is a synchronous (blocking) operation. Use WARMUP_ON_STARTUP=true
    for automatic background warm-up during server startup.

    Examples:
        legal-mcp warmup --status          # Check corpus status
        legal-mcp warmup --max-laws 10     # Quick test with 10 laws
        legal-mcp warmup                   # Full corpus ingestion
        legal-mcp warmup --force           # Re-ingest even if populated
    """
    import json

    if status_only:
        from .ingestion.local_pipeline import get_corpus_status

        corpus_status = get_corpus_status()
        typer.echo(json.dumps(corpus_status, indent=2))
        return

    from .warmup import run_warmup_sync

    typer.echo("Starting corpus warm-up (synchronous)...")
    if max_laws:
        typer.echo(f"  max_laws: {max_laws}")
    if html_root:
        typer.echo(f"  html_root: {html_root}")
    typer.echo(f"  batch_size: {batch_size}")
    typer.echo(f"  max_workers: {max_workers}")
    typer.echo(f"  force: {force}")
    typer.echo("")

    result = run_warmup_sync(
        html_root=html_root,
        max_laws=max_laws,
        batch_size=batch_size,
        max_workers=max_workers,
        force=force,
    )

    typer.echo("")
    typer.echo(json.dumps(result, indent=2))

    if result.get("state") == "completed":
        typer.secho(
            f"Done: {result['documents_added']} documents from "
            f"{result['laws_processed']} laws in "
            f"{result['elapsed_seconds']}s",
            fg=typer.colors.GREEN,
        )
    elif result.get("state") == "skipped":
        typer.secho(
            f"Skipped: {result.get('skip_reason', 'unknown reason')}",
            fg=typer.colors.YELLOW,
        )
    elif result.get("state") == "failed":
        typer.secho(
            f"Failed: {result.get('error_message', 'unknown error')}",
            fg=typer.colors.RED,
            err=True,
        )
        sys.exit(1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-v", help="Show version and exit"
    ),
) -> None:
    """Legal-MCP.

    A comprehensive legal research MCP server built with FastMCP and mcp-refcache, providing AI assistants with structured access to legal information across multiple jurisdictions.
    """
    if version:
        from . import __version__

        typer.echo(f"legal-mcp {__version__}")
        raise typer.Exit()

    # If no command provided, show help
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":
    app()
