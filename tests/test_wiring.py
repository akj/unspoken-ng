"""Tests for the decisions `GlobalPlugin` makes.

`GlobalPlugin` itself is not unit-tested, deliberately and per spec section 10:
it imports NVDA on its first line and its job is property reads, patches and
lifetime, none of which mean anything outside a running screen reader. It is
covered by `docs/smoke-test.md`, run against a live NVDA.

What *is* testable is what it decides, which is why those decisions live in
`wiring.py`. The reading-path play condition is tested against the #32
measurement dataset: 2,473 `getControlFieldSpeech` calls captured from Chrome,
Firefox and Word, collapsed to the 106 distinct (reason, fieldType, role)
triples they contain, in `fixtures/control_field_calls.json`.
"""

import json
from pathlib import Path

import controlTypes
import pytest

import roles
import wiring


FIXTURE = Path(__file__).parent / "fixtures" / "control_field_calls.json"


def _records():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _slot(role_name):
    return roles.slot_for(getattr(controlTypes.Role, role_name))


# --- the reading-path play condition, against the #32 dataset --------------

#: Every triple in the dataset that plays, with the number of measured calls
#: it stands for. Written out rather than computed: a test that recomputes the
#: condition it is testing proves only that Python is deterministic.
#:
#: 113 plays out of 2,473 measured calls. The number is the point: the
#: obvious filters overshoot it badly, and by how much is measured in
#: `test_a_bare_start_filter_would_nearly_double_the_sounds` and
#: `test_in_stack_repeats_never_play`.
EXPECTED_PLAYS = {
    ("CARET", "start_addedToControlFieldStack", "BUTTON"): 7,
    ("CARET", "start_addedToControlFieldStack", "CHECKBOX"): 6,
    ("CARET", "start_addedToControlFieldStack", "EDITABLETEXT"): 6,
    ("CARET", "start_addedToControlFieldStack", "LINK"): 16,
    ("CARET", "start_addedToControlFieldStack", "LISTITEM"): 3,
    ("CARET", "start_addedToControlFieldStack", "STATICTEXT"): 6,
    ("CARET", "start_relative", "LINK"): 15,
    ("QUICKNAV", "start_addedToControlFieldStack", "LINK"): 40,
    ("SAYALL", "start_addedToControlFieldStack", "BUTTON"): 2,
    ("SAYALL", "start_addedToControlFieldStack", "CHECKBOX"): 2,
    ("SAYALL", "start_addedToControlFieldStack", "LINK"): 10,
}


def _plays(record, silence_during_say_all=False):
    return wiring.should_play_control_field(
        record["reason"],
        record["fieldType"],
        _slot(record["role"]),
        silence_during_say_all,
    )


def test_the_fixture_is_the_measured_dataset():
    records = _records()
    assert len(records) == 106
    assert sum(record["calls"] for record in records) == 2473


def test_exactly_the_expected_triples_play():
    played = {
        (record["reason"], record["fieldType"], record["role"]): record["calls"]
        for record in _records()
        if _plays(record)
    }
    assert played == EXPECTED_PLAYS


def test_the_focus_reason_never_plays():
    """`event_gainFocus` already played it. The dedup is this exclusion."""
    focus = [record for record in _records() if record["reason"] == "FOCUS"]
    assert sum(record["calls"] for record in focus) == 374
    assert not any(_plays(record) for record in focus)


def test_the_onlycache_reason_never_plays():
    only_cache = [record for record in _records() if record["reason"] == "ONLYCACHE"]
    assert sum(record["calls"] for record in only_cache) == 360
    assert not any(_plays(record) for record in only_cache)


def test_end_field_types_never_play():
    ends = [record for record in _records() if record["fieldType"].startswith("end")]
    assert sum(record["calls"] for record in ends) == 1293
    assert not any(_plays(record) for record in ends)


def test_in_stack_repeats_never_play():
    """The Word bug a bare `start*` filter would ship.

    `start_inControlFieldStack` fires for every field the caret is *already*
    inside. In the 40-keypress Word pass that is the enclosing EDITABLETEXT,
    76 times: roughly two editable-text sounds per line, forever.
    """
    in_stack = [
        record
        for record in _records()
        if record["fieldType"] == "start_inControlFieldStack"
    ]
    assert not any(_plays(record) for record in in_stack)

    word_repeats = [
        record
        for record in in_stack
        if record["reason"] == "CARET" and record["role"] == "EDITABLETEXT"
    ]
    assert sum(record["calls"] for record in word_repeats) == 76


def test_a_bare_start_filter_would_nearly_double_the_sounds():
    """What the fieldType clause is worth, in the dataset's own numbers."""
    naive = sum(
        record["calls"]
        for record in _records()
        if record["reason"] in wiring.PLAY_REASONS
        and record["fieldType"].startswith("start")
        and _slot(record["role"]) is not None
    )
    assert naive == 203
    assert sum(EXPECTED_PLAYS.values()) == 113


def test_unmapped_roles_never_play():
    unmapped = [record for record in _records() if _slot(record["role"]) is None]
    assert {record["role"] for record in unmapped} == {
        "DOCUMENT",
        "GROUPING",
        "HEADING",
        "LABEL",
        "LANDMARK",
        "LIST",
        "PARAGRAPH",
        "SECTION",
    }
    assert not any(_plays(record) for record in unmapped)


def test_silence_during_say_all_removes_exactly_the_say_all_plays():
    silenced = {
        (record["reason"], record["fieldType"], record["role"])
        for record in _records()
        if _plays(record) and not _plays(record, silence_during_say_all=True)
    }
    assert silenced == {
        key for key in EXPECTED_PLAYS if key[0] == wiring.SAY_ALL_REASON
    }
    assert all(
        _plays(record, silence_during_say_all=True)
        for record in _records()
        if _plays(record) and record["reason"] != wiring.SAY_ALL_REASON
    )


@pytest.mark.parametrize("reason", [None, "", "QUERY", "CHANGE", "MOUSE", "sayall"])
def test_reasons_outside_the_set_never_play(reason):
    assert not wiring.should_play_control_field(
        reason, "start_addedToControlFieldStack", "link", False
    )


@pytest.mark.parametrize("field_type", [None, "", "start", "start_relative_extra"])
def test_field_types_outside_the_set_never_play(field_type):
    assert not wiring.should_play_control_field("CARET", field_type, "link", False)


# --- lead or ride: when a reading-path sound fires (ADR 0002) --------------

#: Of the 113 plays, the ones that lead speech (play at build time): fields
#: the navigation landed inside, under the two reasons whose utterance starts
#: immediately. Written out for the same reason `EXPECTED_PLAYS` is.
EXPECTED_LEADS = {
    ("CARET", "start_addedToControlFieldStack", "BUTTON"): 7,
    ("CARET", "start_addedToControlFieldStack", "CHECKBOX"): 6,
    ("CARET", "start_addedToControlFieldStack", "EDITABLETEXT"): 6,
    ("CARET", "start_addedToControlFieldStack", "LINK"): 16,
    ("CARET", "start_addedToControlFieldStack", "LISTITEM"): 3,
    ("CARET", "start_addedToControlFieldStack", "STATICTEXT"): 6,
    ("QUICKNAV", "start_addedToControlFieldStack", "LINK"): 40,
}


def test_exactly_the_entered_fields_lead():
    leads = {
        (record["reason"], record["fieldType"], record["role"]): record["calls"]
        for record in _records()
        if _plays(record)
        and not wiring.should_ride_speech(record["reason"], record["fieldType"])
    }
    assert leads == EXPECTED_LEADS
    assert sum(EXPECTED_LEADS.values()) == 84


def test_the_rest_of_the_plays_ride():
    rides = {
        (record["reason"], record["fieldType"], record["role"]): record["calls"]
        for record in _records()
        if _plays(record)
        and wiring.should_ride_speech(record["reason"], record["fieldType"])
    }
    assert rides == {
        key: calls for key, calls in EXPECTED_PLAYS.items() if key not in EXPECTED_LEADS
    }
    assert sum(rides.values()) == 113 - 84


def test_say_all_rides_even_into_an_entered_field():
    """Read-ahead queues even the utterance start, so say-all never leads."""
    assert wiring.should_ride_speech("SAYALL", wiring.FIELD_ENTERED) is True


@pytest.mark.parametrize("reason", ["CARET", "QUICKNAV"])
def test_traversed_fields_ride(reason):
    """`start_relative` is announced mid-utterance -- the #52 burst case."""
    assert wiring.should_ride_speech(reason, "start_relative") is True


@pytest.mark.parametrize("reason", ["CARET", "QUICKNAV"])
def test_entered_fields_lead(reason):
    assert wiring.should_ride_speech(reason, wiring.FIELD_ENTERED) is False


# --- the volume the Sound Player sees -------------------------------------


@pytest.mark.parametrize(
    "sound_volume,follows_voice,synth_volume,expected",
    [
        # Not following the voice: the sound volume, whatever the synth is at.
        (100, False, 20, 1.0),
        (50, False, 100, 0.5),
        (0, False, 100, 0.0),
        # Following it: the synth's volume replaces the sound volume.
        (100, True, 20, 0.2),
        (25, True, 80, 0.8),
        # Following it with nothing to follow -- no synth, or a synth with no
        # volume setting -- falls back to the sound volume, as NVDA does.
        (75, True, None, 0.75),
        # ConfigObj hands back strings when a key predates its spec entry.
        ("60", False, None, 0.6),
        (60, True, "30", 0.3),
        # Nothing usable is full gain, never silence.
        (None, False, None, 1.0),
        ("loud", False, None, 1.0),
        # Out of range is clamped rather than trusted.
        (140, False, None, 1.0),
        (-10, False, None, 0.0),
    ],
)
def test_effective_volume(sound_volume, follows_voice, synth_volume, expected):
    assert wiring.effective_volume(sound_volume, follows_voice, synth_volume) == expected


def test_effective_volume_survives_a_nan():
    assert wiring.effective_volume(float("nan"), False, None) == 1.0


# --- "can I produce a role sound?" ----------------------------------------


def _outcome(**overrides):
    outcome = {"engine_ready": True, "device_open": True, "slots_loaded": 14}
    outcome.update(overrides)
    return outcome


def test_a_complete_outcome_can_produce_a_role_sound():
    assert wiring.can_produce_role_sound(_outcome()) is True
    assert wiring.degraded_cause(_outcome()) is None


@pytest.mark.parametrize(
    "overrides,cause",
    [
        ({"engine_ready": False, "device_open": False}, "audio engine"),
        ({"device_open": False}, "output device"),
        ({"slots_loaded": 0}, "sound theme samples"),
    ],
)
def test_a_missing_precondition_degrades_and_names_itself(overrides, cause):
    outcome = _outcome(**overrides)
    assert wiring.can_produce_role_sound(outcome) is False
    assert cause in wiring.degraded_cause(outcome)


def test_an_outcome_missing_a_key_degrades():
    """Anything the wiring forgot to fill counts as missing, not as fine."""
    assert wiring.can_produce_role_sound({}) is False
    assert wiring.can_produce_role_sound({"engine_ready": True}) is False


def test_a_device_opens_but_a_broken_theme_still_degrades():
    """Silence with suppression standing is the failure, not the fallback."""
    assert wiring.can_produce_role_sound(_outcome(slots_loaded=0)) is False


# --- collapsing a burst of live-preview keypresses ------------------------


class _FakeTimer:
    def __init__(self, delay_ms, callback):
        self.delay_ms = delay_ms
        self.callback = callback
        self.stopped = False

    def Stop(self):
        self.stopped = True


class _FakeScheduler:
    """wx.CallLater's contract, minus wx: schedule, Stop, and fire on demand."""

    def __init__(self):
        self.timers = []

    def __call__(self, delay_ms, callback):
        timer = _FakeTimer(delay_ms, callback)
        self.timers.append(timer)
        return timer

    def fire_latest(self):
        timer = self.timers[-1]
        assert not timer.stopped
        timer.callback()

    @property
    def live(self):
        return [timer for timer in self.timers if not timer.stopped]


def test_a_burst_of_calls_collapses_to_one_with_the_last_argument():
    applied = []
    scheduler = _FakeScheduler()
    debounce = wiring.Debounce(300, applied.append, scheduler)

    debounce("default")
    debounce("retro")
    debounce("marimba")
    assert applied == []
    assert len(scheduler.live) == 1

    scheduler.fire_latest()
    assert applied == ["marimba"]


def test_the_delay_is_the_one_it_was_given():
    scheduler = _FakeScheduler()
    wiring.Debounce(300, lambda theme: None, scheduler)("retro")

    assert scheduler.timers[-1].delay_ms == 300


def test_each_settled_burst_fires_once():
    applied = []
    scheduler = _FakeScheduler()
    debounce = wiring.Debounce(300, applied.append, scheduler)

    debounce("retro")
    scheduler.fire_latest()
    debounce("marimba")
    scheduler.fire_latest()

    assert applied == ["retro", "marimba"]


def test_firing_twice_without_a_new_call_does_nothing_twice():
    applied = []
    scheduler = _FakeScheduler()
    debounce = wiring.Debounce(300, applied.append, scheduler)

    debounce("retro")
    timer = scheduler.timers[-1]
    timer.callback()
    timer.callback()

    assert applied == ["retro"]


def test_cancel_drops_the_pending_call():
    applied = []
    scheduler = _FakeScheduler()
    debounce = wiring.Debounce(300, applied.append, scheduler)

    debounce("retro")
    timer = scheduler.timers[-1]
    debounce.cancel()

    assert timer.stopped is True
    timer.callback()
    assert applied == []


def test_cancelling_an_idle_debounce_is_harmless():
    wiring.Debounce(300, lambda theme: None, _FakeScheduler()).cancel()
