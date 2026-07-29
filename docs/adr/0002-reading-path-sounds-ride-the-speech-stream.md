# Reading-path sounds ride the speech stream

The reading path (`TextInfo.getControlFieldSpeech` — browse-mode reading, quicknav, say-all,
the Word caret) no longer plays at hook time. The hook prepends a `speech.commands.CallbackCommand`
to the field's returned speech sequence; NVDA's speech manager converts it to a synth index and
fires it on the main thread when the synthesizer **reaches** the field. Position extraction stays
in the hook, at build time, on the main thread; the callback closes over `(slot, position)` and
calls `play`, which returns in ~0.1 ms.

The trigger was the first 2.0 smoke run (#52): a link's sound played seconds before say-all's
speech reached the link, and a table row with several controls fired every sound in one burst.
Both are the same fact — the hook runs when NVDA *composes* speech, and on the reading path
composition and utterance are decoupled: say-all queues lines ahead of the synth, and a
multi-control line builds all its fields in one pass. The object-event paths do not have this
problem, because a focus change cancels current speech and the freshly built utterance starts
immediately; they are unchanged, and their sounds still lead speech.

**This amends #10 decision 1.** "No syncing against the synth pipeline (it is unobservable from
our side)" was decided with the object-event model in mind, and its premise is false for the
reading path: the speech manager's index machinery is exactly that observation point —
`BaseCallbackCommand`s become `IndexCommand`s, `synthIndexReached` queues the handler onto the
main thread, and say-all's own read-ahead is built on it. The ordering contract splits:
**object-event sounds lead speech; reading-path sounds ride it.**

## Considered options

**Keep playing at build time.** The shipped behaviour. Zero risk and the best possible quicknav
onset (~20 ms after keypress), but the smoke run showed what it buys that onset with: sounds
seconds early under say-all and bursts on multi-control lines — the sound announces the wrong
moment, which for a replacement of speech is wrongness, not lateness.

**Own timing: delay heuristics or a scheduler.** Estimate when speech will reach the field and
schedule the sound. Rejected without measurement: it reintroduces the timers #31 deleted, and it
guesses at a pipeline NVDA will simply tell us about.

**Hybrid: play the navigation target immediately, callbacks for the rest.** Preserves the
sound-leads-speech onset on quicknav while fixing say-all and bursts. Rejected for now: the hook
sees fields, not utterances, so "first field of this utterance" needs utterance-boundary tracking
that does not currently exist on this path. Revisit if riding speech feels sluggish on quicknav
in smoke testing — the callback approach does not foreclose it.

**Callback in the sequence (chosen).** NVDA's designed mechanism for "when speech reaches here".
No timers of ours, no polling, nothing on the hot path beyond constructing one small command
object; #31's rule that every sound is traceable to a synchronous NVDA call still holds — the
call is the manager's index handling instead of the hook.

## Consequences

- **Reading-path sounds arrive with speech, not ahead of it.** On quicknav the sound now onsets
  with the synth's first audio (~50–150 ms after keypress, synth-dependent) instead of ~20 ms.
  Accepted: coinciding with the element being spoken is the announcement doing its job. The
  event→`play()` dispatch budget (§2) is untouched — dispatch now ends at command construction,
  and the play itself is 0.09 ms at fire time.
- **Interrupting speech drops unspoken sounds.** Indexes from cancelled utterances are discarded
  by the manager, so content never spoken is never sounded — previously an interrupted say-all
  had already fired sounds for text the user never heard. #10 decision 5 is refined, not
  reversed: voices already in the air still ring out; what changes is that queued-but-unreached
  sounds die with the utterance.
- **A synth without `synthIndexReached` falls back to build-time play.** The manager waits
  forever for unreported indexes (say-all is equally broken on such a synth). Every in-tree
  synth reports; the check is two attribute reads against a value the 32-bit bridge caches.
- **Volume and device are read at fire time**, because `play` reads its settings provider when
  called — a mid-say-all volume change now applies to the sounds not yet reached.
- **Callback precision is the synth's index granularity** — tens of milliseconds, not
  sample-accurate. Fine for "the sound plays as speech reaches the link"; not a mechanism for
  tighter sync, should anyone ever want it.
