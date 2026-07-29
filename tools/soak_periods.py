"""Soak the output buffer for dropouts, at `periods = 2` against `3` (#40).

ADR 0001 makes the buffer the one latency knob: `[general] periods = 2` gives a
22 ms buffer against 32 ms at the default of 3, and that 10 ms comes straight
off onset. #40 asks whether 2 actually holds on real hardware, and says to fall
back to 3 in the shipped default if it underruns.

An underrun is a gap in the output stream, and OpenAL Soft does not count them
-- the trace carries initialisation detail and nothing per-underrun -- so this
listens to the result instead. It plays a continuous noise **carrier** through
the real Sound Player, fires real role sounds over it for realistic voice
churn, records the system output back through WASAPI loopback, and looks for
collapses in the recorded envelope. A clean stream has none.

The carrier is noise rather than a tone on purpose: overlapping tone voices
interfere and can null out, which reads as a dropout that never happened.
Independent noise segments sum without nulls.

    uv run --group audio tools/soak_periods.py --self-test        # trust it first
    uv run --group audio tools/soak_periods.py --compare --minutes 5
    uv run --group audio tools/soak_periods.py --periods 2 --stall

Automated detection needs `numpy` and `soundcard`, which is what the `audio`
dependency group in `pyproject.toml` is for. Run it under a plain `python` with
neither installed and it degrades to a listening test and says so.

Run `--self-test` before believing a clean result: a soak that reports no
dropouts proves nothing if the detector cannot fire.

`periods` is read once per process by OpenAL Soft, so `--compare` runs each
config in its own subprocess rather than switching in place.

`--device` sends both playback and loopback capture somewhere other than your
ears -- `--list-devices` prints the names that will open. **Read the caveat
before trusting a result from a virtual cable:** buffer behaviour is a property
of the endpoint driver, so a soak on a virtual device measures that device, not
the headphones you actually use. Routing away is right for developing the rig
and wrong for answering #40. For the real answer, run it on the endpoint you
listen on.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import math
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDON = REPO / "addon" / "globalPlugins" / "Unspoken"

CARRIER_SLOT = "__soak_carrier__"
CARRIER_SECONDS = 2.0
CARRIER_REFIRE_SECONDS = 1.0
CARRIER_RATE = 44100
ROLE_INTERVAL_SECONDS = 0.6
RECORD_RATE = 48000

#: A dropout must be this quiet, for this long, to count. One WASAPI period is
#: 10 ms, so a real underrun is far longer than this; the floor is set well
#: below the carrier's own trough so ordinary envelope ripple cannot trip it.
DROPOUT_DB_BELOW = 20.0
DROPOUT_MIN_MS = 3.0
ENVELOPE_HOP_MS = 1.0
#: Trimmed from each end before analysis: stream start-up and tear-down are not
#: what this is asking about.
EDGE_TRIM_SECONDS = 1.5
#: Below this median carrier level the recording is silence, not a clean run.
#: The carrier is played at 0.25 amplitude, so anything near zero means the
#: loopback captured the wrong endpoint -- which would otherwise be reported as
#: "dropouts none", the most dangerous possible false pass.
NO_SIGNAL_LEVEL = 0.001


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_soak_{name}", ADDON / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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
    volume = 1.0

    def __init__(self, device: str = "default"):
        self.output_device = device


def make_carrier(seconds: float, rate: int, amplitude: float = 0.25) -> tuple[bytes, int]:
    """Deterministic white noise, with short fades so re-fires do not click."""
    rng = random.Random(20260729)
    count = int(seconds * rate)
    fade = int(0.01 * rate)
    samples = []
    for index in range(count):
        value = rng.uniform(-1.0, 1.0) * amplitude
        if index < fade:
            value *= index / fade
        elif index >= count - fade:
            value *= (count - index) / fade
        samples.append(max(-32768, min(32767, int(value * 32767))))
    return struct.pack(f"<{len(samples)}h", *samples), rate


# --- recording -------------------------------------------------------------


_OPENAL_PREFIX = "OpenAL Soft on "


def _strip_openal_prefix(name: str) -> str:
    """OpenAL's device name minus its own branding, as Windows spells it."""
    return name[len(_OPENAL_PREFIX):] if name.startswith(_OPENAL_PREFIX) else name


def _recorder_available() -> bool:
    try:
        import numpy  # noqa: F401
        import soundcard  # noqa: F401
    except Exception:
        return False
    return True


class LoopbackRecorder:
    """Records the default speaker's own output, in a background thread."""

    def __init__(self, device: str = "default"):
        self.device = device
        self.captured_from = None
        self.frames = None
        self._stop = threading.Event()
        self._thread = None
        self._error = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            import numpy as np
            import soundcard as sc

            # Loopback must follow playback. Capturing a different endpoint
            # than the one being played to records silence, and silence looks
            # exactly like a perfectly clean soak -- see the no-signal guard in
            # describe().
            if self.device == "default":
                speaker = sc.default_speaker()
            else:
                # OpenAL reports "OpenAL Soft on Line 1 (Virtual Audio Cable)";
                # Windows, and therefore soundcard, calls the same endpoint
                # "Line 1 (Virtual Audio Cable)". One name, two spellings.
                speaker = sc.get_speaker(_strip_openal_prefix(self.device))
            self.captured_from = str(speaker.name)
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            chunks = []
            with mic.recorder(samplerate=RECORD_RATE, channels=2) as rec:
                while not self._stop.is_set():
                    chunks.append(rec.record(numframes=RECORD_RATE // 10))
            self.frames = np.concatenate(chunks, axis=0) if chunks else None
        except Exception as exc:  # pragma: no cover - hardware dependent
            self._error = exc

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        if self._error:
            raise self._error
        return self.frames


def find_dropouts(frames) -> tuple[list[tuple[float, float]], float]:
    """Return [(start_s, duration_ms)] plus the steady envelope level."""
    import numpy as np

    mono = np.abs(frames).max(axis=1) if frames.ndim > 1 else np.abs(frames)
    hop = max(1, int(RECORD_RATE * ENVELOPE_HOP_MS / 1000.0))
    usable = (len(mono) // hop) * hop
    envelope = mono[:usable].reshape(-1, hop).max(axis=1)

    trim = int(EDGE_TRIM_SECONDS * 1000.0 / ENVELOPE_HOP_MS)
    if len(envelope) <= 2 * trim + 10:
        return [], 0.0
    core = envelope[trim:-trim]

    steady = float(np.median(core))
    if steady <= 0.0:
        return [], 0.0
    floor = steady * (10.0 ** (-DROPOUT_DB_BELOW / 20.0))

    dropouts = []
    minimum_blocks = max(1, int(DROPOUT_MIN_MS / ENVELOPE_HOP_MS))
    index = 0
    below = core < floor
    while index < len(below):
        if not below[index]:
            index += 1
            continue
        start = index
        while index < len(below) and below[index]:
            index += 1
        length = index - start
        if length >= minimum_blocks:
            dropouts.append(
                (
                    (trim + start) * ENVELOPE_HOP_MS / 1000.0,
                    length * ENVELOPE_HOP_MS,
                )
            )
    return dropouts, steady


def self_test() -> int:
    """Prove the detector can see a gap before trusting it to report none.

    A soak that reports "dropouts none" is worthless if the detector cannot
    fire at all, so this synthesises a carrier, punches known gaps into it, and
    checks they come back at the right offsets with the right lengths.
    """
    import numpy as np

    rng = np.random.default_rng(20260729)
    seconds = 12.0
    frames = rng.uniform(-0.25, 0.25, size=(int(RECORD_RATE * seconds), 2))

    planted = [(4.0, 15.0), (7.5, 6.0), (9.0, 40.0)]
    for at, length in planted:
        start = int(at * RECORD_RATE)
        frames[start : start + int(length / 1000.0 * RECORD_RATE)] = 0.0

    found, steady = find_dropouts(frames)
    print(f"planted {len(planted)}, found {len(found)} (steady {steady:.3f})")

    failures = []
    for (want_at, want_ms) in planted:
        near = [f for f in found if abs(f[0] - want_at) < 0.05]
        if not near:
            failures.append(f"missed the {want_ms:.0f} ms gap at {want_at:.1f} s")
        elif abs(near[0][1] - want_ms) > 2.0:
            failures.append(
                f"gap at {want_at:.1f} s measured {near[0][1]:.0f} ms, expected {want_ms:.0f}"
            )
    if len(found) > len(planted):
        failures.append(f"{len(found) - len(planted)} false positive(s) on clean noise")

    clean, _ = find_dropouts(rng.uniform(-0.25, 0.25, size=(int(RECORD_RATE * 12), 2)))
    if clean:
        failures.append(f"{len(clean)} dropout(s) reported on an unbroken carrier")

    # A gap shorter than DROPOUT_MIN_MS must not register.
    short = rng.uniform(-0.25, 0.25, size=(int(RECORD_RATE * 12), 2))
    start = int(5.0 * RECORD_RATE)
    short[start : start + int(1.0 / 1000.0 * RECORD_RATE)] = 0.0
    if find_dropouts(short)[0]:
        failures.append("a 1 ms gap registered, below the 3 ms floor")

    for line in failures:
        print(f"  FAIL  {line}")
    print("self-test passed" if not failures else "SELF-TEST FAILED")
    return 1 if failures else 0


# --- the soak itself -------------------------------------------------------


#: `Post-reset: 48000hz, 480 / 1056 buffer` -- rate, period frames, buffer
#: frames. The only place the settled buffer is visible, per probe_periods.py.
POST_RESET = re.compile(r"Post-reset: .*?(\d+)hz, (\d+) / (\d+) buffer")


def soak(periods: int, minutes: float, stall: bool, record: bool,
         device: str = "default") -> dict:
    # Setting ALSOFT_CONF here would be pointless: `player._write_alsoft_conf`
    # writes its own config and overwrites the variable, hardcoding periods = 2
    # (and logging a warning that this is what just happened). So patch the
    # body it writes, which keeps the whole real construction path intact.
    trace_path = Path(tempfile.gettempdir()) / f"alsoft-soak-{periods}.log"
    trace_path.unlink(missing_ok=True)
    os.environ["ALSOFT_LOGLEVEL"] = "3"
    os.environ["ALSOFT_LOGFILE"] = str(trace_path)

    themes = _load("themes")
    player = _load("player")
    player.ALSOFT_CONF_BODY = f"[general]\nperiods = {periods}\n"

    sounds = themes.load("default")
    sounds[CARRIER_SLOT] = make_carrier(CARRIER_SECONDS, CARRIER_RATE)
    role_slots = [s for s in sorted(sounds) if s != CARRIER_SLOT]

    recorder = None
    if record:
        recorder = LoopbackRecorder(device)
        recorder.start()
        time.sleep(1.0)

    sound_player = player.OpenALSoundPlayer(UnitySettings(device))
    started = time.monotonic()
    deadline = started + minutes * 60.0
    next_carrier = started
    next_role = started + 0.25
    next_stall = started + 5.0
    roles = itertools.cycle(role_slots)
    fired = 0

    try:
        sound_player.set_theme(sounds)
        sound_player.set_reverb("smallRoom")
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if now >= next_carrier:
                sound_player.play(CARRIER_SLOT, (0.0, 0.0, -1.0))
                next_carrier += CARRIER_REFIRE_SECONDS
            if now >= next_role:
                angle = math.sin(fired) * 0.9
                sound_player.play(next(roles), (angle, 0.0, -math.sqrt(1 - angle**2)))
                fired += 1
                next_role += ROLE_INTERVAL_SECONDS
            if stall and now >= next_stall:
                # ADR 0001 claims the owned mixer runs on OpenAL's own C thread
                # and cannot be starved by the caller. This is that claim under
                # test: block hard, then look for a dropout at this timestamp.
                time.sleep(0)
                busy_until = time.monotonic() + 0.25
                while time.monotonic() < busy_until:
                    pass
                next_stall += 5.0
            time.sleep(0.005)
    finally:
        time.sleep(CARRIER_SECONDS)
        sound_player.close()

    elapsed = time.monotonic() - started
    result = {"periods": periods, "seconds": elapsed, "sounds": fired}

    # Read the buffer the backend actually settled on. Without this the tool
    # could A/B nothing at all and report two clean runs -- which is exactly
    # what happened before the ALSOFT_CONF ownership above was understood.
    try:
        for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = POST_RESET.search(line)
            if match:
                result["rate"] = int(match.group(1))
                result["period_frames"] = int(match.group(2))
                result["buffer_frames"] = int(match.group(3))
    except OSError:
        pass

    if recorder:
        frames = recorder.stop()
        if frames is None:
            result["error"] = "recorder produced nothing"
            return result
        dropouts, steady = find_dropouts(frames)
        result["dropouts"] = dropouts
        result["steady"] = steady
        result["recorded_seconds"] = len(frames) / RECORD_RATE
        result["captured_from"] = recorder.captured_from
        result["device"] = device
    return result


def describe(result: dict) -> None:
    periods = result["periods"]
    print(f"\n=== periods = {periods} ===")
    print(f"  soak            {result['seconds']:.0f} s, {result['sounds']} role sounds")
    if "buffer_frames" in result:
        rate = result["rate"]
        print(
            f"  settled buffer  {result['buffer_frames']} frames "
            f"({result['buffer_frames'] / rate * 1000.0:.1f} ms) at {rate} Hz, "
            f"period {result['period_frames']}"
        )
    else:
        print("  settled buffer  UNKNOWN -- trace not parsed; do not trust this run")
    if "error" in result:
        print(f"  ERROR           {result['error']}")
        return
    if "dropouts" not in result:
        print("  detection       not run (listening test only)")
        return
    dropouts = result["dropouts"]
    print(f"  recorded        {result['recorded_seconds']:.0f} s")
    if result.get("captured_from"):
        print(f"  captured from   {result['captured_from']!r}")
    if result.get("steady", 0.0) < NO_SIGNAL_LEVEL:
        print(f"  NO SIGNAL       carrier level {result.get('steady', 0.0):.5f} is below")
        print(f"                  {NO_SIGNAL_LEVEL} -- nothing was recorded, so this run")
        print("                  says NOTHING about dropouts. Usual cause: the")
        print("                  loopback captured a different endpoint than the")
        print("                  one played to. Check --device against --list-devices.")
        return
    if not dropouts:
        print("  dropouts        none")
        return
    worst = max(d for _, d in dropouts)
    total = sum(d for _, d in dropouts)
    print(f"  dropouts        {len(dropouts)}  (worst {worst:.0f} ms, {total:.0f} ms total)")
    for at, length in dropouts[:10]:
        print(f"                  {at:7.1f} s  {length:5.0f} ms")
    if len(dropouts) > 10:
        print(f"                  ... and {len(dropouts) - 10} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--periods", type=int, default=2)
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--compare", action="store_true", help="run 2 and 3 in turn")
    parser.add_argument("--stall", action="store_true", help="block the caller periodically")
    parser.add_argument("--no-record", action="store_true", help="listening test only")
    parser.add_argument("--device", default="default",
                        help="OpenAL output device name; see --list-devices")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--self-test", action="store_true",
                        help="check the dropout detector against planted gaps")
    parser.add_argument("--_child", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.list_devices:
        for name in list_devices(_load("player")):
            print(name)
        return 0

    if args.self_test:
        if not _recorder_available():
            print("self-test needs numpy; see the module docstring for the interpreter.")
            return 2
        return self_test()

    record = not args.no_record and _recorder_available()
    if not args.no_record and not record:
        print("numpy/soundcard unavailable -- running as a listening test.")
        print("Listen for gaps or clicks in the noise bed; a clean stream is steady.")

    if args.compare and args._child is None:
        results = []
        for periods in (2, 3):
            print(f"\n--- spawning periods = {periods} "
                  f"(OpenAL reads its config once per process) ---")
            proc = subprocess.run(
                [sys.executable, __file__, "--_child", str(periods),
                 "--periods", str(periods), "--minutes", str(args.minutes),
                 "--device", args.device]
                + (["--stall"] if args.stall else [])
                + (["--no-record"] if args.no_record else []),
            )
            results.append(proc.returncode)
        print("\nBoth configs done. Compare the dropout lines above.")
        print("#40's rule: if periods = 2 shows dropouts and 3 does not, ship 3.")
        return 0 if all(code == 0 for code in results) else 1

    describe(soak(args.periods, args.minutes, args.stall, record, args.device))
    return 0


if __name__ == "__main__":
    sys.exit(main())
