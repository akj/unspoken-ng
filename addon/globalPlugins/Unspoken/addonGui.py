import wx
import config
import gui
from gui import settingsDialogs, guiHelper, NVDASettingsDialog


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = "Unspoken"

	def _add_labeled_slider(self, helper, label, conf_key):
		"""Add a labeled slider bound to onReverbSettingChanged; return the slider."""
		helper.addItem(wx.StaticText(self, label=label))
		slider = helper.addItem(
			wx.Slider(self, value=config.conf["unspoken"][conf_key], minValue=0, maxValue=100)
		)
		slider.Bind(wx.EVT_SLIDER, self.onReverbSettingChanged)
		return slider

	def _add_reverb_sliders(self, helper):
		"""Add EFX reverb parameter sliders to the settings panel."""
		self.RoomSizeSlider = self._add_labeled_slider(helper, "Room Size (0-100)", "RoomSize")
		self.DampingSlider = self._add_labeled_slider(helper, "Damping (0-100)", "Damping")
		self.WetLevelSlider = self._add_labeled_slider(helper, "Wet Level (0-100)", "WetLevel")
		self.DryLevelSlider = self._add_labeled_slider(helper, "Dry Level (0-100)", "DryLevel")
		self.WidthSlider = self._add_labeled_slider(helper, "Width (0-100)", "Width")

	def makeSettings(self, settingsSizer):
		settingsSizer = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.sayAllCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="&Play sounds during say all")
		)
		self.sayAllCheckBox.SetValue(
			(True if config.conf["unspoken"]["sayAll"] == False else False)
		)
		self.speakRolesCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="&Speak object roles")
		)
		self.speakRolesCheckBox.SetValue(config.conf["unspoken"]["speakRoles"])
		self.HRTFCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="Use &HRTF (3D Sound)")
		)
		self.HRTFCheckBox.SetValue(config.conf["unspoken"]["HRTF"])
		self.ReverbCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="Use &Reverb")
		)
		self.ReverbCheckBox.SetValue(config.conf["unspoken"]["Reverb"])
		self.ReverbCheckBox.Bind(wx.EVT_CHECKBOX, self.onReverbSettingChanged)

		self._add_reverb_sliders(settingsSizer)

		self.noSoundsCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="&play sounds for roles (Enable Add-On)")
		)
		self.noSoundsCheckBox.SetValue(
			(True if config.conf["unspoken"]["noSounds"] == False else False)
		)
		self.volumeCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="Automatically adjust sounds with speech &volume")
		)
		self.volumeCheckBox.SetValue(config.conf["unspoken"]["volumeAdjust"])
		self.unspoken_copy = config.conf["unspoken"].copy()

	def onReverbSettingChanged(self, event):
		"""Push slider values to the live OpenALLoopback instance.
		enable_reverb() is called before set_reverb_settings() so the EFX tail
		frame count is only computed when reverb is active."""
		try:
			# Import here to avoid circular imports
			from . import openal_audio

			openal_audio_instance = openal_audio.get_openal_audio()
			if openal_audio_instance and openal_audio_instance.initialized:
				config.conf["unspoken"]["Reverb"] = self.ReverbCheckBox.IsChecked()
				openal_audio_instance.enable_reverb(self.ReverbCheckBox.IsChecked())
				openal_audio_instance.set_reverb_settings(
					room_size=self.RoomSizeSlider.GetValue() / 100.0,
					damping=self.DampingSlider.GetValue() / 100.0,
					wet_level=self.WetLevelSlider.GetValue() / 100.0,
					dry_level=self.DryLevelSlider.GetValue() / 100.0,
					width=self.WidthSlider.GetValue() / 100.0,
				)
		except ImportError:
			pass

	def postInit(self):
		self.sayAllCheckBox.SetFocus()

	def onSave(self):
		if (
			not self.noSoundsCheckBox.IsChecked()
			and not self.speakRolesCheckBox.IsChecked()
		):
			gui.messageBox(
				"Disabling both sounds and  speaking is not allowed. NVDA will not say roles like button and checkbox, and sounds won't play either. Please change one of these settings",
				"Error",
			)
			return
		config.conf["unspoken"]["sayAll"] = not self.sayAllCheckBox.IsChecked()
		config.conf["unspoken"]["speakRoles"] = self.speakRolesCheckBox.IsChecked()

		config.conf["unspoken"]["HRTF"] = self.HRTFCheckBox.IsChecked()
		config.conf["unspoken"]["Reverb"] = self.ReverbCheckBox.IsChecked()

		# Save EFX reverb settings
		config.conf["unspoken"]["RoomSize"] = self.RoomSizeSlider.GetValue()
		config.conf["unspoken"]["Damping"] = self.DampingSlider.GetValue()
		config.conf["unspoken"]["WetLevel"] = self.WetLevelSlider.GetValue()
		config.conf["unspoken"]["DryLevel"] = self.DryLevelSlider.GetValue()
		config.conf["unspoken"]["Width"] = self.WidthSlider.GetValue()
		config.conf["unspoken"]["noSounds"] = not self.noSoundsCheckBox.IsChecked()
		config.conf["unspoken"]["volumeAdjust"] = self.volumeCheckBox.IsChecked()

	def update_reverb_from_config(self):
		# Update OpenAL EFX reverb settings
		try:
			from . import openal_audio

			openal_audio_instance = openal_audio.get_openal_audio()
			if openal_audio_instance and openal_audio_instance.initialized:
				openal_audio_instance.enable_reverb(config.conf["unspoken"]["Reverb"])
				openal_audio_instance.set_reverb_settings(
					room_size=config.conf["unspoken"]["RoomSize"] / 100.0,
					damping=config.conf["unspoken"]["Damping"] / 100.0,
					wet_level=config.conf["unspoken"]["WetLevel"] / 100.0,
					dry_level=config.conf["unspoken"]["DryLevel"] / 100.0,
					width=config.conf["unspoken"]["Width"] / 100.0,
				)
		except ImportError:
			pass

	def onDiscard(self):
		for k, v in self.unspoken_copy.items():
			config.conf["unspoken"][k] = v
		self.update_reverb_from_config()
