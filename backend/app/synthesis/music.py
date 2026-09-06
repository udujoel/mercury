"""
The intro, outro and interlude a briefing is topped and tailed with.

Three sets live in `assets/music/`, each an intro, an outro and a shorter
interlude. See that directory's README for what they sound like and where
they came from.

**Nothing here computes an offset.** That is deliberate and it is the whole
reason this module is thin: `stitch` adds music to the same `cursor_ms` it
advances for every turn, so a prepended intro moves every turn, chapter,
word timing and deep-link for free. A helper here that returned "how much
to shift by" would be a second place doing that arithmetic, which is
exactly the failure ADR-006 named.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from pydub import AudioSegment

log = logging.getLogger(__name__)

# `backend/app/synthesis/music.py` -> repo root -> assets/music
MUSIC_ROOT = Path(__file__).resolve().parents[3] / "assets" / "music"

# The set used when settings say nothing. Warm and unhurried, which suits a
# briefing you listen to before you are fully awake.
DEFAULT_SET = "paper"

# A set named this means silence, and is not a missing set. Someone who
# wants no music should be able to say so rather than have it read as "use
# the default".
NO_MUSIC = "none"

# Music sits under the voice, not beside it. The tracks were generated at
# their own level and the hosts are loudness-normalised to TARGET_DBFS, so
# without this the intro arrives noticeably louder than the people.
MUSIC_GAIN_DB = -8.0

# How long the bed keeps playing UNDER the voice, and how far below the
# music's own level it sits while it does.
#
# The roadmap asked for the music to be ducked under the first and last
# turns rather than hard-cut, and the obvious implementations both move
# every timing in the manifest: a crossfade shortens the file, and appending
# a tail lengthens it. `overlay` does neither — it mixes the tail INTO audio
# that already exists, so the file is exactly as long as it was and ADR-006's
# rule that offsets are computed in one place survives untouched.
#
# 2.5 seconds is about a sentence: long enough to read as the bed receding
# under the host rather than as a glitch, short enough that it is gone
# before the first fact.
DUCK_MS = 2500
DUCK_GAIN_DB = -10.0


@dataclass(frozen=True)
class MusicSet:
    """The three pieces, decoded. Any of them may be None.

    None is ordinary rather than an error: a set with no interlude is a
    briefing with no transitions, which is a fine thing to be. `stitch`
    checks each one, so a missing file costs that piece and nothing else —
    rule 7 at the level of a sound effect.
    """

    name: str
    intro: AudioSegment | None
    outro: AudioSegment | None
    interlude: AudioSegment | None

    @property
    def is_silent(self) -> bool:
        return self.intro is None and self.outro is None and self.interlude is None


def load_music_set(name: str | None) -> MusicSet | None:
    """Load a set by name, or None when the briefing should have no music.

    Returns None for `"none"` and for an unknown name. Unknown rather than
    invalid: this value comes from a settings artifact written by a client
    that may be newer than this build, so a name we do not recognise is an
    ordinary event and must not fail the only run of the day. It is logged,
    because a briefing quietly losing its music is the kind of change nobody
    reports and everybody notices.
    """
    chosen = (name or DEFAULT_SET).strip().lower()
    if chosen == NO_MUSIC:
        return None

    # Checked ONCE, up front, rather than discovered three times inside
    # `_load`. Without it a machine with no ffmpeg logged the same failure
    # per track and said nothing about the cause being shared.
    #
    # Not a reason to bundle WAV instead. ffmpeg is already a hard
    # requirement of the production audio path — `_export` needs it for
    # mp4/aac and falls back to WAV without it — so a run that cannot
    # decode these mp3s is a run already shipping uncompressed audio. Both
    # workflows install it. Trading 1.7 MB of mp3 for roughly 10 MB of WAV
    # to remove a dependency the pipeline still has either way is the wrong
    # side of that bargain.
    if not which("ffmpeg"):
        log.error(
            "ffmpeg is not installed, so the music cannot be decoded and "
            "this briefing will ship WITHOUT it. The same missing binary "
            "also forces uncompressed WAV output — install ffmpeg.",
        )
        return None

    directory = MUSIC_ROOT / chosen
    if not directory.is_dir():
        log.error(
            "music set %r not found in %s; this briefing has no music. "
            "Known sets: %s",
            chosen, MUSIC_ROOT, ", ".join(available_sets()) or "none",
        )
        return None

    loaded = MusicSet(
        name=chosen,
        intro=_load(directory / "intro.mp3"),
        outro=_load(directory / "outro.mp3"),
        interlude=_load(directory / "interlude.mp3"),
    )
    if loaded.is_silent:
        log.error("music set %r has no readable tracks in %s", chosen, directory)
        return None
    return loaded


def available_sets() -> list[str]:
    """Set names on disk, for error messages and for tests that must not
    hardcode a list that can drift from the directory."""
    if not MUSIC_ROOT.is_dir():
        return []
    return sorted(p.name for p in MUSIC_ROOT.iterdir() if p.is_dir())


def _load(path: Path) -> AudioSegment | None:
    if not path.is_file():
        return None
    try:
        clip = AudioSegment.from_file(path)
    except FileNotFoundError:
        # ffprobe, not the track. pydub shells out to decode mp3, and what
        # it raises names a missing BINARY rather than a missing asset —
        # which sends whoever reads the log looking for the wrong thing.
        log.error(
            "could not decode music at %s because ffmpeg/ffprobe is not "
            "installed. The briefing will ship WITHOUT music. Install "
            "ffmpeg on this machine; the Actions workflows both do.",
            path,
        )
        return None
    except Exception:
        # A corrupt or truncated asset. Losing one sting is survivable;
        # failing the run over it is not.
        log.error("could not decode music at %s; skipping it", path, exc_info=True)
        return None
    return clip.apply_gain(MUSIC_GAIN_DB)
