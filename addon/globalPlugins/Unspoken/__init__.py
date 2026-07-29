# Unspoken-ng: spatially positioned role sounds instead of spoken control roles.
# By Bryan Smart (bryansmart@bryansmart.com) and Austin Hicks (camlorn38@gmail.com)

"""`GlobalPlugin`: wiring and entry points (spec sections 4.5 and 5).

This module is the only place that knows both NVDA and the addon. It owns four
things and nothing else:

- **Wiring.** The config spec and its one-shot migration, the user sound-theme
  directory, the settings provider, the Sound Player, and -- because it is the
  thing that attempted the player and caught the failure -- degraded mode.
- **Entry points.** Three object events, one speech-pipeline hook that places
  sounds into the speech stream, and one that only suppresses.
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
- Reading-path timing splits by field (ADR 0002, #52): the field the
  navigation landed inside plays at build time -- its utterance starts now,
  so the sound leads speech like the object events' do -- while traversed
  fields and everything under say-all return a `CallbackCommand` in the
  field's speech sequence, fired by the speech manager on the main thread
  when the synth reaches it. The split is `wiring.should_ride_speech`; #31's
  rule holds either way -- the synchronous call is the hook or the manager's
  index handling. Position is always read in the hook, where the property
  reads are legal and the field is current.
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
import synthDriverHandler
import textInfos
import ui
import wx
from logHandler import log
from speech.commands import CallbackCommand

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
    block. Counted in ConfigObj subscripts: `output_device` is 2
    (`conf["audio"]["outputDevice"]`); `volume` is 3, or **7** with "sound
    volume follows voice" on, plus the arithmetic in
    `wiring.effective_volume`. Every one of them is a dict lookup against an
    already-parsed section.

    `volume` additionally reads the synth's volume when the user has turned on
    "sound volume follows voice", and it reads it *from config* rather than
    from the driver. NVDA's own `nvwave.WavePlayer._setVolumeFromConfig` asks
    the driver (`getSynth().volume`), but that is not a read we can copy onto
    this path: for a synth running under the 32-bit bridge, `.volume` resolves
    to an RPyC round trip over a pipe to `synthDriverHost32`, which is exactly
    the blocking call `play` must not make -- and on 2026.1 that is how every
    remaining 32-bit synth runs.

    `config.conf["speech"][<synth>]["volume"]` is the same number: the settings
    ring writes it (`SynthSetting._set_value` sets the driver attribute *and*
    the config key in one call), so this tracks a live volume change with no
    driver involved and no staleness.
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

    Four ConfigObj subscripts, no driver, nothing that can block -- see
    `_NVDASettingsProvider` for why the driver attribute is off limits here.

    None covers "no synth yet" and "this synth has no volume setting", which
    are the two cases NVDA itself falls back to `soundVolume` for. The second
    is read as the absence of a `volume` key: NVDA registers a driver's config
    spec from its own `supportedSettings` when the driver loads, so the key is
    present exactly when `isSupported("volume")` would have been true --
    without asking the driver.
    """
    try:
        speech_conf = config.conf["speech"]
        synth_name = speech_conf["synth"]
        if not synth_name:
            return None
        return speech_conf[synth_name]["volume"]
    except KeyError:
        return None
    except Exception:
        log.debugWarning("Unspoken: could not read the synth volume", exc_info=True)
        return None


def _synth_reports_indexes():
    """Does the current synth notify `synthIndexReached`?

    A `CallbackCommand` is fired by the speech manager when the synth reports
    reaching the index the manager converted it to; a synth that never reports
    leaves it waiting forever -- NVDA has no timeout for it (say-all is just as
    broken on such a synth, for the same reason). Every in-tree synth reports
    indexes; this check exists for out-of-tree ones, and failing it falls back
    to playing at build time.

    Cost on the reading path: two attribute reads and a frozenset membership.
    For a synth under the 32-bit bridge, `supportedNotifications` is cached on
    the proxy, so no pipe round trip hides here.
    """
    try:
        synth = synthDriverHandler.getSynth()
        return (
            synth is not None
            and synthDriverHandler.synthIndexReached in synth.supportedNotifications
        )
    except Exception:
        log.debugWarning("Unspoken: could not read the synth's notifications", exc_info=True)
        return False


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

    Only the profiles *active at startup* are visible here. A profile that is
    activated later in the session is not migrated: its legacy keys mean
    nothing to the new spec, so the four settings fall through to the base
    profile and the spec defaults until the next NVDA start migrates it.
    """
    try:
        profiles = list(config.conf.profiles)
    except Exception:
        log.error("Unspoken: could not reach the config profiles to migrate", exc_info=True)
        return

    migrated = []
    for profile in profiles:
        try:
            section = profile.get("unspoken")
            if not section:
                continue
            before = set(section)
            migration.migrate(section)
            if set(section) != before:
                migrated.append(profile)
        except Exception:
            log.error("Unspoken: could not migrate a config profile", exc_info=True)

    if not migrated:
        return
    try:
        # Every profile we actually changed has to be marked, by name.
        # `_markWriteProfileDirty` marks only the topmost one and no-ops when
        # the base profile is all there is, so on a three-deep stack it would
        # leave the middle profile migrated in memory and unmigrated on disk --
        # re-migrated, and re-overwriting the user's choices, every session.
        # `save()` always writes the base profile, which is why it needs no
        # name of its own.
        dirty = getattr(config.conf, "_dirtyProfiles", None)
        if dirty is not None:
            for profile in migrated:
                name = getattr(profile, "name", None)
                if name:
                    dirty.add(name)
        else:
            # An NVDA that keeps its dirty set somewhere else. This marks only
            # the topmost profile however many we changed, so it is the
            # fallback, not the path -- and calling it per profile would mark
            # that same one repeatedly.
            mark_dirty = getattr(config.conf, "_markWriteProfileDirty", None)
            if mark_dirty is not None:
                mark_dirty()
        # Writes the whole base profile and fires pre_/post_configSave, earlier
        # in startup than NVDA would on its own. Accepted: the alternative,
        # leaving it to NVDA's save-on-exit, loses the migration entirely for
        # anyone who has that turned off.
        config.conf.save()
        log.info(
            f"Unspoken: migrated legacy settings onto the four-key config "
            f"({len(migrated)} profile(s))"
        )
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
        self._properties_speech_hook = None
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
        #    Both originals are resolved, and both hooks built, before either
        #    is installed. Resolving them is exactly the step that breaks when
        #    a future NVDA renames or moves a hook point -- the case
        #    docs/smoke-test.md exists to catch -- and a raise between the two
        #    assignments would leave suppression installed with nothing to
        #    remove it: `globalPluginHandler` catches the exception, the plugin
        #    never reaches `runningPlugins`, and `terminate` is never called.
        #    Then NVDA would announce no roles and play no sounds, all session.
        original_properties_speech = speech.speech.getPropertiesSpeech
        original_control_field_speech = textInfos.TextInfo.getControlFieldSpeech
        properties_hook = self._make_properties_speech_hook(original_properties_speech)
        control_field_hook = self._make_control_field_hook(original_control_field_speech)

        self._original_properties_speech = original_properties_speech
        self._original_control_field_speech = original_control_field_speech
        self._properties_speech_hook = properties_hook
        self._control_field_hook = control_field_hook
        speech.speech.getPropertiesSpeech = properties_hook
        textInfos.TextInfo.getControlFieldSpeech = control_field_hook

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
        It is a COM call, so it can raise, and it is inside the guard for the
        same reason every other property read is: `nextHandler` runs either way.
        """
        try:
            moved = obj != self._previous_mouse_object
        except Exception:
            log.debugWarning("Unspoken: could not compare mouse objects", exc_info=True)
            moved = False
        if moved:
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

        This hook runs when NVDA *builds* a speech sequence, and on the
        reading path building and speaking are decoupled: say-all queues lines
        ahead of the synth, and a line with several controls builds all its
        fields in one pass -- played from here, sounds fire seconds early and
        in bursts (#52). So `_control_field_sound` plays at build time only
        for the field the navigation landed inside (whose utterance starts
        now); for everything else it hands back a `CallbackCommand`, prepended
        here to the field's sequence, and the sound fires when speech reaches
        the field (ADR 0002). Whatever happens in our half, NVDA's speech is
        produced.
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
            sequence = original(
                info, attrs, ancestorAttrs, fieldType, formatConfig, extraDetail, reason
            )
            try:
                command = plugin._control_field_sound(info, attrs, fieldType, reason)
                if command is not None:
                    return [command, *sequence]
            except Exception:
                log.debugWarning("Unspoken: reading-path hook failed", exc_info=True)
            return sequence

        return getControlFieldSpeech

    def _control_field_sound(self, info, attrs, fieldType, reason):
        """Decide, place, wrap. The decision is `wiring.should_play_control_field`.

        Everything handed to the predicate is a plain value, so the whole
        condition is table-tested off NVDA against #32's measured records
        (`tests/test_wiring.py`). Nothing here re-implements any part of it.

        The position is read here, at build time -- the main thread, where the
        property reads are legal and the field's identifiers are at hand. Then
        `wiring.should_ride_speech` splits the timing: the field the
        navigation just landed inside plays now, leading speech the way the
        object events do, and everything else goes back to the hook as a
        `CallbackCommand` closing over `(slot, position)`; the speech manager
        fires it, on this same thread, when the synth reaches the field. An
        index from a cancelled utterance is dropped by the manager, so
        interrupting speech drops the sounds of everything never spoken --
        while voices already in the air ring out (#10 d5).

        `self._player` is resolved at fire time deliberately: a callback that
        outlives `terminate` finds the `SilentSoundPlayer` and no-ops.
        """
        reason_name = getattr(reason, "name", None)
        slot = roles.slot_for(attrs.get("role"))
        if not wiring.should_play_control_field(
            reason_name,
            fieldType,
            slot,
            _conf("silenceDuringSayAll"),
        ):
            return None
        if _conf("roleAnnouncement") == "speechOnly":
            return None
        position = self._reading_position(info, attrs)
        if not wiring.should_ride_speech(reason_name, fieldType) or not _synth_reports_indexes():
            # Leads: the utterance announcing this field starts now. Or the
            # fallback: a synth that never reports indexes would leave the
            # callback waiting forever, so play at build time, bursts and all.
            self._player.play(slot, position)
            return None

        def _play():
            # Inside the speech manager's index handling, not our hook's try.
            # `play` returns in ~0.1 ms and does not raise, but this must be
            # total regardless: an escape here breaks NVDA's speech pump.
            try:
                self._player.play(slot, position)
            except Exception:
                log.debugWarning("Unspoken: could not play a role sound", exc_info=True)

        return CallbackCommand(_play, name=f"unspoken:{slot}")

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

    def _make_properties_speech_hook(self, original):
        """Patch `speech.speech.getPropertiesSpeech` -- suppression, and only that.

        This hook never plays. It is gated on three things: not degraded, the
        role maps to a slot, and role announcement is "sounds".

        The original is closed over rather than read from `self` at call time,
        which matters for teardown: if another add-on patches over us and later
        restores *its* original, our hook is put back and called again after we
        have terminated. A closure still works then; an attribute we had nulled
        would raise `TypeError` on NVDA's only path for object-property speech.

        `role` can only arrive as a keyword (NVDA's signature is
        `getPropertiesSpeech(reason, **propertyValues)`), so every other
        argument passes through untouched and NVDA's own default for `reason`
        is preserved.
        """
        plugin = self

        def getPropertiesSpeech(*args, **kwargs):
            if not plugin._degraded:
                role = kwargs.get("role")
                if (
                    role is not None
                    and roles.slot_for(role) is not None
                    and _conf("roleAnnouncement") == "sounds"
                ):
                    # NVDA does not announce a role handed to it as `_role`.
                    kwargs["_role"] = kwargs.pop("role")
            return original(*args, **kwargs)

        return getPropertiesSpeech

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

        # Neither hook's saved original is cleared here, and neither hook is
        # dropped. When we decline to restore because someone patched over us,
        # their own teardown will reinstate ours -- and it has to keep working
        # after we are gone, on NVDA's only path for object-property speech.
        try:
            if self._properties_speech_hook is not None and (
                speech.speech.getPropertiesSpeech is self._properties_speech_hook
            ):
                speech.speech.getPropertiesSpeech = self._original_properties_speech
        except Exception:
            log.debugWarning("Unspoken: could not restore getPropertiesSpeech", exc_info=True)

        try:
            if self._control_field_hook is not None and (
                textInfos.TextInfo.getControlFieldSpeech is self._control_field_hook
            ):
                textInfos.TextInfo.getControlFieldSpeech = (
                    self._original_control_field_speech
                )
        except Exception:
            log.debugWarning("Unspoken: could not restore getControlFieldSpeech", exc_info=True)

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

        # A terminated plugin can produce no role sound, so if one of our hooks
        # is ever reinstated by someone else's teardown it must not suppress.
        self._degraded = True

        super().terminate()
