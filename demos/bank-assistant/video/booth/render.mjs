// Render the booth loops frame by frame.
//   node render.mjs                     all five videos → out/*.mp4
//   node render.mjs v3                  one video
//   node render.mjs v3 --stills 0,9,15  PNG stills at those seconds → frames/
//   node render.mjs --reel              concatenate out/v1..v5 → out/booth-reel.mp4
import { spawn } from 'node:child_process';
import { mkdirSync, existsSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const PW = process.env.PLAYWRIGHT_MODULE || '/opt/homebrew/lib/node_modules/playwright/index.mjs';
const { chromium } = await import(PW);

const ROOT = dirname(fileURLToPath(import.meta.url));
const FPS = 30;
const VIDEOS = {
  v1: 'v1-business-scenarios',
  v2: 'v2-evals-first',
  v3: 'v3-trace-first',
  v4: 'v4-failure-modes',
  v5: 'v5-end-to-end',
};

const args = process.argv.slice(2);
const flag = (n) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : null; };
mkdirSync(join(ROOT, 'out'), { recursive: true });
mkdirSync(join(ROOT, 'frames'), { recursive: true });

function run(cmd, a, opts = {}) {
  return new Promise((res, rej) => {
    const p = spawn(cmd, a, { stdio: ['pipe', 'inherit', 'inherit'], ...opts });
    p.on('exit', (c) => (c === 0 ? res() : rej(new Error(`${cmd} exited ${c}`))));
    if (opts.feed) opts.feed(p.stdin);
  });
}

if (args.includes('--reel')) {
  const list = join(ROOT, 'out', 'reel.txt');
  writeFileSync(list, Object.values(VIDEOS).map((v) => `file '${join(ROOT, 'out', v + '.mp4')}'`).join('\n') + '\n');
  await run('ffmpeg', ['-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', '-movflags', '+faststart', join(ROOT, 'out', 'booth-reel.mp4')]);
  console.log('out/booth-reel.mp4');
  process.exit(0);
}

const picked = args.filter((a) => VIDEOS[a]);
const keys = picked.length ? picked : Object.keys(VIDEOS);
const stills = flag('--stills');

const browser = await chromium.launch({ args: ['--force-color-profile=srgb', '--font-render-hinting=none'] });

async function render(key) {
  const name = VIDEOS[key];
  const html = join(ROOT, 'scenes', name + '.html');
  if (!existsSync(html)) { console.log(`skip ${key}: ${html} missing`); return; }
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.error(`[${key}] page error:`, e.message));
  page.on('console', (m) => { if (m.type() === 'error') console.error(`[${key}]`, m.text()); });
  await page.goto(pathToFileURL(html).href + '?render');
  await page.waitForFunction(() => window.tl && window.tl.D > 0 && window.__ready === true, null, { timeout: 30000 });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((i) => (i.complete ? i.decode().catch(() => {}) : new Promise((r) => { i.onload = i.onerror = r; }))));
  });
  const D = await page.evaluate(() => window.tl.D);

  if (stills) {
    for (const s of stills.split(',').map(Number)) {
      await page.evaluate((ms) => window.seek(ms), s * 1000);
      const f = join(ROOT, 'frames', `${key}-${String(s).replace('.', '_')}.png`);
      await page.screenshot({ path: f });
      console.log(f);
    }
    await page.close();
    return;
  }

  const N = Math.round(D * FPS);
  const out = join(ROOT, 'out', name + '.mp4');
  const t0 = Date.now();
  await run('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p', '-r', String(FPS), '-movflags', '+faststart', out], {
    feed: async (stdin) => {
      for (let f = 0; f < N; f++) {
        await page.evaluate((ms) => window.seek(ms), (f * 1000) / FPS);
        const buf = await page.screenshot({ type: 'jpeg', quality: 95 });
        if (!stdin.write(buf)) await new Promise((r) => stdin.once('drain', r));
        if (f % 150 === 0) console.log(`[${key}] ${f}/${N}`);
      }
      stdin.end();
    },
  });
  console.log(`[${key}] ${out} · ${D}s · ${N} frames · ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  await page.close();
}

await Promise.all(keys.map(render));
await browser.close();
