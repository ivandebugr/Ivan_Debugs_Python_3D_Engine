"""Verification harness for the recursive asset-registry scan (v1.7).

Framework-free, like asset_registry itself — no Ursina/pytest fixtures needed,
runnable as a plain script. Confirms:

  1. Every top-level (single-level) asset still resolves under its original bare
     name — zero regression for existing gun models / block textures / sounds.
  2. Kenney subfolder images are discoverable via qualified 'subdir/name' keys,
     and the count matches the images actually on disk (868 expected).
  3. Recursive files with duplicate stems across theme folders do NOT overwrite
     each other (the collision the qualified-key scheme exists to prevent).
  4. Hot-reload poll() fires a callback for a recursively-discovered file change.

Run: python3 tests/test_asset_registry_recursive.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Scripts.asset_registry import (  # noqa: E402
    AssetRegistry, CATEGORY_DIRS, CATEGORY_EXTENSIONS,
)


def _disk_files(category):
    """Every recognised file on disk for a category, as (bare_or_qualified_key, path)."""
    directory = CATEGORY_DIRS[category]
    exts = CATEGORY_EXTENSIONS[category]
    out = {}
    for p in sorted(directory.rglob('*')):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(directory)
        key = p.stem if rel.parent == Path('.') else rel.with_suffix('').as_posix()
        out.setdefault(key, str(p))
    return out


def main():
    reg = AssetRegistry()
    failures = []

    def check(cond, msg):
        print(('  PASS' if cond else '  FAIL'), msg)
        if not cond:
            failures.append(msg)

    # 1. No regression: every top-level file resolves under its bare stem.
    print('[1] top-level assets resolve under original bare names')
    for category, manifest, getter in (
        ('texture', reg.textures, reg.get_texture_path),
        ('model', reg.models, reg.get_model_path),
        ('sound', reg.sounds, reg.get_sound_path),
    ):
        directory = CATEGORY_DIRS[category]
        exts = CATEGORY_EXTENSIONS[category]
        top = [p for p in directory.iterdir()
               if p.is_file() and p.suffix.lower() in exts]
        missing = [p.stem for p in top if getter(p.stem) != str(p)]
        check(not missing, f'{category}: all {len(top)} top-level names resolve '
              f'(missing: {missing})')

    # 2. Every disk file is reachable; qualified subfolder count matches disk.
    print('[2] recursive discovery: all disk files reachable, no silent loss')
    for category, manifest in (('texture', reg.textures),
                               ('model', reg.models),
                               ('sound', reg.sounds)):
        disk = _disk_files(category)
        missing = [k for k in disk if k not in manifest]
        check(not missing, f'{category}: {len(disk)} disk keys, '
              f'{len(missing)} unreachable')
    qualified_tex = [k for k in reg.textures if '/' in k]
    check(len(qualified_tex) == 868,
          f'texture: 868 qualified Kenney keys (got {len(qualified_tex)})')

    # 3. Duplicate stems across theme folders did not collapse.
    print('[3] cross-folder duplicate stems preserved (no overwrite)')
    dup = [k for k in qualified_tex if k.endswith('/button_square_flat')]
    check(len(dup) >= 2,
          f'button_square_flat present in >=2 theme folders (got {len(dup)}: {dup[:3]})')
    # Their target paths must be distinct files, not one shadowing the rest.
    paths = {reg.get_texture_path(k) for k in dup}
    check(len(paths) == len(dup), 'each duplicate key maps to a distinct path')

    # 4. Hot-reload fires for a recursively-discovered file.
    print('[4] poll() fires callback for a subfolder file change')
    target_key = qualified_tex[0]
    target_path = reg.get_texture_path(target_key)
    fired = []
    reg.register_callback('texture', lambda name, path: fired.append((name, path)))
    # Bump mtime forward deterministically (no wall-clock dependency).
    st = os.stat(target_path)
    os.utime(target_path, (st.st_atime, st.st_mtime + 5))
    try:
        reg.poll()
        check((target_key, target_path) in fired,
              f'callback fired for {target_key} (fired: {len(fired)})')
    finally:
        os.utime(target_path, (st.st_atime, st.st_mtime))  # restore

    print()
    if failures:
        print(f'RESULT: {len(failures)} FAILURE(S)')
        for f in failures:
            print('  -', f)
        sys.exit(1)
    print('RESULT: all checks passed')


if __name__ == '__main__':
    main()
