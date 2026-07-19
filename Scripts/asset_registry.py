"""Asset registry — pure I/O layer for the v1.3 asset import pipeline (Step 1).

Scans assets/textures, assets/models and assets/sounds, builds a {name: path}
manifest per category, and persists it to assets/manifest.json. On startup it
loads from the cached manifest when the recorded mtimes still match disk,
skipping a full rescan.

Framework-free by design: this module imports nothing from Ursina, Panda3D,
main.py or level_editor.py. The editor drives hot-reload by calling poll() on
a timer (a later step); poll() fires per-category callbacks on file changes.
A single read failure (permissions, corrupt manifest) is skipped, never fatal.
"""
import json
import os
from pathlib import Path

ASSET_ROOT = Path('assets')

CATEGORY_DIRS = {
    'texture': ASSET_ROOT / 'textures',
    'model':   ASSET_ROOT / 'models',
    'sound':   ASSET_ROOT / 'sounds',
}

CATEGORY_EXTENSIONS = {
    'texture': {'.png', '.jpg', '.jpeg'},
    'model':   {'.obj', '.gltf', '.glb', '.egg'},
    'sound':   {'.wav', '.ogg'},
}

MANIFEST_PATH = ASSET_ROOT / 'manifest.json'

# Bumped whenever the scan's keying scheme changes so a manifest written by an
# older build is rejected and rebuilt rather than silently reused. v2 = the
# recursive scan with qualified 'subdir/name' keys for subfolder files.
MANIFEST_SCHEMA_VERSION = 2


class AssetRegistry:
    """Scans asset folders and tracks files for hot-reload."""

    def __init__(self):
        # Per-category manifests: {name (filename without extension): path}.
        self.textures: dict[str, str] = {}
        self.models: dict[str, str] = {}
        self.sounds: dict[str, str] = {}
        # {category: {fn, ...}} change callbacks fired by poll().
        self._callbacks: dict[str, list] = {'texture': [], 'model': [], 'sound': []}
        # {path: st_mtime} for every tracked file — the poll() baseline.
        self._mtimes: dict[str, float] = {}

        self._ensure_dirs()
        if not self._load_from_cache():
            self.rebuild()

    # -- public API ----------------------------------------------------------

    def rebuild(self):
        """Full rescan of all three folders; regenerate manifest.json."""
        self.textures = self._scan_category('texture')
        self.models = self._scan_category('model')
        self.sounds = self._scan_category('sound')
        self._mtimes = self._collect_mtimes()
        self._write_manifest()

    def get_texture_path(self, name: str) -> str | None:
        # Ursina sets Texture.name to the filename *with* extension, so level.json
        # stores 'texture_orange_test.png' while the manifest keys by stem
        # ('texture_orange_test'). Look up the name as given first (registry keys,
        # picker output), then retry on the stem so a saved '.png' name still
        # resolves on reload instead of falling through to the asset_folder glob
        # (which misses → texture=None → next save writes '', stripping it).
        return self.textures.get(name) or self.textures.get(Path(name).stem)

    def get_model_path(self, name: str) -> str | None:
        return self.models.get(name)

    def get_sound_path(self, name: str) -> str | None:
        return self.sounds.get(name)

    def register_callback(self, category: str, fn):
        """Register fn(name, path), called by poll() when a file in `category`
        ('texture' | 'model' | 'sound') changes on disk."""
        if category in self._callbacks:
            self._callbacks[category].append(fn)

    def poll(self):
        """Check st_mtime of every tracked file; fire callbacks on change.

        Call this on a timer (the editor uses Ursina's invoke at a 2s interval).
        No background thread — this is a plain synchronous method.
        """
        for category, manifest in self._manifests():
            for name, path in manifest.items():
                try:
                    mtime = os.stat(path).st_mtime
                except Exception as e:
                    print(f'AssetRegistry.poll stat failed for {path}: {e}')
                    continue
                if self._mtimes.get(path) != mtime:
                    self._mtimes[path] = mtime
                    for fn in self._callbacks[category]:
                        fn(name, path)

    # -- internals -----------------------------------------------------------

    def _manifests(self):
        """Yield (category, manifest dict) for each category."""
        yield 'texture', self.textures
        yield 'model', self.models
        yield 'sound', self.sounds

    def _ensure_dirs(self):
        for directory in CATEGORY_DIRS.values():
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f'AssetRegistry: could not create {directory}: {e}')

    def _scan_category(self, category: str) -> dict[str, str]:
        """Recursively scan a category folder into a {name: path} manifest.

        Keying scheme (collision-safe): a file directly under the category root
        keeps its bare stem as the key ('floor_stone') — this preserves every
        existing flat-name lookup unchanged. A file in a subfolder is keyed by
        its POSIX relative path without extension ('ui/Green/Default/button'),
        which is unique by construction, so recursively-discovered duplicates
        (the Kenney UI pack repeats 'button.png' across 6 theme folders) never
        overwrite each other in the dict. The only residual collision is two
        files that differ only by extension in the same folder; that is logged.
        """
        directory = CATEGORY_DIRS[category]
        extensions = CATEGORY_EXTENSIONS[category]
        manifest: dict[str, str] = {}
        try:
            entries = sorted(directory.rglob('*'))
        except Exception as e:
            print(f'AssetRegistry: could not scan {directory}: {e}')
            return manifest
        for entry in entries:
            try:
                if not entry.is_file() or entry.suffix.lower() not in extensions:
                    continue
                rel = entry.relative_to(directory)
                if rel.parent == Path('.'):
                    key = entry.stem                       # top-level: bare stem
                else:
                    key = rel.with_suffix('').as_posix()   # subfolder: qualified path
                if key in manifest:
                    print(f'AssetRegistry: {category} key collision on {key!r} '
                          f'({manifest[key]} vs {entry}); keeping first')
                    continue
                manifest[key] = str(entry)
            except Exception as e:
                print(f'AssetRegistry: skipped {entry}: {e}')
        return manifest

    def _collect_mtimes(self) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for _category, manifest in self._manifests():
            for path in manifest.values():
                try:
                    mtimes[path] = os.stat(path).st_mtime
                except Exception as e:
                    print(f'AssetRegistry: could not stat {path}: {e}')
        return mtimes

    def _write_manifest(self):
        data = {
            'schema': MANIFEST_SCHEMA_VERSION,
            'textures': self.textures,
            'models': self.models,
            'sounds': self.sounds,
            'mtimes': self._mtimes,
        }
        try:
            ASSET_ROOT.mkdir(parents=True, exist_ok=True)
            with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f'AssetRegistry: could not write {MANIFEST_PATH}: {e}')

    def _load_from_cache(self) -> bool:
        """Populate manifests from manifest.json when its recorded mtimes still
        match disk. Returns True on a cache hit (no rescan), False otherwise."""
        try:
            with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return False

        # A manifest without the current schema marker predates the recursive
        # scan; discard it and force one full rebuild so we never run on cached
        # data that doesn't reflect the current keying scheme.
        if isinstance(data, dict) and data.get('schema') != MANIFEST_SCHEMA_VERSION:
            print('AssetRegistry: manifest schema outdated, rebuilding')
            return False

        try:
            textures = data['textures']
            models = data['models']
            sounds = data['sounds']
            cached_mtimes = data['mtimes']
        except (KeyError, TypeError) as e:
            print(f'AssetRegistry: malformed manifest, rescanning: {e}')
            return False

        # The cache is valid only if every recorded file still exists with the
        # recorded mtime, AND disk has no files the manifest is missing.
        all_paths = list(textures.values()) + list(models.values()) + list(sounds.values())
        for path in all_paths:
            try:
                if os.stat(path).st_mtime != cached_mtimes.get(path):
                    return False
            except Exception:
                return False
        if self._collect_disk_count() != len(all_paths):
            return False

        self.textures = textures
        self.models = models
        self.sounds = sounds
        self._mtimes = cached_mtimes
        print('AssetRegistry: loaded from cache')
        return True

    def _collect_disk_count(self) -> int:
        """Total number of recognised asset files currently on disk."""
        total = 0
        for category in CATEGORY_DIRS:
            total += len(self._scan_category(category))
        return total


# Module-level singleton.
asset_registry = AssetRegistry()
