"""The Unspoken-ng settings panel: four controls, sound theme first (spec §8)."""

from typing import Callable

import addonHandler
import config
import gui
import wx


addonHandler.initTranslation()


def _apply_theme_noop(theme_id):
	pass


def _apply_reverb_noop(preset):
	pass


# The live-preview seam. GlobalPlugin replaces both hooks in issue #38 so that
# choosing a sound theme or a reverb preset is heard while the panel is open;
# until then they are no-ops, which keeps this module importable and testable
# off NVDA.
#
# Contract for #38: both hooks are called on NVDA's main thread, once per
# selection change — that is, once per arrow keypress while the user moves
# through a combo box. Neither may block: decoding a sound theme belongs on the
# Sound Player's worker, not here.
apply_theme: Callable[[str], None] = _apply_theme_noop
apply_reverb: Callable[[str], None] = _apply_reverb_noop


# TODO(#38): temporary config bridge. GlobalPlugin is the designed home for
# CONF_SPEC registration; delete this whole block once #38 lands. The guard
# keeps the bridge from fighting a spec #38 has already registered.
try:
	from . import migration

	if "theme" not in config.conf.spec.get("unspoken", {}):
		config.conf.spec["unspoken"] = migration.CONF_SPEC
except Exception:
	# A partial config stub or an older NVDA config object must not break import.
	pass


ROLE_ANNOUNCEMENT_CHOICES = (
	# Translators: A role announcement mode: roles are announced by sounds instead of speech.
	("sounds", _("Sounds")),
	# Translators: A role announcement mode: roles are announced by sounds and spoken as well.
	("soundsAndSpeech", _("Sounds and speech")),
	# Translators: A role announcement mode: roles are spoken and no sounds play.
	("speechOnly", _("Speech only")),
)

REVERB_CHOICES = (
	# Translators: A reverb preset: role sounds play with no reverb.
	("none", _("None")),
	# Translators: A reverb preset: role sounds play as if in a small room.
	("smallRoom", _("Small room")),
	# Translators: A reverb preset: role sounds play as if in a medium-sized room.
	("mediumRoom", _("Medium room")),
	# Translators: A reverb preset: role sounds play as if in a hall.
	("hall", _("Hall")),
)

_CONFIG_KEYS = (
	"theme",
	"roleAnnouncement",
	"reverb",
	"silenceDuringSayAll",
)

_DEFAULT_THEME_ID = "default"


def build_theme_choices(discovered_themes):
	"""Return the (labels, IDs) pair for the sound theme combo box.

	Discovery only comes up empty if the bundled default theme is unusable, and
	an empty combo box is a dead end for a keyboard user, so the panel still
	offers the bundled default in that case.
	"""

	if not discovered_themes:
		# Translators: The name of the bundled default sound theme.
		return ([_("Default")], [_DEFAULT_THEME_ID])
	return (
		[theme.name for theme in discovered_themes],
		[theme.id for theme in discovered_themes],
	)


def theme_index_for(theme_ids, selected_id):
	try:
		return theme_ids.index(selected_id)
	except ValueError:
		return 0


def theme_id_for_index(theme_ids, index):
	"""Return the sound theme ID shown at ``index``, or the bundled default.

	wx.Choice reports wx.NOT_FOUND (-1) when nothing is selected, which would
	otherwise select the last theme in the list by negative indexing.
	"""

	if 0 <= index < len(theme_ids):
		return theme_ids[index]
	return _DEFAULT_THEME_ID


def role_announcement_index_for(selected_value):
	values = [value for value, label in ROLE_ANNOUNCEMENT_CHOICES]
	try:
		return values.index(selected_value)
	except ValueError:
		return 0


def role_announcement_value_for_index(index):
	if 0 <= index < len(ROLE_ANNOUNCEMENT_CHOICES):
		return ROLE_ANNOUNCEMENT_CHOICES[index][0]
	return "sounds"


def reverb_index_for(selected_value):
	values = [value for value, label in REVERB_CHOICES]
	try:
		return values.index(selected_value)
	except ValueError:
		return 1


def reverb_value_for_index(index):
	if 0 <= index < len(REVERB_CHOICES):
		return REVERB_CHOICES[index][0]
	return "smallRoom"


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	# Translators: The title of this add-on's category in NVDA's settings dialog.
	title = _("Unspoken-ng")

	def makeSettings(self, settingsSizer):
		from . import themes

		self._priorSettings = self._readSettings()

		theme_labels, self._themeIds = build_theme_choices(themes.discover())
		role_labels = [label for value, label in ROLE_ANNOUNCEMENT_CHOICES]
		reverb_labels = [label for value, label in REVERB_CHOICES]

		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: The label of a combo box to choose the active sound theme.
		self.themeChoice = sHelper.addLabeledControl(
			_("Sound &theme:"),
			wx.Choice,
			choices=theme_labels,
		)
		self.themeChoice.SetSelection(
			theme_index_for(self._themeIds, self._priorSettings.get("theme"))
		)
		self.themeChoice.Bind(wx.EVT_CHOICE, self.onThemeChanged)

		# Translators: The label of a combo box to choose how control roles are announced.
		self.roleAnnouncementChoice = sHelper.addLabeledControl(
			_("&Role announcement:"),
			wx.Choice,
			choices=role_labels,
		)
		self.roleAnnouncementChoice.SetSelection(
			role_announcement_index_for(self._priorSettings.get("roleAnnouncement"))
		)

		# Translators: The label of a combo box to choose the reverb preset for role sounds.
		self.reverbChoice = sHelper.addLabeledControl(
			_("Re&verb:"),
			wx.Choice,
			choices=reverb_labels,
		)
		self.reverbChoice.SetSelection(
			reverb_index_for(self._priorSettings.get("reverb"))
		)
		self.reverbChoice.Bind(wx.EVT_CHOICE, self.onReverbChanged)

		# This silences role sounds only; whether roles are spoken during say all
		# stays governed by Role announcement (spec §8, deliberate per #10/#15).
		self.silenceDuringSayAllCheckBox = sHelper.addItem(
			wx.CheckBox(
				self,
				# Translators: The label of a checkbox to stop role sounds playing during say all.
				label=_("&Silence role sounds during say all"),
			)
		)
		self.silenceDuringSayAllCheckBox.SetValue(
			bool(self._priorSettings.get("silenceDuringSayAll"))
		)

		# What Cancel reverts to: the theme and reverb preset the panel opened
		# showing, which is what the user is hearing before touching anything.
		self._priorThemeId = self._selectedThemeId()
		self._priorReverbPreset = self._selectedReverbPreset()

	def onThemeChanged(self, event):
		apply_theme(self._selectedThemeId())

	def onReverbChanged(self, event):
		apply_reverb(self._selectedReverbPreset())

	def onSave(self):
		section = config.conf["unspoken"]
		section["theme"] = self._selectedThemeId()
		section["roleAnnouncement"] = role_announcement_value_for_index(
			self.roleAnnouncementChoice.GetSelection()
		)
		section["reverb"] = self._selectedReverbPreset()
		section["silenceDuringSayAll"] = self.silenceDuringSayAllCheckBox.IsChecked()
		# The theme and reverb preset are already live; saving must not re-apply
		# them. Saving is also what Apply does, and the dialog stays open
		# afterwards, so a later Cancel reverts to what was applied rather than
		# to what the panel opened with — as NVDA's own magnifier panel does.
		self._priorSettings = self._readSettings()
		self._priorThemeId = self._selectedThemeId()
		self._priorReverbPreset = self._selectedReverbPreset()

	def onDiscard(self):
		# wx can close an open combo box while the dialog is cancelling, firing a
		# late EVT_CHOICE that would re-apply the very selection being reverted.
		# NVDA's own driver settings panel unbinds for this reason.
		self.themeChoice.Unbind(wx.EVT_CHOICE)
		self.reverbChoice.Unbind(wx.EVT_CHOICE)

		section = config.conf["unspoken"]
		for key, value in self._priorSettings.items():
			section[key] = value

		# Revert the live preview, but only where it actually moved: reloading a
		# sound theme is expensive, and every Cancel of NVDA's settings dialog
		# reaches this panel once it has been visited.
		if self._selectedThemeId() != self._priorThemeId:
			apply_theme(self._priorThemeId)
		if self._selectedReverbPreset() != self._priorReverbPreset:
			apply_reverb(self._priorReverbPreset)

	def _readSettings(self):
		"""Snapshot the four settings, tolerating a config spec not yet registered.

		Keys that are missing are simply absent from the snapshot; the choice
		helpers then fall back to the spec §8 defaults, so a panel that opens
		before #38 registers CONF_SPEC still builds.
		"""

		snapshot = {}
		try:
			section = config.conf["unspoken"]
		except KeyError:
			return snapshot
		for key in _CONFIG_KEYS:
			try:
				snapshot[key] = section[key]
			except KeyError:
				pass
		return snapshot

	def _selectedThemeId(self):
		return theme_id_for_index(self._themeIds, self.themeChoice.GetSelection())

	def _selectedReverbPreset(self):
		return reverb_value_for_index(self.reverbChoice.GetSelection())
