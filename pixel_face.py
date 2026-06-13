"""Design Lily's pixel face (based on mira.webp) at 120-wide. Clean SYMMETRIC
base (hair w/ real bangs, face, neck, hoodie). Eyes/brows/mouth animate in RN,
so they're drawn here only for preview. Emits the RN grid + prints geometry.

  .venv/bin/python pixel_face.py            -> preview PNG (full face)
  .venv/bin/python pixel_face.py --emit     -> also print the RN grid array
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
GW, GH = 120, 140

PAL = {
    "H": (30, 26, 38),    # hair (near-black, mira)
    "S": (240, 198, 158), # skin warm
    "T": (230, 219, 198), # cream hoodie
    "t": (205, 192, 168), # hoodie fold (clothing depth, not face shadow)
}
PAL_ITEMS = list(PAL.items())


def draw_base(scale=6):
    W, H = GW * scale, GH * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = W / 2

    def s(v):
        return v * scale

    # ── hoodie (drawn first, behind neck) ────────────────────────────────────
    d.rounded_rectangle([cx - s(52), s(116), cx + s(52), H], radius=s(26), fill=PAL["T"])
    # hood cowl around the neck
    d.rounded_rectangle([cx - s(30), s(108), cx + s(30), s(132)], radius=s(16), fill=PAL["T"])
    # neckline scoop + drawstrings (fold tone)
    d.ellipse([cx - s(11), s(112), cx + s(11), s(128)], fill=PAL["t"])
    d.rectangle([cx - s(6), s(122), cx - s(3.5), s(138)], fill=PAL["t"])
    d.rectangle([cx + s(3.5), s(122), cx + s(6), s(138)], fill=PAL["t"])

    # ── hair back / sides (frames the face) ──────────────────────────────────
    d.rounded_rectangle([cx - s(44), s(14), cx + s(44), s(118)], radius=s(40), fill=PAL["H"])
    # neck
    d.rounded_rectangle([cx - s(12), s(100), cx + s(12), s(122)], radius=s(5), fill=PAL["S"])
    # face — soft round
    d.rounded_rectangle([cx - s(36), s(30), cx + s(36), s(116)], radius=s(33), fill=PAL["S"])

    # ── bangs: a curtain fringe over the forehead, parted in the center ───────
    # main fringe dome
    d.pieslice([cx - s(38), s(16), cx + s(38), s(70)], 180, 360, fill=PAL["H"])
    # wispy side tips (lobes dipping lower at the temples)
    d.ellipse([cx - s(40), s(40), cx - s(20), s(74)], fill=PAL["H"])
    d.ellipse([cx + s(20), s(40), cx + s(40), s(74)], fill=PAL["H"])
    # center part — small skin notch low in the fringe (a subtle parting, not a spike)
    d.polygon([(cx, s(46)), (cx - s(5), s(64)), (cx + s(5), s(64))], fill=PAL["S"])
    return img


def quantize_to_grid(img):
    small = img.resize((GW, GH), Image.LANCZOS)
    px = small.load()
    rows = []
    for y in range(GH):
        allowed = PAL_ITEMS if y >= GH - 30 else [it for it in PAL_ITEMS if it[0] in ("H", "S")]
        for_row = ""
        for x in range(GW):
            r, g, b, a = px[x, y]
            if a < 120:
                for_row += "."
                continue
            best, bd = ".", 1e9
            for ch, (pr, pg, pb) in allowed:
                dd = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
                if dd < bd:
                    bd, best = dd, ch
            for_row += best
        rows.append(for_row)
    out = []
    for row in rows:
        c = list(row)
        for x in range(GW // 2):
            c[GW - 1 - x] = c[x]
        out.append("".join(c))
    return out


# Feature geometry (cells). Ported to RN.
EYES = dict(cy=70, lx=44, rx=76, scW=17, scH=17, irisD=13, pupD=6.5, hiD=4)
BROWS = dict(cy=52, lx=44, rx=76, w=15, h=2.2)
BLUSH = dict(cy=82, lx=40, rx=80, w=16, h=8)
MOUTH = dict(cy=94, x=60, w=10, h=4.5)
C_WHITE = (255, 255, 255)
C_IRIS = (96, 64, 56)
C_PUP = (26, 18, 24)
C_BROW = (60, 50, 54)
C_BLUSH = (244, 172, 154)
C_LIP = (196, 110, 106)


def draw_features_smooth(base_img, cell):
    big = base_img.resize((base_img.width * 3, base_img.height * 3), Image.NEAREST)
    d = ImageDraw.Draw(big, "RGBA")
    sc = cell * 3

    def el(cx, cy, w, h, fill):
        d.ellipse([cx - w / 2 * sc, cy - h / 2 * sc, cx + w / 2 * sc, cy + h / 2 * sc], fill=fill)

    # blush
    b = BLUSH
    for lx in (b["lx"], b["rx"]):
        el(lx * sc, b["cy"] * sc, b["w"], b["h"], (*C_BLUSH, 130))
    # eyes — clean, no heavy lid
    e = EYES
    for ex in (e["lx"], e["rx"]):
        cx, cy = ex * sc, e["cy"] * sc
        el(cx, cy, e["scW"], e["scH"], (*C_WHITE, 255))
        el(cx, cy + 0.8 * sc, e["irisD"], e["irisD"], (*C_IRIS, 255))
        el(cx, cy + 1.0 * sc, e["pupD"], e["pupD"], (*C_PUP, 255))
        el(cx - e["irisD"] * 0.24 * sc, cy - e["irisD"] * 0.14 * sc, e["hiD"], e["hiD"], (*C_WHITE, 255))
        el(cx + e["irisD"] * 0.18 * sc, cy + e["irisD"] * 0.22 * sc, e["hiD"] * 0.5, e["hiD"] * 0.5, (*C_WHITE, 220))
    # brows — thin gentle arches, well above the eyes
    br = BROWS
    for ex in (br["lx"], br["rx"]):
        x0, x1 = (ex - br["w"] / 2) * sc, (ex + br["w"] / 2) * sc
        y0, y1 = (br["cy"] - 2.2) * sc, (br["cy"] + 3) * sc
        d.arc([x0, y0, x1, y1], 200, 340, fill=(*C_BROW, 255), width=int(br["h"] * sc))
    # mouth (resting)
    m = MOUTH
    d.rounded_rectangle([m["x"] * sc - m["w"] / 2 * sc, m["cy"] * sc, m["x"] * sc + m["w"] / 2 * sc, m["cy"] * sc + m["h"] * sc], radius=m["h"] * sc * 0.5, fill=(*C_LIP, 255))
    return big.resize(base_img.size, Image.LANCZOS)


def render(rows, pal, cell=5, bg=(2, 13, 24, 255)):
    W, H = len(rows[0]) * cell, len(rows) * cell
    img = Image.new("RGBA", (W, H), bg)
    px = img.load()
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            col = pal.get(ch)
            if not col:
                continue
            for y in range(cell):
                for x in range(cell):
                    px[c * cell + x, r * cell + y] = (*col, 255)
    return img


if __name__ == "__main__":
    grid = quantize_to_grid(draw_base())
    base = render(grid, PAL, cell=5)
    draw_features_smooth(base, cell=5).save(ROOT / "pixel_face_full.png")
    print("wrote pixel_face_full.png", GW, "x", GH)
    if "--emit" in sys.argv:
        print("\nGRID:")
        for row in grid:
            print(f'  "{row}",')
