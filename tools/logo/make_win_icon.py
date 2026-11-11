"""Build the Windows icon, desktop/mmm_app/resources/app-win.ico, from logo-win.png.

logo-win.png is logo-win.svg rendered at 1024 px (make_logo.py, then
`node render.js logo-win`). Windows picks the size it needs from the .ico (16 px in
title bars, 24-32 px on the taskbar, 40-64 px in Start and Explorer, 256 px for
large icons), so every size is stored. The small ones are sharpened after the
resize, since a plain downscale of a 1024 px drawing blurs the mesh's edges and the
seam into the background at 16-32 px.

Usage: python make_win_icon.py [logo-win.png] [out.ico]
"""
import os
import sys

from PIL import Image, ImageFilter

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)  # px: what Windows asks for at 100-200% scaling
SHARPEN_BELOW = 64                                   # px: sizes under this get an unsharp mask
HERE = os.path.dirname(os.path.abspath(__file__))


def build(src, out):
    big = Image.open(src).convert("RGBA")
    frames = []
    for s in SIZES:
        im = big.resize((s, s), Image.LANCZOS)
        if s < SHARPEN_BELOW:
            im = im.filter(ImageFilter.UnsharpMask(radius=0.6, percent=80, threshold=1))
        frames.append(im)
    # (Pillow writes one image per entry of sizes, taking each from the frame of that size)
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    return out


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "logo-win.png")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        HERE, "..", "..", "desktop", "mmm_app", "resources", "app-win.ico")
    print(build(src, out))
