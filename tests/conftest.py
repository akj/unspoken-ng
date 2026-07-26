import sys
from pathlib import Path


THEMES_MODULE_DIR = (
    Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "Unspoken"
)
sys.path.insert(0, str(THEMES_MODULE_DIR))
