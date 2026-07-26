import logging
import math
from pathlib import Path
import struct
import wave

import pytest

import themes


def _encode_24_bit(samples):
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


def _write_wav(path, samples, *, channels=1, sample_width=2, rate=22050):
    path.parent.mkdir(parents=True, exist_ok=True)
    if sample_width == 2:
        frames = struct.pack(f"<{len(samples)}h", *samples)
    elif sample_width == 3:
        frames = _encode_24_bit(samples)
    else:
        frames = bytes(samples)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(rate)
        wav_file.writeframes(frames)


def _decode_pcm(frames, sample_width):
    if sample_width == 2:
        return [sample[0] for sample in struct.iter_unpack("<h", frames)]

    samples = []
    for offset in range(0, len(frames), 3):
        chunk = frames[offset : offset + 3]
        value = chunk[0] | (chunk[1] << 8) | (chunk[2] << 16)
        if value & 0x800000:
            value -= 1 << 24
        samples.append(value)
    return samples


def _rms_dbfs(samples, sample_width):
    full_scale = 1 << (sample_width * 8 - 1)
    rms = math.sqrt(sum((sample / full_scale) ** 2 for sample in samples) / len(samples))
    return 20 * math.log10(rms)


@pytest.fixture(autouse=True)
def reset_user_themes_dir():
    themes.set_user_themes_dir(None)
    yield
    themes.set_user_themes_dir(None)


@pytest.fixture
def theme_roots(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled" / "sound-themes"
    user = tmp_path / "user" / "unspoken-ng" / "sound-themes"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(themes, "_BUNDLED_THEMES_DIR", bundled)
    themes.set_user_themes_dir(user)
    yield bundled, user
    themes.set_user_themes_dir(None)


def test_sparse_theme_merges_over_bundled_default(theme_roots):
    bundled, user = theme_roots
    default = bundled / "default"
    sparse = user / "sparse"
    _write_wav(default / "button.wav", [-1000, 1000])
    _write_wav(default / "link.wav", [-2000, 2000])
    _write_wav(sparse / "button.wav", [-3000, 3000])

    default_result = themes.load("default")
    sparse_result = themes.load("sparse")

    assert set(sparse_result) == {"button", "link"}
    assert sparse_result["link"] == default_result["link"]
    assert sparse_result["button"] != default_result["button"]


def test_stereo_is_downmixed_to_mono_before_normalization(theme_roots):
    _, user = theme_roots
    # Interleaved frames average to [2000, 0, -2000].
    _write_wav(
        user / "stereo" / "button.wav",
        [1000, 3000, 1000, -1000, -3000, -1000],
        channels=2,
        rate=44100,
    )

    frames, source_rate = themes.load("stereo")["button"]
    samples = _decode_pcm(frames, 2)

    assert source_rate == 44100
    assert len(samples) == 3
    assert samples[1] == 0
    assert samples[0] == -samples[2]
    assert _rms_dbfs(samples, 2) == pytest.approx(-20.0, abs=0.01)


@pytest.mark.parametrize(
    ("manifest_gain", "effective_gain"),
    [
        (6.0, 6.0),
        (20.0, 12.0),
        (-50.0, -12.0),
    ],
)
def test_rms_normalization_and_manifest_gain_clamp(
    theme_roots,
    manifest_gain,
    effective_gain,
):
    _, user = theme_roots
    theme = user / f"gain-{manifest_gain}"
    _write_wav(theme / "button.wav", [-1000, 1000] * 8)
    (theme / "theme.ini").write_text(
        f"[theme]\ngain = {manifest_gain}\n",
        encoding="utf-8",
    )

    frames, _ = themes.load(theme.name)["button"]
    samples = _decode_pcm(frames, 2)

    assert _rms_dbfs(samples, 2) == pytest.approx(
        -20.0 + effective_gain,
        abs=0.01,
    )


def test_manifest_gain_allows_trailing_inline_comment(theme_roots):
    _, user = theme_roots
    theme = user / "inline-comment"
    _write_wav(theme / "button.wav", [-1000, 1000] * 8)
    (theme / "theme.ini").write_text(
        "[theme]\n"
        "name = Inline Comment\n"
        "gain = -2.5        ; optional dB offset, applied after auto normalization\n",
        encoding="utf-8",
    )

    assert themes._read_manifest(theme).gain_db == -2.5

    frames, _ = themes.load(theme.name)["button"]
    samples = _decode_pcm(frames, 2)
    assert _rms_dbfs(samples, 2) == pytest.approx(-22.5, abs=0.01)


def test_malformed_wavs_are_rejected_and_fall_back(theme_roots):
    bundled, user = theme_roots
    default = bundled / "default"
    broken = user / "broken"
    _write_wav(default / "button.wav", [-1200, 1200])
    _write_wav(default / "link.wav", [-2400, 2400])
    _write_wav(broken / "icon.wav", [-500, 500])
    _write_wav(broken / "button.wav", [128, 128], sample_width=1)
    (broken / "link.wav").write_bytes(b"not a wav")

    default_result = themes.load("default")
    broken_result = themes.load("broken")

    assert broken_result["button"] == default_result["button"]
    assert broken_result["link"] == default_result["link"]
    assert "icon" in broken_result
    assert "broken" in {info.id for info in themes.discover()}


@pytest.mark.parametrize("bad_gain", ["loud", "nan"])
def test_bad_manifest_gain_preserves_valid_metadata(theme_roots, bad_gain):
    _, user = theme_roots
    theme = user / f"bad-manifest-{bad_gain}"
    _write_wav(theme / "button.wav", [-1000, 1000])
    (theme / "theme.ini").write_text(
        "[theme]\n"
        "name = Preserved Name\n"
        "author = Somebody\n"
        "description = Still valid\n"
        f"gain = {bad_gain}\n",
        encoding="utf-8",
    )

    info = next(info for info in themes.discover() if info.id == theme.name)
    result = themes.load(theme.name)

    assert info.name == "Preserved Name"
    assert info.author == "Somebody"
    assert info.description == "Still valid"
    assert "button" in result
    samples = _decode_pcm(result["button"][0], 2)
    assert _rms_dbfs(samples, 2) == pytest.approx(-20.0, abs=0.01)


def test_structurally_bad_manifest_falls_back_to_folder_metadata(theme_roots):
    _, user = theme_roots
    theme = user / "broken-ini"
    _write_wav(theme / "button.wav", [-1000, 1000])
    (theme / "theme.ini").write_text(
        "[theme\nname = Should Not Be Used\n",
        encoding="utf-8",
    )

    info = next(info for info in themes.discover() if info.id == theme.name)

    assert info.name == theme.name
    assert info.author is None
    assert info.description is None


def test_discover_creates_user_dir_drops_empty_and_prefers_user(theme_roots):
    bundled, user = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    _write_wav(bundled / "shared" / "button.wav", [-1000, 1000])

    assert not user.exists()
    first_result = themes.discover()
    assert user.is_dir()
    assert {info.id for info in first_result} == {"default", "shared"}

    _write_wav(user / "normal" / "link.wav", [-1000, 1000])
    _write_wav(user / "shared" / "button.wav", [-1000, 1000])
    (user / "shared" / "theme.ini").write_text(
        "[theme]\nname = User Shared\n",
        encoding="utf-8",
    )
    (user / "empty").mkdir()
    (user / "empty" / "theme.ini").write_text(
        "[theme]\nname = Empty\n",
        encoding="utf-8",
    )

    result = {info.id: info for info in themes.discover()}

    assert set(result) == {"default", "normal", "shared"}
    assert result["shared"].name == "User Shared"
    assert result["shared"].path == user / "shared"


def test_discover_uses_bundled_collision_when_user_theme_is_unusable(theme_roots):
    bundled, user = theme_roots
    bundled_default = bundled / "default"
    _write_wav(bundled_default / "button.wav", [-1000, 1000])
    (user / "default").mkdir(parents=True)

    result = {info.id: info for info in themes.discover()}

    assert result["default"].path == bundled_default
    assert "button" in themes.load("default")


def test_discover_checks_wav_headers_without_reading_frames(
    theme_roots,
    monkeypatch,
):
    bundled, _ = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])

    def fail_if_called(self, frame_count):
        raise AssertionError("discover() must not read WAV frames")

    monkeypatch.setattr(wave.Wave_read, "readframes", fail_if_called)

    assert [info.id for info in themes.discover()] == ["default"]


def test_24_bit_pcm_is_preserved(theme_roots):
    _, user = theme_roots
    _write_wav(
        user / "twenty-four-bit" / "button.wav",
        [-1_000_000, 500_000, 1_000_000, -500_000],
        sample_width=3,
        rate=48000,
    )

    frames, source_rate = themes.load("twenty-four-bit")["button"]
    samples = _decode_pcm(frames, 3)

    assert source_rate == 48000
    assert len(frames) == len(samples) * 3
    assert _rms_dbfs(samples, 3) == pytest.approx(-20.0, abs=0.001)


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        (b"", 0),
        (b"\x01", 1),
        (b"\x40\xe2\x01", 123456),
        (b"\xff\xff\x7f", 8388607),
        (b"\x00\x00\x80", -8388608),
        (b"\xff\xff\xff", -1),
    ],
)
def test_decode_24_bit_sample_vectors(encoded, expected):
    assert themes._decode_24_bit(encoded) == expected


def test_clipping_logs_one_warning_per_theme(caplog):
    decoded = {
        "button": themes._DecodedWav(
            samples=[32767] + [0] * 999,
            sample_width=2,
            source_rate=22050,
        ),
        "link": themes._DecodedWav(
            samples=[0] * 1000,
            sample_width=2,
            source_rate=22050,
        ),
    }

    with caplog.at_level(logging.WARNING, logger=themes.__name__):
        themes._process_theme(decoded, 0.0, "clipping-theme")

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert warnings == [
        "Sound theme 'clipping-theme' clipped 1 samples; "
        "peak overshoot 13.01 dB"
    ]


def test_processing_without_clipping_logs_no_warning(caplog):
    decoded = {
        "button": themes._DecodedWav(
            samples=[-1000, 1000] * 8,
            sample_width=2,
            source_rate=22050,
        ),
    }

    with caplog.at_level(logging.WARNING, logger=themes.__name__):
        themes._process_theme(decoded, 0.0, "clean-theme")

    assert not caplog.records


def test_sparse_fallback_logs_only_slots_actually_loaded(theme_roots, caplog):
    bundled, user = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    _write_wav(user / "sparse" / "icon.wav", [-1000, 1000])

    with caplog.at_level(logging.INFO, logger=themes.__name__):
        themes.load("sparse")

    fallback_messages = [
        record.getMessage()
        for record in caplog.records
        if "falling back to default" in record.getMessage()
    ]
    assert fallback_messages == [
        "Sound theme 'sparse' has no usable button slot; falling back to default"
    ]


def test_user_themes_dir_round_trip(tmp_path):
    user_dir = tmp_path / "sound-themes"

    themes.set_user_themes_dir(str(user_dir))
    assert themes.get_user_themes_dir() == user_dir

    themes.set_user_themes_dir(None)
    assert themes.get_user_themes_dir() is None


def test_discover_and_load_work_without_configured_user_dir(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled" / "sound-themes"
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    monkeypatch.setattr(themes, "_BUNDLED_THEMES_DIR", bundled)

    discovered = themes.discover()
    loaded = themes.load("default")

    assert [info.id for info in discovered] == ["default"]
    assert "button" in loaded
    assert themes.get_user_themes_dir() is None


def test_unknown_theme_returns_default(theme_roots):
    bundled, _ = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])

    assert themes.load("missing") == themes.load("default")
