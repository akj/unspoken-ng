"""Role-to-slot mapping from spec section 4.1.

This is the only module that imports ``controlTypes``. The canonical slots are
button, checkbox, clock, combobox, editabletext, icon, link, listitem,
menuitem, radiobutton, slider, splitbutton, tab, and treeviewitem.
"""

from __future__ import annotations

import controlTypes


ROLE_TO_SLOT = {
    controlTypes.ROLE_CHECKBOX: "checkbox",
    controlTypes.ROLE_RADIOBUTTON: "radiobutton",
    controlTypes.ROLE_STATICTEXT: "editabletext",
    controlTypes.ROLE_EDITABLETEXT: "editabletext",
    controlTypes.ROLE_BUTTON: "button",
    controlTypes.ROLE_MENUBAR: "menuitem",
    controlTypes.ROLE_MENUITEM: "menuitem",
    controlTypes.ROLE_MENU: "menuitem",
    controlTypes.ROLE_COMBOBOX: "combobox",
    controlTypes.ROLE_LISTITEM: "listitem",
    controlTypes.ROLE_GRAPHIC: "icon",
    controlTypes.ROLE_LINK: "link",
    controlTypes.ROLE_TREEVIEWITEM: "treeviewitem",
    controlTypes.ROLE_TAB: "tab",
    controlTypes.ROLE_TABCONTROL: "tab",
    controlTypes.ROLE_SLIDER: "slider",
    controlTypes.ROLE_DROPDOWNBUTTON: "combobox",
    controlTypes.ROLE_CLOCK: "clock",
    controlTypes.ROLE_ANIMATION: "icon",
    controlTypes.ROLE_ICON: "icon",
    controlTypes.ROLE_IMAGEMAP: "icon",
    controlTypes.ROLE_RADIOMENUITEM: "radiobutton",
    controlTypes.ROLE_RICHEDIT: "editabletext",
    controlTypes.ROLE_SHAPE: "icon",
    controlTypes.ROLE_TEAROFFMENU: "menuitem",
    controlTypes.ROLE_TOGGLEBUTTON: "checkbox",
    controlTypes.ROLE_CHART: "icon",
    controlTypes.ROLE_DIAGRAM: "icon",
    controlTypes.ROLE_DIAL: "slider",
    controlTypes.ROLE_DROPLIST: "combobox",
    controlTypes.ROLE_MENUBUTTON: "button",
    controlTypes.ROLE_DROPDOWNBUTTONGRID: "button",
    controlTypes.ROLE_HOTKEYFIELD: "editabletext",
    controlTypes.ROLE_INDICATOR: "icon",
    controlTypes.ROLE_SPINBUTTON: "slider",
    controlTypes.ROLE_TREEVIEWBUTTON: "button",
    controlTypes.ROLE_DESKTOPICON: "icon",
    controlTypes.ROLE_PASSWORDEDIT: "editabletext",
    controlTypes.ROLE_CHECKMENUITEM: "checkbox",
    controlTypes.ROLE_SPLITBUTTON: "splitbutton",
}


def slot_for(role: object) -> str | None:
    """Return the canonical slot for a control role, if one is mapped."""
    return ROLE_TO_SLOT.get(role)
