#!/usr/bin/env python3
"""
Phase 0, Spike 5 — the gate that matters.

Synthesise a two-host script with two voices, stitch it with correct pacing,
and listen to it with headphones.

THE TEN SECOND TEST
If you would have turned it off in the first ten seconds, do not proceed to
Phase 1. Change the TTS provider first. Every decision downstream compounds on
this one, and no amount of prompt engineering rescues a flat voice.

    python scripts/spike_05_tts.py
    afplay out/spike_tts.m4a
"""

import asyncio
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from google.cloud import texttospeech  # noqa: E402
from pydub import AudioSegment  # noqa: E402
from pydub.effects import normalize  # noqa: E402

VOICE_A = os.getenv("TTS_VOICE_HOST_A", "en-US-Neural2-D")
VOICE_B = os.getenv("TTS_VOICE_HOST_B", "en-US-Neural2-F")

GAP_DIFFERENT = 220
GAP_SAME = 80
TARGET_DBFS = -16.0

# A realistic sample. Note: no filler words. Naturalness comes from the
# audio model. If this reads flat on the page but sounds alive in the
# headphones, the TTS provider is doing its job.
SCRIPT = [
    ("A", "Good morning Joel. It's Friday the twenty-first, just past seven."),
    ("B", "And you've got a genuinely strange looking day ahead."),
    ("A", "Strange how?"),
    ("B", "Three back to back reviews and then nothing after two. Either "
          "someone cleared your afternoon or you did and forgot."),
    ("A", "Let's assume it was deliberate. Weather first. Sixteen degrees in "
          "Aveiro, clouds burning off by ten, high of twenty-four."),
    ("B", "Classic late August. Now, your inbox. The interesting one is from "
          "the platform team about the provider upgrade."),
    ("A", "I'd push back slightly on interesting. It's a version bump."),
    ("B", "It's a version bump that touches every module you own. That's not "
          "nothing."),
    ("A", "Fair. Alright, that one goes on the list."),
]


def synthesize(client, text: str, voice_name: str) -> AudioSegment:
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code="en-US", name=voice_name
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.0,
        ),
    )
    return AudioSegment.from_mp3(io.BytesIO(response.audio_content))


async def main() -> None:
    client = texttospeech.TextToSpeechClient()
    combined = AudioSegment.silent(duration=0)

    for idx, (speaker, text) in enumerate(SCRIPT):
        voice = VOICE_A if speaker == "A" else VOICE_B
        print(f"  [{speaker}] {text[:64]}...")

        clip = synthesize(client, text, voice)
        clip = normalize(clip)
        clip = clip.apply_gain(TARGET_DBFS - clip.dBFS)

        if idx > 0:
            gap = GAP_SAME if SCRIPT[idx - 1][0] == speaker else GAP_DIFFERENT
            combined += AudioSegment.silent(duration=gap)

        combined += clip

    out = Path("out")
    out.mkdir(exist_ok=True)
    path = out / "spike_tts.m4a"
    combined.export(path, format="mp4", codec="aac", bitrate="64k",
                    parameters=["-ac", "1"])

    print(f"\n  Wrote {path}  ({len(combined) / 1000:.1f}s, "
          f"{path.stat().st_size / 1024:.0f}KB)")
    print("\n  Now listen with headphones.")
    print("  Would you have turned it off in the first ten seconds?")
    print("  If yes, change the TTS provider before writing any product code.")


if __name__ == "__main__":
    asyncio.run(main())
