/*
 * Browser snippets for the unbake-text-from-image skill.
 *
 * Run these through the Chrome extension's javascript_tool, on any page of the
 * project (the real font must be loaded — that is the whole point of fitting in
 * the browser rather than offline).
 *
 *   1. FIT      — find the weight + letter-spacing that reproduce the artwork
 *   2. VERIFY   — check the rendered overlay against the measured targets
 *   3. GHOST    — visual confirmation, on top of the numbers
 *
 * All widths here are INK widths (actualBoundingBoxLeft + actualBoundingBoxRight),
 * never advance width. Advance width includes side bearings that leave no pixels,
 * so fitting against it lands ~2-5% too wide on every word.
 */

// ── 1. FIT ───────────────────────────────────────────────────────────────────
// Feed it the ink widths measured from the artwork and the measured cap height.
// Sweeps size × weight × tracking, keeping only sizes whose cap height matches,
// and returns the lowest mean-absolute-error combination.

await document.fonts.ready;
const ctx = document.createElement("canvas").getContext("2d");

function fitType(targets, capHeightPx, {
  family = "Manrope, sans-serif",
  weights = [400, 500, 600, 700, 800],
  lsRange = [-0.12, 0.12],
  lsStep = 0.005,
  sizeTolerance = 0.6,   // how far the rendered cap height may sit from the measured one
} = {}) {
  const results = [];
  const approxSize = capHeightPx / 0.714;               // rough start; refined by the cap check
  for (const weight of weights) {
    for (let size = approxSize - 3; size <= approxSize + 3; size += 0.2) {
      ctx.font = `${weight} ${size}px ${family}`;
      const cap = ctx.measureText("H").actualBoundingBoxAscent;
      if (Math.abs(cap - capHeightPx) > sizeTolerance) continue;   // wrong size for this cap band
      for (let ls = lsRange[0]; ls <= lsRange[1]; ls += lsStep) {
        const errs = Object.entries(targets).map(([word, target]) => {
          const m = ctx.measureText(word);
          const ink = m.actualBoundingBoxLeft + m.actualBoundingBoxRight + ls * size * (word.length - 1);
          return ink - target;
        });
        const mae = errs.reduce((a, b) => a + Math.abs(b), 0) / errs.length;
        results.push({ weight, size: +size.toFixed(1), ls: +ls.toFixed(3),
                       cap: +cap.toFixed(1), mae: +mae.toFixed(2),
                       errs: errs.map(e => +e.toFixed(1)) });
      }
    }
  }
  results.sort((a, b) => a.mae - b.mae);
  return { words: Object.keys(targets), best: results[0], runnersUp: results.slice(1, 4) };
}

// example — ink widths and cap height come from measure_baked_text.py
JSON.stringify(fitType({ Designer: 115, Researcher: 149, "Finance Planner": 207, "Fitness Coach": 186 }, 18), null, 1);

/*
 * Reading the result:
 *   mae <= ~1px on words 130-260px wide  -> trust it
 *   all errors share a sign              -> the sweep range is too narrow, widen lsRange
 *   two candidates within ~0.5 mae       -> size/weight/tracking are trading off; the stroke-width
 *                                           check (measure_baked_text.py strokes) decides
 */


// ── 2. VERIFY ────────────────────────────────────────────────────────────────
// After implementing, re-measure what actually rendered and compare with the
// artwork. A screenshot at ~0.5 display scale hides 2px errors; this does not.
//
// Edit the three CONFIG lines, then run.

const IMG_MATCH = "network.png";     // substring of the new art's src
const ART_W = 1190, ART_H = 1016;    // the artwork's own pixel canvas
const TARGETS = {                    // word -> { inkW, capTop, centreX } measured off the original
  Mom: { inkW: 63, capTop: 54, centreX: 519 },
};

(() => {
  const img = [...document.querySelectorAll("img")].find(i => i.src.includes(IMG_MATCH));
  if (!img) return "image not found";
  // The overlay layer is pinned to the ARTWORK's rect, which is what percentages
  // resolve against — measure relative to that, not to the container.
  const layer = img.parentElement.querySelector("[data-overlay], div[aria-hidden]") || img.parentElement;
  const lr = layer.getBoundingClientRect();
  const scale = ART_W / lr.width;                    // displayed px -> artwork px
  const c = document.createElement("canvas").getContext("2d");

  return JSON.stringify([...layer.querySelectorAll("span")].map(s => {
    const text = s.textContent.trim();
    const r = s.getBoundingClientRect();
    const cs = getComputedStyle(s);
    c.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const ls = parseFloat(cs.letterSpacing) || 0;
    const fm = c.measureText("Hxg");
    const capH = c.measureText("H").actualBoundingBoxAscent;
    const m = c.measureText(text);
    // baseline of a vertically-centred line box, then cap top from it
    const baseline = r.top + (r.height - (fm.fontBoundingBoxAscent + fm.fontBoundingBoxDescent)) / 2
                     + fm.fontBoundingBoxAscent;
    const got = {
      inkW: (m.actualBoundingBoxLeft + m.actualBoundingBoxRight + ls * (text.length - 1)) * scale,
      capTop: ((baseline - capH) - lr.top) * scale,
      centreX: ((r.left + r.width / 2) - lr.left) * scale,
    };
    const want = TARGETS[text];
    return {
      text,
      inkW: +got.inkW.toFixed(1), capTop: +got.capTop.toFixed(1), centreX: +got.centreX.toFixed(1),
      delta: want ? { inkW: +(got.inkW - want.inkW).toFixed(1),
                      capTop: +(got.capTop - want.capTop).toFixed(1),
                      centreX: +(got.centreX - want.centreX).toFixed(1) } : "no target",
    };
  }), null, 1);
})();

/*
 * Expect every delta within ~1-2px. A constant capTop offset of ~1px is normal
 * (cap-band centring vs line-box centring differ by (ascent-descent)/2 - capH/2).
 * A large constant centreX offset with negative letter-spacing means the
 * text-indent compensation is missing: letter-spacing is added after the LAST
 * glyph too, so centred text sits half a step off.
 */


// ── 3. GHOST ─────────────────────────────────────────────────────────────────
// Overlay the original at 50% and screenshot. Aligned text reads as one crisp
// word; misalignment doubles. Expect doubling only where the copy changed.

(() => {
  const img = [...document.querySelectorAll("img")].find(i => i.src.includes("network.png"));
  const g = document.createElement("img");
  g.id = "__ghost";
  g.src = "/src/assets/images/detail-page-for-everyone/network.webp";   // the ORIGINAL
  g.className = img.className;                                          // same object-fit/sizing
  g.style.cssText = "position:absolute;inset:0;z-index:60;opacity:.5;pointer-events:none";
  img.parentElement.appendChild(g);
  return "ghost added — screenshot, then: document.getElementById('__ghost').remove()";
})();
