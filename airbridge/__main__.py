"""AirBridge entry point — launch the server from command line."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from airbridge import __version__
from airbridge.config import load_config
from airbridge.server import run_server


def main() -> None:
    """Parse arguments and start AirBridge server."""
    parser = argparse.ArgumentParser(
        prog="airbridge",
        description="AirBridge — wireless file transfer between PC and mobile devices",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"AirBridge {__version__}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Server port (default: 8090)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--downloads-dir",
        type=str,
        default=None,
        help="Directory to save received files",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    # Load base config from environment, override with CLI args
    config = load_config()

    # Apply CLI overrides via environment (config is frozen dataclass)
    import os
    from pathlib import Path

    from airbridge.config import Config

    overrides: dict[str, object] = {}
    if args.port is not None:
        overrides["port"] = args.port
    if args.host is not None:
        overrides["host"] = args.host
    if args.downloads_dir is not None:
        overrides["downloads_dir"] = Path(args.downloads_dir)
    if args.log_level is not None:
        overrides["log_level"] = args.log_level

    if overrides:
        # Reconstruct config with overrides
        from dataclasses import asdict

        current = asdict(config)
        current.update(overrides)
        config = Config(**current)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Run the server
    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        print("\nAirBridge stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
