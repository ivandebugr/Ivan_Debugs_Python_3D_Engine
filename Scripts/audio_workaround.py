"""audio_workaround.py — OpenAL crash workaround, import before `from ursina import *`.

ursina/audio.py calls AudioManager.create_AudioManager() at import time, which
crashes inside libp3openal_audio.dylib against this machine's audio setup
(macOS 15.7.4; reproduces even isolated from Ursina, independent of which
audio device is targeted — see obsidian-mind-main/brain/Gotchas.md). Setting
audio-library-name makes it create a NullAudioManager instead.

Every entry point that imports ursina must import this module first, before
`from ursina import *`. Zero project/ursina dependencies by design.
"""
from panda3d.core import loadPrcFileData

loadPrcFileData('', 'audio-library-name null')
