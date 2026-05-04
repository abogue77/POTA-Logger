"""
Generate POTA-Logger icon as PNG and ICO from scratch using Pillow.
Matches the SVG design: light sky background, pine tree, radio arcs, antenna dot.
"""

from PIL import Image, ImageDraw
import math, os

OUT = os.path.dirname(os.path.abspath(__file__))


def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256  # scale factor

    # ── Background: rounded rect, light sky ───────────────────────────────────
    r = int(40 * s)
    bg = (238, 246, 251, 255)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=bg)

    # ── Trunk ─────────────────────────────────────────────────────────────────
    tx = int(118 * s); tw = int(20 * s); ty = int(200 * s); th = int(28 * s)
    d.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=int(4 * s), fill=(92, 61, 30))

    # ── Pine tree tiers (bottom to top so lighter tiers overlap darker) ───────
    cx = size / 2
    tier_data = [
        (140 * s, 210 * s, 52 * s, (30, 107, 47)),
        (108 * s, 168 * s, 42 * s, (42, 140, 63)),
        ( 72 * s, 132 * s, 32 * s, (53, 160, 80)),
    ]
    for apex_y, base_y, half_w, color in tier_data:
        poly = [(cx, apex_y), (cx - half_w, base_y), (cx + half_w, base_y)]
        d.polygon(poly, fill=color)

    # ── Radio arcs (above treetop) ────────────────────────────────────────────
    arc_color = (43, 126, 193)
    lw = max(1, int(7 * s))
    apex_y = 72 * s  # treetop

    arcs = [
        (24 * s, 88 * s),   # innermost
        (42 * s, 72 * s),   # middle
        (60 * s, 56 * s),   # outermost
    ]
    for radius, top_y in arcs:
        # Draw arc: upper half of a circle centered at (cx, apex_y)
        # bounding box of the full circle
        bbox = [cx - radius, apex_y - radius, cx + radius, apex_y + radius]
        d.arc(bbox, start=200, end=340, fill=arc_color, width=lw)

    # ── Antenna dot ───────────────────────────────────────────────────────────
    dot_r = int(6 * s)
    d.ellipse([cx - dot_r, apex_y - dot_r, cx + dot_r, apex_y + dot_r],
              fill=arc_color)

    return img


# ── Render at 256 for PNG ──────────────────────────────────────────────────────
icon_256 = draw_icon(256)
png_path = os.path.join(OUT, "icon.png")
icon_256.save(png_path, "PNG")
print(f"Saved {png_path}")

# ── Multi-size ICO ────────────────────────────────────────────────────────────
sizes = [16, 32, 48, 256]
icons = [draw_icon(sz) for sz in sizes]
ico_path = os.path.join(OUT, "icon.ico")
icons[0].save(
    ico_path,
    format="ICO",
    sizes=[(sz, sz) for sz in sizes],
    append_images=icons[1:],
)
print(f"Saved {ico_path}")
