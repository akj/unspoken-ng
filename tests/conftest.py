import sys
from pathlib import Path
from types import ModuleType


ROLE_NAMES = (
    "ROLE_CHECKBOX",
    "ROLE_RADIOBUTTON",
    "ROLE_STATICTEXT",
    "ROLE_EDITABLETEXT",
    "ROLE_BUTTON",
    "ROLE_MENUBAR",
    "ROLE_MENUITEM",
    "ROLE_MENU",
    "ROLE_COMBOBOX",
    "ROLE_LISTITEM",
    "ROLE_GRAPHIC",
    "ROLE_LINK",
    "ROLE_TREEVIEWITEM",
    "ROLE_TAB",
    "ROLE_TABCONTROL",
    "ROLE_SLIDER",
    "ROLE_DROPDOWNBUTTON",
    "ROLE_CLOCK",
    "ROLE_ANIMATION",
    "ROLE_ICON",
    "ROLE_IMAGEMAP",
    "ROLE_RADIOMENUITEM",
    "ROLE_RICHEDIT",
    "ROLE_SHAPE",
    "ROLE_TEAROFFMENU",
    "ROLE_TOGGLEBUTTON",
    "ROLE_CHART",
    "ROLE_DIAGRAM",
    "ROLE_DIAL",
    "ROLE_DROPLIST",
    "ROLE_MENUBUTTON",
    "ROLE_DROPDOWNBUTTONGRID",
    "ROLE_HOTKEYFIELD",
    "ROLE_INDICATOR",
    "ROLE_SPINBUTTON",
    "ROLE_TREEVIEWBUTTON",
    "ROLE_DESKTOPICON",
    "ROLE_PASSWORDEDIT",
    "ROLE_CHECKMENUITEM",
    "ROLE_SPLITBUTTON",
)

control_types_stub = ModuleType("controlTypes")
for value, role_name in enumerate(ROLE_NAMES):
    setattr(control_types_stub, role_name, value)
control_types_stub.ROLE_UNKNOWN_TEST_ROLE = len(ROLE_NAMES)
sys.modules["controlTypes"] = control_types_stub

UNSPOKEN_DIR = (
    Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "Unspoken"
)
sys.path.insert(0, str(UNSPOKEN_DIR))
