# Unspoken/

NVDA GlobalPlugin providing HRTF-spatialized earcons via OpenAL Soft loopback.

## Files

| File | What | When to read |
| ---- | ---- | ------------ |
| `__init__.py` | NVDA GlobalPlugin; event hooks, persistent-worker-thread audio dispatch, generation-counter interrupts, WAV-to-AL-buffer pre-upload at init | Adding sound triggers, modifying audio dispatch, debugging earcon playback |
| `openal_audio.py` | OpenAL Soft ctypes loopback wrapper; HRTF spatialization, EFX reverb, persistent AL buffer management, singleton | Modifying audio processing, debugging DLL issues, changing reverb or buffer parameters |
| `addonGui.py` | Settings panel; reverb sliders, HRTF/Reverb checkboxes, live update on change | Modifying user settings, adding config options |
| `soft_oal.dll` | OpenAL Soft Windows x64 binary (vendor, do not edit) | Never edit directly; replace only with official OpenAL Soft release |
| `README.md` | Architecture decisions, threading model, reverb parameter mapping | Understanding design rationale before modifying audio pipeline |

## Subdirectories

| Directory | What | When to read |
| --------- | ---- | ------------ |
| `sounds/` | WAV earcon files mapped to NVDA control roles | Adding new sounds, replacing existing earcons |
