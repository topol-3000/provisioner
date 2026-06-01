"""Entry point for `python -m provisioning_worker`."""

import asyncio
import sys

from provisioning_worker.main import run
from provisioning_worker.settings import get_settings


def main() -> None:
    """Boot the worker. Exits non-zero on any unhandled exception."""
    try:
        asyncio.run(run(get_settings()))
    except* Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
