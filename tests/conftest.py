import enum
import sys
from pathlib import Path
from types import ModuleType


ROLE_NAMES = (
    "CHECKBOX",
    "RADIOBUTTON",
    "STATICTEXT",
    "EDITABLETEXT",
    "BUTTON",
    "MENUBAR",
    "MENUITEM",
    "MENU",
    "COMBOBOX",
    "LISTITEM",
    "GRAPHIC",
    "LINK",
    "TREEVIEWITEM",
    "TAB",
    "TABCONTROL",
    "SLIDER",
    "DROPDOWNBUTTON",
    "CLOCK",
    "ANIMATION",
    "ICON",
    "IMAGEMAP",
    "RADIOMENUITEM",
    "RICHEDIT",
    "SHAPE",
    "TEAROFFMENU",
    "TOGGLEBUTTON",
    "CHART",
    "DIAGRAM",
    "DIAL",
    "DROPLIST",
    "MENUBUTTON",
    "DROPDOWNBUTTONGRID",
    "HOTKEYFIELD",
    "INDICATOR",
    "SPINBUTTON",
    "TREEVIEWBUTTON",
    "DESKTOPICON",
    "PASSWORDEDIT",
    "CHECKMENUITEM",
    "SPLITBUTTON",
    "UNKNOWN_TEST_ROLE",
)

# The review flagged that NVDA deprecated the module-level ROLE_* aliases
# in favor of controlTypes.Role, an IntEnum. Stubbing with plain ints could
# silently hide an alias collision, so this stub mirrors the real shape: a
# genuine IntEnum with one distinct member per role, matching production
# controlTypes.Role in kind (a real enum), not merely in appearance.
Role = enum.IntEnum("Role", ROLE_NAMES)

control_types_stub = ModuleType("controlTypes")
control_types_stub.Role = Role
sys.modules["controlTypes"] = control_types_stub

UNSPOKEN_DIR = (
    Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "Unspoken"
)
sys.path.insert(0, str(UNSPOKEN_DIR))
