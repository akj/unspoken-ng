# unspoken-ng

NVDA addon that replaces spoken control-role announcements with spatially positioned sounds (OpenAL Soft HRTF + reverb). Python, running inside NVDA's runtime; see `README.md` for build/packaging via scons.

## Domain vocabulary

`CONTEXT.md` defines the shared vocabulary for the audio pipeline — role sound, audio engine, sound player, onset latency, sound theme, slot, ducking. Each entry records the preferred term and the synonyms to avoid. Use those words in code, commits, and issues; when a term shifts meaning, update `CONTEXT.md` in the same change.

Architectural decisions are recorded as ADRs in `docs/adr/` — one file per decision, capturing the options considered and why the losing ones lost.

## Issue tracker

Issues live on GitHub at `akj/unspoken-ng`, managed with the `gh` CLI. Note that a bare `gh` command may resolve to the upstream fork, so pass `--repo akj/unspoken-ng` explicitly.

Triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.

## Working in this repo

- The addon runs inside NVDA's process. Blocking the main thread delays speech, so keep work off it and treat onset latency as the primary quality bar.
- Scratch work — research notes, spikes, measurement runs — belongs in `.scratch/`, which is not tracked. Durable conclusions belong in `CONTEXT.md`, an ADR, or the issue thread.
