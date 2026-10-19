// Deterministic timeline for frame-accurate rendering.
// Every visual state is a pure function of t: window.seek(ms) replays, in start
// order, every segment that has begun by t (later segments win). render.mjs
// calls seek() once per frame and screenshots the page.
(function () {
  const E = {
    linear: (p) => p,
    out: (p) => 1 - Math.pow(1 - p, 3),
    in: (p) => p * p * p,
    inOut: (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    outBack: (p) => { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
    outExpo: (p) => (p === 1 ? 1 : 1 - Math.pow(2, -10 * p)),
  };
  const DEF = { x: 0, y: 0, s: 1, o: 1, r: 0, blur: 0 };
  const segs = [];
  const resets = [];
  const els = new Set();
  const clamp = (v) => Math.max(0, Math.min(1, v));
  const lerp = (a, b, p) => a + (b - a) * p;
  const $ = (q, root = document) => (typeof q === 'string' ? root.querySelector(q) : q);

  function st(el) { if (!el._s) { el._s = { ...DEF }; els.add(el); } return el._s; }
  function write(el) {
    const s = el._s;
    el.style.transform = `translate(${s.x}px, ${s.y}px) scale(${s.s}) rotate(${s.r}deg)`;
    el.style.opacity = s.o;
    el.style.filter = s.blur > 0.01 ? `blur(${s.blur}px)` : '';
  }

  const tl = {
    E,
    D: 0,
    add(t0, dur, fn, ease = 'inOut') {
      segs.push({ t0, t1: t0 + dur, fn, ease: typeof ease === 'function' ? ease : E[ease], i: segs.length });
      return tl;
    },
    onReset(fn) { resets.push(fn); return tl; },
    set(t, q, props) { const el = $(q); st(el); return tl.add(t, 0, () => { Object.assign(el._s, props); write(el); }); },
    // Tween numeric transform/opacity props. `from` may be omitted per key: the value
    // in force when the segment starts is used.
    to(q, t0, dur, to, from = null, ease = 'out') {
      const el = $(q); st(el);
      return tl.add(t0, dur, (p) => {
        const start = { ...el._s, ...(from || {}) };
        for (const k in to) el._s[k] = lerp(start[k], to[k], p);
        write(el);
      }, ease);
    },
    // Convenience entrances / exits.
    in(q, t0, dur = 0.6, kind = 'up', ease = 'out') {
      const from = { o: 0, ...(kind === 'up' ? { y: 40 } : kind === 'down' ? { y: -40 } : kind === 'left' ? { x: -60 } : kind === 'right' ? { x: 60 } : kind === 'pop' ? { s: 0.86 } : kind === 'blur' ? { blur: 14, s: 1.04 } : {}) };
      const to = { o: 1, x: 0, y: 0, s: 1, blur: 0 };
      tl.set(0, q, from);
      return tl.to(q, t0, dur, to, from, kind === 'pop' ? 'outBack' : ease);
    },
    out(q, t0, dur = 0.5, kind = 'fade', ease = 'in') {
      const to = { o: 0, ...(kind === 'up' ? { y: -40 } : kind === 'down' ? { y: 40 } : kind === 'left' ? { x: -60 } : kind === 'blur' ? { blur: 14, s: 0.98 } : kind === 'shrink' ? { s: 0.94 } : {}) };
      return tl.to(q, t0, dur, to, null, ease);
    },
    // Show an element only inside [t0, t1] with entrance/exit.
    span(q, t0, t1, inKind = 'up', outKind = 'fade', dIn = 0.6, dOut = 0.45) {
      tl.in(q, t0, dIn, inKind);
      return tl.out(q, t1 - dOut, dOut, outKind);
    },
    type(q, t0, dur, text) {
      const el = $(q);
      tl.onReset(() => { el.textContent = ''; });
      return tl.add(t0, dur, (p) => { el.textContent = text.slice(0, Math.round(text.length * p)); }, 'linear');
    },
    count(q, t0, dur, a, b, fmt = (v) => v.toFixed(2), ease = 'out') {
      const el = $(q);
      tl.onReset(() => { el.textContent = fmt(a); });
      return tl.add(t0, dur, (p) => { el.textContent = fmt(lerp(a, b, p)); }, ease);
    },
    cls(q, t, name, on = true) {
      const el = $(q);
      if (!el._cls) el._cls = {};
      if (!(name in el._cls)) { const init = el.classList.contains(name); el._cls[name] = init; tl.onReset(() => el.classList.toggle(name, init)); }
      return tl.add(t, 0, () => el.classList.toggle(name, on));
    },
    // Camera on a screenshot window: frame rect [x, y, w, h] in capture CSS px.
    cam(q, t0, dur, rect, ease = 'inOut') {
      const win = $(q);
      const layer = win.querySelector('.layer');
      const vp = win.querySelector('.vp');
      const W = vp.clientWidth, H = vp.clientHeight;
      const IW = +win.dataset.w, IH = +win.dataset.h;
      const fit = (r) => {
        const s = Math.min(W / r[2], H / r[3]);
        let tx = W / 2 - s * (r[0] + r[2] / 2), ty = H / 2 - s * (r[1] + r[3] / 2);
        tx = Math.min(0, Math.max(W - s * IW, tx));
        ty = Math.min(0, Math.max(H - s * IH, ty));
        return { s, tx, ty };
      };
      const b = fit(rect);
      if (!win._cam) {
        win._cam = fit([0, 0, IW, IW * H / W]);
        const init = { ...win._cam };
        tl.onReset(() => { win._cam = { ...init }; applyCam(win, layer); });
      }
      return tl.add(t0, dur, (p) => {
        const a = { ...win._cam };
        const s = Math.exp(lerp(Math.log(a.s), Math.log(b.s), p));
        win._cam = { s, tx: lerp(a.tx, b.tx, p), ty: lerp(a.ty, b.ty, p) };
        applyCam(win, layer);
      }, ease);
    },
    // Coral highlight box (with optional spotlight dim) at rect, image CSS px.
    hl(q, t0, t1, rect, opts = {}) {
      const win = $(q);
      const box = document.createElement('div');
      box.className = 'hl' + (opts.dim === false ? '' : ' dim') + (opts.blue ? ' blue' : '');
      const pad = opts.pad ?? 8;
      Object.assign(box.style, { left: rect[0] - pad + 'px', top: rect[1] - pad + 'px', width: rect[2] + 2 * pad + 'px', height: rect[3] + 2 * pad + 'px' });
      win.querySelector('.layer').appendChild(box);
      tl.set(0, box, { o: 0, s: 1.12 });
      tl.to(box, t0, 0.5, { o: 1, s: 1 }, null, 'outBack');
      if (t1) tl.to(box, t1 - 0.35, 0.35, { o: 0 }, null, 'in');
      return box;
    },
  };

  function applyCam(win, layer) {
    const c = win._cam;
    layer.style.transform = `translate(${c.tx}px, ${c.ty}px) scale(${c.s})`;
    layer.style.setProperty('--inv', (1 / c.s).toFixed(4));
  }

  let ordered = null;
  window.tl = tl;
  window.seek = function (ms) {
    const t = ms / 1000;
    if (!ordered) ordered = segs.slice().sort((a, b) => a.t0 - b.t0 || a.i - b.i);
    for (const el of els) { el._s = { ...DEF }; write(el); }
    for (const r of resets) r();
    for (const sg of ordered) {
      if (t < sg.t0) break;
      const raw = sg.t1 === sg.t0 ? 1 : clamp((t - sg.t0) / (sg.t1 - sg.t0));
      sg.fn(sg.ease(raw));
    }
    return t;
  };
  // Preview in a normal browser: ?play loops in real time, ?t=12.5 freezes a frame.
  window.addEventListener('load', () => {
    const q = new URLSearchParams(location.search);
    const ready = () => document.fonts.ready.then(() => {
      window.__ready = true;
      if (q.has('t')) { window.seek(parseFloat(q.get('t')) * 1000); return; }
      if (q.has('play')) {
        const t0 = performance.now();
        const loop = () => { window.seek((performance.now() - t0) % (tl.D * 1000)); requestAnimationFrame(loop); };
        loop();
      } else window.seek(0);
    });
    if (window.__build) window.__build().then(ready); else ready();
  });
})();
