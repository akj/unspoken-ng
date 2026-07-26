import controlTypes

import roles


EXPECTED_ROLE_SLOTS = {
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

CANONICAL_SLOTS = {
    "button",
    "checkbox",
    "clock",
    "combobox",
    "editabletext",
    "icon",
    "link",
    "listitem",
    "menuitem",
    "radiobutton",
    "slider",
    "splitbutton",
    "tab",
    "treeviewitem",
}


def test_every_current_role_resolves_to_its_slot():
    for role, expected_slot in EXPECTED_ROLE_SLOTS.items():
        assert roles.slot_for(role) == expected_slot


def test_unmapped_role_returns_none():
    assert roles.slot_for(controlTypes.ROLE_UNKNOWN_TEST_ROLE) is None


def test_mapping_contains_exactly_the_canonical_slots():
    assert set(roles.ROLE_TO_SLOT.values()) == CANONICAL_SLOTS
