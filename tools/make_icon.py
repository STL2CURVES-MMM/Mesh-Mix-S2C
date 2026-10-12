"""Draw the Manifold Mesh Mix app icon and build AppIcon.icns.

A dark squircle with a sphere that is a low-poly triangle mesh on the left
half and a smooth, glossy surface on the right half, joined by a glowing
accent seam.
"""
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ACCENT = (34, 211, 238)
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "app/Resources"


def squircle_mask(size, pad, radius_frac=0.225):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = int((size - 2 * pad) * radius_frac)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=r, fill=255)
    return m


def draw(size=1024):
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pad = int(S * 0.098)
    # background gradient
    y = np.linspace(0, 1, S)[:, None]
    x = np.linspace(0, 1, S)[None, :]
    top = np.array([24, 27, 34])
    bot = np.array([9, 10, 13])
    t = np.clip(y * 0.85 + x * 0.15, 0, 1)
    bg = (top[None, None, :] * (1 - t[..., None]) + bot[None, None, :] * t[..., None]).astype(np.uint8)
    bg_img = Image.fromarray(np.dstack([bg, np.full((S, S), 255, np.uint8)]), "RGBA")
    mask = squircle_mask(S, pad)
    # drop shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shm = squircle_mask(S, pad).filter(ImageFilter.GaussianBlur(S * 0.02))
    sh.putalpha(shm.point(lambda v: int(v * 0.55)))
    img = Image.alpha_composite(img, sh.transform(sh.size, Image.AFFINE, (1, 0, 0, 0, 1, -S * 0.012)))
    card = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    card.paste(bg_img, (0, 0), mask)
    img = Image.alpha_composite(img, card)

    # subtle grid
    grid = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = S // 16
    for k in range(0, S, step):
        gd.line([(k, 0), (k, S)], fill=(255, 255, 255, 10), width=max(1, S // 512))
        gd.line([(0, k), (S, k)], fill=(255, 255, 255, 10), width=max(1, S // 512))
    gmask = Image.new("L", (S, S), 0)
    gmask.paste(mask, (0, 0))
    grid.putalpha(Image.fromarray(np.minimum(np.array(grid.split()[3]), np.array(gmask))))
    img = Image.alpha_composite(img, grid)

    # sphere
    cx, cy, R = S * 0.5, S * 0.52, S * 0.29
    light = np.array([-0.45, -0.55, 0.70])
    light /= np.linalg.norm(light)

    # smooth right half via per-pixel shading
    yy, xx = np.mgrid[0:S, 0:S]
    nx = (xx - cx) / R
    ny = (yy - cy) / R
    rr = nx * nx + ny * ny
    inside = rr <= 1.0
    nz = np.sqrt(np.clip(1 - rr, 0, 1))
    lam = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0, 1)
    h = np.array([0.0, 0.0, 1.0]) + light
    h /= np.linalg.norm(h)
    spec = np.clip(nx * h[0] + ny * h[1] + nz * h[2], 0, 1) ** 60
    base = np.array(ACCENT) / 255.0
    col = (0.10 + 0.85 * lam)[..., None] * base[None, None, :] + spec[..., None] * 0.95
    rim = np.clip(1 - nz, 0, 1) ** 3
    col += rim[..., None] * base[None, None, :] * 0.35
    col = np.clip(col, 0, 1)
    right = inside & (xx >= cx)
    smooth = np.zeros((S, S, 4), np.uint8)
    smooth[..., :3] = (col * 255).astype(np.uint8)
    smooth[..., 3] = np.where(right, 255, 0)
    smooth_img = Image.fromarray(smooth, "RGBA").filter(ImageFilter.SMOOTH)
    img = Image.alpha_composite(img, smooth_img)

    # low-poly left half: icosphere-ish triangles projected orthographically
    tris = icosphere(2)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    polys = []
    for a, b, c in tris:
        # the y flip to screen space mirrors the mesh, so reverse the winding
        n = np.cross(c - a, b - a)
        n /= np.linalg.norm(n)
        cen = (a + b + c) / 3
        if n[2] <= 0:
            continue
        if cen[0] > 0.25:
            continue
        shade = max(0.0, float(n @ light))
        g = 0.18 + 0.62 * shade
        fill = tuple(int(v) for v in (np.array([150, 160, 178]) * g + 20))
        pts = [(cx + p[0] * R, cy + p[1] * R) for p in (a, b, c)]
        polys.append((cen[2], pts, fill))
    polys.sort(key=lambda t: t[0])
    for _, pts, fill in polys:
        ld.polygon(pts, fill=fill + (255,))
    for _, pts, _ in polys:
        ld.line(pts + [pts[0]], fill=(12, 14, 18, 255), width=max(1, S // 220))
    # clip the faceted half to the left side
    lm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(lm).rectangle([0, 0, cx, S], fill=255)
    la = np.minimum(np.array(layer.split()[3]), np.array(lm))
    layer.putalpha(Image.fromarray(la))
    img = Image.alpha_composite(img, layer)

    # glowing seam
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    w = max(2, S // 110)
    gdraw.line([(cx, cy - R * 1.08), (cx, cy + R * 1.08)], fill=ACCENT + (255,), width=w)
    blur = glow.filter(ImageFilter.GaussianBlur(S * 0.018))
    img = Image.alpha_composite(img, blur)
    img = Image.alpha_composite(img, blur)
    img = Image.alpha_composite(img, glow)
    return img


def icosphere(level):
    t = (1 + 5 ** 0.5) / 2
    V = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
         (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    V = [np.array(v, float) / np.linalg.norm(v) for v in V]
    F = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
         (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
         (8, 6, 7), (9, 8, 1)]
    for _ in range(level):
        cache = {}
        nf = []

        def mid(i, j):
            k = (min(i, j), max(i, j))
            if k not in cache:
                m = V[i] + V[j]
                V.append(m / np.linalg.norm(m))
                cache[k] = len(V) - 1
            return cache[k]

        for a, b, c in F:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        F = nf
    # rotate a bit so the silhouette looks interesting; flip y for screen coords
    rot = rotation(0.35, -0.5)
    out = []
    for a, b, c in F:
        pa, pb, pc = (rot @ V[a]), (rot @ V[b]), (rot @ V[c])
        flip = np.array([1, -1, 1])
        out.append((pa * flip, pb * flip, pc * flip))
    return out


def rotation(ax, ay):
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    return Ry @ Rx


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    big = draw(1024)
    big.save(os.path.join(OUT_DIR, "AppIcon-1024.png"))
    iconset = os.path.join(OUT_DIR, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for s in (16, 32, 128, 256, 512):
        big.resize((s, s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}.png"))
        big.resize((2 * s, 2 * s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}@2x.png"))
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(OUT_DIR, "AppIcon.icns")], check=True)
    print("icon written")


if __name__ == "__main__":
    main()
