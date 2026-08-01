"""The documented contract, checked against the code that implements it.

`scons` copies `README.md` into the shipped addon as the user guide, so the
claims it makes about settings, slots and version floors are part of what we
ship. This module pins the ones that are mechanically checkable, so that
changing the code without changing the guide fails here rather than in a user's
hands.

Deliberately narrow. It checks facts with exactly one right answer -- key
names, option values, slot names, a version number, whether a link resolves.
It does not check prose, because a docs test that fires on rewording gets
disabled within a month.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")
CONTEXT = (REPO / "CONTEXT.md").read_text(encoding="utf-8")
BUILD_VARS = (REPO / "buildVars.py").read_text(encoding="utf-8")
ADR_DIR = REPO / "docs" / "adr"


# --- the four settings -----------------------------------------------------


def test_readme_documents_every_setting_in_the_spec(migration):
    """Every key in CONF_SPEC has a bullet in the guide, under its panel label."""
    labels = {
        "theme": "Sound theme",
        "roleAnnouncement": "Role announcement",
        "reverb": "Reverb",
        "silenceDuringSayAll": "Silence role sounds during say all",
    }
    assert set(labels) == set(migration.CONF_SPEC), (
        "CONF_SPEC changed; update this table and the README bullets together"
    )
    for key, label in labels.items():
        assert f"**{label}**" in README, f"README has no bullet for {key!r} ({label})"


def test_readme_lists_the_real_reverb_presets(migration, player):
    """The guide names the presets; the player defines them."""
    documented = {"None", "Small room", "Medium room", "Hall"}
    spec_values = set(
        re.findall(r'"([^"]+)"', migration.CONF_SPEC["reverb"].split("default=")[0])
    )
    assert spec_values == set(player.REVERB_PRESETS)
    for label in documented:
        assert f"*{label}*" in README, f"README does not name the {label!r} preset"


def test_readme_role_announcement_choices_match_the_spec(migration):
    spec_values = set(
        re.findall(
            r'"([^"]+)"',
            migration.CONF_SPEC["roleAnnouncement"].split("default=")[0],
        )
    )
    assert spec_values == {"sounds", "soundsAndSpeech", "speechOnly"}


# --- sound themes ----------------------------------------------------------


def test_readme_slot_list_matches_the_code(themes):
    """A theme author following the guide must get every slot name right."""
    documented = set(re.findall(r"`([a-z]+)`", README.split("## Sound themes")[1]))
    missing = set(themes._SLOTS) - documented
    assert not missing, f"README's slot list is missing: {sorted(missing)}"


def test_readme_states_the_real_manifest_gain_clamp(themes):
    """The guide promises +/-12 dB; themes.py is where that number lives."""
    assert "±12" in README or "+/-12" in README
    source = (
        REPO / "addon" / "globalPlugins" / "Unspoken" / "themes.py"
    ).read_text(encoding="utf-8")
    assert "max(-12.0, min(12.0" in source, (
        "the manifest gain clamp moved; README still promises +/-12 dB"
    )


def test_the_bundled_theme_has_the_manifest_the_readme_describes():
    manifest = REPO / "addon" / "globalPlugins" / "Unspoken" / "sound-themes"
    manifest = manifest / "default" / "theme.ini"
    text = manifest.read_text(encoding="utf-8")
    assert "[theme]" in text and "name" in text


# --- version floor ---------------------------------------------------------


def test_readme_version_floor_matches_the_build_vars():
    # buildVars.py, not the generated addon/manifest.ini: the manifest is
    # build output and does not exist on a fresh checkout.
    minimum = re.search(
        r'"addon_minimum_nvda_version"\s*:\s*"([^"]+)"', BUILD_VARS
    ).group(1)
    assert f"NVDA {minimum} or later" in README, (
        f"buildVars requires {minimum}; README does not say so"
    )


# --- ADRs ------------------------------------------------------------------


def test_every_relative_adr_link_resolves():
    broken = []
    for adr in sorted(ADR_DIR.glob("*.md")):
        for target in re.findall(r"\]\((0\d{3}-[a-z0-9-]+\.md)\)", adr.read_text(encoding="utf-8")):
            if not (ADR_DIR / target).is_file():
                broken.append(f"{adr.name} -> {target}")
    assert not broken, f"broken ADR links: {broken}"


def test_adr_numbers_are_unique_and_contiguous():
    numbers = sorted(int(p.name[:4]) for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"ADR numbering has a gap or a duplicate: {numbers}"
    )


# --- CONTEXT.md ------------------------------------------------------------


def test_context_defines_a_term_for_every_vocabulary_entry_agents_md_names():
    """AGENTS.md points at CONTEXT.md for the pipeline vocabulary."""
    required = [
        "Role sound",
        "Sound Player",
        "Onset latency",
        "Sound theme",
        "Slot",
        "Ducking",
    ]
    for term in required:
        assert f"**{term}**" in CONTEXT, f"CONTEXT.md no longer defines {term!r}"


def test_context_entries_all_carry_an_avoid_line():
    """The Avoid list is the half that stops synonyms creeping back in."""
    terms = re.findall(r"^\*\*(.+?)\*\*:$", CONTEXT, re.MULTILINE)
    missing = [
        term
        for term in terms
        if f"**{term}**" in CONTEXT
        and "_Avoid_" not in CONTEXT.split(f"**{term}**:")[1].split("\n\n")[0]
    ]
    assert not missing, f"CONTEXT.md entries with no _Avoid_ line: {missing}"
    assert len(terms) >= 6


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def migration():
    return _addon_module("migration")


@pytest.fixture
def themes():
    return _addon_module("themes")


@pytest.fixture
def player():
    return _addon_module("player")


def _addon_module(name):
    import importlib.util
    import sys

    path = REPO / "addon" / "globalPlugins" / "Unspoken" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_docs_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
