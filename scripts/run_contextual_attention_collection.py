#!/usr/bin/env python3
"""LaunchAgent entry point: execute exactly one bounded collection session."""
from pathlib import Path
import json
import sys

PROJECT = Path("/Users/oksanachovgan/PumpAgent")
sys.path.insert(0, str(PROJECT / "src"))

from pumpagent.live_data.semi_autonomous_collection import run_once

if __name__ == "__main__":
    print(json.dumps(run_once(PROJECT), sort_keys=True))
