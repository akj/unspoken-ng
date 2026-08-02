# Unspoken-ng

NVDA addon that replaces spoken control-role announcements with spatially positioned sounds. This context covers the audio pipeline from NVDA event to audible sound.

## Language

**Role sound**:
The audio cue mapped from an NVDA control role (button, checkbox, link, …).
_Avoid_: sound effect, earcon

**Sound Player**:
The module below the seam, and the only one that knows OpenAL exists (`player.py`). It owns the device, the voice pool, reverb, and rendering a role sound at a position. Its interface is four fire-and-forget commands — `play(slot, position)`, `set_theme(sounds)`, `set_reverb(preset)`, `close()` — with no return handles, no callbacks into Python, and no shared buffers (ADR 0003). `play` returns in ~0.1 ms; `set_theme` and `set_reverb` affect the next voice. Two adapters implement it: the OpenAL one and a silent one, used for degraded mode and off-NVDA tests.
_Avoid_: audio engine, backend, mixer, playback manager, audio controller, audio service

**Voice**:
One playing instance of a role sound — an OpenAL source drawn from a fixed pool (cap 8). Voices are never cut from above: interrupts overlap, tails ring out, and stealing under load is internal to the pool, which retires a stolen voice down a gain ramp rather than stopping it outright. Nothing above the seam can name or reach a voice.
_Avoid_: channel, source, handle, playing sound

**Position**:
Where a role sound is placed, as a listener-relative unit vector — +x right, +y up, -z forward, distance fixed at 1 (`spatial.position_for`). Mapped from the control's screen rectangle against the desktop rectangle, and always resolved on the main thread *before* the voice exists: ADR 0003 dropped the two-phase `move` interface because most role sounds (11–492 ms) end before COM extraction could correct them. The addon hands the player positions, not angles.
_Avoid_: coordinates, location, panning, 3D vector

**Onset latency**:
Time from the NVDA event (focus, navigation, mouse) to the first audible sample of the role sound. The pipeline's primary quality bar wherever the sound answers the user's movement — object events and reading-path fields the navigation lands inside. For controls speech merely traverses, the bar is speech-sync instead: the sound fires when speech reaches the control (ADR 0002), and the budget governs dispatch, not audibility.
_Avoid_: lag, delay, response time

**Playback verdict**:
The one three-way answer for an NVDA event — *lead* (the role sound plays now, ahead of the speech announcing the control), *ride* (the sound goes into the field's speech sequence and fires when the synth reaches it), or *silent*. Decided in one place, `playback.decide`, from event facts and a config snapshot; the role-suppression predicate sits beside it, and `GlobalPlugin`'s call sites gather inputs and obey (ADR 0004). Lead-versus-ride semantics are ADR 0002's: sounds announcing where the user just arrived lead speech; sounds announcing content speech is traversing ride it.
_Avoid_: play decision, should-play flag, playback mode, sound gating

**Sound theme**:
A swappable set of slot sounds, one active at a time. May be sparse — missing slots fall back to the bundled default theme. Audio-only: a folder of correctly-named wav files (plus an optional manifest); the addon owns the role→slot mapping.
_Avoid_: sound pack, sound scheme, sound set

**Slot**:
A canonical sound identity (button, link, checkbox, …) that a sound theme provides a file for. The addon maps the ~40 NVDA control roles onto the ~15 slots.
_Avoid_: sound name, sound key

**Ducking**:
NVDA's lowering of other applications' audio while the screen reader plays. **This addon never asserts it.** The term survives here only so the absence is deliberate rather than forgotten: role sounds are short, they answer the user's own movement, and ducking for them would pump system audio on every focus change. Owning the output device directly (ADR 0001) removed the mechanism as well as the obligation — there is no output path left to hold or leak a duck.
_Avoid_: attenuation, audio focus

**Degraded mode**:
The addon running speech-only, because it cannot produce a role sound at all. It is decided once, at the end of construction, by the outcome predicate `playback.can_produce_role_sound` over the three things that must be true to put a sound in the air: the engine started, a device opened, and the theme decoded to at least one slot. There is no fallback output path below the seam (ADR 0001), so anything that fails later stays below it. In degraded mode the silent adapter sits under the seam, the addon suppresses nothing, and NVDA speaks control roles as it would without the addon. The user's saved role-announcement setting is left untouched, so a repaired install returns to sounds with nothing to re-set. The user is told once, on a delay, so the message does not collide with NVDA's startup announcement.
_Avoid_: fallback mode, safe mode, silent mode, failure mode
