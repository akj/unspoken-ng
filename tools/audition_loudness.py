"""Pick the theme reference loudness by ear (#40, spec section 7).

`themes._REFERENCE_RMS_DBFS` is the level every sound theme is normalised to as
a whole. It is currently -20.0 dBFS, marked provisional in the spec pending
this session. The number matters because the 1.x chain's effective output was
about -36.2 dBFS, so v2.0 ships role sounds roughly **+16 dB hotter than the
addon they replace** -- a jump the user did not ask for, on headphones.

People judge *relative* loudness well and *absolute* loudness badly, so this
does not ask "is -20 right?". It plays the real theme through the real Sound
Player at two candidate levels and asks which one you want, blind, as many
times as it takes.

Runs outside NVDA, against the addon's own `themes.py` and `player.py` and the
bundled `soft_oal.dll`. Standard library only -- no numpy, no NVDA.

    uv run tools/audition_loudness.py                 # A/B the candidates
    uv run tools/audition_loudness.py --mode tour     # one level, all slots
    uv run tools/audition_loudness.py --mode upgrade  # 1.x level vs the default

Standard library only -- no dependency group needed, unlike the soak next door.

Console-only and line-based on purpose: it has to be usable with a screen
reader, so there is no curses UI, no cursor positioning and no colour.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import random
import sys
import time
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "addon" / "globalPlugins" / "Unspoken"

#: The 1.x chain's measured effective output level, from the PR #46 review:
#: bundled assets pooled at -27.65 dBFS, +1.94 dB from `_compute_volume`,
#: -10.46 dB from the DryLevel 0.30 master source gain.
LEGACY_REFERENCE_DBFS = -36.2

#: What the session is choosing between. The shipped value is in here; so is
#: the 1.x level, because "quieter than you think" is a live answer.
DEFAULT_CANDIDATES = [-36.2, -32.0, -28.0, -24.0, -20.0]

#: Played in this order in a tour: loud/short, quiet/long, the two that serve
#: the most roles, and the one with 123 ms of dead lead.
TOUR_SLOTS = ["button", "link", "icon", "editabletext", "listitem", "slider"]

#: Fixed straight ahead. HRTF gain varies with angle, so a position that moved
#: between the two sides would confound the comparison being made.
POSITION = (0.0, 0.0, -1.0)


def _load(name: str):
    """Import a module out of the addon folder without importing NVDA."""
    spec = importlib.util.spec_from_file_location(name, ADDON / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def list_devices(player) -> list[str]:
    """Every output device OpenAL can see, by the exact name it wants back.

    `alcOpenDevice` takes the name verbatim with no fuzzy matching (ADR 0001),
    so guessing at a device string does not work -- this prints the ones that
    will actually open.
    """
    import ctypes

    dll_path = os.path.join(str(ADDON), "soft_oal.dll")
    al = player._bind(ctypes.CDLL(dll_path))
    pointer = al.alcGetString(None, player.ALC_ALL_DEVICES_SPECIFIER)
    if not pointer:
        return []
    # A null-separated list terminated by an empty string, so walk it rather
    # than taking string_at, which stops at the first device.
    names = []
    offset = 0
    while True:
        name = ctypes.string_at(pointer + offset)
        if not name:
            break
        names.append(name.decode("utf-8", "replace"))
        offset += len(name) + 1
    return names


class UnitySettings:
    """Unity listener gain, on the chosen device.

    The player folds `volume` straight into `alListenerf(AL_GAIN)`, so anything
    but 1.0 here would silently rescale the very thing being judged.
    """

    volume = 1.0

    def __init__(self, device: str = "default"):
        self.output_device = device


def theme_at(themes, theme_id: str, level_dbfs: float) -> dict:
    """Load `theme_id` normalised to `level_dbfs` instead of the shipped value."""
    original = themes._REFERENCE_RMS_DBFS
    themes._REFERENCE_RMS_DBFS = level_dbfs
    try:
        return themes.load(theme_id)
    finally:
        themes._REFERENCE_RMS_DBFS = original


def play_sequence(sound_player, sounds, slots, gap_s: float) -> None:
    for slot in slots:
        if slot not in sounds:
            continue
        sound_player.play(slot, POSITION)
        time.sleep(gap_s)


def ask(prompt: str, allowed: set[str]) -> str:
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return "q"
        if answer in allowed:
            return answer
        print(f"  Please type one of: {', '.join(sorted(allowed))}")


def run_ab(sound_player, themes, theme_id, candidates, gap_s, rng) -> None:
    """Blind pairwise comparison. Every unordered pair, sides shuffled."""
    loaded = {level: theme_at(themes, theme_id, level) for level in candidates}
    pairs = list(itertools.combinations(candidates, 2))
    rng.shuffle(pairs)
    wins: dict[float, int] = {level: 0 for level in candidates}
    judged = 0

    print(f"\n{len(pairs)} pairs to judge. Sides are shuffled and unlabelled.")
    print("  a / b  play that side      1 / 2  choose A / B")
    print("  =      no preference       q      stop and report\n")

    for index, pair in enumerate(pairs, start=1):
        sides = list(pair)
        rng.shuffle(sides)
        left, right = sides
        print(f"--- pair {index} of {len(pairs)} ---")
        while True:
            answer = ask("  a/b/1/2/=/q: ", {"a", "b", "1", "2", "=", "q"})
            if answer == "a":
                sound_player.set_theme(loaded[left])
                play_sequence(sound_player, loaded[left], TOUR_SLOTS, gap_s)
            elif answer == "b":
                sound_player.set_theme(loaded[right])
                play_sequence(sound_player, loaded[right], TOUR_SLOTS, gap_s)
            elif answer == "q":
                report(wins, judged)
                return
            else:
                if answer == "1":
                    wins[left] += 1
                elif answer == "2":
                    wins[right] += 1
                judged += 1
                print(f"      (that was A={left:+.1f} dBFS, B={right:+.1f} dBFS)\n")
                break

    report(wins, judged)


def report(wins: dict[float, int], judged: int) -> None:
    if not judged:
        print("\nNothing judged, nothing to report.")
        return
    print(f"\n=== {judged} comparison(s) judged ===")
    for level, score in sorted(wins.items(), key=lambda kv: (-kv[1], kv[0])):
        note = ""
        if abs(level - LEGACY_REFERENCE_DBFS) < 0.05:
            note = "   <- the 1.x level"
        print(f"  {level:+6.1f} dBFS   {score} win(s){note}")
    winner = max(wins.items(), key=lambda kv: kv[1])
    print(
        f"\nMost preferred: {winner[0]:+.1f} dBFS. If that is not "
        f"{-20.0:+.1f}, change `_REFERENCE_RMS_DBFS` in `themes.py`,"
    )
    print("and record the number and this session on issue #40.")


def run_tour(sound_player, themes, theme_id, level, gap_s) -> None:
    sounds = theme_at(themes, theme_id, level)
    print(f"\nTheme {theme_id!r} at {level:+.1f} dBFS. Enter replays, q quits.")
    print(f"Slots: {', '.join(s for s in TOUR_SLOTS if s in sounds)}\n")
    sound_player.set_theme(sounds)
    while True:
        play_sequence(sound_player, sounds, TOUR_SLOTS, gap_s)
        if ask("  enter=again, q=quit: ", {"", "q"}) == "q":
            return


def run_upgrade(sound_player, themes, theme_id, gap_s) -> None:
    """The upgrade-shock check: what a 1.x user hears before and after."""
    print("\nThis is the jump a 1.x user gets on upgrade, in both directions.")
    print("Judge whether the new level is acceptable unannounced, on headphones,")
    print("at a volume you would normally have set for the old addon.\n")
    old = theme_at(themes, theme_id, LEGACY_REFERENCE_DBFS)
    new = theme_at(themes, theme_id, -20.0)
    while True:
        print(f"  1.x  ({LEGACY_REFERENCE_DBFS:+.1f} dBFS)")
        sound_player.set_theme(old)
        play_sequence(sound_player, old, TOUR_SLOTS, gap_s)
        time.sleep(0.8)
        print(f"  2.0  ({-20.0:+.1f} dBFS)  -- {-20.0 - LEGACY_REFERENCE_DBFS:+.1f} dB")
        sound_player.set_theme(new)
        play_sequence(sound_player, new, TOUR_SLOTS, gap_s)
        if ask("\n  enter=again, q=quit: ", {"", "q"}) == "q":
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["ab", "tour", "upgrade"], default="ab")
    parser.add_argument("--theme", default="default")
    parser.add_argument("--level", type=float, default=-20.0, help="tour mode only")
    parser.add_argument(
        "--candidates",
        type=float,
        nargs="+",
        default=DEFAULT_CANDIDATES,
        help="dBFS levels to compare in ab mode",
    )
    parser.add_argument("--gap", type=float, default=0.55, help="seconds between sounds")
    parser.add_argument("--seed", type=int, default=None, help="fix the side shuffle")
    parser.add_argument("--reverb", default="smallRoom")
    parser.add_argument("--device", default="default",
                        help="OpenAL output device name; see --list-devices")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    themes = _load("themes")
    player = _load("player")

    if args.list_devices:
        for name in list_devices(player):
            print(name)
        return 0

    print("Before you start:")
    print("  - Headphones on, and set your system volume ONCE, now.")
    print("    Changing it mid-session invalidates every comparison after it.")
    print(f"  - Reverb is fixed at {args.reverb!r} and position at straight ahead,")
    print("    so neither can confound the level judgement.")
    print("  - Listener gain is unity: what you hear is the theme's own level.")

    sound_player = player.OpenALSoundPlayer(UnitySettings(args.device))
    try:
        sound_player.set_reverb(args.reverb)
        if args.mode == "tour":
            run_tour(sound_player, themes, args.theme, args.level, args.gap)
        elif args.mode == "upgrade":
            run_upgrade(sound_player, themes, args.theme, args.gap)
        else:
            run_ab(
                sound_player,
                themes,
                args.theme,
                sorted(set(args.candidates)),
                args.gap,
                random.Random(args.seed),
            )
    finally:
        sound_player.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
