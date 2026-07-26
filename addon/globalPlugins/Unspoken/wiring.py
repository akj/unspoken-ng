"""The decisions `GlobalPlugin` makes, taken out of `GlobalPlugin`.

`__init__.py` imports NVDA on its first line -- `globalPluginHandler`, `speech`,
`textInfos`, `wx` -- so pytest cannot import it, and spec section 10 says so:
the plugin itself is smoke-tested live (`docs/smoke-test.md`), not unit-tested.
But it also requires the reading-path play condition to be *"a pure predicate --
extract it as a function and table-test it off-NVDA with the #32 dataset as
fixtures"*, and the same argument applies to the other three judgments the
wiring makes: what volume the Sound Player should see, whether this session can
produce a role sound at all, and how a burst of live-preview keypresses
collapses into one sound theme load.

So this module is where those four live. Everything here takes plain values and
returns plain values: no NVDA, no OpenAL, no I/O, no globals. `GlobalPlugin`
keeps the property reads, the patches and the lifetime; this keeps the logic
that can be wrong in a way a test can catch.
"""

from __future__ import annotations


# --------------------------------------------------------------------------
# The reading-path play condition (spec section 5)
# --------------------------------------------------------------------------

#: The say-all reason, named because the fourth argument only applies to it.
SAY_ALL_REASON = "SAYALL"

#: `OutputReason` names the reading path plays for.
#:
#: `FOCUS` is excluded deliberately rather than accidentally: `event_gainFocus`
#: has already played that object, so excluding it is the dedup (zero
#: double-fires across 1,789 measured records in #32). `ONLYCACHE` never
#: reaches a hook that is about to speak. Everything else -- `QUERY`, `CHANGE`,
#: `MESSAGE`, `MOUSE`, `FOCUSENTERED` -- is not the reading path.
PLAY_REASONS = frozenset({"CARET", "QUICKNAV", SAY_ALL_REASON})

#: The two `fieldType` values NVDA announces a control on.
#:
#: The tempting filter is `fieldType.startswith("start")`, and it is wrong:
#: `start_inControlFieldStack` fires for every field the caret is *already*
#: inside, which in Word is the enclosing EDITABLETEXT on every line --- 76
#: repeats in 40 keypresses (#32). That filter would play the editable-text
#: sound roughly twice per line, forever.
#:
#: **Known accepted cost.** Excluding `start_inControlFieldStack` is not quite
#: exact, so "suppress if and only if we play" is *nearly* true rather than
#: true. `speech.py` does emit a role for that fieldType in one case:
#: `speakWithinForLine`, which applies only to `PRESCAT_SINGLELINE` fields. A
#: single-line field whose role maps to a slot is therefore suppressed with no
#: sound replacing it. It does not occur anywhere in #32's 2,473 measured calls
#: -- Word's repeated EDITABLETEXT is multiline and LISTITEM is
#: `PRESCAT_MARKER` -- and widening the filter to catch it would reinstate
#: those 76 repeats per 40 keypresses, which is far worse. Recorded here and in
#: PR #51 so it lands as a stated cost; folding it into spec section 13 is
#: Andrew's call, not this module's.
PLAY_FIELD_TYPES = frozenset({"start_addedToControlFieldStack", "start_relative"})


def should_play_control_field(
    reason: str | None,
    field_type: str | None,
    slot: str | None,
    silence_during_say_all: bool,
) -> bool:
    """Should the reading path play a role sound for this control field?

    Every argument is a plain value the caller has already extracted:

    - `reason`: the `OutputReason` member's *name*, or None.
    - `field_type`: NVDA's `fieldType` string.
    - `slot`: what `roles.slot_for()` returned for the field's role, so None
      means "this role has no sound".
    - `silence_during_say_all`: the spec section 8 setting. It gates say-all
      and nothing else -- suppression of *spoken* roles stays governed by role
      announcement, which is a separate setting and a separate decision.

    Position is not an argument, and that is the point: when this returns True
    the sound plays, and the position tiers degrade underneath it. A sound
    whose presence depends on a metadata lookup teaches users to hear our
    lookup failures (#31).
    """
    if field_type not in PLAY_FIELD_TYPES:
        return False
    if reason not in PLAY_REASONS:
        return False
    if slot is None:
        return False
    if reason == SAY_ALL_REASON and silence_during_say_all:
        return False
    return True


# --------------------------------------------------------------------------
# The volume the Sound Player sees (spec section 4.4)
# --------------------------------------------------------------------------


def effective_volume(
    sound_volume: float | int | str | None,
    follows_voice: bool,
    synth_volume: float | int | str | None,
) -> float:
    """Fold NVDA's two sound-volume settings down to one gain in 0.0-1.0.

    This is NVDA's own rule, from `nvwave.WavePlayer._setVolumeFromConfig`:
    the configured sound volume, unless the user asked sound volume to follow
    the voice and there is a synth volume to follow. Both inputs are
    percentages.

    `synth_volume` is None when there is no synth or the synth does not support
    a volume setting -- exactly the case NVDA falls back to `soundVolume` for.

    Owning the output stream (ADR 0001) means nothing computes this for us, so
    the rule is reproduced here rather than inherited. A value NVDA would never
    store -- None, a string that is not a number -- yields full gain rather
    than silence: the addon's failure mode is never "quietly plays nothing".
    """
    value = synth_volume if (follows_voice and synth_volume is not None) else sound_volume
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return 1.0
    if percent != percent:  # NaN
        return 1.0
    return max(0.0, min(1.0, percent / 100.0))


# --------------------------------------------------------------------------
# "Can I produce a role sound?" (spec section 9.2)
# --------------------------------------------------------------------------

#: The keys `can_produce_role_sound` reads. `GlobalPlugin` fills all three
#: during construction; anything it forgets counts as missing, which degrades.
OUTCOME_KEYS = ("engine_ready", "device_open", "slots_loaded")

_CAUSES = (
    ("engine_ready", "the audio engine could not be started"),
    ("device_open", "no output device would open"),
    ("slots_loaded", "no sound theme samples could be loaded"),
)


def can_produce_role_sound(outcome) -> bool:
    """Spec section 9.2's one question, asked once, at the end of construction.

    It is an *outcome* predicate, not a health check: it asks whether this
    session can put a role sound in the air, over the three things that have to
    be true for that -- the engine started, a device opened, and the theme
    decoded to at least one slot. Any fourth cause is covered by construction:
    whatever it was, it raised, and `engine_ready`/`device_open` stay False.

    `outcome` is a plain mapping so the wiring can fill it from three different
    exception branches and hand the same shape to the log, the flag and the
    tests.

    False means the session runs speech-only: silent adapter below the seam, no
    suppression above it, and the saved role-announcement setting untouched, so
    a repaired install returns to sounds with nothing for the user to re-set.
    """
    return all(bool(outcome.get(key)) for key in OUTCOME_KEYS)


def degraded_cause(outcome) -> str | None:
    """The first missing precondition, phrased for the log; None if there is none.

    Spec section 9.4 gives the user one sentence and the log the cause. This is
    the cause.
    """
    for key, phrase in _CAUSES:
        if not outcome.get(key):
            return phrase
    return None


# --------------------------------------------------------------------------
# Collapsing a burst of live-preview keypresses (issue #38, panel review)
# --------------------------------------------------------------------------


class Debounce:
    """Run `action` once, `delay_ms` after the last call, with the last argument.

    The settings panel applies a sound theme live so the user hears the choice
    while arrowing through the combo box -- which means the hook is called once
    per keypress. `themes.load()` decodes fourteen WAVs sample by sample in
    pure Python: about 20 ms for the bundled default, linear in the theme's
    size. Per keypress that is a 20 ms stall on the thread NVDA speaks from;
    once, after the user settles, it is a stall while nothing is being
    announced.

    `schedule(delay_ms, callback)` returns a handle with a `Stop()` method --
    `wx.CallLater`'s contract, which is what `GlobalPlugin` passes, and which a
    fake can satisfy in three lines. Nothing here touches wx, so the collapsing
    itself is testable.

    Not thread-safe, and does not need to be: every caller is NVDA's main
    thread.
    """

    def __init__(self, delay_ms: int, action, schedule):
        self._delay_ms = delay_ms
        self._action = action
        self._schedule = schedule
        self._timer = None
        self._pending: tuple | None = None

    def __call__(self, *args) -> None:
        self._pending = args
        self._stop_timer()
        self._timer = self._schedule(self._delay_ms, self._fire)

    def _fire(self) -> None:
        self._timer = None
        pending, self._pending = self._pending, None
        if pending is not None:
            self._action(*pending)

    def cancel(self) -> None:
        """Drop the pending call, if any. `GlobalPlugin.terminate` calls this."""
        self._stop_timer()
        self._pending = None

    def _stop_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.Stop()
