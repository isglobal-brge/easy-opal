#!/usr/bin/env python3

import sys
from pathlib import Path

# Add src directory to path to allow for clean imports
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cli import main

if __name__ == "__main__":
    main() 