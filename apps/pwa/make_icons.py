#!/usr/bin/env python3
"""Generate the app icons for the Disassemble Dynamics app family.
Run once (python apps/pwa/make_icons.py); outputs land in apps/pwa/icons/.
Each app gets 192, 512 (PWA) and 180 (iPhone home screen) PNGs plus a maskable 512."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "icons"
OUT.mkdir(exist_ok=True)
FONT = "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
APPS = {
    # key: (mark, background, accent)
    "ddd":       ("DD", "#0B111A", "#0A84FF"),   # Disassemble Dynamics hub
    "demex":     ("DX", "#0B111A", "#2AD1B8"),   # DEMEX operations cockpit
    "estimatio": ("E",  "#0B111A", "#3B9DFF"),   # Estimatio estimating
    "qualora":   ("Q",  "#0B111A", "#F3B23E"),   # Qualora prequalification
    "pavora":    ("P",  "#1A1404", "#E8D35E"),   # PAVORA flooring operations (Wayne's gold/espresso)
}

def icon(mark, bg, accent, size, maskable=False):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = 0 if maskable else int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=bg)
    # accent bar along the bottom
    bar = int(size * 0.08)
    d.rounded_rectangle([int(size*.18), size - int(size*.2) - bar, size - int(size*.18), size - int(size*.2)],
                        radius=bar // 2, fill=accent)
    f = ImageFont.truetype(FONT, int(size * (0.46 if len(mark) == 1 else 0.40)))
    bbox = d.textbbox((0, 0), mark, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - int(size*.06)), mark, font=f, fill="#FFFFFF")
    return im

for key, (mark, bg, accent) in APPS.items():
    for s in (180, 192, 512):
        icon(mark, bg, accent, s).save(OUT / f"{key}-{s}.png")
    icon(mark, bg, accent, 512, maskable=True).save(OUT / f"{key}-512-maskable.png")
print("icons written to", OUT)
