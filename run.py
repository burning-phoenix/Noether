#!/usr/bin/env python3
"""
Noether — Multi-Agent Code Editor

Convenience wrapper. For installed usage, run: noether
"""

import sys
from pathlib import Path

# Allow running directly without pip install
sys.path.insert(0, str(Path(__file__).parent))

from src.cli import main

if __name__ == "__main__":
    main()
