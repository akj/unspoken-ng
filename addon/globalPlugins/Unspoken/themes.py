"""Discover and decode Unspoken sound themes without depending on NVDA."""

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
    created on first discovery. When IDs collide, the user folder is the sole
    candidate and therefore wins even if it later proves unusable.
    """

    try:
        candidates: dict[str, Path] = {}
        for path in _theme_directories(_BUNDLED_THEMES_DIR):
            candidates[path.name] = path

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
                    candidates[path.name] = path

        discovered = []
        for theme_id in sorted(candidates):
            path = candidates[theme_id]
            try:
                manifest = _read_manifest(path)
                if not _read_theme_wavs(path):
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
    """Load a sound theme, filling sparse slots from the bundled default."""

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
            log.info(
                "Sound theme %r has no usable %s slot; falling back to default",
                theme_id,
                slot,
            )
            if slot in default:
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
        parser = configparser.ConfigParser()
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            parser.read_file(manifest_file)
        if not parser.has_section("theme"):
            raise configparser.Error("missing [theme] section")

        section = parser["theme"]
        gain_db = float(section.get("gain", "0"))
        if not math.isfinite(gain_db):
            raise ValueError("gain must be finite")
        return _Manifest(
            name=section.get("name", theme_path.name).strip() or theme_path.name,
            author=section.get("author", "").strip() or None,
            description=section.get("description", "").strip() or None,
            gain_db=gain_db,
        )
    except Exception:
        log.warning(
            "Ignoring malformed sound theme manifest %s",
            manifest_path,
            exc_info=True,
        )
        return fallback


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
    value = sample[0] | (sample[1] << 8) | (sample[2] << 16)
    if value & 0x800000:
        value -= 1 << 24
    return value


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
    for slot, wav in decoded.items():
        bits = wav.sample_width * 8
        minimum = -(1 << (bits - 1))
        maximum = (1 << (bits - 1)) - 1
        # Scale in floating point and clamp only at final quantization, avoiding
        # intermediate integer overflow or repeated clipping.
        samples = [
            max(minimum, min(maximum, round(sample * gain_factor)))
            for sample in wav.samples
        ]
        processed[slot] = (
            _encode_samples(samples, wav.sample_width),
            wav.source_rate,
        )
    return processed


def _encode_samples(samples: list[int], sample_width: int) -> bytes:
    if sample_width == 2:
        return struct.pack(f"<{len(samples)}h", *samples)

    encoded = bytearray()
    for sample in samples:
        if sample < 0:
            sample += 1 << 24
        encoded.extend(
            (
                sample & 0xFF,
                (sample >> 8) & 0xFF,
                (sample >> 16) & 0xFF,
            )
        )
    return bytes(encoded)


def _load_default(default_path: Path | None) -> dict[str, tuple[bytes, int]]:
    if default_path is None:
        log.warning("The bundled default sound theme is unavailable")
        return {}
    return _load_processed_theme(default_path)
