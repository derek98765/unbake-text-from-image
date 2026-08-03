#!/usr/bin/env python3
"""Measure text baked into an image, by comparing it with a text-free version.

Four subcommands, meant to be used in this order as each one resolves what the
previous left ambiguous:

  diff      OLD NEW              where the removed text/boxes are, roughly
  profile   IMG --region --color row-by-row ink profile: separates text bands
                                 from photo borders and rules that share a colour
  colorkey  IMG --region --color exact bbox of one flat-coloured element
  strokes   IMG --region --color stem widths, for pinning the font weight

Every measurement is reported in the pixel space of the FIRST image passed, so
quote that canvas size alongside the numbers.

Requires Pillow.
"""

import argparse
import sys
from collections import Counter, deque

try:
    from PIL import Image, ImageChops, ImageFilter
except ImportError:
    sys.exit("Pillow is required:  pip install Pillow")


# ── helpers ──────────────────────────────────────────────────────────────────

def load_on_white(path, size=None):
    """RGBA -> RGB composited over white.

    Without this, transparent pixels convert to black and a transparent PNG
    differs from an opaque original across the entire image.
    """
    im = Image.open(path).convert("RGBA")
    if size and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, im).convert("RGB")


def luminance(px):
    return 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]


def near(c, target, tol):
    return all(abs(a - b) <= tol for a, b in zip(c[:3], target))


def parse_region(s, im):
    if not s:
        return (0, 0, im.width, im.height)
    x0, y0, x1, y1 = (int(v) for v in s.split(","))
    return (x0, y0, x1, y1)


def parse_color(s):
    return tuple(int(v) for v in s.split(","))


# ── diff ─────────────────────────────────────────────────────────────────────

def cmd_diff(args):
    old = load_on_white(args.old)
    new = load_on_white(args.new, size=old.size)
    W, H = old.size
    print(f"canvas {W}x{H}   (all coordinates below are in this space)")
    if Image.open(args.new).size != (W, H):
        print(f"note: {args.new} was {Image.open(args.new).size}, resized to match")

    if args.directional:
        # text is DARKER than the art behind it: keep only old-darker-than-new,
        # which drops most lossy-compression noise
        sub = ImageChops.subtract(new.convert("L"), old.convert("L"))
    else:
        sub = ImageChops.difference(old, new).convert("L")
    mask = sub.point(lambda v: 255 if v > args.threshold else 0)
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))  # de-noise

    grow = args.merge if args.merge % 2 else args.merge + 1
    merged = mask.filter(ImageFilter.MaxFilter(grow)) if grow > 1 else mask
    inset = grow // 2  # MaxFilter grows every blob by this much on each side

    px, opx = merged.load(), old.load()
    seen = bytearray(W * H)
    comps = []
    for sx in range(W):
        for sy in range(H):
            if not px[sx, sy] or seen[sy * W + sx]:
                continue
            q, cells = deque([(sx, sy)]), []
            seen[sy * W + sx] = 1
            while q:
                x, y = q.popleft()
                cells.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < W and 0 <= ny < H and px[nx, ny] and not seen[ny * W + nx]:
                        seen[ny * W + nx] = 1
                        q.append((nx, ny))
            if len(cells) >= args.min_area:
                comps.append(cells)

    print(f"\n{len(comps)} component(s) >= {args.min_area}px, sorted top-to-bottom.")
    print("bboxes are de-dilated; colours are sampled from the ORIGINAL.\n")
    for cells in sorted(comps, key=lambda c: min(y for _, y in c)):
        xs = [x for x, _ in cells]
        ys = [y for _, y in cells]
        l, r = min(xs) + inset, max(xs) - inset
        t, b = min(ys) + inset, max(ys) - inset
        cnt = Counter(opx[x, y] for x, y in cells if mask.getpixel((x, y)))
        top = ", ".join(f"rgb{c}" for c, _ in cnt.most_common(2))
        print(f"  x {l}-{r} (w={r - l + 1})   y {t}-{b} (h={b - t + 1})   "
              f"centre=({(l + r) / 2:.1f}, {(t + b) / 2:.1f})   {top}")

    print("\nNext: the diff shows WHERE. Use `profile` on each area to find the exact cap band,\n"
          "and `colorkey` for anything with a flat fill (pills, chips, rules).")


# ── profile ──────────────────────────────────────────────────────────────────

def cmd_profile(args):
    im = load_on_white(args.image)
    l, t, r, b = parse_region(args.region, im)
    color, tol = parse_color(args.color), args.tol
    px = im.load()
    print(f"row profile of x {l}-{r}, colour {color} +/-{tol}")
    print("gaps between bands separate the text from photo borders / rules:\n")
    bands, run = [], None
    for y in range(t, b):
        n = sum(1 for x in range(l, r) if near(px[x, y], color, tol))
        if n:
            print(f"  y={y:5} {'#' * min(n, 60)} ({n})")
            if run is None:
                run = [y, y]
            else:
                run[1] = y
        elif run is not None and y - run[1] > args.gap:
            bands.append(tuple(run))
            run = None
    if run is not None:
        bands.append(tuple(run))

    print("\nbands found (cap top -> baseline, unless the band includes a descender):")
    for i, (y0, y1) in enumerate(bands, 1):
        xs = [x for y in range(y0, y1 + 1) for x in range(l, r) if near(px[x, y], color, tol)]
        print(f"  band {i}: y {y0}-{y1}  height={y1 - y0 + 1}   "
              f"x {min(xs)}-{max(xs)} (w={max(xs) - min(xs) + 1})  centreX={(min(xs) + max(xs)) / 2:.1f}")
    if len(bands) > 1:
        pitch = bands[1][0] - bands[0][0]
        print(f"\n  line pitch = {pitch}px  ->  line-height {pitch}/fontSize")
    print("\nfont-size ~= capHeight / capRatio  (measure capRatio for the real font; Manrope ~0.714)")


# ── colorkey ─────────────────────────────────────────────────────────────────

def cmd_colorkey(args):
    im = load_on_white(args.image)
    l, t, r, b = parse_region(args.region, im)
    color, tol = parse_color(args.color), args.tol
    px = im.load()
    hits = [(x, y) for y in range(t, b) for x in range(l, r) if near(px[x, y], color, tol)]
    if not hits:
        sys.exit("no pixels matched — widen --tol or check --region")
    xs = [x for x, _ in hits]
    ys = [y for _, y in hits]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    print(f"fill bbox: x {x0}-{x1} (w={x1 - x0 + 1})  y {y0}-{y1} (h={y1 - y0 + 1})  "
          f"centre=({(x0 + x1) / 2:.1f}, {(y0 + y1) / 2:.1f})")

    # corner radius: how far in the fill starts on its first row
    row0 = [x for x in range(x0, x1 + 1) if near(px[x, y0 + 1], color, tol * 2)]
    if row0:
        print(f"corner inset on the top row ~= {min(row0) - x0}px  (approximates the border radius)")

    if args.inner:
        ic, itol = parse_color(args.inner), args.inner_tol
        # inset past the rounded corners, or they leak background into the result
        pad = max(4, (min(row0) - x0) * 2 if row0 else 8)
        inner = [(x, y) for y in range(y0 + 3, y1 - 2) for x in range(x0 + pad, x1 - pad + 1)
                 if near(px[x, y], ic, itol)]
        if inner:
            ixs = [x for x, _ in inner]
            iys = [y for _, y in inner]
            print(f"inner text ink: x {min(ixs)}-{max(ixs)} (w={max(ixs) - min(ixs) + 1})  "
                  f"y {min(iys)}-{max(iys)} (h={max(iys) - min(iys) + 1})  "
                  f"centreX={(min(ixs) + max(ixs)) / 2:.1f}")
            print(f"padding: left={min(ixs) - x0} right={x1 - max(ixs)} "
                  f"top={min(iys) - y0} bottom={y1 - max(iys)}")


# ── strokes ──────────────────────────────────────────────────────────────────

def cmd_strokes(args):
    im = load_on_white(args.image)
    l, t, r, b = parse_region(args.region, im)
    color, tol = parse_color(args.color), args.tol
    px = im.load()
    print("stem widths (run lengths of matching pixels) across the cap band:\n")
    all_runs = []
    for y in range(t, b, max(1, (b - t) // 5)):
        runs, run = [], 0
        for x in range(l, r):
            if near(px[x, y], color, tol):
                run += 1
            elif run:
                runs.append(run)
                run = 0
        if run:
            runs.append(run)
        if runs:
            print(f"  y={y:5} {runs}")
            all_runs += runs
    if all_runs:
        all_runs.sort()
        stem = all_runs[len(all_runs) // 2]  # median resists round glyphs and joins
        cap = b - t + 1
        print(f"\n  median stem ~{stem}px over cap {cap}px  ->  stem/cap = {stem / cap:.2f}")
        print("  ~0.11 light/medium · ~0.15 semibold/bold · ~0.19 extrabold")
        print("  Cross-check this against the width fit; agreement means the weight is right.")


# ── cli ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="locate removed text/boxes by comparing the pair")
    d.add_argument("old"); d.add_argument("new")
    d.add_argument("--threshold", type=int, default=12)
    d.add_argument("--merge", type=int, default=9, help="dilation used to merge glyphs into words")
    d.add_argument("--min-area", type=int, default=400)
    d.add_argument("--directional", action="store_true",
                   help="keep only old-darker-than-new; cuts lossy-compression noise")
    d.set_defaults(func=cmd_diff)

    pr = sub.add_parser("profile", help="row profile of one region, to find cap bands")
    pr.add_argument("image"); pr.add_argument("--region", required=True)
    pr.add_argument("--color", required=True); pr.add_argument("--tol", type=int, default=70)
    pr.add_argument("--gap", type=int, default=5, help="blank rows that end a band")
    pr.set_defaults(func=cmd_profile)

    ck = sub.add_parser("colorkey", help="exact bbox of a flat-coloured element")
    ck.add_argument("image"); ck.add_argument("--region", required=True)
    ck.add_argument("--color", required=True); ck.add_argument("--tol", type=int, default=26)
    ck.add_argument("--inner", help="colour of text inside the fill, e.g. 255,255,255")
    ck.add_argument("--inner-tol", type=int, default=20)
    ck.set_defaults(func=cmd_colorkey)

    st = sub.add_parser("strokes", help="stem widths, for pinning the font weight")
    st.add_argument("image"); st.add_argument("--region", required=True)
    st.add_argument("--color", required=True); st.add_argument("--tol", type=int, default=70)
    st.set_defaults(func=cmd_strokes)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
