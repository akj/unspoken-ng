# Unspoken-ng: spatially positioned role sounds instead of spoken control roles.
# By Bryan Smart (bryansmart@bryansmart.com) and Austin Hicks (camlorn38@gmail.com)

"""`GlobalPlugin`: wiring and entry points (spec sections 4.5 and 5).

This module is the only place that knows both NVDA and the addon. It owns four
things and nothing else:

- **Wiring.** The config spec and its one-shot migration, the user sound-theme
  directory, the settings provider, the Sound Player, and -- because it is the
  thing that attempted the player and caught the failure -- degraded mode.
- **Entry points.** Three object events, one speech-pipeline hook that plays,
  and one that only suppresses.
- **The main-thread property reads.** `obj.role` and `obj.location`, once each,
  from the object the event handed us.
- **Lifetime.** Everything patched here is unpatched in `terminate`.

Everything it *decides* lives in `wiring.py`, which imports no NVDA and is
table-tested off NVDA. Everything below the seam lives in `player.py`, which is
the only module that knows OpenAL exists.

The latency rules this module has to keep (spec section 2), because they are
invisible in the code that keeps them:

- One read of `obj.role` and one of `obj.location`, from the handed object.
  Never `treeInterceptor.currentNVDAObject`: #30 measured it a median 58.9
  degrees *wrong* on the Tab path, at a ~15 ms floor. Never a second read of a
  property already read in this dispatch (#28's 88 ms bug).
- No timers, no polls, no background extraction thread on the sound path. Every
  sound is traceable to a synchronous call from NVDA (#31). The 100 ms
  navigation timer is gone; `event_becomeNavigatorObject` covers what it
  covered, and its `isFocus` flag replaces the timer's timing guess.
- No desktop-size cache: `getDesktopObject().location` costs 0.002 ms (#28).
- `player.play` is the only audio call on the path, and it returns in ~0.1 ms.
"""

import os

import addonHandler
import api
import config
import core
import globalPluginHandler
import globalVars
import gui
import speech
import textInfos
import ui
import wx
from logHandler import log

from . import migration, roles, spatial, themes, wiring
from .player import NoAudioEndpointError, OpenALSoundPlayer, SilentSoundPlayer


addonHandler.initTranslation()


#: The user sound-theme tree, under NVDA's user config (spec section 7). The
#: parent folder is the addon's identity, deliberately distinct from the
#: `config.conf["unspoken"]` section name the migration inherits.
USER_DATA_DIR_NAME = "unspoken-ng"
SOUND_THEMES_DIR_NAME = "sound-themes"

#: Spec section 8's defaults, repeated here only as the answer when a key
#: cannot be read at all. The registered spec is the real source.
CONFIG_DEFAULTS = {
    "theme": "default",
    "roleAnnouncement": "sounds",
    "reverb": "smallRoom",
    "silenceDuringSayAll": False,
}

#: How long a burst of live-preview keypresses is collapsed over before the
#: sound theme is decoded. Long enough that holding an arrow key through a
#: ten-theme list decodes once, short enough to still feel like a preview.
THEME_PREVIEW_DEBOUNCE_MS = 300

#: Spec section 9.4's one message, deferred past NVDA's own startup speech.
#: Nothing is raised from `__init__` and nothing is spoken from it either: at
#: plugin-construction time NVDA is not yet ready to speak.
DEGRADED_MESSAGE_DELAY_MS = 4000


def _noop(*args, **kwargs):
    """What `addonGui`'s live-preview hooks are restored to on terminate."""


def _conf(key):
    """Read one `unspoken` setting, falling back to its spec section 8 default.

    On the hot path this is two ConfigObj lookups against already-parsed
    sections; the `try` costs nothing when it does not fire. It exists because
    a config section can be absent in ways NVDA does not consider errors, and a
    role sound that does not play is worse than one that plays with a default.
    """
    try:
        return config.conf["unspoken"][key]
    except Exception:
        return CONFIG_DEFAULTS[key]


class _NVDASettingsProvider:
    """The settings the Sound Player is allowed to know about (spec section 4.4).

    The player reads both properties on NVDA's main thread inside `play`, once
    each, ahead of `alSourcePlay`. So both have to be cheap and neither may
    block: `output_device` is one ConfigObj lookup, and `volume` is two plus
    the arithmetic in `wiring.effective_volume`.

    `volume` additionally reads the synth's volume, but only when the user has
    turned on "sound volume follows voice" -- and then it is the same read
    NVDA's own `nvwave.WavePlayer._setVolumeFromConfig` makes, which NVDA calls
    on every `open()` and `stop()`, i.e. at least as often as we play. It is an
    in-process attribute read on the thread the synth already lives on: no I/O,
    no lock, nothing that can block. Caching it instead would go stale exactly
    when the setting exists to not be stale -- when the user changes voice
    volume from the settings ring.
    """

    __slots__ = ()

    @property
    def output_device(self):
        return config.conf["audio"]["outputDevice"]

    @property
    def volume(self):
        audio = config.conf["audio"]
        follows_voice = audio["soundVolumeFollowsVoice"]
        return wiring.effective_volume(
            audio["soundVolume"],
            follows_voice,
            _synth_volume() if follows_voice else None,
        )


def _synth_volume():
    """The current synth's volume percentage, or None if there is none to follow.

    None covers both "no synth yet" and "this synth has no volume setting",
    which are the two cases NVDA itself falls back to `soundVolume` for.
    """
    try:
        from synthDriverHandler import getSynth

        synth = getSynth()
        if synth is not None and synth.isSupported("volume"):
            return synth.volume
    except Exception:
        log.debugWarning("Unspoken: could not read the synth volume", exc_info=True)
    return None


def _user_themes_dir():
    """`<NVDA user config>/unspoken-ng/sound-themes`, or None if unavailable."""
    try:
        return os.path.join(
            globalVars.appArgs.configPath, USER_DATA_DIR_NAME, SOUND_THEMES_DIR_NAME
        )
    except Exception:
        log.warning(
            "Unspoken: could not locate the user config path; "
            "user sound themes are unavailable this session",
            exc_info=True,
        )
        return None


def _migrate_legacy_config():
    """Run spec section 8's one-shot migration where it can actually run.

    Two facts decide the shape of this function, both from PR #43:

    1. `config.conf["unspoken"]` is an `AggregatedSection`, which has no
       `__delitem__`. Migration deletes the legacy keys -- that is what makes
       it one-shot -- so run against the raw ConfigObj profile sections
       underneath, which do support deletion, and which are also where the
       legacy values still sit as strings once the new spec is registered.
    2. Writing through a raw profile section bypasses NVDA's own dirty
       marking, so nothing would ever be saved. The migration must persist or
       it re-runs every session and overwrites whatever the user has since
       chosen in the panel.

    Only the *active* profiles are visible here; legacy keys in a profile that
    is not active at startup are migrated the first time it is.
    """
    try:
        profiles = list(config.conf.profiles)
    except Exception:
        log.error("Unspoken: could not reach the config profiles to migrate", exc_info=True)
        return

    migrated = False
    for profile in profiles:
        try:
            section = profile.get("unspoken")
            if not section:
                continue
            before = set(section)
            migration.migrate(section)
            if set(section) != before:
                migrated = True
        except Exception:
            log.error("Unspoken: could not migrate a config profile", exc_info=True)

    if not migrated:
        return
    try:
        mark_dirty = getattr(config.conf, "_markWriteProfileDirty", None)
        if mark_dirty is not None:
            mark_dirty()
        config.conf.save()
        log.info("Unspoken: migrated legacy settings onto the four-key config")
    except Exception:
        log.error("Unspoken: could not save the migrated configuration", exc_info=True)


def _log_ancestor_coinstall():
    """Spec section 9.5: one warning line if Unspoken 1.x is installed too.

    Nothing else -- no dialog, no announcement. The 2026.1 / 64-bit floor
    leaves the ancestor disabled-incompatible anyway; this line exists so that
    when someone reports their old Unspoken settings vanished, the log says
    why: our migration deletes the `config.conf["unspoken"]` keys the ancestor
    also uses.
    """
    try:
        for addon in addonHandler.getAvailableAddons():
            if addon.name.lower() == "unspoken":
                log.warning(
                    f"Unspoken-ng: the ancestor add-on Unspoken {addon.version} is "
                    f"installed alongside this one (running={not addon.isDisabled}). "
                    f"Both patch NVDA's speech path, and Unspoken-ng's config migration "
                    f"deletes the config.conf['unspoken'] keys the ancestor also uses."
                )
                return
    except Exception:
        log.debugWarning("Unspoken: could not check for a co-installed ancestor", exc_info=True)


# --------------------------------------------------------------------------
# Position sources
# --------------------------------------------------------------------------


def _rect(location):
    """NVDA's `RectLTWH` (or None) as the plain 4-tuple `spatial` wants."""
    if not location:
        return None
    return (location[0], location[1], location[2], location[3])


def _desktop_rect():
    """The screen bounds, read fresh. 0.002 ms (#28) -- the 5 s cache is gone."""
    try:
        return _rect(api.getDesktopObject().location) or (0, 0, 0, 0)
    except Exception:
        log.debugWarning("Unspoken: could not read the desktop bounds", exc_info=True)
        return (0, 0, 0, 0)


def _focus_rect():
    """Reading-path tier 3: where the focus is."""
    try:
        focus = api.getFocusObject()
        return _rect(focus.location) if focus is not None else None
    except Exception:
        return None


def _identifier_rect(source, doc_handle, control_id):
    """Reading-path tier 1: materialise the field's own object and read its rect.

    Virtual buffers carry `controlIdentifier_*` on the control field, which is
    the only source that gives the *element's* rect rather than the line's.
    It costs 6.6-8.9 ms p50 and ~13 ms p95 (#32) -- structural per-call COM
    object construction, over the ~10 ms budget at p95, accepted on the record
    in spec section 13.
    """
    try:
        materialise = getattr(source, "getNVDAObjectFromIdentifier", None)
        if materialise is None:
            return None
        obj = materialise(int(doc_handle), int(control_id))
        return _rect(obj.location) if obj is not None else None
    except Exception:
        log.debugWarning("Unspoken: could not materialise a control field", exc_info=True)
        return None


def _point_rect(info):
    """Reading-path tier 2: the start of the text being read, as a zero-size rect."""
    try:
        point = info.pointAtStart
    except Exception:
        return None
    if point is None:
        return None
    try:
        return (point.x, point.y, 0, 0)
    except Exception:
        return None


#: Answers `_is_word_text_info` for a `TextInfo` class, computed once per class.
_WORD_TEXT_INFO_CLASSES = {}


def _is_word_text_info(info):
    """Is this the Word caret path, where tier 2 is cheap?

    `pointAtStart` costs 0.42 ms p50 in Word and 6-8 ms in a browser (#32), so
    it is asked for only where it is cheap; everything else falls to tier 3.
    Whether a `TextInfo` is Word's is a property of its *class*, so the MRO walk
    happens once per class and the reading path pays one dict lookup. Both
    Word implementations -- the object-model one and the UIA one -- name their
    TextInfo `WordDocumentTextInfo`.
    """
    klass = type(info)
    known = _WORD_TEXT_INFO_CLASSES.get(klass)
    if known is None:
        try:
            known = any("WordDocument" in c.__name__ for c in klass.__mro__)
        except Exception:
            known = False
        _WORD_TEXT_INFO_CLASSES[klass] = known
    return known


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Degraded until construction earns otherwise: every early return below
        # leaves a session that speaks roles rather than one that silently
        # suppresses them (spec section 9.2).
        self._degraded = True
        self._player = SilentSoundPlayer()
        self._previous_mouse_object = None
        self._original_properties_speech = None
        self._original_control_field_speech = None
        self._control_field_hook = None
        self._degraded_message_timer = None
        self._apply_theme = None
        self._settings_panel = None

        # 1. Config. The spec is registered here -- `GlobalPlugin` is its
        #    designed home -- and the migration runs immediately after, before
        #    anything has read a key and warmed the aggregated section's cache
        #    with a value the migration is about to change.
        config.conf.spec["unspoken"] = migration.CONF_SPEC
        _migrate_legacy_config()

        # 2. Where user sound themes live, before anything discovers or loads.
        themes.set_user_themes_dir(_user_themes_dir())

        # 3. The settings provider, the samples, and the player. Construction
        #    is the only thing the player can fail; after it, failures stay
        #    below the seam.
        sounds = themes.load(_conf("theme"))
        outcome = {
            "engine_ready": False,
            "device_open": False,
            "slots_loaded": len(sounds),
        }
        player = None
        try:
            player = OpenALSoundPlayer(_NVDASettingsProvider())
        except NoAudioEndpointError as error:
            # The one failure that means "this machine has nothing to play
            # through" rather than "this build is broken".
            outcome["engine_ready"] = True
            log.error(f"Unspoken: no usable audio output device: {error}")
        except Exception as error:
            log.error(f"Unspoken: could not start the Sound Player: {error}", exc_info=True)
        else:
            outcome["engine_ready"] = True
            outcome["device_open"] = True

        # 4. Spec section 9.2's one question, asked once. The answer is
        #    immutable for the session: the suppression patch reads a plain
        #    attribute, there is no hot-path branch beyond it, and it cannot
        #    race the player's worker. Mid-session device trouble never
        #    escalates to speech-only -- it drops plays behind the player's own
        #    boolean and recovers on the next device event.
        self._degraded = not wiring.can_produce_role_sound(outcome)
        if self._degraded:
            if player is not None:
                player.close()
            log.error(
                f"Unspoken: running speech-only this session -- "
                f"{wiring.degraded_cause(outcome)}."
            )
            self._announce_degraded()
        else:
            self._player = player
            self._player.set_theme(sounds)
            self._player.set_reverb(_conf("reverb"))

        # 5. The settings panel, and the live-preview hooks it calls.
        from . import addonGui

        self._settings_panel = addonGui.SettingsPanel
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(self._settings_panel)
        self._apply_theme = wiring.Debounce(
            THEME_PREVIEW_DEBOUNCE_MS, self._load_theme, wx.CallLater
        )
        addonGui.apply_theme = self._apply_theme
        addonGui.apply_reverb = self._apply_reverb

        # 6. The two entry points that are patches rather than events.
        self._original_properties_speech = speech.speech.getPropertiesSpeech
        speech.speech.getPropertiesSpeech = self._hook_getPropertiesSpeech
        self._original_control_field_speech = textInfos.TextInfo.getControlFieldSpeech
        self._control_field_hook = self._make_control_field_hook(
            self._original_control_field_speech
        )
        textInfos.TextInfo.getControlFieldSpeech = self._control_field_hook

        _log_ancestor_coinstall()
        log.info(
            f"Unspoken-ng ready: {len(sounds)} slots, theme {_conf('theme')!r}, "
            f"reverb {_conf('reverb')!r}, degraded={self._degraded}"
        )

    # ---------------------------------------------------------------- events

    def event_gainFocus(self, obj, nextHandler):
        """Focus everywhere, browse-mode Tab included (spec section 5).

        The sound goes first: its onset must not lag speech's (spec section 6),
        and the whole block is 0.13-0.22 ms p50 (#28, #30).
        """
        self._play_object(obj)
        nextHandler()

    def event_becomeNavigatorObject(self, obj, nextHandler, isFocus=False):
        """Object navigation and screen/touch exploration (spec section 5).

        `api.setNavigatorObject` sets `isFocus` when the navigator moved
        because focus did, and `event_gainFocus` has already played that
        object. The flag *is* the dedup -- it replaces the deleted timer's
        guess that anything within 100 ms was an echo.
        """
        if not isFocus:
            self._play_object(obj)
        nextHandler()

    def event_mouseMove(self, obj, nextHandler, x, y):
        """The mouse, with today's behaviour unchanged (spec section 5).

        Plays when the mouse moves onto a different object. `!=` on NVDAObjects
        compares through the underlying accessible; #28 measured it at 0.01 ms.
        """
        if obj != self._previous_mouse_object:
            self._previous_mouse_object = obj
            self._play_object(obj)
        nextHandler()

    def _play_object(self, obj):
        """The object-event sound path: one `role` read, one `location` read.

        The object is the one the event handed us. There is no
        `treeInterceptor` branch and no browse-mode special case: #30 proved
        the handed object is the right one on the Tab path, and that the
        deleted branch was median 58.9 degrees wrong as well as ~15 ms slow.

        A missing rect falls back to the screen centre rather than dropping the
        sound -- position degrades, the sound does not.
        """
        try:
            if _conf("roleAnnouncement") == "speechOnly":
                return
            slot = roles.slot_for(obj.role)
            if slot is None:
                return
            location = obj.location
            desktop_rect = _desktop_rect()
            self._player.play(
                slot, spatial.position_for(_rect(location) or desktop_rect, desktop_rect)
            )
        except Exception:
            log.debugWarning("Unspoken: could not play a role sound", exc_info=True)

    # ------------------------------------------------- reading-path entry point

    def _make_control_field_hook(self, original):
        """Patch `TextInfo.getControlFieldSpeech` -- the reading-path entry point.

        Browse-mode reading, quicknav, say-all and the Word caret reach the
        user through the speech pipeline and dispatch no object event, so this
        is where they are covered (#31). The base class is patched because no
        subclass overrides this method anywhere in NVDA, and `getPropertiesSpeech`
        cannot serve: it receives only `role=role`, never the field.

        The sound is placed before the original runs so the onset leads speech.
        Whatever happens in our half, NVDA's speech is produced.
        """
        plugin = self

        def getControlFieldSpeech(
            info,
            attrs,
            ancestorAttrs,
            fieldType,
            formatConfig=None,
            extraDetail=False,
            reason=None,
        ):
            try:
                plugin._play_control_field(info, attrs, fieldType, reason)
            except Exception:
                log.debugWarning("Unspoken: reading-path hook failed", exc_info=True)
            return original(
                info, attrs, ancestorAttrs, fieldType, formatConfig, extraDetail, reason
            )

        return getControlFieldSpeech

    def _play_control_field(self, info, attrs, fieldType, reason):
        """Decide, then place. The decision is `wiring.should_play_control_field`.

        Everything handed to the predicate is a plain value, so the whole
        condition is table-tested off NVDA against #32's measured records
        (`tests/test_wiring.py`). Nothing here re-implements any part of it.
        """
        slot = roles.slot_for(attrs.get("role"))
        if not wiring.should_play_control_field(
            getattr(reason, "name", None),
            fieldType,
            slot,
            _conf("silenceDuringSayAll"),
        ):
            return
        if _conf("roleAnnouncement") == "speechOnly":
            return
        self._player.play(slot, self._reading_position(info, attrs))

    def _reading_position(self, info, attrs):
        """Spec section 5's position tiers. The sound plays either way.

        1. virtual buffers: the field's own `controlIdentifier_*`, materialised
        2. Word: the start of the text being read
        3. anything else, UIA documents included: the focus rect
        4. nothing at all: screen centre

        Tiers degrade *spatialized*. A sound that changed character when a
        lookup failed would teach users to hear our lookup failures (#31).
        """
        desktop_rect = _desktop_rect()
        doc_handle = attrs.get("controlIdentifier_docHandle")
        control_id = attrs.get("controlIdentifier_ID")

        rect = None
        if doc_handle is not None and control_id is not None:
            rect = _identifier_rect(info.obj, doc_handle, control_id)
        elif _is_word_text_info(info):
            rect = _point_rect(info)
        if rect is None:
            rect = _focus_rect()
        if rect is None:
            rect = desktop_rect
        return spatial.position_for(rect, desktop_rect)

    # ------------------------------------------------------ suppression only

    def _hook_getPropertiesSpeech(self, *args, **kwargs):
        """Suppression, and only suppression: this hook never plays.

        Gated on three things -- not degraded, the role maps to a slot, and
        role announcement is "sounds". Because every path that announces a role
        now also plays one, suppress-iff-play holds by construction; the
        pre-existing bug where Word's "link" was deleted with no sound ever
        replacing it retires with the reading-path hook above.

        `role` can only arrive as a keyword (NVDA's signature is
        `getPropertiesSpeech(reason, **propertyValues)`), so every other
        argument passes through untouched and NVDA's own default for `reason`
        is preserved.
        """
        if not self._degraded:
            role = kwargs.get("role")
            if (
                role is not None
                and roles.slot_for(role) is not None
                and _conf("roleAnnouncement") == "sounds"
            ):
                # NVDA does not announce a role handed to it as `_role`.
                kwargs["_role"] = kwargs.pop("role")
        return self._original_properties_speech(*args, **kwargs)

    # ------------------------------------------------------- settings panel

    def _load_theme(self, theme_id):
        """Decode and upload a sound theme. Debounced -- see `wiring.Debounce`."""
        try:
            self._player.set_theme(themes.load(theme_id))
        except Exception:
            log.error(f"Unspoken: could not apply sound theme {theme_id!r}", exc_info=True)

    def _apply_reverb(self, preset):
        """The panel's other live-preview hook. A handful of EFX writes; no debounce."""
        try:
            self._player.set_reverb(preset)
        except Exception:
            log.error(f"Unspoken: could not apply reverb preset {preset!r}", exc_info=True)

    # ------------------------------------------------------------- lifetime

    def _announce_degraded(self):
        """Spec section 9.4's one message, spoken *and* brailled, deferred.

        `ui.message` does both. It is deferred because NVDA is not ready to
        speak while global plugins are being constructed, and because spec
        section 9.4 forbids raising from `__init__`. `core.callLater` puts it
        on the main loop; the delay lets NVDA's own startup speech go first.
        One shot, and stopped in `terminate`.
        """
        try:
            self._degraded_message_timer = core.callLater(
                DEGRADED_MESSAGE_DELAY_MS,
                ui.message,
                # Translators: Spoken and brailled once at startup when the addon
                # cannot play sounds, so NVDA speaks control roles instead.
                _("Unspoken: audio unavailable, speaking roles instead."),
            )
        except Exception:
            log.error("Unspoken: could not schedule the startup message", exc_info=True)

    def terminate(self):
        """Give everything back: hooks, panel, timers, device."""
        try:
            if self._degraded_message_timer is not None:
                self._degraded_message_timer.Stop()
        except Exception:
            pass
        self._degraded_message_timer = None

        if self._apply_theme is not None:
            try:
                self._apply_theme.cancel()
            except Exception:
                pass

        # Unpatch only what is still ours. Another addon may have patched over
        # us since; restoring then would delete its hook, not ours.
        try:
            from . import addonGui

            if addonGui.apply_theme is self._apply_theme:
                addonGui.apply_theme = _noop
            if addonGui.apply_reverb == self._apply_reverb:
                addonGui.apply_reverb = _noop
        except Exception:
            log.debugWarning("Unspoken: could not release the panel hooks", exc_info=True)

        try:
            if self._original_properties_speech is not None and (
                speech.speech.getPropertiesSpeech == self._hook_getPropertiesSpeech
            ):
                speech.speech.getPropertiesSpeech = self._original_properties_speech
        except Exception:
            log.debugWarning("Unspoken: could not restore getPropertiesSpeech", exc_info=True)
        self._original_properties_speech = None

        try:
            if self._original_control_field_speech is not None and (
                textInfos.TextInfo.getControlFieldSpeech is self._control_field_hook
            ):
                textInfos.TextInfo.getControlFieldSpeech = (
                    self._original_control_field_speech
                )
        except Exception:
            log.debugWarning("Unspoken: could not restore getControlFieldSpeech", exc_info=True)
        self._original_control_field_speech = None
        self._control_field_hook = None

        try:
            if self._settings_panel is not None:
                gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(
                    self._settings_panel
                )
        except Exception:
            pass
        self._settings_panel = None

        try:
            self._player.close()
        except Exception:
            log.debugWarning("Unspoken: could not close the Sound Player", exc_info=True)
        self._player = SilentSoundPlayer()

        super().terminate()
