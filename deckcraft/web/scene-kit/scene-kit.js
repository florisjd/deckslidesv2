/* scene-kit: pure render(scene, theme, orientation) + seekable GSAP timeline + deterministic linter.
 * The exporter only ever calls SceneKit.seek(t); nothing plays in real time. */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const R = (cls, inner, extra = "") => `<div class="${cls}" ${extra}>${inner}</div>`;
  const li = (t, mark) => R("row rv", `<span class="mk">${mark}</span><span>${esc(t)}</span>`);

  const LAYOUTS = {
    cover: (s) => R("eyebrow rv", esc(s.eyebrow)) + R("h1 rv", esc(s.title)) + (s.subtitle ? R("sub rv", esc(s.subtitle)) : ""),
    statement: (s) => R("h1 rv", esc(s.headline)) + (s.body ? R("sub rv", esc(s.body)) : ""),
    stat: (s) => R("big rv", esc(s.value)) + R("h2 rv", esc(s.label)) + (s.body ? R("sub rv", esc(s.body)) : ""),
    cards: (s) => R("h2 rv", esc(s.heading)) + (s.lead ? R("sub lead", esc(s.lead)) : "") + R("grid", s.cards.map((c) => R("card rv", c.title ? R("ct", esc(c.title)) + (c.text ? R("cx", esc(c.text)) : "") : R("ct", esc(c.text)))).join("")),
    process: (s) => R("h2 rv", esc(s.heading)) + R("steps", s.steps.map((c, i) => R("step rv", `<span class="num">${i + 1}</span><div>${R("ct", esc(c.title))}${R("cx", esc(c.text))}</div>`)).join("")),
    checklist: (s) => R("h2 rv", esc(s.heading)) + (s.lead ? R("sub lead", esc(s.lead)) : "") + R("list", s.items.map((t) => li(t, "✓")).join("")),
    compare: (s) => R("h2 rv", esc(s.heading)) + R("two", R("col do", R("ch rv", "Do") + (s.do_lead ? R("sub lead", esc(s.do_lead)) : "") + s.do.map((t) => li(t, "✓")).join("")) + R("col dont", R("ch rv", "Don’t") + (s.dont_lead ? R("sub lead", esc(s.dont_lead)) : "") + s.dont.map((t) => li(t, "✕")).join(""))),
    scenario: (s) => R("eyebrow rv", "Scenario") + R("h2 rv", esc(s.situation)) + R("list", s.options.map((t, i) => li(t, "ABC"[i])).join("")) + R("sub rv ans", esc(s.why)),
    myth_fact: (s) => R("card rv myth", R("ct", "Myth") + R("cx", esc(s.myth))) + R("card rv fact", R("ct", "Fact") + R("cx", esc(s.fact))),
    spotlight: (s) => R("eyebrow rv", "In focus") + R("h1 rv", esc(s.term)) + R("sub rv", esc(s.definition)),
    escalation: (s) => R("h2 rv", esc(s.heading)) + R("steps", s.levels.map((c, i) => R("step rv", `<span class="num">${i + 1}</span><div>${R("ct", esc(c.title))}${R("cx", esc(c.text))}</div>`)).join("")),
    takeaways: (s) => R("h2 rv", esc(s.heading)) + (s.lead ? R("sub lead", esc(s.lead)) : "") + R("list", s.points.map((t) => li(t, "★")).join("")),
    image: (s) => `<img class="rv img" src="${esc(s.image_url || "")}">` + (s.caption ? R("sub rv", esc(s.caption)) : ""),
  };

  const CSS = `
  *{box-sizing:border-box;margin:0}
  html,body{width:100%;height:100%;overflow:hidden}
  #stage{position:relative;width:var(--W);height:var(--H);background:var(--bg);color:var(--ink);font-family:var(--body);overflow:hidden}
  #content{position:absolute;inset:0;padding:var(--pad);display:flex;flex-direction:column;justify-content:center;gap:calc(var(--u)*1.2);font-size:calc(var(--u)*var(--k))}
  #content>*{max-width:100%}
  .eyebrow{font-size:.5em;letter-spacing:.14em;text-transform:uppercase;color:var(--primary);font-weight:700}
  .h1{font-family:var(--heading);font-size:1.9em;line-height:1.12;font-weight:700}
  .h2{font-family:var(--heading);font-size:1.25em;line-height:1.18;font-weight:700;color:var(--primary)}
  .sub{font-size:.7em;line-height:1.35;color:var(--muted)}
  .big{font-family:var(--heading);font-size:3.6em;line-height:1;font-weight:800;color:var(--primary)}
  .row{display:flex;gap:.5em;align-items:flex-start;font-size:.7em;line-height:1.3;padding:.45em .6em;background:var(--card);border-radius:var(--radius)}
  .mk{flex:none;min-width:1.4em;height:1.4em;border-radius:50%;background:var(--primary);color:var(--onPrimary);display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:.8em}
  .list,.steps,.grid{display:flex;flex-direction:column;gap:.5em}
  .grid{display:grid;grid-template-columns:repeat(var(--cols),1fr);gap:.6em}
  .card{background:var(--card);border-radius:var(--radius);padding:.7em .8em;font-size:.7em}
  .ct{font-weight:700;font-size:1em;line-height:1.2}.cx{font-size:.85em;line-height:1.3;color:var(--muted);margin-top:.2em}
  .step{display:flex;gap:.7em;align-items:flex-start;background:var(--card);border-radius:var(--radius);padding:.6em .8em;font-size:.7em}
  .num{flex:none;width:1.8em;height:1.8em;border-radius:50%;background:var(--primary);color:var(--onPrimary);display:inline-flex;align-items:center;justify-content:center;font-weight:800}
  .two{display:grid;grid-template-columns:repeat(var(--cols),1fr);gap:.8em}.col{display:flex;flex-direction:column;gap:.4em}
  .ch{font-weight:800;font-size:.75em;color:var(--primary)}.dont .ch{color:var(--danger)}.dont .mk{background:var(--danger)}
  .myth .ct{color:var(--danger)}.fact .ct{color:var(--primary)}
  #content[data-layout="image"]{align-items:center}
  .img{max-width:100%;max-height:70%;width:auto;height:auto;flex:0 1 auto;min-height:0;object-fit:contain;border-radius:var(--radius)}
  #content[data-layout="image"] .img:only-child{max-height:92%}
  `;

  let tl = null, cur = null;

  function render(scene, theme, orientation, opts = {}) {
    const portrait = orientation !== "landscape";
    const W = portrait ? 1080 : 1920, H = portrait ? 1920 : 1080, c = theme.tokens.color;
    const root = document.documentElement.style;
    const vars = { W: W + "px", H: H + "px", u: (portrait ? 54 : 46) + "px", k: 1, pad: (portrait ? 96 : 110) + "px",
      cols: portrait ? 1 : 2, radius: (theme.tokens.radius || 20) + "px", heading: theme.tokens.font.heading, body: theme.tokens.font.body };
    for (const [k, v] of Object.entries({ ...c, ...vars })) root.setProperty("--" + k, v);
    document.body.innerHTML = `<style>${CSS}</style><div id="stage"><div id="content" data-layout="${scene.kind}">${LAYOUTS[scene.kind](scene)}</div></div>`;
    document.querySelectorAll("#content > .h1,#content > .h2,#content > .big").forEach((e) => e.classList.add("rv"));
    fit();
    buildTimeline(theme, opts.duration || 6, opts.reveals);
    return { width: W, height: H };
  }

  // Scale type up until the content fills ~78% of the frame without overflowing (fixes empty-frame layouts).
  function fit() {
    const el = document.getElementById("content"), H = el.clientHeight, W = el.clientWidth;
    const inner = () => { let top = 1e9, bot = 0; for (const ch of el.children) { const r = ch.getBoundingClientRect(); top = Math.min(top, r.top); bot = Math.max(bot, r.bottom); } return bot - top; };
    const pad = parseFloat(getComputedStyle(el).paddingTop) * 2;
    const avail = H - pad;
    const isImg = el.dataset.layout === "image";
    let lo = isImg ? 0.85 : 0.7, hi = isImg ? 1.6 : 3.6;
    const fillLimit = avail * (isImg ? 0.92 : 0.82);
    for (let i = 0; i < 14; i++) {
      const mid = (lo + hi) / 2;
      el.style.setProperty("--k", mid);
      const over = inner() > fillLimit || el.scrollWidth > W + 1 || [...el.querySelectorAll("*")].some((n) => n.getBoundingClientRect().right > W - pad / 2 + 1);
      if (over) hi = mid; else lo = mid;
    }
    el.style.setProperty("--k", lo);
  }

  function buildTimeline(theme, duration, reveals) {
    if (tl) tl.kill();
    const els = [...document.querySelectorAll(".rv")];
    const stagger = (theme.motion && theme.motion.stagger_s) || 0.3;
    tl = gsap.timeline({ paused: true });
    // opts.reveals: {elementIndex: seconds}. Unanchored elements appear with the previous anchored one.
    const rv = reveals || {};
    let last = 0.2;
    els.forEach((e, i) => {
      const t = rv[i] !== undefined ? Math.max(0.1, rv[i] - 0.15) : (Object.keys(rv).length ? last : 0.2 + i * stagger);
      last = t;
      tl.fromTo(e, { opacity: 0, y: 28 }, { opacity: 1, y: 0, duration: 0.5, ease: "power2.out" }, t);
    });
    tl.set({}, {}, duration);
  }

  const seek = (t) => { tl.seek(t, false); return true; };
  function frameKey(t) {
    if (!tl) return null;
    const bits = [];
    for (const tw of tl.getChildren(true, true, false)) {
      if (tw.totalDuration() <= 0) continue;
      const s = tw.startTime(), e = tw.endTime();
      if (t >= s - 1e-4 && t <= e + 1e-4) return null;
      bits.push(t < s ? "0" : "1");
    }
    return "S:" + bits.join("");
  }
  async function ready() {
    await document.fonts.ready;
    const imgs = [...document.images];
    if (imgs.length) {
      await Promise.all(imgs.map((img) => (
        img.complete
          ? Promise.resolve()
          : new Promise((res) => { img.onload = res; img.onerror = res; })
      ).then(() => (img.decode ? img.decode().catch(() => {}) : undefined))));
      fit();
    }
    return true;
  }

  // Deterministic linter: overflow, min font size, WCAG contrast, empty-area ratio.
  function lum([r, g, b]) { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); }
  function rgb(s) { const m = s.match(/[\d.]+/g); return m ? m.slice(0, 3).map(Number) : [0, 0, 0]; }
  function alpha(s) { const m = s.match(/[\d.]+/g); return m && m.length > 3 ? +m[3] : 1; }
  function bgOf(n) { while (n) { const c = getComputedStyle(n).backgroundColor; if (alpha(c) > 0.5) return rgb(c); n = n.parentElement; } return [255, 255, 255]; }

  function lint(minBodyPx) {
    seek(1e6);
    const out = [], stage = document.getElementById("stage"), sr = stage.getBoundingClientRect(), pad = parseFloat(getComputedStyle(document.getElementById("content")).paddingTop);
    const minPx = minBodyPx || (sr.width < sr.height ? 28 : 24);
    let top = 1e9, bot = 0;
    for (const n of document.querySelectorAll("#content *")) {
      const own = [...n.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
      const r = n.getBoundingClientRect();
      if (r.width && (r.right > sr.right - pad / 2 + 1 || r.left < sr.left + pad / 2 - 1 || r.bottom > sr.bottom - pad / 2 + 1 || r.top < sr.top + pad / 2 - 1)) out.push({ severity: "error", kind: "overflow", message: `<${n.tagName.toLowerCase()} class="${n.className}"> leaves safe area` });
      if (n.tagName === "IMG") {
        if (n.naturalWidth === 0) out.push({ severity: "error", kind: "missing-image", message: "Slide image failed to load" });
        else { top = Math.min(top, r.top); bot = Math.max(bot, r.bottom); }
      }
      if (!own) continue;
      top = Math.min(top, r.top); bot = Math.max(bot, r.bottom);
      const cs = getComputedStyle(n), fs = parseFloat(cs.fontSize);
      if (fs < minPx) out.push({ severity: "error", kind: "min-font", message: `${fs.toFixed(0)}px < ${minPx}px: "${n.textContent.trim().slice(0, 30)}"` });
      const fg = rgb(cs.color), bg = bgOf(n), L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      if (ratio < (fs >= 40 ? 3 : 4.5)) out.push({ severity: "error", kind: "contrast", message: `contrast ${ratio.toFixed(2)} on "${n.textContent.trim().slice(0, 30)}"` });
    }
    const empty = 1 - (bot - top) / sr.height;
    if (empty > 0.4) out.push({ severity: "warn", kind: "empty-area", message: `${(empty * 100).toFixed(0)}% of frame is empty` });
    return { findings: out, emptyRatio: empty };
  }

  window.SceneKit = { render, seek, ready, lint, frameKey, kinds: Object.keys(LAYOUTS) };
})();
