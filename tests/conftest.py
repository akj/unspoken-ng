"""Off-NVDA test harness for the Sound Player (spec §10).

`player.py` lives inside the addon package, whose `__init__.py` imports NVDA
modules that do not exist outside NVDA's process. The player deliberately
imports nothing from its own package, so it loads straight from the file and
runs in plain Python -- which is what the map's spike rigs proved the DLL needs
(no NVDA, no wave player, no COM).

The module is registered as `unspoken_player` so tests can `import
unspoken_player as player` without going through the addon package.
"""

import importlib.util
import sys
from pathlib import Path

PLAYER_PATH = (
    Path(__file__).resolve().parents[1]
    / "addon"
    / "globalPlugins"
    / "Unspoken"
    / "player.py"
)


def load_player_module():
    """Import `player.py` by path, bypassing the addon package."""
    spec = importlib.util.spec_from_file_location("unspoken_player", PLAYER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["unspoken_player"] = module
    spec.loader.exec_module(module)
    return module


load_player_module()
