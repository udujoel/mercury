# Music

Three sets. Each is an intro, an outro and an inter-segment interlude,
written to sound like they belong to the same show.

| Set | Character | Instruments |
|---|---|---|
| `paper` | Warm, unhurried, editorial | Felt piano, upright bass, brushed snare |
| `broadsheet` | Crisp and purposeful, news-forward | Muted staccato strings, marimba, low pulse |
| `first-light` | Very quiet and spacious | Ambient pad, distant clean guitar harmonic |

Intros and outros are ~8 seconds; interludes are ~3.

## Where these came from

Generated on 2026-09-05 with ElevenLabs Music (`eleven_music_v2`), nine
tracks at 900 credits each. The prompts are in the flow, and each one is
also the first line of the matching section above — regenerating a track
that does not sit right means changing that description rather than
starting from nothing.

They are committed rather than fetched at build time. Together they are
about 1.7 MB, they change approximately never, and the alternative is a
generation run that depends on a third-party API being up at 04:00 to
produce the same eight seconds it produced yesterday.

## The rule that matters when these are used

**Every turn's `start_ms` is an offset into the audio file.** Prepending
eight seconds of intro moves every turn, every chapter boundary and every
story deep-link by eight seconds. ADR-006 named this when ElevenLabs was
first planned, and it is why the stitcher has to treat music as a track
element alongside the turns rather than something concatenated afterwards
— so offsets are computed once, by the thing that already computes them.

It is invisible today. It becomes glaring the moment word-level
highlighting is on, because the words light up eight seconds ahead of the
voice.

## Interludes do not resolve

Deliberately. An interlude that lands on a resolved chord reads as the end
of the briefing, and the listener stops paying attention to the segment
that follows it. The intros and outros resolve; the interludes do not.
