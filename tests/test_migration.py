import importlib.util
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "addon"
    / "globalPlugins"
    / "Unspoken"
    / "migration.py"
)
SPEC = importlib.util.spec_from_file_location("unspoken_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_conf_spec_matches_new_configuration_schema():
    assert migration.CONF_SPEC == {
        "theme": 'string(default="default")',
        "roleAnnouncement": (
            'option("sounds", "soundsAndSpeech", "speechOnly", default="sounds")'
        ),
        "reverb": (
            'option("none", "smallRoom", "mediumRoom", "hall", default="smallRoom")'
        ),
        "silenceDuringSayAll": "boolean(default=False)",
    }


@pytest.mark.parametrize(
    ("no_sounds", "speak_roles", "expected"),
    [
        (False, True, "soundsAndSpeech"),
        (False, False, "sounds"),
        (True, True, "speechOnly"),
        (True, False, "speechOnly"),
    ],
)
def test_role_announcement_truth_table(no_sounds, speak_roles, expected):
    section = {"noSounds": no_sounds, "speakRoles": speak_roles}

    migration.migrate(section)

    assert section == {"roleAnnouncement": expected}


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ({"speakRoles": True}, "soundsAndSpeech"),
        ({"noSounds": True}, "speechOnly"),
    ],
)
def test_missing_role_setting_uses_legacy_default(section, expected):
    migration.migrate(section)

    assert section == {"roleAnnouncement": expected}


def test_say_all_true_enables_silence_during_say_all():
    section = {"sayAll": True}

    migration.migrate(section)

    assert section["silenceDuringSayAll"] is True


@pytest.mark.parametrize("say_all", [False, None])
def test_say_all_absent_or_false_does_not_set_silence_during_say_all(say_all):
    section = {"HRTF": True}
    if say_all is not None:
        section["sayAll"] = say_all

    migration.migrate(section)

    assert "silenceDuringSayAll" not in section


def test_reverb_false_maps_to_none():
    section = {"Reverb": False}

    migration.migrate(section)

    assert section["reverb"] == "none"


def test_absent_reverb_setting_leaves_new_reverb_unset():
    section = {"HRTF": True}

    migration.migrate(section)

    assert "reverb" not in section


def test_reverb_true_maps_to_small_room_and_discards_slider_values():
    slider_values = {
        "RoomSize": 17,
        "Damping": 23,
        "WetLevel": 41,
        "DryLevel": 59,
        "Width": 83,
    }
    section = {"Reverb": True, **slider_values}

    migration.migrate(section)

    assert section["reverb"] == "smallRoom"
    assert not (set(slider_values) & set(section))
    assert not (set(slider_values.values()) & set(section.values()))


def test_hrtf_and_volume_adjust_are_dropped_without_replacements():
    section = {"HRTF": False, "volumeAdjust": True}

    migration.migrate(section)

    assert section == {"roleAnnouncement": "sounds"}


@pytest.mark.parametrize(
    "section",
    [
        {},
        {
            "theme": "custom",
            "roleAnnouncement": "speechOnly",
            "reverb": "hall",
            "silenceDuringSayAll": True,
        },
    ],
)
def test_no_legacy_keys_is_a_complete_no_op(section):
    before = section.copy()

    migration.migrate(section)

    assert section == before


def test_migration_is_idempotent():
    section = {
        "theme": "custom",
        "noSounds": False,
        "speakRoles": True,
        "sayAll": True,
        "Reverb": False,
    }

    migration.migrate(section)
    after_first_migration = section.copy()
    migration.migrate(section)

    assert section == after_first_migration
    assert not (set(migration._OLD_KEYS) & set(section))


def test_all_legacy_keys_are_deleted_from_full_legacy_config():
    section = {
        "sayAll": True,
        "speakRoles": False,
        "noSounds": False,
        "HRTF": True,
        "volumeAdjust": True,
        "Reverb": True,
        "RoomSize": 10,
        "Damping": 100,
        "WetLevel": 9,
        "DryLevel": 30,
        "Width": 100,
    }

    migration.migrate(section)

    assert not (set(migration._OLD_KEYS) & set(section))
