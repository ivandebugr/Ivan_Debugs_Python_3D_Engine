"""audio_workaround.py — OpenAL crash workaround, import before `from ursina import *`.

Panda3D 1.10.16 switched macOS from Apple's OpenAL to OpenAL Soft. On this
machine (macOS 15.7.4) OpenAL Soft SIGSEGVs at import time inside its HRTF
loader:

    EXC_BAD_ACCESS (SIGSEGV) at 0x8
    libp3openal_audio.dylib  GetLoadedHrtf(...)
    libp3openal_audio.dylib  aluInitRenderer(...)
    libp3openal_audio.dylib  alcCreateContext
    libp3openal_audio.dylib  OpenALAudioManager::OpenALAudioManager()

Disabling HRTF (an OpenAL Soft feature we don't use — the game is stereo, not
binaural) avoids the crashing code path, so real OpenAL initializes and plays
sound. Verified: SFX audible, 5/5 stable init, real .ogg plays to completion.
panda3d 1.10.15 didn't crash (still used Apple OpenAL); this fix keeps us on the
pinned 1.10.16 with working audio instead of downgrading.

Mechanism: write an OpenAL Soft config with `hrtf = false` and point
`ALSOFT_CONF` at it BEFORE the audio dylib loads (ursina loads it when it calls
create_AudioManager() at import). Every entry point that imports ursina must
import this module first, before `from ursina import *`.

If real OpenAL init ever fails despite this, ursina falls back to
NullAudioManager on its own (logged as an audio error), so audio goes silent
rather than crashing.

Zero project dependencies by design.
"""
import os
import tempfile

from panda3d.core import loadPrcFileData

# Write the HRTF-disable config to a temp file and point OpenAL Soft at it.
# Must be in the environment before the audio dylib is first loaded.
_conf_path = os.path.join(tempfile.gettempdir(), 'skyjumper_alsoft.conf')
try:
    with open(_conf_path, 'w') as _f:
        _f.write('[general]\nhrtf = false\n')
    os.environ.setdefault('ALSOFT_CONF', _conf_path)
    loadPrcFileData('', 'audio-library-name p3openal_audio')
except Exception:
    # Couldn't stage the config — fall back to the null manager so the game
    # still launches (silent) instead of crashing on OpenAL init.
    loadPrcFileData('', 'audio-library-name null')
