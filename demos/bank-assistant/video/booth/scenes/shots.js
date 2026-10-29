// Camera focus + highlight rects per capture, in capture CSS px (1920×1000).
// Filled in from DOM measurements at capture time (see capture.mjs → shots.json).
window.SHOTS = window.SHOTS || {};
const _d = { focus: [0, 0, 1920, 1000], box: [700, 300, 500, 200] };
for (const k of ['v1-scores', 'v1-dashboard']) window.SHOTS[k] = window.SHOTS[k] || _d;
