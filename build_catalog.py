#!/usr/bin/env python
"""Build the shape catalogue. Run `python build_catalog.py --help` for the options."""
import sys

from drawio_agent.catalog.cli import main

if __name__ == "__main__":
    sys.exit(main())
