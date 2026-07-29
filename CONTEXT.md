# Unspoken-ng

NVDA addon that replaces spoken control-role announcements with spatially positioned sounds. This context covers the audio pipeline from NVDA event to audible sound.

## Language

**Role sound**:
The audio cue mapped from an NVDA control role (button, checkbox, link, …).
_Avoid_: sound effect, earcon

**Audio engine**:
The module that renders a role sound at a 3D position with reverb, producing output-ready samples. Today: the OpenAL Soft loopback wrapper.
_Avoid_: backend, mixer, audio service

**Sound Player**:
The module that owns the playback protocol — interruption of the previous sound, feeding the output, and ducking discipline. Named in the 2026-07-19 architecture review; being deepened out of `GlobalPlugin`.
_Avoid_: playback manager, audio controller

**Onset latency**:
Time from the NVDA event (focus, navigation, mouse) to the first audible sample of the role sound. The pipeline's primary quality bar on the object-event paths. On the reading path the bar is speech-sync instead: the sound fires when speech reaches the control (ADR 0002), so the budget there governs dispatch, not audibility.
_Avoid_: lag, delay, response time

**Sound theme**:
A swappable set of slot sounds, one active at a time. May be sparse — missing slots fall back to the bundled default theme. Audio-only: a folder of correctly-named wav files (plus an optional manifest); the addon owns the role→slot mapping.
_Avoid_: sound pack, sound scheme, sound set

**Slot**:
A canonical sound identity (button, link, checkbox, …) that a sound theme provides a file for. The addon maps the ~40 NVDA control roles onto the ~15 slots.
_Avoid_: sound name, sound key

**Ducking**:
NVDA's lowering of other applications' audio while the screen reader (or this addon) plays. Held/released by the output path; a leaked duck leaves system audio quiet.
