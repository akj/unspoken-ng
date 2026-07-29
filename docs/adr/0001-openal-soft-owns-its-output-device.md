# OpenAL Soft owns its output device

The audio engine is **OpenAL Soft**, and it opens the output device itself via `alcOpenDevice`,
running its own C mixer thread inside NVDA's process. `nvwave.WavePlayer` is not in the path:
measurement showed the engine was never the bottleneck — a *Python* feeder cannot hold a bounded
output queue through `nvwave`, so after any main-thread stall it must either burst (243 ms of
permanently inflated onset under fast navigation) or leave a gap, and `nvwave`'s only depth
telemetry misreports under exactly that load. With the device owned directly, `alSourcePlay`
returns in 0.09 ms on the calling thread and no Python code sits in the audio path at all.

## Considered options

**`nvwave.WavePlayer` owns the stream.** Rejected for the queue behaviour above. It bought three
things for free, and all three turned out cheap to own: ducking is void because role sounds never
assert it, volume is one `alListenerf(AL_GAIN, …)` at 0.086 ms, and device-follow is NVDA's stored
`[audio] outputDevice` passed straight through to `alcOpenDevice` — `"default"` becomes NULL and
any other value is used verbatim, with no name matching — plus a non-destructive
`alcReopenDeviceSOFT` and `ALC_SOFT_system_events` for change notification.

**A Rust audio core owning its stream.** Architecturally the same property, and it ties or loses
every column. It does not lower the Windows latency floor (`cpal` is shared-mode-only, the same
period class as OpenAL Soft's backend); it measured ~62 ms event→air against the owned OpenAL
stream's 52.4 ms; its HRTF layer is a ~3-year-stale, bus-factor-1 crate against OpenAL Soft's
actively maintained one; it needs a toolchain, a second build artifact and CI from zero; and it
requires `catch_unwind` at *every* FFI export, where a single miss takes the user's screen reader
down. Its one advantage was a listening test in which it localized ±45° better — but that test
judged OpenAL's loopback configuration, which runs at 44100 and therefore resamples the built-in
HRTF down to a 59-tap filter. The owned device runs at the endpoint's native 48000 and uses the
64-tap filter with no output resampler, so the comparison does not describe the option chosen here.

**An off-the-shelf native engine.** Closed on its own evidence: Synthizer is archived and already
failed in this addon's lineage (device switching, squealing, 64-bit breakage), miniaudio has
neither HRTF nor reverb, SoLoud has no HRTF and a stalled release cadence, and FMOD's license does
not fit an open NVDA addon.

**The engine in a separate process.** Buys crash isolation and nothing else — it is latency-neutral,
because the triggering event is born inside NVDA's process and still waits on the GIL to be noticed,
and an IPC hop costs tens of microseconds. Its sharpest argument was the Rust panic hazard, which
choosing OpenAL retires; what remains is a `soft_oal.dll` fault, which is the status quo this addon
has already shipped for years. Not worth process lifecycle management, orphan reaping after an NVDA
crash, a device-follow split across the boundary, and two artifacts to build, package and sign.

## Consequences

- **The Sound Player seam is fire-and-forget commands** — `play(slot, position)`,
  `set_theme(sounds)`, `set_reverb(preset)`, `close()` — with no callbacks into Python, no shared
  buffers, and no handles. That API is already command-shaped, so the constraint is close to free,
  and it keeps a later retreat behind a process boundary a relocation rather than a redesign.

  *Amended by #25, which pinned the signatures.* This bullet originally read `play(slot, position)
  -> voice`, `move(voice, position)`, `stop(voice)`, over opaque handles. `move` was called
  load-bearing here — fire at dispatch, position when COM extraction completes — and the sound
  durations refute it: the bundled slots run 11–492 ms against 60–170 ms measured extraction, so
  for 10 of 14 slots the sound is over before the position arrives, and the four still ringing
  would be spatially wrong for their first third and then jump. Position is therefore resolved
  *before* the voice exists. With `move` gone, `stop` was the handle's only remaining use and it
  has no caller: in-flight voices are never cut (#10), voice-stealing is internal to the source
  pool, the suppression settings stop sounds from *starting*, and teardown is `close()`. So `play`
  returns nothing. This **strengthens** the reasoning above rather than weakening it — a `play`
  returning a handle is the one shape that would force a synchronous round trip across a future
  process boundary, on the latency path.
- **There is no fallback output path.** When the owned stream cannot open, the addon has nowhere to
  fall back to, so the failure and degraded-mode behaviour must be specified deliberately.
- **The buffer is the one latency knob**, and it works 1:1 with onset. `ALSOFT_CONF`
  `[general] periods = 2` yields a 22 ms buffer — the floor, since the backend clamps 960 frames up
  to 1056 — against 32 ms at the default of 3. `ALC_REFRESH` is ignored; the 10 ms period is pinned
  by WASAPI shared mode. The addon must write `ALSOFT_CONF` into `os.environ` *before* the DLL
  loads. (#25 found this imposes no import-order rule on anything else: the player's constructor
  writes the env var immediately before its own `ctypes.CDLL` call, so the constraint is satisfied
  inside one module. The variable is process-global, though — another addon loading OpenAL Soft in
  the same NVDA inherits our config file.)
- **`alcReopenDeviceSOFT` blocks for 31–470 ms** and must never run on NVDA's main thread.
- **The loopback justification is discharged.** `openal_audio.py`'s docstring defends loopback as
  "preserving NVDA ducking and device routing"; ducking no longer applies on any branch, and device
  routing is now handled directly. The docstring goes with the loopback path.
- **The latency budget remains dispatch-bound**: ~10 ms from event dispatch to the sound being handed
  to the output stream, with COM extraction off the critical path. True event→air rides the
  platform's shared-mode floor, which this decision minimises but does not own.
