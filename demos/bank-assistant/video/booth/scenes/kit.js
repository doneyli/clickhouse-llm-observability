// Building blocks shared by the five booth loops.
(function () {
  const A = '../assets/brand/';
  const LF_ICON = A + 'icon/color/langfuse-icon.svg';
  const NW_LOGO = `<svg class="logo" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#2A2724"/><path d="M16 4l3.2 8.8L28 16l-8.8 3.2L16 28l-3.2-8.8L4 16l8.8-3.2z" fill="#4E9CFF"/><path d="M16 9.5l1.6 4.9 4.9 1.6-4.9 1.6-1.6 4.9-1.6-4.9-4.9-1.6 4.9-1.6z" fill="#FF5D5F"/></svg>`;
  const h = (html) => { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild; };
  let stage;

  const K = {
    LF_ICON, NW_LOGO, h,
    stage() {
      stage = document.createElement('div');
      stage.className = 'stage';
      stage.appendChild(h('<div class="bgimg"></div>'));
      document.body.appendChild(stage);
      return stage;
    },
    add(el, parent) { (parent || stage).appendChild(typeof el === 'string' ? h(el) : el); return (parent || stage).lastElementChild; },
    scene(html = '') { return K.add(`<div class="scene">${html}</div>`); },

    // Series chip + context line + wordmark, and one tick per beat start.
    chrome(series, starts, D) {
      K.add(`<div class="top"><span class="chip"><i></i>${series}</span><span class="ctx">Northwind Bank · fictional demo, synthetic data</span>
        <img class="mark" src="${A}wordmark/Langfuse by ClickHouse/light/langfuse-by-clickhouse.svg"></div>`);
      const ticks = K.add(`<div class="ticks">${starts.map(() => '<div class="tick"></div>').join('')}</div>`);
      const els = [...ticks.children];
      const only = (i) => els.forEach((e, j) => e.classList.toggle('on', j === i));
      tl.onReset(() => only(0));
      starts.forEach((t, i) => tl.add(t, 0, () => only(i)));
      tl.add(D - 0.45, 0, () => only(0));
      return ticks;
    },

    // Screenshot window. shot = file in ../shots (capture is 1920×1000 CSS px at 2×).
    win(shot, url, opts = {}) {
      const w = opts.w || 1920, hh = opts.h || 1000;
      const el = K.add(`<div class="win" data-w="${w}" data-h="${hh}">
          <div class="bar"><i></i><i></i><i></i><span class="url">${url}</span></div>
          <div class="vp"><div class="layer" style="width:${w}px;height:${hh}px"><img src="../shots/${shot}.png"></div></div>
        </div>`, opts.parent);
      if (opts.style) Object.assign(el.style, opts.style);
      const img = el.querySelector('img');
      img.onerror = () => { img.replaceWith(h(`<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:repeating-linear-gradient(45deg,#f3f1ea 0 24px,#ebe8df 24px 48px);font:600 48px var(--mono);color:#8A857C">shot pending: ${shot}</div>`)); };
      return el;
    },

    cap(text, kicker, opts = {}, parent) {
      const el = K.add(`<div class="cap ${opts.cls || ''}">${kicker ? `<small>${kicker}</small>` : ''}${text}</div>`, parent);
      if (opts.style) Object.assign(el.style, opts.style);
      return el;
    },

    // Hook card: on screen at t = 0 (the loop point), leaves at tOut, and is
    // wiped back in at the end so the last frame matches the first.
    hook(html, tOut, D, wipe = 'coral') {
      const sc = K.scene(`<div class="center">${html}</div>`);
      tl.out(sc, tOut, 0.55, 'blur');
      const w = K.add(`<div class="wipe ${wipe}"></div>`);
      tl.set(0, w, { x: 0 });
      tl.to(w, D - 1.0, 0.5, { x: 2400 }, { x: 0 }, 'inOut');
      tl.to(w, D - 0.5, 0.5, { x: 4400 }, null, 'inOut');
      tl.set(D - 0.5, sc, { o: 1, blur: 0, s: 1, x: 0, y: 0 });
      return sc;
    },

    endCard(line, sub, t0, D) {
      const sc = K.scene(`<div class="center">
          <img src="${A}icon/color/langfuse-icon.svg" style="width:150px;height:150px;margin-bottom:30px">
          <div class="h2">${line}</div>
          <div class="sub">${sub}</div>
          <div style="margin-top:52px;display:flex;gap:18px;align-items:center;justify-content:center">
            <span class="pill ink">langfuse.com</span><span class="pill blue">Ask us for a live demo</span>
          </div></div>`);
      tl.in(sc, t0, 0.7, 'blur');
      tl.set(D - 0.5, sc, { o: 0 });
      return sc;
    },

    // Animated chat transcript: msgs = [{who:'user'|'bot', html, at, meta}]
    chat(opts) {
      const el = K.add(`<div class="chat" style="left:${opts.x}px;top:${opts.y}px;width:${opts.w}px;${opts.h ? `height:${opts.h}px` : ''}">
          <div class="hd">${NW_LOGO}<div><b>Northwind Assistant</b><span>${opts.who || 'Signed in as Ana Torres'}</span></div><span class="ch">${opts.channel || 'Web'}</span></div>
          <div class="body"></div></div>`, opts.parent);
      const body = el.querySelector('.body');
      for (const m of opts.msgs) {
        const b = K.add(`<div class="msg ${m.who}">${m.html}${m.meta ? `<span class="meta">${m.meta}</span>` : ''}</div>`, body);
        if (m.at != null) tl.in(b, m.at, 0.5, m.who === 'user' ? 'right' : 'left');
      }
      return el;
    },

    score(name, value, cls = '') {
      return `<span class="score ${cls}"><img class="lf" src="${LF_ICON}">${name}<b>${value}</b></span>`;
    },
  };
  window.K = K;
})();
