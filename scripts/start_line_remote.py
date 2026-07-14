"""Start the private LINE controller and its ngrok tunnel at Windows logon."""

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from trading_bot.control import remote_start  # noqa: E402


raise SystemExit(remote_start())
