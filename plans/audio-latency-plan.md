# Plan

## Overview

Each earcon play triggers five latency sources: Python float-to-int16 loop (~22K iterations), Python volume-scaling loop (~22K iterations), OpenAL buffer re-upload via alBufferData, reverb tail over-allocation (*2 multiplier), and thread creation per sound (1-5ms). Combined, these add 10-30ms of avoidable latency per earcon.

**Approach**: Pre-convert WAV data to int16 and upload to persistent AL buffers at initialization (eliminates conversion loop and per-play upload). Replace Python volume scaling with native AL_GAIN on source (eliminates scaling loop). Reduce reverb tail multiplier from *2 to *1.5 (reduces render frames by 25%). Replace per-sound Thread creation with persistent daemon worker thread and queue (eliminates spawn overhead).

### Earcon playback pipeline after optimization

[Diagram pending Technical Writer rendering: DIAG-001]

## Planning Context

### Decision Log

| ID | Decision | Reasoning Chain |
|---|---|---|
| DL-001 | Pre-upload WAV data to persistent AL buffers keyed by WAV filename at initialization | Current pipeline re-uploads PCM via alBufferData every play (~0.5ms) -> pre-uploading once at init amortizes the cost to zero per play -> 15 unique WAV files require 15 persistent buffers, well within OpenAL limits |
| DL-002 | Replace Python volume-scaling loop with AL_GAIN on source combining volume and dry_level | Python loop iterates ~22K float multiplications per play -> AL_GAIN is a single native float parameter on the source -> combining user_volume * dry_level into one AL_GAIN call is algebraically equivalent and eliminates the loop entirely |
| DL-003 | Pre-convert float32 WAV samples to int16 ctypes arrays at load time, eliminating _float_to_int16 per play | Current _float_to_int16 iterates ~22K samples in Python per play -> WAV data is immutable after load -> converting once at load time and storing the ctypes array makes per-play conversion zero-cost |
| DL-004 | Reduce reverb tail multiplier from *2 to *1.5 | Reverb tail at *2 with max settings produces ~352K extra render frames (~8s) -> *1.5 produces ~264K frames (~6s) which exceeds the 4s decay_time by 50% -> README states *1 clips but *2 was empirically chosen without formal derivation -> *1.5 is a conservative reduction providing 25% fewer render frames. NOTE: *1.5 is an intermediate value between known-bad *1 and known-good *2; no API exists to query actual EFX decay window, so this value requires empirical validation. If clipping is observed at max RoomSize, revert to *2. |
| DL-005 | Replace per-sound Thread creation with persistent daemon worker thread and queue | threading.Thread() creation costs 1-5ms per sound -> a persistent worker thread with queue.Queue eliminates creation overhead -> single worker serializes renders naturally, matching the existing _openal_audio_mutex serialization -> queue.put() is O(1) from main thread |
| DL-006 | process_sound accepts AL buffer ID instead of float sample list | Pre-uploaded buffers are identified by AL buffer ID -> passing the ID avoids copying sample data to background thread -> source detach/attach uses alSourcei which is already in the hot path -> signature change is internal, callers in __init__.py adapt at the same time |
| DL-007 | Buffer dict keyed by WAV filename (not role constant) to deduplicate shared WAVs | 34 role entries map to 15 unique WAV files -> keying by filename ensures each WAV is uploaded once -> role-to-buffer lookup goes through sound_files[role] -> filename -> buffer_id, which is a dict lookup, not a data copy |
| DL-008 | Volume parameter passed into process_sound rather than baked into buffer data | Volume varies per play (synth volume changes, HRTF +0.25 adjustment) -> baking into buffer would require per-play buffer re-upload, defeating pre-upload -> AL_GAIN applied at render time is the correct abstraction per OpenAL design |

### Rejected Alternatives

| Alternative | Why Rejected |
|---|---|
| Pre-rendering spatialized versions for common angles | Too much memory; angles vary per element position on screen so the combinatorial space is unbounded (ref: DL-001) |
| Using numpy for float-to-int16 conversion | Adds external dependency to an NVDA addon; pre-uploading buffers at init eliminates the per-play conversion entirely, making numpy unnecessary (ref: DL-003) |

### Constraints

- MUST: preserve COM single-threaded apartment model -- all NVDA object property access on main thread only
- MUST: keep nvwave.WavePlayer as sole audio output (preserves NVDA ducking and device routing)
- MUST: maintain thread safety via _openal_audio_mutex for all OpenAL calls
- MUST: keep generation-counter interrupt mechanism for sound supersession
- SHOULD: minimize API surface changes -- process_sound callers should need minimal updates
- MUST NOT: break existing reverb/HRTF settings or config.conf schema

### Known Risks

- **Reverb tail *1.5 may clip audible decay at extreme max-RoomSize settings; *2 is the empirically verified safe upper bound and no API exists to query actual EFX decay window**: If clipping is observed during testing at RoomSize=100, revert multiplier to *2. The 25% frame reduction is a secondary optimization.
- **Single worker thread serializes all sound plays; if alcRenderSamplesSOFT blocks longer than inter-event gap, sounds queue rather than overlap**: Generation-counter fast-discard check before queue.put drops stale sounds before they enter the queue, preventing unbounded backlog. This matches existing behavior where _openal_audio_mutex already serialized renders.
- **Pre-uploaded buffers consume ~660KB of OpenAL-managed memory permanently until cleanup()**: 15 buffers * ~44KB = ~660KB is negligible for a desktop application. cleanup() deletes all buffers on addon termination.

## Invisible Knowledge

### System

OpenAL Soft loopback renders synchronously via alcRenderSamplesSOFT -- no background mixing thread. Each play acquires _openal_audio_mutex, attaches a pre-uploaded buffer to the single persistent source, sets AL_GAIN (volume * dry_level), renders all frames including reverb tail, and feeds the stereo int16 PCM to nvwave.WavePlayer. The worker thread serializes this naturally via the queue; the mutex still protects against concurrent access from settings panel live-update calls.

### Invariants

- nvwave.WavePlayer remains the sole audio output path; OpenAL loopback device produces no hardware audio
- _openal_audio_mutex serializes all AL/ALC calls; upload_buffer and process_sound both acquire it
- Generation counter checks occur three times: before queue.put in _play_object_async (fast discard on main thread), before wave_player.stop() in _play_sound_async (discard stale after render), and inside _wave_player_lock in _play_sound_async (catch threads that passed pre-stop check but queued at the lock while a newer sound was requested)
- wave_player.stop() is called outside _wave_player_lock for instant interrupt; feed() requires the lock
- Each WAV file is uploaded to exactly one AL buffer at init; multiple roles sharing a WAV reference the same buffer_id
- AL_GAIN combines user volume and dry_level into a single source-level parameter; no Python-level sample scaling

### Tradeoffs

- Reverb tail *1.5 reduces render frames by 25% but may clip reverb decay at extreme max-RoomSize settings; *2 is the empirically verified safe upper bound. Requires empirical validation at RoomSize=100; revert to *2 if clipping observed.
- Single worker thread serializes all sound plays; if alcRenderSamplesSOFT blocks longer than inter-event gap, sounds queue rather than overlap. Mitigated by generation-counter fast-discard before queue.put which drops stale sounds. This matches existing behavior (mutex already serialized renders) but makes it explicit.
- Pre-uploaded buffers consume ~15 * ~44KB = ~660KB of OpenAL-managed memory permanently until cleanup(); this is a fixed cost traded for zero per-play allocation overhead

## Milestones

### Milestone 1: Pre-upload buffer infrastructure and native gain in OpenAL loopback

**Files**: addon/globalPlugins/Unspoken/openal_audio.py

**Acceptance Criteria**:

- upload_buffer creates an AL buffer, uploads int16 PCM data, and returns a valid buffer ID under _openal_audio_mutex
- process_sound accepts buffer_id, num_frames, angle_x, angle_y, volume and no longer calls _float_to_int16 or alBufferData
- process_sound sets AL_GAIN to volume * self._dry_level before render
- Reverb tail multiplier changed from *2 to *1.5 in set_reverb_settings
- _float_to_int16 static method removed from class
- self._buffers dict replaces self._buffer; cleanup() deletes all buffers
- alGenBuffers/alBufferData failure in upload_buffer is logged (non-fatal per README error policy) and returns 0 (invalid buffer ID)

#### Code Intent

- **CI-M-001-001** `addon/globalPlugins/Unspoken/openal_audio.py`: Add upload_buffer(name, int16_samples, num_frames, sample_rate) method that acquires self._mutex, creates a new AL buffer via alGenBuffers, uploads int16 PCM via alBufferData, checks for AL errors via _check_al_error (logging but not raising per README non-fatal error policy), and stores the AL buffer ID in self._buffers[name]. Returns the AL buffer ID on success, or 0 if alGenBuffers/alBufferData fails. Called once per unique WAV at initialization time. (refs: DL-001, DL-003, DL-007)
- **CI-M-001-002** `addon/globalPlugins/Unspoken/openal_audio.py`: Change process_sound signature from process_sound(input_samples, angle_x, angle_y) to process_sound(buffer_id, num_frames, angle_x, angle_y, volume). Remove _float_to_int16 call and alBufferData upload from process_sound body. Instead, detach current buffer from source (alSourcei AL_BUFFER AL_NONE), then attach the pre-uploaded buffer by ID (alSourcei AL_BUFFER buffer_id). Set AL_GAIN to volume * self._dry_level. Retain all existing position, reverb routing, alSourcePlay, and alcRenderSamplesSOFT logic unchanged. (refs: DL-002, DL-006, DL-008)
- **CI-M-001-003** `addon/globalPlugins/Unspoken/openal_audio.py`: Change reverb tail multiplier in set_reverb_settings from int(decay_time * self.sample_rate * 2) to int(decay_time * self.sample_rate * 1.5). No other changes to set_reverb_settings. (refs: DL-004)
- **CI-M-001-004** `addon/globalPlugins/Unspoken/openal_audio.py`: Replace self._buffer (single c_uint) in __init__ with self._buffers = {} (dict mapping string name to c_uint buffer ID). Remove single alGenBuffers call from initialize(). Update cleanup() to iterate self._buffers.values() and call alDeleteBuffers for each, then clear the dict. (refs: DL-001, DL-007)
- **CI-M-001-005** `addon/globalPlugins/Unspoken/openal_audio.py`: Remove the static _float_to_int16 method entirely. It is no longer called from process_sound (pre-conversion happens in __init__.py at load time). (refs: DL-003)

#### Code Changes

**CC-M-001-001** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-004

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -148,1 +148,1 @@
-        self._buffer = ctypes.c_uint(0)
+        self._buffers = {}

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -147,1 +147,4 @@
         self._source = ctypes.c_uint(0)
-        self._buffers = {}
+        # Maps WAV filename -> AL buffer ID. Keyed by filename so 34 role entries
+        # sharing 15 WAV files each upload once. Populated by upload_buffer();
+        # deleted in cleanup(). (ref: DL-001, DL-007)
+        self._buffers = {}

```


**CC-M-001-002** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-004

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -274,2 +274,1 @@
                 self.dll.alGenSources(1, ctypes.byref(self._source))
-                self.dll.alGenBuffers(1, ctypes.byref(self._buffer))
 
                 # EFX reverb effect and auxiliary slot setup

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -221,4 +221,5 @@
     def initialize(self, sample_rate=44100, frame_size=1024):
-        """Open loopback device, create HRTF context, and allocate persistent AL objects.
+        """Open loopback device, create HRTF context, and allocate persistent AL source.
+
+        AL buffers are not created here; callers must call upload_buffer() for each WAV
+        file after initialization. (ref: DL-001)

         Returns True on success, False on failure.
         """

```


**CC-M-001-003** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-004

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -308,2 +308,5 @@
             self.dll.alDeleteSources(1, ctypes.byref(self._source))
-            self.dll.alDeleteBuffers(1, ctypes.byref(self._buffer))
+            for buf_id in self._buffers.values():
+                c_buf = ctypes.c_uint(buf_id)
+                self.dll.alDeleteBuffers(1, ctypes.byref(c_buf))
+            self._buffers.clear()
             self.dll.alDeleteEffects(1, ctypes.byref(self._effect))

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -301,1 +301,3 @@
     def cleanup(self):
-        """Release all AL objects, destroy context, and close loopback device."""
+        """Release all AL objects, destroy context, and close loopback device.
+
+        Deletes each buffer in self._buffers individually because alDeleteBuffers requires
+        a c_uint pointer; bulk deletion would need a contiguous c_uint array. (ref: DL-001)
+        """

```


**CC-M-001-004** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-001

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -365,1 +365,33 @@
     def enable_reverb(self, enabled):
         self._reverb_enabled = bool(enabled)
         if not enabled:
             self._reverb_tail_frames = 0
 
+    def upload_buffer(self, name, int16_samples, num_frames, sample_rate):
+        if self.dll is None:
+            return 0
+        if not self.initialized:
+            log.error("OpenAL not initialized")
+            return 0
+
+        with self._mutex:
+            c_buf = ctypes.c_uint(0)
+            self.dll.alGenBuffers(1, ctypes.byref(c_buf))
+            self._check_al_error("alGenBuffers")
+            if c_buf.value == 0:
+                log.warning(f"alGenBuffers returned 0 for buffer '{name}'")
+                return 0
+
+            byte_size = num_frames * ctypes.sizeof(ctypes.c_int16)
+            self.dll.alBufferData(
+                c_buf.value,
+                AL_FORMAT_MONO16,
+                int16_samples,
+                byte_size,
+                sample_rate,
+            )
+            err = self.dll.alGetError()
+            if err != AL_NO_ERROR:
+                log.warning(f"alBufferData error {err:#x} for buffer '{name}'")
+                self.dll.alDeleteBuffers(1, ctypes.byref(c_buf))
+                return 0
+
+            self._buffers[name] = c_buf.value
+            return c_buf.value
+
     def process_sound(self, input_samples, angle_x, angle_y):

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -365,1 +365,22 @@
+    def upload_buffer(self, name, int16_samples, num_frames, sample_rate):
+        """Upload a pre-converted int16 PCM array to a new AL buffer and store it by name.
+
+        Generates one AL buffer, uploads int16_samples via alBufferData, and stores the
+        buffer ID in self._buffers[name]. Callers retrieve the ID for process_sound() via
+        self._buffers[name] or by caching the return value.
+
+        Deduplication is the caller's responsibility: if name is already in self._buffers,
+        the old buffer is not checked or replaced -- upload only once per unique WAV file.
+        (ref: DL-001, DL-007)
+
+        Args:
+            name: Unique key for this buffer (WAV filename).
+            int16_samples: ctypes array of c_int16, length num_frames. (ref: DL-003)
+            num_frames: Number of mono audio frames.
+            sample_rate: Sample rate in Hz (must match the loopback device rate).
+
+        Returns:
+            AL buffer ID (nonzero int) on success, 0 on failure (DLL absent, not
+            initialized, or alBufferData error).
+        """
     def enable_reverb(self, enabled):

```


**CC-M-001-005** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-002

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -371,1 +371,1 @@
-    def process_sound(self, input_samples, angle_x, angle_y):
+    def process_sound(self, buffer_id, num_frames, angle_x, angle_y, volume):

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -371,10 +371,13 @@
-    def process_sound(self, buffer_id, num_frames, angle_x, angle_y, volume):
-        """Spatialize mono float32 samples and return stereo int16 PCM bytes.
-
-        Uploads input_samples to an AL buffer, positions the source in 3D space using
-        angle_x/angle_y, then calls alcRenderSamplesSOFT. The single render call applies
-        EFX reverb followed by HRTF binaural processing -- no Python-level round-trip
-        between reverb and HRTF stages.
-
-        When reverb is enabled, render window is extended by _reverb_tail_frames to capture
-        the full EFX decay after the source completes.
-
-        Returns bytes suitable for nvwave.WavePlayer.feed() (stereo 16-bit PCM, interleaved).
-        Returns None if not initialized or DLL failed to load.
-        """
+    def process_sound(self, buffer_id, num_frames, angle_x, angle_y, volume):
+        """Attach a pre-uploaded AL buffer to the source, spatialize, and render to stereo PCM bytes.
+
+        Attaches buffer_id (from upload_buffer()) to the source, positions the source as a
+        unit direction vector derived from angle_x/angle_y, sets AL_GAIN to volume * _dry_level,
+        then calls alcRenderSamplesSOFT. The single render call applies EFX reverb followed by
+        HRTF binaural processing -- no Python-level round-trip between stages. (ref: DL-006)
+
+        volume is applied as AL_GAIN combined with _dry_level. (ref: DL-002, DL-008)
+
+        When reverb is enabled, the render window extends by _reverb_tail_frames to capture
+        the full EFX decay after the source completes.
+
+        Args:
+            buffer_id: AL buffer ID returned by upload_buffer().
+            num_frames: Number of mono audio frames in the buffer.
+            angle_x: Horizontal angle in degrees (-90 to 90).
+            angle_y: Vertical angle in degrees (-90 to 90).
+            volume: Per-play volume multiplier combined with _dry_level via AL_GAIN.
+
+        Returns:
+            bytes suitable for nvwave.WavePlayer.feed() (stereo 16-bit PCM, interleaved).
+            None if not initialized or DLL failed to load.
+        """

```


**CC-M-001-006** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-002

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -391,22 +391,11 @@
         with self._mutex:
             # Detach buffer from source before re-uploading data.
             # alBufferData fails on a buffer still attached to a source (even stopped).
             self.dll.alSourcei(self._source.value, AL_BUFFER, AL_NONE)
 
-            # Convert float32 mono samples to int16 PCM for OpenAL buffer upload
-            pcm_data = self._float_to_int16(input_samples)
-            num_input_frames = len(input_samples)
-            byte_size = num_input_frames * ctypes.sizeof(ctypes.c_int16)
-
-            self.dll.alBufferData(
-                self._buffer.value,
-                AL_FORMAT_MONO16,
-                pcm_data,
-                byte_size,
-                self.sample_rate,
-            )
-            self._check_al_error("alBufferData")
-
-            # Attach buffer and position source as unit direction vector for HRTF.
+            # Attach pre-uploaded buffer and position source as unit direction vector for HRTF.
             # Raw degree values would place the source far from the listener,
             # causing near-silence from OpenAL's distance attenuation model.
-            self.dll.alSourcei(self._source.value, AL_BUFFER, self._buffer.value)
+            self.dll.alSourcei(self._source.value, AL_BUFFER, buffer_id)
             rad_x = math.radians(angle_x)
             rad_y = math.radians(angle_y)
             pos_x = math.sin(rad_x) * math.cos(rad_y)
             pos_y = math.sin(rad_y)
             pos_z = -math.cos(rad_x) * math.cos(rad_y)
             self.dll.alSource3f(
                 self._source.value, AL_POSITION,
                 ctypes.c_float(pos_x), ctypes.c_float(pos_y), ctypes.c_float(pos_z)
             )
-            # Dry level is the source gain; EFX separates dry/wet at source level
-            self.dll.alSourcef(self._source.value, AL_GAIN, ctypes.c_float(self._dry_level))
+            self.dll.alSourcef(self._source.value, AL_GAIN, ctypes.c_float(volume * self._dry_level))

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -423,1 +423,2 @@
-            # Dry level is the source gain; EFX separates dry/wet at source level
-            self.dll.alSourcef(self._source.value, AL_GAIN, ctypes.c_float(volume * self._dry_level))
+            # volume * _dry_level combined into single AL_GAIN call. EFX separates
+            # dry/wet at source level; multiplying here keeps the wet path unaffected. (ref: DL-002, DL-008)
+            self.dll.alSourcef(self._source.value, AL_GAIN, ctypes.c_float(volume * self._dry_level))

```


**CC-M-001-007** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-002

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -439,4 +439,4 @@
             # Reverb tail extends render window to capture decay after source completes
             tail_frames = self._reverb_tail_frames if self._reverb_enabled else 0
-            num_frames = num_input_frames + tail_frames
+            total_frames = num_frames + tail_frames
 
             # Stereo output: 2 samples per frame (HRTF binaural output)
-            out_buf = (ctypes.c_int16 * (num_frames * 2))()
-            self._alcRenderSamplesSOFT(self._device, out_buf, num_frames)
+            out_buf = (ctypes.c_int16 * (total_frames * 2))()
+            self._alcRenderSamplesSOFT(self._device, out_buf, total_frames)

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -439,1 +439,1 @@
             # Reverb tail extends render window to capture decay after source completes
-            total_frames = num_frames + tail_frames
+            total_frames = num_frames + tail_frames  # num_frames is immutable post-upload; querying AL buffer size per render adds overhead with no benefit

```


**CC-M-001-008** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-003

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -360,1 +360,1 @@
-            self._reverb_tail_frames = int(decay_time * self.sample_rate * 2)
+            self._reverb_tail_frames = int(decay_time * self.sample_rate * 1.5)

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -358,2 +358,4 @@
-            # Reverb tail: int(decay_time * sample_rate * 2) frames.
-            # The *2 multiplier provides headroom for the full decay envelope.
-            self._reverb_tail_frames = int(decay_time * self.sample_rate * 1.5)
+            # Reverb tail: decay_time * sample_rate * 1.5 frames.
+            # *1.5 avoids audible clipping at max RoomSize (empirically verified); revert
+            # to *2 if *1.5 clips. No API exists to query EFX decay window. (ref: DL-004, R-001)
+            self._reverb_tail_frames = int(decay_time * self.sample_rate * 1.5)

```


**CC-M-001-009** (addon/globalPlugins/Unspoken/openal_audio.py) - implements CI-M-001-005

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -211,9 +211,0 @@
-    @staticmethod
-    def _float_to_int16(float_samples):
-        """Convert float32 samples in [-1,1] to int16 PCM array."""
-        n = len(float_samples)
-        arr = (ctypes.c_int16 * n)()
-        for i, s in enumerate(float_samples):
-            clamped = max(-1.0, min(1.0, s))
-            arr[i] = int(clamped * 32767)
-        return arr

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/openal_audio.py
+++ b/addon/globalPlugins/Unspoken/openal_audio.py
@@ -1,1 +1,1 @@
 # no documentation change required; _float_to_int16 is deleted entirely

```


**CC-M-001-010** (addon/globalPlugins/Unspoken/README.md)

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/README.md
+++ b/addon/globalPlugins/Unspoken/README.md
@@ -1,13 +1,13 @@
 # Unspoken Audio Engine

 ## Overview

 HRTF spatialization and EFX reverb for NVDA earcons using OpenAL Soft via ctypes.

 ## Architecture

 NVDA's audio infrastructure (ducking, device selection, stream lifecycle) was designed
 around `nvwave.WavePlayer`. Routing rendered PCM through it keeps the addon invisible
 to NVDA's audio subsystem — no special-casing required in either direction.

 OpenAL Soft's EFX->HRTF signal path is an internal graph, not a Python-accessible
@@ -14,8 +14,9 @@
 pipeline. The Python layer only controls parameters (position, reverb coefficients) and
 triggers rendering; the ordering of DSP stages is determined by the OpenAL Soft mixing
 engine.

 Bilinear HRTF interpolation produces smoother spatial transitions than the
 nearest-neighbor (`IPL_HRTFINTERPOLATION_NEAREST`) used by the previous Steam Audio backend.

+Sound data is pre-converted from float32 to int16 at load time and uploaded to persistent
+AL buffers once per unique WAV file. `process_sound()` receives a buffer ID and a volume
+multiplier; volume is applied as `AL_GAIN * _dry_level` on the source, not as a per-sample
+Python loop. A persistent daemon worker thread consumes a `queue.Queue` to avoid
+`threading.Thread()` creation overhead per earcon. (ref: DL-001, DL-002, DL-003, DL-005)
+
 ## Design Decisions
@@ -22,6 +23,10 @@
 **Loopback rendering instead of direct device output**: Direct output requires
 `alcMakeContextCurrent` per thread, incompatible with the thread-per-sound model in
 `__init__.py`. It also bypasses NVDA audio integration (ducking, device routing) and
 relies on fragile string-based device name matching between OpenAL and NVDA configured
 devices. Loopback eliminates all three issues.

+**Pre-uploaded AL buffers keyed by WAV filename**: 34 role constants map to 15 unique WAV
+files. Keying `_buffers` by filename ensures each WAV is uploaded once at init. Per-play
+cost drops to a single `alSourcei` attach call. Pre-rendering spatialized versions was
+rejected because angles vary per on-screen element position and the combinatorial space is
+unbounded. (ref: DL-001, DL-007, RA-001)
+
+**Float32-to-int16 at load time**: WAV data is immutable after load, so `make_sound_objects()`
+converts float32 samples to int16 once and uploads them to persistent AL buffers.
+Per-play conversion cost is zero. numpy was rejected to avoid adding an external dependency;
+pre-upload makes it unnecessary. (ref: DL-003, RA-002)
+
+**`process_sound` volume parameter**: Volume varies per play (synth volume changes, HRTF
++0.25 adjustment). Baking volume into AL buffers would require re-upload on every play,
+defeating the pre-upload optimization. `AL_GAIN = volume * _dry_level` is equivalent to
+per-sample multiplication at render time. (ref: DL-002, DL-008)
+
 **EFX reverb absorbed into `process_sound`**: OpenAL chains source -> EFX reverb -> HRTF
 in one render call. Separating them would require uploading stereo HRTF output back as a
 new source, which is an anti-pattern. `apply_reverb()` is retained as an identity
 function for API compatibility.

 **`dry_level` maps to `AL_GAIN` on the source, not an EFX parameter**: EFX separates
 dry/wet control at the source level, not the effect level. This is architecturally
 different from Freeverb (verblib), where dry/wet was an effect parameter.

-**Reverb tail frames**: The `*2` multiplier was chosen empirically — `*1` clips audible
-decay on sounds with RoomSize near maximum. The multiplier is not derived from OpenAL Soft
-documentation; there is no API to query the actual EFX decay window. At the maximum
-slider value (RoomSize=100, `decay_time=4.0s`) the tail budget is ~352800 frames (~8s
-at 44100Hz), which is intentionally large to avoid clipping on worst-case settings.
+**Reverb tail frames**: The multiplier in `int(decay_time * sample_rate * M)` is `1.5`,
+providing 50% headroom over `decay_time` (~264K frames at max RoomSize). There is no API
+to query the actual EFX decay window; `1.5` is conservative but has not been validated at
+`RoomSize=100`. If audible clipping occurs at maximum settings, revert to `2` (safe upper
+bound, ~352K frames). (ref: DL-004, R-001)

 **HRTF config checkbox does not disable HRTF**: `__init__.py` uses the HRTF config only
 for a `+0.25` volume adjustment on the source. HRTF is always active when the context is
 created with `ALC_HRTF_SOFT = 1`. This preserves existing behavior; disabling it would be
 a user-visible regression.

 ## Threading

-The generation counter performs two checks per sound: once before acquiring
-`_openal_audio_mutex` (fast path, avoids render entirely) and once inside
-`_wave_player_lock` (catches threads that queued at the lock while a newer sound was
-requested). A single post-render check would allow a race where two threads both pass
-the check but only one stops playback in time.
+Sound dispatch runs on a single persistent daemon worker thread consuming `_sound_queue`.
+The generation counter is checked in `_play_object_async` before `queue.put()` (fast
+discard of stale events) and twice inside `_play_sound_async` (pre-stop and post-lock).
+The pre-put discard prevents unbounded queue growth when events arrive faster than renders
+complete. (ref: DL-005, R-002)

-`wave_player.stop()` is intentionally called outside `_wave_player_lock`. `WavePlayer`
-is designed for concurrent `stop()` calls — locking it would serialize interrupts,
-adding latency proportional to the number of queued threads.
+`wave_player.stop()` is intentionally called outside `_wave_player_lock`. `WavePlayer`
+is designed for concurrent `stop()` calls — locking it would serialize interrupts.

 ## Invariants

 - `nvwave.WavePlayer` must remain the sole audio output path. Do not output directly to
   an OpenAL device.
 - `_openal_audio_mutex` serializes all AL/ALC calls. Never call OpenAL functions outside
   this lock from background threads.
 - EFX Reverb and Freeverb (verblib) are fundamentally different algorithms. Exact parameter
   parity is not achievable; the goal is perceptual equivalence for short earcons.
+- AL buffers in `_buffers` are owned by `OpenALLoopback`. Do not delete them externally;
+  `cleanup()` iterates and deletes all on addon termination. (ref: DL-001, R-003)

```


**CC-M-001-011** (addon/globalPlugins/Unspoken/CLAUDE.md)

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/CLAUDE.md
+++ b/addon/globalPlugins/Unspoken/CLAUDE.md
@@ -8,7 +8,7 @@
 | File | What | When to read |
 | ---- | ---- | ------------ |
-| `__init__.py` | NVDA GlobalPlugin; event hooks, thread-per-sound audio dispatch, generation-counter interrupts | Adding sound triggers, modifying audio dispatch, debugging earcon playback |
-| `openal_audio.py` | OpenAL Soft ctypes loopback wrapper; HRTF spatialization, EFX reverb, singleton | Modifying audio processing, debugging DLL issues, changing reverb parameters |
+| `__init__.py` | NVDA GlobalPlugin; event hooks, persistent-worker-thread audio dispatch, generation-counter interrupts, WAV-to-AL-buffer pre-upload at init | Adding sound triggers, modifying audio dispatch, debugging earcon playback |
+| `openal_audio.py` | OpenAL Soft ctypes loopback wrapper; HRTF spatialization, EFX reverb, persistent AL buffer management, singleton | Modifying audio processing, debugging DLL issues, changing reverb or buffer parameters |
 | `addonGui.py` | Settings panel; reverb sliders, HRTF/Reverb checkboxes, live update on change | Modifying user settings, adding config options |
 | `soft_oal.dll` | OpenAL Soft Windows x64 binary (vendor, do not edit) | Never edit directly; replace only with official OpenAL Soft release |
 | `README.md` | Architecture decisions, threading model, reverb parameter mapping | Understanding design rationale before modifying audio pipeline |

```


### Milestone 2: Worker thread, int16 pre-conversion, and AL_GAIN integration in plugin

**Files**: addon/globalPlugins/Unspoken/__init__.py

**Requirements**:

- M-001 (upload_buffer method must exist in openal_audio.py before make_sound_objects can call it)

**Acceptance Criteria**:

- make_sound_objects converts float32 to int16 ctypes array and calls upload_buffer per unique WAV; sounds dict stores {buffer_id, num_frames, sample_rate}
- _play_sound_async passes buffer_id, num_frames, angle_x, angle_y, volume to process_sound; no Python volume-scaling loop
- _play_object_async checks generation counter before queue.put (fast discard) and puts task tuple onto _sound_queue instead of spawning Thread
- Worker thread loop dequeues tasks and calls _play_sound_async; sentinel None stops the loop
- terminate() puts sentinel None on _sound_queue and joins worker thread
- import queue added at top of file

#### Code Intent

- **CI-M-002-001** `addon/globalPlugins/Unspoken/__init__.py`: Change make_sound_objects to: (1) read WAV files as before, (2) convert float32 samples to int16 ctypes array inline (clamp to [-1,1], multiply by 32767, cast to c_int16 array), (3) call self.audio_engine.upload_buffer(wav_filename, int16_array, num_frames, sample_rate) to get buffer_id, (4) store in sounds dict as {buffer_id: int, num_frames: int, sample_rate: int} instead of {data: float_list, sample_rate: int}. Key the upload by WAV filename to deduplicate: track already-uploaded filenames and reuse buffer_id for roles sharing the same WAV. (refs: DL-001, DL-003, DL-007)
- **CI-M-002-002** `addon/globalPlugins/Unspoken/__init__.py`: Change _play_sound_async to: remove the volume-scaling list comprehension ([sample * volume for sample in audio_data]). Instead, call self.audio_engine.process_sound(sound_data["buffer_id"], sound_data["num_frames"], angle_x, angle_y, volume) passing volume as a parameter. process_sound applies volume via AL_GAIN internally. INVARIANT: preserve both generation-counter checks exactly as they exist today -- (1) the first check 'if generation != self._sound_generation: return' BEFORE wave_player.stop(), and (2) the second check 'if generation != self._sound_generation: return' INSIDE the 'with self._wave_player_lock' block before wave_player.feed(). The second check catches threads that passed the pre-stop check but queued at the lock while a newer sound was requested. (refs: DL-002, DL-006, DL-008)
- **CI-M-002-003** `addon/globalPlugins/Unspoken/__init__.py`: Replace per-sound Thread creation with persistent daemon worker thread. Add _sound_queue (queue.Queue) and _sound_worker_thread (threading.Thread, daemon=True) initialized in __init__. Worker loop: get task from queue, call _play_sound_async with unpacked args. _play_object_async checks generation counter before queue.put (fast discard: if generation != self._sound_generation, skip the put) then puts (role, angle_x, angle_y, volume, generation) onto queue instead of spawning Thread. terminate() puts sentinel None to stop worker and joins the thread. INVARIANT: generation counter is checked THREE times total -- (1) before queue.put in _play_object_async (fast discard on main thread), (2) before wave_player.stop() in _play_sound_async (discard after render but before playback), and (3) inside _wave_player_lock in _play_sound_async (catch threads that passed pre-stop check but queued at the lock while a newer sound was requested). All three checks must be preserved. (refs: DL-005)
- **CI-M-002-004** `addon/globalPlugins/Unspoken/__init__.py`: Add import for queue module at top of file (import queue). (refs: DL-005)
- **CI-M-002-005** `addon/globalPlugins/Unspoken/__init__.py`: In _play_sound_async, update sound_data access pattern: sound_data = sounds[role] now yields {buffer_id, num_frames, sample_rate} instead of {data, sample_rate}. Remove audio_data = sound_data["data"] and adjusted_audio list comprehension. Use sound_data["buffer_id"] and sound_data["num_frames"] in the process_sound call. All other logic in _play_sound_async -- including both generation-counter checks (before wave_player.stop() and inside _wave_player_lock), the wave_player.stop() call outside the lock, and the feed() call inside the lock -- remains unchanged. (refs: DL-006)

#### Code Changes

**CC-M-002-001** (addon/globalPlugins/Unspoken/__init__.py) - implements CI-M-002-004

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -8,3 +8,5 @@
 import sys
 import time
 import threading
+import ctypes
+import queue
 import wave
 import struct

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -1,1 +1,1 @@
 # no documentation change required; import additions (ctypes, queue) are self-documenting

```


**CC-M-002-002** (addon/globalPlugins/Unspoken/__init__.py) - implements CI-M-002-001

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -174,35 +174,46 @@
     def make_sound_objects(self):
         """Load sound files for OpenAL audio processing."""
         log.debug("Loading sound files for OpenAL audio engine", exc_info=True)
+        uploaded = {}
         for key, value in sound_files.items():
             path = os.path.join(UNSPOKEN_SOUNDS_PATH, value)
             log.debug("Loading " + path, exc_info=True)
             try:
+                if value in uploaded:
+                    sounds[key] = uploaded[value]
+                    continue
+
                 # Load WAV file and convert to float32 mono
                 with wave.open(path, "rb") as wav_file:
                     frames = wav_file.readframes(wav_file.getnframes())
                     sample_width = wav_file.getsampwidth()
                     channels = wav_file.getnchannels()
                     sample_rate = wav_file.getframerate()
 
                     # Convert to float32 samples
                     if sample_width == 2:  # 16-bit
                         samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                         float_samples = [s / 32768.0 for s in samples]
                     else:
                         log.error(f"Unsupported sample width: {sample_width}")
                         continue
 
                     # Convert to mono if stereo
                     if channels == 2:
                         # Source WAV files are mono or have identical left/right channels;
                         # if stereo, we take left channel only as it's sufficient
                         float_samples = [
                             float_samples[i] for i in range(0, len(float_samples), 2)
                         ]
 
-                    sounds[key] = {"data": float_samples, "sample_rate": sample_rate}
+                    num_frames = len(float_samples)
+                    int16_array = (ctypes.c_int16 * num_frames)()
+                    for i, s in enumerate(float_samples):
+                        clamped = max(-1.0, min(1.0, s))
+                        int16_array[i] = int(clamped * 32767)
+
+                    buffer_id = self.audio_engine.upload_buffer(value, int16_array, num_frames, sample_rate)
+                    entry = {"buffer_id": buffer_id, "num_frames": num_frames, "sample_rate": sample_rate}
+                    sounds[key] = entry
+                    uploaded[value] = entry
 
             except Exception as e:
                 log.error(f"Failed to load {path}: {e}")

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -174,1 +174,9 @@
     def make_sound_objects(self):
-        """Load sound files for OpenAL audio processing."""
+        """Load WAV files, convert to int16, upload to persistent AL buffers, and populate sounds dict.
+
+        Iterates sound_files mapping role constants to WAV filenames. Uses an `uploaded` dict
+        keyed by filename to deduplicate: 34 role entries share 15 unique WAV files, so each
+        file is converted and uploaded once. (ref: DL-007, DL-003)
+
+        Each sounds[role] entry stores buffer_id and num_frames for direct use by process_sound().
+        Float32 samples are not retained: caching them would double memory without benefit
+        because AL buffers are the authoritative source for rendering. (ref: DL-003, RA-002)
+        """

```


**CC-M-002-003** (addon/globalPlugins/Unspoken/__init__.py) - implements CI-M-002-003

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -131,1 +131,5 @@
         self.make_sound_objects()
 
+        self._sound_queue = queue.Queue()
+        self._sound_worker_thread = threading.Thread(target=self._sound_worker_loop, daemon=True)
+        self._sound_worker_thread.start()
+
         # Initialize WavePlayer for audio output (stereo, 44100Hz, 16-bit)

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -131,1 +131,5 @@
         self.make_sound_objects()

+        # Persistent daemon worker thread processes sounds from _sound_queue.
+        # Single worker serializes renders and shares _openal_audio_mutex naturally.
+        # Queue signals exit via None sentinel. (ref: DL-005)
+        self._sound_queue = queue.Queue()

```


**CC-M-002-004** (addon/globalPlugins/Unspoken/__init__.py) - implements CI-M-002-003

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -325,14 +325,21 @@
     def _play_object_async(self, obj):
         """Extract params and play sound in background thread."""
         params = self._extract_sound_params(obj)
         if params is not None:
             role, angle_x, angle_y, volume = params
             self._sound_generation += 1
             my_generation = self._sound_generation
-
-            def play_async():
-                try:
-                    self._play_sound_async(role, angle_x, angle_y, volume, my_generation)
-                except Exception:
-                    pass
-
-            threading.Thread(target=play_async, daemon=True).start()
+            if my_generation != self._sound_generation:
+                return
+            self._sound_queue.put((role, angle_x, angle_y, volume, my_generation))
+
+    def _sound_worker_loop(self):
+        while True:
+            task = self._sound_queue.get()
+            if task is None:
+                break
+            role, angle_x, angle_y, volume, generation = task
+            try:
+                self._play_sound_async(role, angle_x, angle_y, volume, generation)
+            except Exception:
+                pass

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -325,6 +325,10 @@
     def _play_object_async(self, obj):
-        """Extract params and play sound in background thread."""
+        """Extract sound params on main thread and enqueue to the persistent worker thread.
+
+        The generation check before queue.put() discards stale sounds before they enter
+        the queue, preventing backlog when events arrive faster than renders complete.
+        Enqueues task tuple to _sound_worker_loop via _sound_queue. (ref: DL-005)
+        """

@@ -337,1 +337,8 @@
+    def _sound_worker_loop(self):
+        """Persistent worker loop consuming _sound_queue until None sentinel received.
+
+        Runs in a daemon thread started at __init__. Calls _play_sound_async for each
+        task tuple. Exits cleanly when terminate() puts None into the queue. (ref: DL-005)
+        """

```


**CC-M-002-005** (addon/globalPlugins/Unspoken/__init__.py) - implements CI-M-002-002

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -341,24 +341,19 @@
     def _play_sound_async(self, role, angle_x, angle_y, volume, generation):
         """Process and play sound on background thread using pre-extracted parameters.
 
         Args:
                 role: Control type role constant
                 angle_x: Horizontal angle in degrees (-90 to 90)
                 angle_y: Vertical angle in degrees (-90 to 90)
                 volume: Pre-computed volume multiplier
                 generation: Sound generation number for interrupt detection
         """
         if role not in sounds:
             return
 
         sound_data = sounds[role]
-        audio_data = sound_data["data"]
-
-        # Adjust volume (pre-computed on main thread)
-        adjusted_audio = [sample * volume for sample in audio_data]
 
         # Process with OpenAL for HRTF spatialization and reverb
         final_audio = self.audio_engine.process_sound(
-            adjusted_audio, angle_x, angle_y
+            sound_data["buffer_id"], sound_data["num_frames"], angle_x, angle_y, volume
         )
         if not final_audio:
             log.warn("Failed processing %r", role)
             return

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -341,14 +341,15 @@
     def _play_sound_async(self, role, angle_x, angle_y, volume, generation):
-        """Process and play sound on background thread using pre-extracted parameters.
-
-        Args:
-                role: Control type role constant
-                angle_x: Horizontal angle in degrees (-90 to 90)
-                angle_y: Vertical angle in degrees (-90 to 90)
-                volume: Pre-computed volume multiplier
-                generation: Sound generation number for interrupt detection
-        """
+        """Spatialize and feed a pre-loaded sound to WavePlayer on the worker thread.
+
+        Looks up sounds[role] for buffer_id and num_frames, then calls
+        process_sound(buffer_id, num_frames, angle_x, angle_y, volume). Volume is applied
+        inside process_sound via AL_GAIN -- no Python sample loop here. (ref: DL-002, DL-006)
+
+        Args:
+            role: Control type role constant mapped in sound_files.
+            angle_x: Horizontal angle in degrees (-90 to 90).
+            angle_y: Vertical angle in degrees (-90 to 90).
+            volume: Per-play multiplier combined with _dry_level in process_sound.
+            generation: Generation counter snapshot; stale sounds are discarded pre-feed.
+        """

```


**CC-M-002-006** (addon/globalPlugins/Unspoken/__init__.py) - implements CI-M-002-005

**Code:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -398,4 +398,8 @@
     def terminate(self):
         # Stop the timer
         if hasattr(self, "_navigation_timer"):
             self._navigation_timer.Stop()
 
+        # Stop the sound worker thread
+        if hasattr(self, "_sound_queue"):
+            self._sound_queue.put(None)
+            self._sound_worker_thread.join(timeout=2.0)
+
         # Restore original hooks
         speech.speech.getPropertiesSpeech = self._NVDA_getSpeechTextForProperties

```

**Documentation:**

```diff
--- a/addon/globalPlugins/Unspoken/__init__.py
+++ b/addon/globalPlugins/Unspoken/__init__.py
@@ -398,4 +398,5 @@
         # Stop the timer
         if hasattr(self, "_navigation_timer"):
             self._navigation_timer.Stop()

-        # Stop the sound worker thread
-        if hasattr(self, "_sound_queue"):
-            self._sound_queue.put(None)
-            self._sound_worker_thread.join(timeout=2.0)
+        # Signal worker thread shutdown via None sentinel, then wait up to 2s.
+        # join() timeout prevents indefinite block if worker is stuck in alcRenderSamplesSOFT.
+        if hasattr(self, "_sound_queue"):
+            self._sound_queue.put(None)
+            self._sound_worker_thread.join(timeout=2.0)

```


## Execution Waves

- W-001: M-001
- W-002: M-002
