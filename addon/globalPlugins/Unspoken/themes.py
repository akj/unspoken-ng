"""Discover and decode Unspoken sound themes without depending on NVDA.

`load()` returns what crosses the Sound Player seam: `slot -> (frames,
source_rate)`, where **frames are always mono 16-bit little-endian PCM** and
`source_rate` is whatever the file really was (spec §4.3 -- resampling happens
below the seam, per source, in OpenAL).

The width is part of the seam, not a detail of this module. Core OpenAL has no
24-bit buffer format, so 24-bit frames handed across would be uploaded as
`AL_FORMAT_MONO16` and rendered as full-scale broadband noise -- with no error
reported by anything, into the headphones of a user who cannot see what
happened. Assets may be 16- or 24-bit (spec §7); this module is where that
stops being true.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import struct
import wave


log = logging.getLogger(__name__)

_SLOTS = (
    "button",
    "checkbox",
    "clock",
    "combobox",
    "editabletext",
    "icon",
    "link",
    "listitem",
    "menuitem",
    "radiobutton",
    "slider",
    "splitbutton",
    "tab",
    "treeviewitem",
)
_REFERENCE_RMS_DBFS = -20.0
#: The seam's PCM width: mono 16-bit little-endian, whatever the asset was.
_OUTPUT_SAMPLE_WIDTH = 2
_OUTPUT_FULL_SCALE = float(1 << (_OUTPUT_SAMPLE_WIDTH * 8 - 1))
_OUTPUT_MINIMUM = -(1 << (_OUTPUT_SAMPLE_WIDTH * 8 - 1))
_OUTPUT_MAXIMUM = (1 << (_OUTPUT_SAMPLE_WIDTH * 8 - 1)) - 1
_BUNDLED_THEMES_DIR = Path(__file__).resolve().parent / "sound-themes"
_user_themes_dir: Path | None = None


@dataclass
class ThemeInfo:
    id: str
    name: str
    path: Path
    author: str | None = None
    description: str | None = None


@dataclass
class _Manifest:
    name: str
    author: str | None = None
    description: str | None = None
    gain_db: float = 0.0


@dataclass
class _DecodedWav:
    samples: list[int]
    sample_width: int
    source_rate: int


def set_user_themes_dir(path: str | os.PathLike[str] | None) -> None:
    """Configure the full user ``unspoken-ng/sound-themes`` directory.

    This is the module's NVDA-free injection point: the later NVDA wiring can
    derive the directory from NVDA's user config path and pass it here. Passing
    ``None`` disables user themes. User themes override bundled themes with the
    same folder name.
    """

    global _user_themes_dir
    _user_themes_dir = None if path is None else Path(path)


def get_user_themes_dir() -> Path | None:
    """Return the currently configured user sound-themes directory."""

    return _user_themes_dir


def discover() -> list[ThemeInfo]:
    """Return usable bundled and configured user themes, sorted by ID.

    Discovery is best-effort and never raises. A configured user directory is
    created on first discovery. When IDs collide, a usable user folder wins;
    otherwise discovery falls back to the bundled folder with the same ID.
    """

    try:
        candidates: dict[str, list[Path]] = {}
        for path in _theme_directories(_BUNDLED_THEMES_DIR):
            candidates[path.name] = [path]

        user_dir = _user_themes_dir
        if user_dir is not None:
            try:
                user_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                log.warning(
                    "Could not create user sound-themes directory %s",
                    user_dir,
                    exc_info=True,
                )
            else:
                for path in _theme_directories(user_dir):
                    candidates.setdefault(path.name, []).insert(0, path)

        discovered = []
        for theme_id in sorted(candidates):
            for path in candidates[theme_id]:
                try:
                    manifest = _read_manifest(path)
                    if not _theme_has_usable_slot(path):
                        continue
                    discovered.append(
                        ThemeInfo(
                            id=theme_id,
                            name=manifest.name,
                            path=path,
                            author=manifest.author,
                            description=manifest.description,
                        )
                    )
                    break
                except Exception:
                    log.warning(
                        "Skipping malformed sound theme folder %s",
                        path,
                        exc_info=True,
                    )
        return discovered
    except Exception:
        log.warning("Sound theme discovery failed", exc_info=True)
        return []


def load(theme_id: str) -> dict[str, tuple[bytes, int]]:
    """Load a sound theme, filling sparse slots from the bundled default.

    Returns `slot -> (mono 16-bit little-endian PCM frames, source_rate)` --
    the seam's format for every slot, whatever width the asset was authored at.
    """

    try:
        requested_path = _find_requested_theme(theme_id)
        default_path = _find_theme(_BUNDLED_THEMES_DIR, "default")

        if requested_path is None:
            log.warning(
                "Sound theme %r was not found; using the bundled default",
                theme_id,
            )
            return _load_default(default_path)

        requested = _load_processed_theme(requested_path)
        if requested_path == default_path:
            return requested

        default = _load_default(default_path)
        merged = dict(requested)
        for slot in _SLOTS:
            if slot in requested:
                continue
            if slot in default:
                log.info(
                    "Sound theme %r has no usable %s slot; falling back to default",
                    theme_id,
                    slot,
                )
                merged[slot] = default[slot]
        return merged
    except Exception:
        log.warning("Could not load sound theme %r", theme_id, exc_info=True)
        try:
            return _load_default(_find_theme(_BUNDLED_THEMES_DIR, "default"))
        except Exception:
            log.warning("Could not recover with the bundled default", exc_info=True)
            return {}


def _theme_directories(root: Path) -> list[Path]:
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return []
    except Exception:
        log.warning("Could not inspect sound-themes directory %s", root, exc_info=True)
        return []

    directories = []
    for entry in entries:
        try:
            if entry.is_dir():
                directories.append(entry)
        except Exception:
            log.warning("Could not inspect sound theme candidate %s", entry, exc_info=True)
    return directories


def _find_theme(root: Path | None, theme_id: str) -> Path | None:
    if root is None:
        return None
    for path in _theme_directories(root):
        if path.name == theme_id:
            return path
    return None


def _find_requested_theme(theme_id: str) -> Path | None:
    user_theme = _find_theme(_user_themes_dir, theme_id)
    if user_theme is not None:
        return user_theme
    return _find_theme(_BUNDLED_THEMES_DIR, theme_id)


def _read_manifest(theme_path: Path) -> _Manifest:
    fallback = _Manifest(name=theme_path.name)
    manifest_path = theme_path / "theme.ini"

    try:
        if not manifest_path.is_file():
            return fallback
        parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            parser.read_file(manifest_file)
        if not parser.has_section("theme"):
            raise configparser.Error("missing [theme] section")
    except Exception:
        log.warning(
            "Ignoring malformed sound theme manifest %s",
            manifest_path,
            exc_info=True,
        )
        return fallback

    section = parser["theme"]

    def read_text(key: str, default: str) -> str:
        try:
            return section.get(key, default).strip()
        except Exception:
            log.warning(
                "Ignoring invalid %s field in sound theme manifest %s",
                key,
                manifest_path,
                exc_info=True,
            )
            return default

    try:
        gain_db = float(section.get("gain", "0"))
        if not math.isfinite(gain_db):
            raise ValueError("gain must be finite")
    except Exception:
        log.warning(
            "Ignoring invalid gain field in sound theme manifest %s",
            manifest_path,
            exc_info=True,
        )
        gain_db = 0.0

    return _Manifest(
        name=read_text("name", theme_path.name) or theme_path.name,
        author=read_text("author", "") or None,
        description=read_text("description", "") or None,
        gain_db=gain_db,
    )


def _theme_has_usable_slot(theme_path: Path) -> bool:
    for slot in _SLOTS:
        wav_path = theme_path / f"{slot}.wav"
        try:
            exists = wav_path.is_file()
        except Exception:
            log.warning("Could not inspect sound theme file %s", wav_path, exc_info=True)
            continue
        if exists and _has_usable_wav_header(wav_path):
            return True
    return False


def _has_usable_wav_header(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            source_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if wav_file.getcomptype() != "NONE":
                raise ValueError("compressed WAV data is not supported")
            if channels not in (1, 2):
                raise ValueError(f"unsupported channel count: {channels}")
            if sample_width not in (2, 3):
                raise ValueError(f"unsupported sample width: {sample_width}")
            if source_rate <= 0:
                raise ValueError(f"invalid sample rate: {source_rate}")
            if frame_count <= 0:
                raise ValueError("WAV contains no audio frames")
        return True
    except Exception:
        log.warning("Rejecting malformed sound theme WAV %s", path, exc_info=True)
        return False


def _read_theme_wavs(theme_path: Path) -> dict[str, _DecodedWav]:
    decoded = {}
    for slot in _SLOTS:
        wav_path = theme_path / f"{slot}.wav"
        try:
            exists = wav_path.is_file()
        except Exception:
            log.warning("Could not inspect sound theme file %s", wav_path, exc_info=True)
            continue
        if not exists:
            continue
        wav = _decode_wav(wav_path)
        if wav is not None:
            decoded[slot] = wav
    return decoded


def _decode_wav(path: Path) -> _DecodedWav | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            source_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if wav_file.getcomptype() != "NONE":
                raise ValueError("compressed WAV data is not supported")
            if channels not in (1, 2):
                raise ValueError(f"unsupported channel count: {channels}")
            if sample_width not in (2, 3):
                raise ValueError(f"unsupported sample width: {sample_width}")
            if source_rate <= 0:
                raise ValueError(f"invalid sample rate: {source_rate}")

            frames = wav_file.readframes(frame_count)

        expected_size = frame_count * channels * sample_width
        if len(frames) != expected_size:
            raise ValueError(
                f"truncated WAV data: expected {expected_size} bytes, got {len(frames)}"
            )
        if not frames:
            raise ValueError("WAV contains no audio frames")

        if sample_width == 2:
            samples = [sample[0] for sample in struct.iter_unpack("<h", frames)]
        else:
            samples = [
                _decode_24_bit(frames[offset : offset + 3])
                for offset in range(0, len(frames), 3)
            ]

        if channels == 2:
            samples = [
                _average_samples(samples[index], samples[index + 1])
                for index in range(0, len(samples), 2)
            ]

        return _DecodedWav(samples, sample_width, source_rate)
    except Exception:
        log.warning("Rejecting malformed sound theme WAV %s", path, exc_info=True)
        return None


def _decode_24_bit(sample: bytes) -> int:
    return int.from_bytes(sample, byteorder="little", signed=True)


def _average_samples(left: int, right: int) -> int:
    # Python integers cannot overflow; round .5 ties to the nearest even value
    # so positive and negative stereo pairs receive symmetric treatment.
    return round((left + right) / 2)


def _load_processed_theme(theme_path: Path) -> dict[str, tuple[bytes, int]]:
    try:
        manifest = _read_manifest(theme_path)
        decoded = _read_theme_wavs(theme_path)
        return _process_theme(decoded, manifest.gain_db, theme_path.name)
    except Exception:
        log.warning("Could not process sound theme %s", theme_path, exc_info=True)
        return {}


def _process_theme(
    decoded: dict[str, _DecodedWav],
    manifest_gain_db: float,
    theme_id: str,
) -> dict[str, tuple[bytes, int]]:
    if not decoded:
        return {}

    square_sum = 0.0
    sample_count = 0
    for wav in decoded.values():
        full_scale = float(1 << (wav.sample_width * 8 - 1))
        square_sum += math.fsum(
            (sample / full_scale) ** 2 for sample in wav.samples
        )
        sample_count += len(wav.samples)

    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    if rms:
        reference_rms = 10.0 ** (_REFERENCE_RMS_DBFS / 20.0)
        normalization_factor = reference_rms / rms
        clamped_gain_db = max(-12.0, min(12.0, manifest_gain_db))
        if clamped_gain_db != manifest_gain_db:
            log.info(
                "Clamped sound theme %r manifest gain from %s dB to %s dB",
                theme_id,
                manifest_gain_db,
                clamped_gain_db,
            )
        gain_factor = normalization_factor * (10.0 ** (clamped_gain_db / 20.0))
    else:
        log.info("Sound theme %r is silent; skipping RMS normalization", theme_id)
        gain_factor = 1.0

    processed = {}
    clipped_sample_count = 0
    peak_overshoot_ratio = 1.0
    for slot, wav in decoded.items():
        source_full_scale = float(1 << (wav.sample_width * 8 - 1))
        # One conversion, at the end: the theme gain and the width change are a
        # single floating-point scale, so a 24-bit asset keeps its full
        # resolution through the RMS pass above and is quantized exactly once.
        # Clamping happens only here, so there is no intermediate overflow and
        # nothing clips twice.
        scale = gain_factor * _OUTPUT_FULL_SCALE / source_full_scale
        samples = []
        for sample in wav.samples:
            scaled = sample * scale
            if scaled < _OUTPUT_MINIMUM or scaled > _OUTPUT_MAXIMUM:
                clipped_sample_count += 1
                peak_overshoot_ratio = max(
                    peak_overshoot_ratio,
                    abs(scaled) / _OUTPUT_FULL_SCALE,
                )
            samples.append(max(_OUTPUT_MINIMUM, min(_OUTPUT_MAXIMUM, round(scaled))))
        processed[slot] = (_encode_samples(samples), wav.source_rate)
    if clipped_sample_count:
        peak_overshoot_db = 20.0 * math.log10(peak_overshoot_ratio)
        log.warning(
            "Sound theme %r clipped %d samples; peak overshoot %.2f dB",
            theme_id,
            clipped_sample_count,
            peak_overshoot_db,
        )
    return processed


def _encode_samples(samples: list[int]) -> bytes:
    """Pack into the seam's one format: mono 16-bit little-endian PCM."""

    return struct.pack(f"<{len(samples)}h", *samples)


def _load_default(default_path: Path | None) -> dict[str, tuple[bytes, int]]:
    if default_path is None:
        log.warning("The bundled default sound theme is unavailable")
        return {}
    return _load_processed_theme(default_path)
