import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


UNSPOKEN_DIR = (
	Path(__file__).parents[1]
	/ "addon"
	/ "globalPlugins"
	/ "Unspoken"
)


class _Conf(dict):
	def __init__(self):
		super().__init__(
			unspoken={
				"theme": "default",
				"roleAnnouncement": "sounds",
				"reverb": "smallRoom",
				"silenceDuringSayAll": False,
			}
		)
		self.spec = {}


@pytest.fixture(scope="module")
def addon_gui():
	module_names = ("wx", "gui", "config")
	original_modules = {
		name: sys.modules.get(name)
		for name in module_names
	}
	package_name = "_test_unspoken_package"

	wx_stub = ModuleType("wx")
	wx_stub.Choice = object
	wx_stub.CheckBox = object
	wx_stub.EVT_CHOICE = object()

	gui_stub = ModuleType("gui")
	gui_stub.settingsDialogs = SimpleNamespace(SettingsPanel=object)
	gui_stub.guiHelper = SimpleNamespace(BoxSizerHelper=object)

	config_stub = ModuleType("config")
	config_stub.conf = _Conf()

	package = ModuleType(package_name)
	package.__path__ = [str(UNSPOKEN_DIR)]

	sys.modules["wx"] = wx_stub
	sys.modules["gui"] = gui_stub
	sys.modules["config"] = config_stub
	sys.modules[package_name] = package

	try:
		yield importlib.import_module(f"{package_name}.addonGui")
	finally:
		for name in tuple(sys.modules):
			if name == package_name or name.startswith(f"{package_name}."):
				sys.modules.pop(name, None)
		for name, original in original_modules.items():
			if original is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = original


def test_build_theme_choices_uses_names_and_ids(addon_gui):
	themes = [
		SimpleNamespace(id="default", name="Default theme"),
		SimpleNamespace(id="retro", name="Retro"),
	]

	labels, ids = addon_gui.build_theme_choices(themes)

	assert labels == ["Default theme", "Retro"]
	assert ids == ["default", "retro"]


def test_build_theme_choices_falls_back_when_discovery_is_empty(addon_gui):
	assert addon_gui.build_theme_choices([]) == (["Default"], ["default"])


@pytest.mark.parametrize(
	("selected_id", "expected"),
	[
		("default", 0),
		("retro", 1),
		("missing", 0),
	],
)
def test_theme_index_for(addon_gui, selected_id, expected):
	assert addon_gui.theme_index_for(["default", "retro"], selected_id) == expected


@pytest.mark.parametrize(
	("value", "expected_index"),
	[
		("sounds", 0),
		("soundsAndSpeech", 1),
		("speechOnly", 2),
		("unknown", 0),
	],
)
def test_role_announcement_index_for(addon_gui, value, expected_index):
	assert addon_gui.role_announcement_index_for(value) == expected_index


@pytest.mark.parametrize(
	("index", "expected_value"),
	[
		(0, "sounds"),
		(1, "soundsAndSpeech"),
		(2, "speechOnly"),
		(99, "sounds"),
	],
)
def test_role_announcement_value_for_index(addon_gui, index, expected_value):
	assert addon_gui.role_announcement_value_for_index(index) == expected_value


@pytest.mark.parametrize(
	("value", "expected_index"),
	[
		("none", 0),
		("smallRoom", 1),
		("mediumRoom", 2),
		("hall", 3),
		("unknown", 1),
	],
)
def test_reverb_index_for(addon_gui, value, expected_index):
	assert addon_gui.reverb_index_for(value) == expected_index


@pytest.mark.parametrize(
	("index", "expected_value"),
	[
		(0, "none"),
		(1, "smallRoom"),
		(2, "mediumRoom"),
		(3, "hall"),
		(99, "smallRoom"),
	],
)
def test_reverb_value_for_index(addon_gui, index, expected_value):
	assert addon_gui.reverb_value_for_index(index) == expected_value


# The wx panel itself, including makeSettings, onSave, and onDiscard, is not
# unit-testable outside NVDA and is covered by manual smoke testing instead.
