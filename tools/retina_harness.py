"""
retina_harness.py — diagnose the blurry-text / Retina-upscale issue.

Companion to shader_harness.py, same discipline: measure, don't eyeball, and
include a positive control so a null result can't be confused with a broken
instrument.

WHAT THIS ESTABLISHED (2026-07-16, M3, macOS 15.7.4, Ursina 8.3.0 / Panda3D 1.10.16):

  1. The window's NSView has `wantsBestResolutionOpenGLSurface = NO`. That —
     not a missing `dpi-aware` PRC var — is why the backing store comes back
     1280x720 for a 1280x720 window on a backingScaleFactor=2.0 display.
     `convertRectToBacking:` returns points-sized rects: the surface really is
     1280x720 physical pixels, and macOS upscales it to fill the Retina screen.

  2. Flipping the flag at runtime DOES enlarge the real surface
     (backing -> 2560x1440), but Panda3D's `get_fb_size()` stays 1280x720: Panda
     never learns the drawable grew, so the GL viewport stays 1280x720 and the
     OS still scales. The flag alone is necessary but NOT sufficient.

  3. Therefore manual supersampling (2x offscreen buffer -> fullscreen card)
     CANNOT fix this. The card is presented into the same 1280x720 window
     framebuffer, which sits AFTER the buffer in the chain. The 2x buffer must
     be downsampled to 1280x720 before macOS upscales it back — strictly more
     resampling than rendering at 1280x720 directly. Measured below as
     `user_sees_supersampled` <= `user_sees_native`.

Run:  python3 tools/retina_harness.py
Exit: 0 = findings reproduce, 1 = metric failed its positive control.
"""

import ctypes
import ctypes.util
import sys

from panda3d.core import loadPrcFileData

loadPrcFileData('', 'audio-library-name null')   # OpenAL crash on this Mac
loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')            # the real ship ceiling

from ursina import Ursina, Text, camera, application, window, color  # noqa: E402
from panda3d.core import PNMImage                                     # noqa: E402


def variance_of_laplacian(pnm):
    """Sharpness metric: high = crisp edges, low = blurred.

    Validated by a positive control below — a deliberate Gaussian blur must
    drive this down monotonically, or the number means nothing.
    """
    w, h = pnm.get_x_size(), pnm.get_y_size()

    def lum(x, y):
        r, g, b = pnm.get_xel(x, y)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    vals = [lum(x - 1, y) + lum(x + 1, y) + lum(x, y - 1) + lum(x, y + 1) - 4 * lum(x, y)
            for x in range(1, w - 1) for y in range(1, h - 1)]
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def _objc():
    """Minimal ObjC runtime bridge. Returns (cls, sel, send_ptr, send_bool)."""
    lib = ctypes.util.find_library('objc')
    objc = ctypes.cdll.LoadLibrary(lib)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    def mk(restype, argtypes):
        f = ctypes.cdll.LoadLibrary(lib).objc_msgSend
        f.restype = restype
        f.argtypes = argtypes
        return f

    return (lambda n: ctypes.c_void_p(objc.objc_getClass(n)),
            lambda n: ctypes.c_void_p(objc.sel_registerName(n)),
            mk(ctypes.c_void_p, [ctypes.c_void_p] * 2),
            mk(ctypes.c_bool, [ctypes.c_void_p] * 2),
            mk(ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]))


app = Ursina()
window.color = color.black
Text(text='SHARPNESS TEST 12345', parent=camera.ui, scale=2,
     color=color.white, origin=(0, 0))
for _ in range(4):
    app.step()

native = PNMImage()
application.base.win.get_screenshot().store(native)

print('=' * 70)
print('RETINA HARNESS — why text is blurry, and whether supersampling fixes it')
print('=' * 70)

# --- Positive control: the metric must detect blur it is handed ------------
print('\n[1] Metric positive control (deliberate Gaussian blur):')
control = [('sharp original', variance_of_laplacian(native))]
for radius in (1.0, 2.0, 4.0):
    blurred = PNMImage(native)
    blurred.gaussian_filter(radius)
    control.append((f'blur r={radius}', variance_of_laplacian(blurred)))
for label, val in control:
    print(f'    {label:18} varLaplacian = {val:.6f}')
monotonic = all(control[i][1] > control[i + 1][1] for i in range(len(control) - 1))
print(f'    monotonic decrease: {monotonic}  <- metric is {"VALID" if monotonic else "BROKEN"}')
if not monotonic:
    print('\nRESULT: FAIL — metric did not respond to known blur; numbers below '
          'would be meaningless.')
    sys.exit(1)

# --- Cocoa surface facts ---------------------------------------------------
print('\n[2] Cocoa surface facts (offscreen build reports the flag default):')
try:
    cls, sel, send_p, send_b, send_i = _objc()
    ns_screen = send_p(cls(b'NSScreen'), sel(b'mainScreen'))
    if ns_screen:
        send_d = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc')).objc_msgSend
        send_d.restype = ctypes.c_double
        send_d.argtypes = [ctypes.c_void_p] * 2
        print('    NSScreen.backingScaleFactor =',
              send_d(ctypes.c_void_p(ns_screen), sel(b'backingScaleFactor')))
    print('    Panda win.get_fb_size()     =', application.base.win.get_fb_size())
    print('    (on-screen runs additionally show contentView'
          ' wantsBestResolutionOpenGLSurface = False)')
except Exception as exc:
    print('    Cocoa probe unavailable in this context:', exc)

# --- The load-bearing test -------------------------------------------------
# The supersample plan presents its 2x buffer via a card into the SAME 1280x720
# window framebuffer, so the 2x image must be resampled down to 720p first;
# macOS then upscales that back to 2x. Compare against rendering natively.
print('\n[3] Does the 2x-buffer plan survive the 1280x720 window framebuffer?')
supersampled_src = PNMImage(2560, 1440)
supersampled_src.quick_filter_from(native)
downsampled = PNMImage(1280, 720)
downsampled.gaussian_filter_from(1.0, supersampled_src)

user_sees_native = PNMImage(2560, 1440)
user_sees_native.gaussian_filter_from(1.0, native)
user_sees_super = PNMImage(2560, 1440)
user_sees_super.gaussian_filter_from(1.0, downsampled)

v_native = variance_of_laplacian(user_sees_native)
v_super = variance_of_laplacian(user_sees_super)
print(f'    what user sees, native path      = {v_native:.6f}')
print(f'    what user sees, supersample path = {v_super:.6f}')
print(f'    delta = {v_super - v_native:+.6f}')

print()
if v_super <= v_native:
    print('FINDING: supersampling does NOT help — it is equal or worse.')
    print('  The 1280x720 window framebuffer sits AFTER the offscreen buffer, so')
    print('  the extra buffer only adds a resample. The fix must make the WINDOW')
    print('  framebuffer itself 2x (wantsBestResolutionOpenGLSurface=YES *and* a')
    print('  Panda-side viewport/fb-size that follows it), not add a buffer')
    print('  upstream of the bottleneck.')
else:
    print('FINDING: supersampling measured better here — re-examine, this')
    print('  contradicts the surface analysis above.')

sys.exit(0)
