---
name: unbake-text-from-image
description: >
  Turn an image with baked-in text into text-free art plus a CSS text overlay, by measuring the
  original's type geometry and reproducing it exactly. Trigger whenever an image has text burned
  into the pixels that needs to become real text — "the text is baked into the image", "make this
  label editable", "these need to be translatable", "overlay the text with CSS instead", or when a
  text-free version of an existing illustration is handed over to be wired up. Measures position,
  size, weight, tracking and colour off the original, implements the overlay so it scales with the
  image, and verifies the result numerically against the artwork.
---

# Unbake Image Text

Baked-in text can't be translated, restyled, or read by a screen reader. This converts it into a
CSS overlay that lands on the same pixels as the original.

**Requires:** Python with Pillow (measuring), Claude in Chrome (fitting type + verifying).

The inputs are a **pair**: the new text-free art, and the original with text baked in. The original
is the measuring stick — without it you are guessing. It is usually the same filename stem with a
different extension, in the same folder, still referenced by the component you're about to change.

Everything below is ordered so each step's output feeds the next. Don't skip to implementation:
positions eyeballed from a screenshot will be visibly wrong, and the error is hard to see until
it sits next to the original.

---

## 1. Find the pair and the render context

- Locate the new art and the baked original (`git status` often shows the new file as untracked).
- Read the component that renders it. Note the container size, `object-fit`, and whether the art
  is transparent — all three change the implementation.
- Confirm both images' pixel dimensions. **They are often different scales** (a 2x export vs a 1x
  one). Everything gets normalised to one canvas, and every measurement is quoted against it.

## 2. Measure the original

Use `scripts/measure_baked_text.py`. Start with `diff`, then use `profile` and `colorkey` to
resolve anything the diff leaves ambiguous.

```bash
# start here — --directional plus a min-area filter is what separates the labels
# from lossy-compression noise. Without it you get dozens of junk components.
python3 scripts/measure_baked_text.py diff OLD.webp NEW.png --directional --min-area 1200

# then resolve each area the diff found
python3 scripts/measure_baked_text.py profile  OLD.webp --region 460,35,600,110  --color 58,80,145
python3 scripts/measure_baked_text.py colorkey OLD.webp --region 880,155,1175,290 --color 253,141,29 --inner 255,255,255
python3 scripts/measure_baked_text.py strokes  OLD.webp --region 25,681,150,701   --color 58,80,145
```

What you need out of this, per text element:

| measurement | how | used for |
|---|---|---|
| ink bbox | diff or colorkey | position |
| cap band (cap top → baseline) | row profile | font size, vertical anchor |
| line pitch | distance between cap tops | `line-height` |
| centre x | bbox midpoint | horizontal anchor |
| ink width per word | bbox width | fitting tracking (step 3) |
| flat colour | darkest in-stroke pixels | `color` |
| stroke width | `strokes` | weight (step 3) |
| box/pill rect, padding, radius | colorkey on the fill colour | the chip behind the text |

**Check sampled colours against the design tokens before hardcoding a hex.** They frequently *are*
tokens — in one case the sampled `#fd8d1d` and `#394f95` were exactly `brand-orange-01` and
`brand-blue-01`. Use the token, not the sample.

## 3. Derive the type

**Size** comes from cap height, not from guessing:
`font-size ≈ capHeight / capRatio`, where `capRatio` is the font's own cap height per em (measure
it with `measureText('H').actualBoundingBoxAscent` at a known size rather than trusting a table;
Manrope is ~0.714).

**Tracking and weight** are fitted, in `reference/fit-and-verify.js`. Sweep candidate weights ×
letter-spacings, render each word with canvas `measureText`, and compare **ink width**
(`actualBoundingBoxLeft + actualBoundingBoxRight`) against the measured ink widths — *not* advance
width, which includes side bearings the pixels don't show. Take the lowest mean absolute error.

**Sanity-check the weight against stroke width** (stem ÷ cap height). Size, weight and tracking
trade off against each other, so a width fit alone can settle on a wrong weight that happens to
match. Roughly: ~0.11 light/medium, ~0.15 semibold/bold, ~0.19 extrabold.

Treat this as a coarse check, not a reading. Below ~25px cap height the stem is only 3–4px, so a
single pixel of antialiasing spans a whole weight step — it reliably separates "light" from "bold"
but not 600 from 700. **The width fit wins** when they disagree; the stroke check is there to catch
a fit that is wrong by a lot.

A good fit lands within ~1px on words 130–260px wide. If the best fit is several px out and all
errors share a sign, your sweep range is too narrow — widen it.

## 4. Implement

**Map to the artwork's canvas.** Express positions as percentages of the artwork's own pixel
dimensions and type sizes in container-query units, so the overlay tracks the image at every width:

```js
const ART_W = 1190, ART_H = 1016;
const pctX = px => `${((px / ART_W) * 100).toFixed(3)}%`;
const pctY = px => `${((px / ART_H) * 100).toFixed(3)}%`;
const cqw  = px => `${((px / ART_W) * 100).toFixed(3)}cqw`;   // needs container-type on the parent
```

**Pin the overlay to the artwork's rect, not the container**, whenever `object-fit` crops. With
`object-cover` in a box wider than the art, the art overflows vertically and is centred — so the
overlay layer must be `width:100%`, its own `aspect-ratio`, and vertically centred. Percentages
then map 1:1 onto the canvas. Anchoring to the container instead drifts everything.

**Anchor each label by its centre** (`left/top` + `translate(-50%, -50%)`) and set `whitespace-nowrap`.
Translated text is a different length; centre-anchored it grows evenly either side and stays on its
node, where left-anchored it walks off.

**Order for occlusion.** If the art is transparent around its subject and the original text passed
*behind* the subject, put the overlay **before** the image in the DOM. The subject then occludes
the text exactly as it did when baked in. Check the alpha channel — the transparency is usually the
tell that this is expected.

**Wire the text through `t()`**, since translation is normally the whole point.

**Never add a stroke to the text without asking first.** If the artwork's weight falls between the
font's available weights, it is tempting to reach for `-webkit-text-stroke`, a `text-shadow` ring,
or `paint-order: stroke` to thicken the glyphs into a match. Don't do it silently — it is a fake
weight that thickens unevenly, renders differently across browsers, and looks wrong at other sizes.
Use the nearest real weight, say which one you used and how far off it is, and **ask the owner**
before adding any stroke. Same rule for faux-bold by other means (double-drawing, transforms).

**Comment the measured values** into the code — canvas size, cap bands, centres, colours, and what
each fit matched to. And explicitly flag any value that *departs* from the measurement (a softened
radius, a lighter weight the designer asked for). Otherwise the next person diffing against the
artwork reads the difference as a bug and "corrects" it back.

## 5. Verify

Both checks, not one:

1. **Numerically** — re-measure the *rendered* result in the browser (cap tops, centres, ink widths)
   and compare against the targets from step 2. `reference/fit-and-verify.js` has this. Screenshots
   at typical display scale (~0.5) hide 2px errors that this catches.
2. **Visually** — ghost the original over the result at 50% opacity. Aligned text reads as one
   crisp word; misalignment shows as doubling. Expect doubling only where the copy deliberately
   changed.

Then check the responsive range — at minimum one width below the layout's main breakpoint (see the
container-query pitfall below).

---

## Pitfalls

Each of these produced a wrong or broken result before being caught.

- **Transparent pixels read as black.** Converting RGBA to RGB makes transparent areas `(0,0,0)`,
  so a transparent PNG diffed against an opaque original differs *everywhere*. Composite both over
  white first. The script does this; do it too in any ad-hoc check.
- **Lossy originals add noise everywhere.** A webp original diffs against re-exported art at ±10
  across the whole image. Use a directional diff (old darker than new) plus erode→dilate, or ignore
  the diff and colour-key the original instead.
- **Dilation inflates bboxes.** Merging glyphs into words with a MaxFilter(N) grows every box by
  N//2 on each side. Subtract it, or re-measure the final bbox undilated.
- **Other elements share the text colour.** Photo borders, icons and rules match a colour key and
  silently extend the bbox. Row profiles separate them: text is a contiguous band with gaps above
  and below.
- **`letter-spacing` is added after the last glyph too**, so centred text shifts by half a step.
  Compensate with `text-indent: <-letterSpacing>em`.
- **`container-type: inline-size` stops an element's width depending on its contents.** That is
  what containment means. If its parent only has a `max-width` and no definite width, it resolves
  to **zero** and the image vanishes — and only below the breakpoint where a definite width was set.
  Give the parent a real width (`w-full` + the existing `max-w-*`), and comment why it's needed.
- **Verify at more than one viewport.** These bugs are breakpoint-specific by nature.
