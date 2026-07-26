from typing import Callable

import config
import gui
import wx


def _apply_theme_noop(theme_id):
	pass


def _apply_reverb_noop(preset):
	pass


# Assigned by GlobalPlugin in issue #38 to wire live preview into the real
# Sound Player. Until #38 lands these are no-ops so the panel is safely
# importable and testable standalone.
apply_theme: Callable[[str], None] = _apply_theme_noop
apply_reverb: Callable[[str], None] = _apply_reverb_noop


# TEMPORARY CONFIG BRIDGE: GlobalPlugin is the designed home for CONF_SPEC
# registration in issue #38. Delete this entire bridge once #38 lands.
try:
	from . import migration

	unspoken_spec = config.conf.spec.get("unspoken", {})
	if "unspoken" not in config.conf.spec or "theme" not in unspoken_spec:
		config.conf.spec["unspoken"] = migration.CONF_SPEC
except Exception:
	# A partial config stub or an older NVDA config object must not break import.
	pass


ROLE_ANNOUNCEMENT_CHOICES = (
	# Translators: A role announcement mode that replaces spoken roles with sounds.
	("sounds", "Sounds"),
	# Translators: A role announcement mode that plays sounds and also speaks roles.
	("soundsAndSpeech", "Sounds and speech"),
	# Translators: A role announcement mode that speaks roles without playing sounds.
	("speechOnly", "Speech only"),
)

REVERB_CHOICES = (
	# Translators: Disables room reverb for role sounds.
	("none", "None"),
	# Translators: Applies a small-room reverb preset to role sounds.
	("smallRoom", "Small room"),
	# Translators: Applies a medium-room reverb preset to role sounds.
	("mediumRoom", "Medium room"),
	# Translators: Applies a hall reverb preset to role sounds.
	("hall", "Hall"),
)

_CONFIG_KEYS = (
	"theme",
	"roleAnnouncement",
	"reverb",
	"silenceDuringSayAll",
)


def build_theme_choices(discovered_themes):
	if not discovered_themes:
		# Translators: The built-in fallback sound theme.
		return (["Default"], ["default"])
	return (
		[theme.name for theme in discovered_themes],
		[theme.id for theme in discovered_themes],
	)


def theme_index_for(theme_ids, selected_id):
	try:
		return theme_ids.index(selected_id)
	except ValueError:
		return 0


def role_announcement_index_for(selected_value):
	values = [value for value, label in ROLE_ANNOUNCEMENT_CHOICES]
	try:
		return values.index(selected_value)
	except ValueError:
		return 0


def role_announcement_value_for_index(index):
	try:
		return ROLE_ANNOUNCEMENT_CHOICES[index][0]
	except IndexError:
		return "sounds"


def reverb_index_for(selected_value):
	values = [value for value, label in REVERB_CHOICES]
	try:
		return values.index(selected_value)
	except ValueError:
		return 1


def reverb_value_for_index(index):
	try:
		return REVERB_CHOICES[index][0]
	except IndexError:
		return "smallRoom"


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	# Translators: Title of the Unspoken add-on settings panel.
	title = "Unspoken"

	def makeSettings(self, settingsSizer):
		from . import themes

		section = config.conf["unspoken"]
		self.unspoken_copy = {
			key: section[key]
			for key in _CONFIG_KEYS
		}
		self._originalThemeId = section["theme"]
		self._originalReverbPreset = section["reverb"]

		theme_labels, self._themeIds = build_theme_choices(themes.discover())
		role_labels = [label for value, label in ROLE_ANNOUNCEMENT_CHOICES]
		reverb_labels = [label for value, label in REVERB_CHOICES]

		settingsSizer = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: Label for choosing the active sound theme.
		self.themeChoice = settingsSizer.addLabeledControl(
			"Sound theme",
			wx.Choice,
			choices=theme_labels,
		)
		self.themeChoice.SetSelection(
			theme_index_for(self._themeIds, self._originalThemeId)
		)
		self.themeChoice.Bind(wx.EVT_CHOICE, self.onThemeChanged)

		# Translators: Label for choosing how control roles are announced.
		self.roleAnnouncementChoice = settingsSizer.addLabeledControl(
			"Role announcement",
			wx.Choice,
			choices=role_labels,
		)
		self.roleAnnouncementChoice.SetSelection(
			role_announcement_index_for(section["roleAnnouncement"])
		)

		# Translators: Label for choosing a room reverb preset for role sounds.
		self.reverbChoice = settingsSizer.addLabeledControl(
			"Reverb",
			wx.Choice,
			choices=reverb_labels,
		)
		self.reverbChoice.SetSelection(
			reverb_index_for(self._originalReverbPreset)
		)
		self.reverbChoice.Bind(wx.EVT_CHOICE, self.onReverbChanged)

		# This controls role sounds only; spoken-role suppression is controlled
		# entirely by the Role announcement choice.
		# Translators: Silences role sounds while NVDA's say-all reading is active.
		self.silenceDuringSayAllCheckBox = settingsSizer.addItem(
			wx.CheckBox(
				self,
				label="Silence role sounds during say all",
			)
		)
		self.silenceDuringSayAllCheckBox.SetValue(
			section["silenceDuringSayAll"]
		)

	def onThemeChanged(self, event):
		apply_theme(self._themeIds[self.themeChoice.GetSelection()])

	def onReverbChanged(self, event):
		apply_reverb(
			reverb_value_for_index(self.reverbChoice.GetSelection())
		)

	def postInit(self):
		self.themeChoice.SetFocus()

	def onSave(self):
		section = config.conf["unspoken"]
		section["theme"] = self._themeIds[self.themeChoice.GetSelection()]
		section["roleAnnouncement"] = role_announcement_value_for_index(
			self.roleAnnouncementChoice.GetSelection()
		)
		section["reverb"] = reverb_value_for_index(
			self.reverbChoice.GetSelection()
		)
		section["silenceDuringSayAll"] = (
			self.silenceDuringSayAllCheckBox.IsChecked()
		)

	def onDiscard(self):
		section = config.conf["unspoken"]
		for key, value in self.unspoken_copy.items():
			section[key] = value
		apply_theme(self._originalThemeId)
		apply_reverb(self._originalReverbPreset)
