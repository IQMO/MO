(function () {
'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const api = () => window.pywebview && window.pywebview.api;
const reduced = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const initial = window.MOLOGRTHIM_INITIAL || {};
const N = 14, WALL = 4.6;
const MONO = 'ui-monospace,"Cascadia Mono",Consolas,monospace';
const BAYS = [[3.5, 6.6], [5.9, 11.2], [10.0, 11.3], [11.5, 7.0], [7.5, 3.3], [2.9, 10.9]];
const STATIONS = [[1.15, -0.75], [1.2, 0.7], [-0.3, 1.05], [0.35, -1.15], [-1.15, 0.35]];
const PADS = [[10.3, 13.6], [13.6, 10.3], [12.0, 12.0], [11.1, 12.9]];   // one screen row along the front edge
const CORE = [7.5, 7.3];
const HUES = [0, 80, -40, 40, 110, -70];
const state = {data: null, selected: null, focus: initial.focus || '', composer: null, hits: [], hover: null,
  revealed: false, visible: true, conversation: {}, panelKey: '', toastAt: 0};
let canvas, ctx, staticLayer = null, W = 0, H = 0, DPR = 1, g = null, theme = null, raf = 0;

// ------------------------------------------------------------------ colour
function css(name, fallback) { const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fallback; }
function rgb(color) {
  const probe = document.createElement('canvas').getContext('2d');
  probe.fillStyle = '#000'; probe.fillStyle = color; const v = probe.fillStyle;
  if (v.startsWith('#')) { const n = parseInt(v.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  const m = v.match(/\d+(\.\d+)?/g) || [0, 0, 0]; return [+m[0], +m[1], +m[2]];
}
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
function hueShift(c, deg) {
  let [r, gg, b] = c.map(v => v / 255); const max = Math.max(r, gg, b), min = Math.min(r, gg, b); let h = 0, s = 0; const l = (max + min) / 2;
  if (max !== min) { const d = max - min; s = l > .5 ? d / (2 - max - min) : d / (max + min);
    h = max === r ? (gg - b) / d + (gg < b ? 6 : 0) : max === gg ? (b - r) / d + 2 : (r - gg) / d + 4; h *= 60; }
  h = (h + deg + 360) % 360; const q = l < .5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const f = t => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < .5 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
  return [f(h / 360 + 1 / 3), f(h / 360), f(h / 360 - 1 / 3)].map(v => Math.round(v * 255));
}
function readTheme() {
  const bg = rgb(css('--surface', '#0b0f14')), brand = rgb(css('--brand', '#19d3da'));
  theme = {bg, brand, text: rgb(css('--text', '#dfe7ee')), muted: rgb(css('--muted', '#8a99a6')), line: rgb(css('--line', '#1f2a33')),
    ok: rgb(css('--ok', '#49d18a')), warn: rgb(css('--warn', '#f2b84b')), error: rgb(css('--error', '#ef6b6b')),
    purple: hueShift(brand, 85), floor: mix(bg, brand, .045), wallL: mix(bg, brand, .07), wallR: mix(bg, [255, 255, 255], .035)};
  staticLayer = null;
}
const bayColor = i => i === 0 ? theme.brand : hueShift(theme.brand, HUES[i % HUES.length]);

// ------------------------------------------------------------------ geometry
function resize() {
  const box = $('#stage').getBoundingClientRect(); DPR = window.devicePixelRatio || 1;
  W = Math.max(320, box.width); H = Math.max(240, box.height);
  canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
  const tw = Math.min((W - 40) / N, (H - 36) / (N / 2 + WALL * 0.62));
  g = {tw, hx: tw / 2, hy: tw / 4, zu: tw * 0.62, ox: W / 2, oy: 18 + WALL * tw * 0.62};
  staticLayer = null;
}
const P = (gx, gy, z = 0) => ({x: g.ox + (gx - gy) * g.hx, y: g.oy + (gx + gy) * g.hy - z * g.zu});
function poly(points, fill, stroke, width = 1) {
  ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = width; ctx.stroke(); }
}

// ------------------------------------------------------------------ static room (cached until resize or skin change)
function buildStatic() {
  staticLayer = document.createElement('canvas'); staticLayer.width = canvas.width; staticLayer.height = canvas.height;
  const main = ctx; ctx = staticLayer.getContext('2d'); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const T = theme;
  poly([P(0, 0), P(0, N), P(0, N, WALL), P(0, 0, WALL)], rgba(T.wallL), rgba(T.line, .9));
  poly([P(0, 0), P(N, 0), P(N, 0, WALL), P(0, 0, WALL)], rgba(T.wallR), rgba(T.line, .9));
  const shade = ctx.createLinearGradient(0, P(0, 0, WALL).y, 0, P(0, 0).y);
  shade.addColorStop(0, rgba(T.bg, .55)); shade.addColorStop(1, rgba(T.bg, 0));
  poly([P(0, 0), P(0, N), P(0, N, WALL), P(0, 0, WALL)], shade); poly([P(0, 0), P(N, 0), P(N, 0, WALL), P(0, 0, WALL)], shade);
  poly([P(0, 0), P(N, 0), P(N, N), P(0, N)], rgba(T.floor), rgba(T.brand, .25), 1.2);
  ctx.strokeStyle = rgba(T.line, .55); ctx.lineWidth = 1;
  for (let i = 1; i < N; i++) {
    let a = P(i, 0), b = P(i, N); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    a = P(0, i); b = P(N, i); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  const glow = ctx.createRadialGradient(P(...CORE).x, P(...CORE).y, 0, P(...CORE).x, P(...CORE).y, g.tw * 6);
  glow.addColorStop(0, rgba(T.brand, .10)); glow.addColorStop(1, rgba(T.brand, 0));
  ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);
  for (let row = 0; row < 4; row++) {           // archive shelves on the left wall
    const z = 0.9 + row * 0.8;
    poly([P(0, 1.0, z), P(0, 5.4, z), P(0, 5.4, z + 0.08), P(0, 1.0, z + 0.08)], rgba(T.muted, .45));
    for (let k = 0; k < 11; k++) {
      const gy = 1.15 + k * 0.38, hgt = 0.42 + ((k * 7 + row * 3) % 5) * 0.05;
      poly([P(0.05, gy, z + 0.08), P(0.05, gy + 0.3, z + 0.08), P(0.05, gy + 0.3, z + 0.08 + hgt), P(0.05, gy, z + 0.08 + hgt)],
        rgba(mix(T.wallL, T.muted, .35 + (k % 3) * .1)), rgba(T.bg, .6));
    }
  }
  ctx = main;
}

// ------------------------------------------------------------------ primitives
function cube(gx, gy, size, base, o = {}) {
  const p = P(gx, gy, o.lift || 0), a = size * g.hx, b = size * g.hy, h = size * g.zu;
  const L = {x: p.x - a, y: p.y}, R = {x: p.x + a, y: p.y}, F = {x: p.x, y: p.y + b}, B = {x: p.x, y: p.y - b};
  const up = q => ({x: q.x, y: q.y - h});
  ctx.globalAlpha = o.alpha ?? 1;
  if (!o.noShadow) {
    const sh = P(gx, gy, 0), rad = ctx.createRadialGradient(sh.x, sh.y + b * .2, 0, sh.x, sh.y + b * .2, a * 1.6);
    rad.addColorStop(0, rgba(o.glow || base, .32)); rad.addColorStop(1, rgba(o.glow || base, 0));
    ctx.fillStyle = rad; ctx.beginPath(); ctx.ellipse(sh.x, sh.y + b * .2, a * 1.6, b * 1.7, 0, 0, Math.PI * 2); ctx.fill();
  }
  const edge = o.edge || rgba(mix(base, [255, 255, 255], .45), .55);
  if (o.outline) {
    const dark = rgba(mix(theme.bg, [0, 0, 0], .35), .95);
    poly([L, F, up(F), up(L)], dark, edge, 1.3); poly([F, R, up(R), up(F)], dark, edge, 1.3); poly([up(L), up(F), up(R), up(B)], dark, edge, 1.3);
  } else {
    poly([L, F, up(F), up(L)], rgba(base), edge, 1); poly([F, R, up(R), up(F)], rgba(mix(base, [0, 0, 0], .32)), edge, 1);
    poly([up(L), up(F), up(R), up(B)], rgba(mix(base, [255, 255, 255], .28)), edge, 1);
  }
  ctx.globalAlpha = 1;
  return {x: p.x - a, y: p.y - h - b, w: 2 * a, h: h + 2 * b, top: {x: p.x, y: p.y - h}};
}
function plate(x0, y0, x1, y1, color, z = 0.18, strong = false) {
  const top = [P(x0, y0, z), P(x1, y0, z), P(x1, y1, z), P(x0, y1, z)];
  poly([P(x1, y0), P(x1, y1), P(x1, y1, z), P(x1, y0, z)], rgba(mix(color, theme.bg, .7), .9));
  poly([P(x0, y1), P(x1, y1), P(x1, y1, z), P(x0, y1, z)], rgba(mix(color, theme.bg, .6), .9));
  poly(top, rgba(mix(theme.floor, color, .14), .96), rgba(color, strong ? .95 : .6), strong ? 2 : 1.3);
}
function tag(x, y, title, sub, color, subColor, hit) {
  ctx.font = `600 12px ${MONO}`; const tw = ctx.measureText(title).width;
  ctx.font = `12px ${MONO}`; const sw = sub ? ctx.measureText(sub).width : 0;
  const w = Math.max(tw, sw) + 18, h = sub ? 36 : 22;
  let bx = Math.round(x - w / 2), by = Math.round(y - h);
  const clash = (ax, ay) => state.placed.some(r => ax < r.x + r.w + 4 && ax + w + 4 > r.x && ay < r.y + r.h + 3 && ay + h + 3 > r.y);
  let tries = 0;
  while (clash(bx, by) && tries < 8) { by -= h * .55 + 2; if (tries % 3 === 2) bx += (tries % 2 ? -1 : 1) * w * .35; tries++; }
  if (tries) { ctx.strokeStyle = rgba(color, .45); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(bx + w / 2, by + h); ctx.stroke(); }
  state.placed.push({x: bx, y: by, w, h});
  ctx.fillStyle = rgba(mix(theme.bg, [0, 0, 0], .2), .88); ctx.strokeStyle = rgba(color, .75); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.roundRect(bx, by, w, h, 6); ctx.fill(); ctx.stroke();
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.font = `600 12px ${MONO}`; ctx.fillStyle = rgba(theme.text); ctx.fillText(title, x, by + (sub ? 11 : 11));
  if (sub) { ctx.font = `12px ${MONO}`; ctx.fillStyle = rgba(subColor || color); ctx.fillText(sub, x, by + 26); }
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  if (hit) state.hits.push({...hit, x: bx, y: by, w, h});
}
function wallText(wall, s0, z, lines, width) {
  ctx.save();
  const o = wall === 'R' ? P(s0, 0, z) : P(0, s0, z), u = wall === 'R' ? [g.hx, g.hy] : [g.hx, -g.hy];
  const len = Math.hypot(u[0], u[1]);
  ctx.setTransform(DPR * u[0] / len, DPR * u[1] / len, 0, DPR, DPR * o.x, DPR * o.y);
  lines.forEach(([text, color, font, dy]) => {
    ctx.font = font || `12px ${MONO}`; ctx.fillStyle = color;
    let shown = String(text || ''); while (shown && ctx.measureText(shown).width > width) shown = shown.slice(0, -2);
    ctx.fillText(shown === String(text || '') ? shown : shown + '…', 12, dy);
  });
  ctx.restore();
}
function board(wall, s0, s1, z0, z1, color) {
  const pts = wall === 'R' ? [P(s0, 0, z1), P(s1, 0, z1), P(s1, 0, z0), P(s0, 0, z0)] : [P(0, s0, z1), P(0, s1, z1), P(0, s1, z0), P(0, s0, z0)];
  poly(pts, rgba(mix(theme.bg, [0, 0, 0], .25), .85), rgba(color, .55), 1.2);
  return Math.abs(s1 - s0) * Math.hypot(g.hx, g.hy) - 24;
}

// ------------------------------------------------------------------ scene
function layout(d) {
  const mos = (d.mos || []).slice(0, BAYS.length);
  const bays = mos.map((m, i) => ({mo: m, i, at: BAYS[i], color: bayColor(i)}));
  const byProject = {};
  bays.forEach(b => { if (!b.mo.desktop && !(b.mo.cwd in byProject)) byProject[b.mo.cwd] = b; });
  const specialists = (d.specialists || []).map(s => ({s, bay: byProject[s.cwd]})).filter(x => x.bay);
  const used = {};
  specialists.forEach(x => { const k = x.bay.i; used[k] = (used[k] || 0); const off = STATIONS[used[k] % STATIONS.length]; used[k]++;
    x.at = [x.bay.at[0] + off[0], x.bay.at[1] + off[1]]; });
  return {bays, specialists, candidates: (d.candidates || []).slice(0, PADS.length).map((c, i) => ({c, at: PADS[i], bay: byProject[c.cwd]}))};
}

function frame(now) {
  raf = 0;
  if (!ctx || !g || !theme) return;
  const t = reduced ? 0 : now / 1000;
  if (!staticLayer) buildStatic();
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(staticLayer, 0, 0);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  state.hits = []; state.placed = [];
  const d = state.data || {mos: []}, L = layout(d), T = theme, labels = [];
  // walls: learning, archive, taskboard of all MOs, power
  const learn = d.learning || {}, brain = d.brain || {};
  let w = board('L', 11.2, 5.6, 0.9, 3.6, T.ok);
  wallText('L', 11.2, 3.6, [['LEARNING', rgba(T.ok), `600 13px ${MONO}`, 20],
    [`${brain.lessons_adopted ?? learn.confirmed_suggestions ?? 0} adopted · ${brain.lessons_pending ?? learn.pending_suggestions ?? 0} waiting for you`, rgba(T.text), null, 40],
    [`memory ${brain.memory_turns ?? learn.memory_turns ?? 0} turns · ${brain.recall || learn.recall_mode || 'recall'}`, rgba(T.muted), null, 58],
    [brain.profile ? `profile ${brain.profile} · ${brain.product_intents || 0} product notes staged` : '', rgba(T.muted), null, 76]], w);
  state.hits.push(hitQuad('learning', P(0, 11.2, 3.6), P(0, 5.6, 0.9)));
  const arch = d.archive || {}, lit = arch.recent > 0;
  const archLabel = lit ? `indexing · ${arch.recent} in 10 min` : arch.last_at ? `last ${ago(arch.last_at)}` : 'no record yet';
  wallText('L', 4.9, 4.25, [['ARCHIVE', rgba(lit ? T.brand : T.muted), `600 13px ${MONO}`, 0], [archLabel, rgba(lit ? T.brand : T.muted), null, 18]], 260);
  if (lit) {
    const p = P(0, 2.9, 2.2), pulse = .25 + .2 * Math.sin(t * 3);
    const gl = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, g.tw * 2.6); gl.addColorStop(0, rgba(T.brand, pulse)); gl.addColorStop(1, rgba(T.brand, 0));
    ctx.fillStyle = gl; ctx.beginPath(); ctx.arc(p.x, p.y, g.tw * 2.6, 0, Math.PI * 2); ctx.fill();
  }
  state.hits.push(hitQuad('archive', P(0, 4.7, 3.7), P(0, 0.9, 0.8)));
  w = board('R', 2.2, 9.4, 0.9, 4.2, T.brand);
  const rows = [['TASKBOARD · ALL MOs', rgba(T.brand), `600 13px ${MONO}`, 20]];
  L.bays.forEach((b, i) => { const m = b.mo, task = m.task.active || m.task.next || (m.busy ? m.request : '') || 'idle';
    rows.push([`${moName(m).padEnd(12).slice(0, 12)} ${task}${m.task.open ? `  · ${m.task.open} open` : ''}`,
      rgba(m.task.state === 'blocked' ? T.error : b.color), null, 42 + i * 19]); });
  if (!L.bays.length) rows.push(['no MO running', rgba(T.muted), null, 42]);
  wallText('R', 2.2, 4.2, rows, w);
  state.hits.push(hitQuad('taskboard', P(2.2, 0, 4.2), P(9.4, 0, 0.9)));
  const res = d.resources || {}, pressure = res.pressure || 'ok', pc = pressure === 'error' ? T.error : pressure === 'warn' ? T.warn : T.brand;
  w = board('R', 10.2, 13.5, 1.0, 3.5, pc);
  wallText('R', 10.2, 3.5, [['POWER', rgba(pc), `600 13px ${MONO}`, 20], [`CPU ${pct(res.system_cpu)}`, rgba(T.text), null, 40],
    [`RAM ${pct(res.memory_percent)}`, rgba(T.text), null, 58], [`MO ${gb(res.mo_memory)}`, rgba(T.muted), null, 76]], w * .64);
  bars(res, pc, t);
  state.hits.push(hitQuad('power', P(10.2, 0, 3.5), P(13.5, 0, 1.0)));
  // floor tracks from each bay to the brain
  ctx.setLineDash([6, 6]); ctx.lineDashOffset = -t * 18;
  L.bays.forEach(b => { const a = P(...b.at, 0.2), c = P(...CORE, 0.2); ctx.strokeStyle = rgba(b.color, .35); ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(c.x, c.y); ctx.stroke(); });
  ctx.setLineDash([]);
  // bays
  L.bays.forEach(b => { const [x, y] = b.at, sel = state.selected && state.selected.type === 'mo' && state.selected.id === b.mo.id;
    plate(x - 1.8, y - 1.4, x + 1.8, y + 1.4, b.color, .2, sel);
    const front = P(x - 1.65, y + 1.3, 0); labels.push(() => {
      ctx.save(); const u = [g.hx, g.hy], len = Math.hypot(...u); const o = P(x - 1.7, y + 1.55, 0);
      ctx.setTransform(DPR * u[0] / len, DPR * u[1] / len, 0, DPR, DPR * o.x, DPR * o.y);
      ctx.font = `11px ${MONO}`; ctx.fillStyle = rgba(b.color, .8);
      let name = `${b.mo.project.toUpperCase()} · ${(b.mo.desktop ? 'DESKTOP' : b.mo.slot.toUpperCase())}`;
      const room = 3.4 * Math.hypot(g.hx, g.hy); while (name && ctx.measureText(name).width > room) name = name.slice(0, -2);
      ctx.fillText(name, 0, 12); ctx.restore(); void front; }); });
  // brain core with the goal ring
  const goalMo = goalOwner(d), goal = goalMo ? goalMo.goal : null;
  const c = P(...CORE, 0.05), rx = g.tw * 2.35, ry = g.tw * 1.18;
  ctx.strokeStyle = rgba(T.brand, .55); ctx.lineWidth = 2; ctx.beginPath(); ctx.ellipse(c.x, c.y, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = rgba(T.brand, .18); ctx.lineWidth = 8; ctx.beginPath(); ctx.ellipse(c.x, c.y, rx * .82, ry * .82, 0, 0, Math.PI * 2); ctx.stroke();
  if (goal && goal.total) for (let k = 0; k < goal.total; k++) {
    const ang = -Math.PI / 2 + k * Math.PI * 2 / goal.total + (goal.state === 'running' ? t * .15 : 0);
    ctx.fillStyle = rgba(k < goal.done ? T.brand : T.line, k < goal.done ? .95 : .9);
    ctx.beginPath(); ctx.arc(c.x + Math.cos(ang) * rx, c.y + Math.sin(ang) * ry, 4.2, 0, Math.PI * 2); ctx.fill();
  }
  const bob = reduced ? 0 : Math.sin(t * 1.6) * .06;
  const core = [[-.5, -.5], [.5, -.5], [-.5, .5], [.5, .5]].map(([dx, dy]) => cube(CORE[0] + dx * 1.05, CORE[1] + dy * 1.05, .95, T.brand,
    {lift: .35 + bob + (dx + dy === 0 ? .03 : 0), noShadow: dx + dy !== 1}));
  state.hits.push({type: 'brain', id: 'brain', x: core[0].x - 10, y: core[0].y - 10, w: core[3].x + core[3].w - core[0].x + 20, h: 90});
  labels.push(() => tag(c.x, c.y - g.tw * 1.65, 'MO · the brain', goal ? `goal loop · step ${Math.min(goal.done + 1, goal.total)} of ${goal.total}` : 'shared by every MO',
    T.brand, T.brand, {type: 'brain', id: 'brain'}));
  // mologs: leads and specialists, back to front
  const items = [];
  L.bays.forEach(b => items.push({depth: b.at[0] + b.at[1] - .4, draw: () => lead(b, t, labels)}));
  L.specialists.forEach(x => items.push({depth: x.at[0] + x.at[1], draw: () => specialist(x, t, labels)}));
  L.candidates.forEach(x => items.push({depth: x.at[0] + x.at[1], draw: () => candidate(x, t, labels)}));
  items.push({depth: 12.0 + 4.3, draw: () => gate(d, labels)});
  items.push({depth: 13.2 + 2.4, draw: () => care(d, t, labels)});
  if ((d.owner_desk || []).length) items.push({depth: 1.5 + 12.4, draw: () => desk(labels)});
  items.sort((a, b) => a.depth - b.depth).forEach(i => i.draw());
  beams(d, L, t);
  labels.forEach(fn => fn());
  if (state.visible && !reduced && !document.hidden) raf = requestAnimationFrame(frame);
}
function hitQuad(type, a, b) { return {type, id: type, x: Math.min(a.x, b.x), y: Math.min(a.y, b.y) - 10, w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) + 40}; }
function bars(res, color, t) {
  const vals = [res.system_cpu, res.memory_percent].map(v => v == null ? 0 : Math.max(0, Math.min(100, v)) / 100);
  vals.forEach((v, i) => { const s0 = 12.0, z0 = 2.75 - i * .42, len = 1.3;
    poly([P(s0, 0.02, z0), P(s0 + len, 0.02, z0), P(s0 + len, 0.02, z0 + .18), P(s0, 0.02, z0 + .18)], rgba(theme.line, .9));
    poly([P(s0, 0.03, z0), P(s0 + len * v, 0.03, z0), P(s0 + len * v, 0.03, z0 + .18), P(s0, 0.03, z0 + .18)], rgba(color, .85)); });
  void t;
}
function goalOwner(d) {
  const mos = (d.mos || []).filter(m => m.goal && m.goal.state && !['completed', 'cancelled'].includes(m.goal.state));
  return mos.find(m => m.id === state.focus) || mos[0] || null;
}
function lead(b, t, labels) {
  const m = b.mo, [x, y] = [b.at[0] - .6, b.at[1] - .2], blocked = m.task.state === 'blocked';
  const waiting = m.goal && ['paused', 'pausing'].includes(m.goal.state);
  const bob = reduced ? 0 : Math.sin(t * 2 + b.i) * .05 + (blocked ? -.12 : 0);
  if (waiting) ring(x, y, .8, theme.warn, t);
  const box = cube(x, y, .95, b.color, {lift: .3 + bob, edge: blocked ? rgba(theme.error, .95) : null});
  mark(box.top.x, box.top.y, b.color);
  if (m.busy && !reduced) orbit(x, y, .3 + bob, b.color, t);
  if (blocked) alarm(box);
  if (state.selected && state.selected.type === 'mo' && state.selected.id === m.id) ring(x, y, .9, theme.brand, t, true);
  state.hits.push({type: 'mo', id: m.id, x: box.x, y: box.y - 14, w: box.w, h: box.h + 14});
  const sub = m.busy ? (m.request || 'working') : waiting ? `goal ${m.goal.state}` : m.task.open ? `${m.task.open} open rows` : 'idle';
  labels.push(() => tag(box.top.x, box.y - 8, `MO · ${moName(m)}`, clip(sub, 34), b.color, waiting ? theme.warn : b.color, {type: 'mo', id: m.id}));
}
function specialist(xs, t, labels) {
  const s = xs.s, [x, y] = xs.at, k = xs.bay.i * 3 + s.role.length, color = mix(xs.bay.color, [255, 255, 255], .08);
  const st = s.state, bob = reduced ? 0 : Math.sin(t * 2.2 + k) * .04 + (st === 'blocked' ? -.14 : 0);
  for (let r = 0; r < s.rank; r++) plateTier(x, y, r, color);
  const lift = .12 + s.rank * .07 + bob;
  const box = cube(x, y, .68, color, {lift, edge: st === 'blocked' ? rgba(theme.error, .95) : null,
    alpha: st === 'blocked' ? .82 : st === 'interrupted' ? .55 : 1});
  if (st === 'working' && !reduced) orbit(x, y, lift, color, t, .55);
  if (st === 'blocked') alarm(box);
  if (st === 'verified') check(box);
  if (st === 'reported') ring(x, y, .55, theme.warn, t);
  if (state.selected && state.selected.type === 'specialist' && state.selected.id === s.cwd + '|' + s.role) ring(x, y, .65, theme.brand, t, true);
  state.hits.push({type: 'specialist', id: s.cwd + '|' + s.role, x: box.x, y: box.y - 12, w: box.w, h: box.h + 12});
  const sub = st === 'working' ? 'working' : st === 'blocked' ? 'blocked' : st === 'reported' ? 'report waits for the check' :
    st === 'interrupted' ? 'stopped: its MO closed' : st === 'verified' ? 'verified' : st === 'corrected' ? 'corrected' : 'idle';
  const col = st === 'blocked' || st === 'corrected' ? theme.error : st === 'reported' || st === 'interrupted' ? theme.warn
    : st === 'verified' ? theme.ok : theme.brand;
  const id = s.cwd + '|' + s.role, shown = ['working', 'blocked', 'reported', 'interrupted'].includes(st)
    || (state.selected && state.selected.id === id) || (state.hover && state.hover.id === id);
  // F1: notable specialists carry a label; quiet ones show theirs on hover or selection.
  if (shown) labels.push(() => tag(box.top.x, box.y - 6, s.name, `${sub}${s.verified ? ` · rank ${s.rank}` : ''}`, col, col, {type: 'specialist', id}));
}
function candidate(xc, t, labels) {
  const c = xc.c, [x, y] = xc.at;
  plate(x - .5, y - .45, x + .5, y + .45, theme.warn, .1);
  const box = cube(x, y, .66, theme.warn, {outline: true, lift: .1, edge: rgba(theme.warn, .95), glow: theme.warn});
  if (state.selected && state.selected.type === 'candidate' && state.selected.id === c.cwd + '|' + c.role) ring(x, y, .65, theme.warn, t, true);
  state.hits.push({type: 'candidate', id: c.cwd + '|' + c.role, x: box.x, y: box.y, w: box.w, h: box.h});
  labels.push(() => tag(box.top.x, box.y + box.h + 44, `Candidate · ${c.name}`, 'waiting for your yes', theme.warn, theme.warn,
    {type: 'candidate', id: c.cwd + '|' + c.role}));
}
function gate(d, labels) {
  const r = d.review || {}, x0 = 12.0, y = 4.3, base = mix(theme.bg, theme.ok, .3);
  const pillar = gx => { const q = [P(gx, y - .18), P(gx + .32, y - .18), P(gx + .32, y + .18), P(gx, y + .18)];
    poly([q[1], q[2], P(gx + .32, y + .18, 2), P(gx + .32, y - .18, 2)], rgba(mix(base, [0, 0, 0], .3)), rgba(theme.ok, .5));
    poly([q[3], q[2], P(gx + .32, y + .18, 2), P(gx, y + .18, 2)], rgba(base), rgba(theme.ok, .5)); };
  pillar(x0); pillar(x0 + 1.5);
  poly([P(x0, y + .18, 2), P(x0 + 1.82, y + .18, 2), P(x0 + 1.82, y + .18, 2.3), P(x0, y + .18, 2.3)], rgba(base), rgba(theme.ok, .7));
  poly([P(x0, y - .18, 2.3), P(x0 + 1.82, y - .18, 2.3), P(x0 + 1.82, y + .18, 2.3), P(x0, y + .18, 2.3)], rgba(mix(base, [255, 255, 255], .2)), rgba(theme.ok, .7));
  const a = P(x0, y, 0), b = P(x0 + 1.82, y, 0), a2 = P(x0, y, 2.3), b2 = P(x0 + 1.82, y, 2.3);
  state.hits.push({type: 'review', id: 'review', x: a2.x - 10, y: a2.y - 40, w: b.x - a2.x + 20, h: a.y - a2.y + 40});
  labels.push(() => tag((a2.x + b2.x) / 2, a2.y - 6, 'Review gate', `✓ ${r.verified || 0} verified · ${r.refused || 0} refused`, theme.ok,
    r.refused ? theme.error : theme.ok, {type: 'review', id: 'review'}));
}
function care(d, t, labels) {
  const cr = d.care || {}, open = (cr.findings || []).length, unshown = cr.unshown || 0, [x, y] = [13.2, 2.4];
  const col = unshown ? theme.error : theme.ok, bob = reduced ? 0 : Math.sin(t * 1.8) * .04;
  const box = cube(x, y, .6, col, {lift: .15 + bob});
  if (!reduced) orbit(x, y, .15 + bob, col, t, .5);
  state.hits.push({type: 'care', id: 'care', x: box.x, y: box.y, w: box.w, h: box.h});
  labels.push(() => tag(box.top.x, box.y - 6, 'MO Care', open ? `watching · ${open} reported${unshown ? ` · ${unshown} new` : ''}` : 'watching · all quiet',
    col, col, {type: 'care', id: 'care'}));
}
function desk(labels) {
  const [x, y] = [1.5, 12.4];
  plate(x - .8, y - .5, x + .8, y + .5, theme.muted, .45);
  const box = cube(x + .25, y - .1, .42, theme.muted, {lift: .45, noShadow: true});
  state.hits.push({type: 'owner', id: 'owner', x: box.x - 30, y: box.y, w: box.w + 60, h: box.h + 30});
  labels.push(() => tag(box.top.x, box.y - 6, 'Owner desk', 'only you · private profile', theme.muted, theme.muted, {type: 'owner', id: 'owner'}));
}
function beams(d, L, t) {
  const now = Date.now() / 1000, find = id => L.bays.find(b => b.mo.id === id || b.mo.slot === id);
  (d.messages || []).filter(m => now - m.at < 600).forEach(m => {
    const from = find(m.from_id), targets = m.to === 'project' ? L.bays.filter(b => from && b !== from && b.mo.cwd === from.mo.cwd) : [find(m.to)].filter(Boolean);
    if (!from) return;
    targets.forEach(to => {
      const a = P(from.at[0] - .55, from.at[1] - .15, 1.1), b = P(to.at[0] - .55, to.at[1] - .15, 1.1), mid = {x: (a.x + b.x) / 2, y: Math.min(a.y, b.y) - g.tw * 1.2};
      const fade = Math.max(.25, 1 - (now - m.at) / 600);
      ctx.strokeStyle = rgba(theme.purple, .75 * fade); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(mid.x, mid.y, b.x, b.y); ctx.stroke();
      if (!reduced) { const k = (t * .5) % 1, x = (1 - k) ** 2 * a.x + 2 * (1 - k) * k * mid.x + k * k * b.x, y = (1 - k) ** 2 * a.y + 2 * (1 - k) * k * mid.y + k * k * b.y;
        ctx.fillStyle = rgba(theme.purple, fade); ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill(); }
      tag(mid.x, mid.y + 10, `${moName(from.mo)} → ${moName(to.mo)}`, clip(m.text, 30), theme.purple, theme.purple, null);
    });
  });
}
function plateTier(x, y, r, color) { const z = .06 + r * .07, s = .5 + (2 - r) * .04;
  poly([P(x - s, y - s, z), P(x + s, y - s, z), P(x + s, y + s, z), P(x - s, y + s, z)], rgba(mix(color, theme.bg, .45), .95), rgba(color, .8), 1); }
function ring(x, y, r, color, t, strong = false) {
  const p = P(x, y, .02), pulse = reduced ? 0 : Math.sin(t * 3) * .06;
  ctx.strokeStyle = rgba(color, strong ? .95 : .75); ctx.lineWidth = strong ? 2.2 : 1.6;
  ctx.beginPath(); ctx.ellipse(p.x, p.y, g.hx * r * (1 + pulse), g.hy * r * (1 + pulse), 0, 0, Math.PI * 2); ctx.stroke();
}
function orbit(x, y, lift, color, t, size = .7) {
  const ang = t * 2.2, rr = size * 1.05;
  cube(x + Math.cos(ang) * rr, y + Math.sin(ang) * rr, size * .22, mix(color, [255, 255, 255], .2), {lift: lift + size * .9, noShadow: true});
}
function mark(x, y, color) {
  const s = g.tw * .085;
  for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const cx = x + (dx - dy) * s * .95, cy = y - g.hy * .05 + (dx + dy) * s * .48 - s * .4;
    poly([{x: cx - s, y: cy}, {x: cx, y: cy - s * .5}, {x: cx + s, y: cy}, {x: cx, y: cy + s * .5}], rgba(mix(color, [255, 255, 255], .45)), rgba(mix(color, [0, 0, 0], .3), .7), .8);
  }
}
function alarm(box) { ctx.strokeStyle = rgba(theme.error); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(box.top.x, box.y - 4); ctx.lineTo(box.top.x, box.y - 16); ctx.stroke();
  ctx.fillStyle = rgba(theme.error); ctx.beginPath(); ctx.arc(box.top.x, box.y - 20, 2.4, 0, Math.PI * 2); ctx.fill(); }
function check(box) { ctx.strokeStyle = rgba(theme.ok); ctx.lineWidth = 2.4; ctx.beginPath(); ctx.moveTo(box.top.x - 6, box.y - 10); ctx.lineTo(box.top.x - 1, box.y - 5); ctx.lineTo(box.top.x + 8, box.y - 16); ctx.stroke(); }
function moName(m) {
  if (!m) return 'MO';
  if (m.desktop) return 'Desktop';
  const same = ((state.data || {}).mos || []).filter(o => !o.desktop && o.slot === m.slot).length > 1;
  return same ? `${m.slot} · ${String(m.id).slice(0, 4)}` : m.slot;          // two MOs share a slot name
}
const clip = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const pct = v => v == null ? '—' : `${Math.round(v)}%`;
const gb = v => v == null ? '—' : v > 1024 ** 3 ? `${(v / 1024 ** 3).toFixed(1)} GB` : `${Math.round(v / 1024 ** 2)} MB`;
function ago(at) { const s = Date.now() / 1000 - at; return s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; }
function clock(at) { return at ? new Date(at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : ''; }

// ------------------------------------------------------------------ panel
function find(type, id) {
  const d = state.data || {};
  if (type === 'mo') return (d.mos || []).find(m => m.id === id);
  if (type === 'specialist') return (d.specialists || []).find(s => s.cwd + '|' + s.role === id);
  if (type === 'candidate') return (d.candidates || []).find(c => c.cwd + '|' + c.role === id);
  return null;
}
function leadFor(cwd) { return ((state.data || {}).mos || []).find(m => m.cwd === cwd && !m.desktop); }
function careCard(d) {
  const f = ((d.care || {}).findings || [])[0];
  if (!f) return '';
  return `<div class="care-card"><div class="panel-kicker">MO Care report · ${esc(clock(f.at))}</div><h3>${esc(f.kind)}</h3><p>${esc(clip(f.detail, 220))}</p>
    <p>${esc(f.source)}</p><div class="panel-actions"><button class="button primary" data-do="investigate" data-id="${esc(f.id)}">Investigate</button>
    <button class="button" data-do="dismiss" data-id="${esc(f.id)}">Dismiss</button></div></div>`;
}
function calledList(d) {
  const rows = d.just_called || [];
  return rows.length ? `<ul class="called">${rows.map(r => `<li class="${esc(r.kind)}">${esc(r.text)} <small class="panel-text dim">${esc(ago(r.at))}</small></li>`).join('')}</ul>`
    : '<p class="panel-text dim">Nothing called in the last hour.</p>';
}
function composer(mo, d) {
  const c = state.composer, specs = (d.specialists || []).filter(s => s.cwd === mo.cwd);
  const chip = (key, label) => `<button class="chip ${c.target === key ? 'on' : ''}" data-do="target" data-target="${esc(key)}">${esc(label)}</button>`;
  return `<div class="panel-kicker">Assign · to MO · ${esc(moName(mo))}</div><textarea class="composer" id="composer" placeholder="What should MO do?">${esc(c.text || '')}</textarea>
    <div class="chips">${chip('mo', `MO · ${moName(mo)} decides who`)}${specs.map(s => chip('s:' + s.name, `ask ${s.name}`)).join('')}${chip('new', 'new MO')}</div>
    <p class="panel-text dim">Goes to MO as a normal request: its lead sends the specialist, checks the report, then rank counts.</p>
    <div class="panel-actions"><button class="button primary" data-do="send-assign">Assign</button><button class="button" data-do="cancel">Cancel</button></div>`;
}
function renderPanel(force = false) {
  const d = state.data || {}, sel = state.selected || {type: 'brain', id: 'brain'};
  const key = JSON.stringify([sel, state.composer && state.composer.target, d.at && Math.floor(d.at / 4), (d.care || {}).findings && (d.care.findings[0] || {}).id,
    (state.conversation[sel.id] || []).length]);
  if (!force && key === state.panelKey) return;
  if (document.activeElement && document.activeElement.id === 'composer') return;   // never rebuild under typing
  state.panelKey = key;
  let html = careCard(d);
  if (sel.type === 'mo' && find('mo', sel.id)) {
    const m = find('mo', sel.id), g2 = m.goal || {};
    html += `<div class="panel-kicker">Selected MO</div><p class="panel-title">MO · ${esc(moName(m))}</p><p class="panel-sub">${esc(m.project)}${m.model ? ' · ' + esc(m.model) : ''}</p>
      <div class="panel-kicker">Now</div><p class="panel-text">${esc(m.busy ? m.request || 'working on a turn' : 'idle')}</p>`;
    if (m.task.open || m.task.active) html += `<div class="panel-kicker">Taskboard</div><p class="panel-text">${esc(m.task.active || m.task.next || m.task.title)}${m.task.open ? ` · ${m.task.open} open` : ''}</p>`;
    if (g2.state) html += `<div class="panel-kicker">Goal loop</div><p class="panel-text">${esc(g2.objective || 'goal')} · step ${Math.min(g2.done + 1, g2.total)} of ${g2.total} · ${esc(g2.state)}</p>
      <div class="panel-actions">${g2.state === 'running' ? '<button class="button" data-do="pause">Pause after this step</button>' : g2.state === 'paused' ? '<button class="button" data-do="resume">Resume</button>' : ''}</div>`;
    if (m.files.length) html += `<div class="panel-kicker">Just edited</div><p class="panel-text">${esc(m.files.join(', '))}</p>`;
    const chat = state.conversation[m.id];
    if (chat && chat.length) html += `<div class="panel-kicker">Talking with you</div><div class="chat">${chat.slice(-4).map(r => `<p class="${esc(r.role)}">${esc(clip(r.text, 260))}</p>`).join('')}</div>`;
    html += state.composer && !m.desktop ? composer(m, d) : `<div class="panel-actions">${m.desktop ? '<p class="panel-text dim">MO Desktop takes requests in its own composer.</p>' :
      '<button class="button primary" data-do="assign">Assign</button><button class="button" data-do="terminal">Open terminal</button>'}</div>`;
  } else if (sel.type === 'specialist' && find('specialist', sel.id)) {
    const s = find('specialist', sel.id), lead = leadFor(s.cwd);
    html += `<div class="panel-kicker">Selected specialist</div><p class="panel-title">${esc(s.name)}</p><p class="panel-sub">${esc(s.project)}${lead ? ' · led by MO · ' + esc(moName(lead)) : ''}</p>
      <div class="rank-row">${[1, 2, 3].map(r => `<i class="${s.rank >= r ? 'on' : ''}"></i>`).join('')}<span>${s.verified ? `rank ${s.rank} · ${s.points} points from verified work` : 'no checked work yet'}</span></div>
      <div class="stat-grid"><div class="stat"><b>${s.runs}</b><small>runs</small></div><div class="stat"><b>${s.verified}</b><small>verified</small></div>
      <div class="stat"><b>${s.corrected}</b><small>corrected</small></div><div class="stat"><b>${esc(s.usual || '—')}</b><small>usual work</small></div></div>
      <div class="panel-kicker">Now</div><p class="panel-text ${s.now ? '' : 'dim'}">${s.state === 'interrupted' ? 'Stopped unreported, its MO closed: ' : ''}${esc(s.now || (s.state === 'reported' ? 'report waits for the architect\'s check' : 'idle'))}</p>
      <div class="panel-kicker">Last report</div><p class="panel-text ${s.last_report ? '' : 'dim'}">${esc(s.last_report || 'no report yet')}</p>
      ${s.last_verdict ? `<p class="panel-text dim">${s.last_verdict === 'accepted' ? 'verified by the architect' : 'refused by the architect'}${s.last_reason ? ': ' + esc(clip(s.last_reason, 200)) : ''}</p>` : ''}
      <div class="panel-kicker">Responsibility</div><p class="panel-text dim">${esc(s.focus)}</p>
      <div class="panel-kicker">Just called</div>${calledList(d)}`;
    html += lead ? (state.composer ? composer(lead, d) : `<div class="panel-actions"><button class="button primary" data-do="assign" data-specialist="${esc(s.name)}">Ask ${esc(s.name)}</button>
      <button class="button" data-do="terminal" data-mo="${esc(lead.id)}">Open terminal</button></div>`) : '';
  } else if (sel.type === 'candidate' && find('candidate', sel.id)) {
    const c = find('candidate', sel.id);
    html += `<div class="panel-kicker">Candidate · waiting for your yes</div><p class="panel-title">${esc(c.name)}</p><p class="panel-sub">${esc(c.project)} · proposed ${esc(ago(c.at))}</p>
      <div class="panel-kicker">Would do</div><p class="panel-text">${esc(c.description)}</p><div class="panel-kicker">Why (its CV)</div><div class="candidate-why">${esc(c.why || 'no reason recorded')}</div>
      <p class="panel-text dim">Hiring adds it to that project's team. Work still goes through MO, and only checked reports build its rank.</p>
      <div class="panel-actions"><button class="button primary" data-do="hire">Hire</button></div>`;
  } else if (sel.type === 'care') {
    const fs = (d.care || {}).findings || [];
    html += `<div class="panel-kicker">MO Care</div><p class="panel-title">Background problems</p><p class="panel-sub">Checks without a model; each problem is reported once, with evidence.</p>
      ${fs.length ? fs.map(f => `<div class="panel-kicker">${esc(clock(f.at))} · ${esc(f.kind)}</div><p class="panel-text">${esc(clip(f.detail, 220))}</p>
      <div class="panel-actions"><button class="button" data-do="investigate" data-id="${esc(f.id)}">Investigate</button><button class="button" data-do="dismiss" data-id="${esc(f.id)}">Dismiss</button></div>`).join('')
      : '<p class="panel-text dim">All quiet: nothing new since MO Care\'s last look.</p>'}`;
  } else if (sel.type === 'taskboard') {
    html += `<div class="panel-kicker">Taskboard · all MOs</div>${(d.mos || []).map(m => `<div class="board-row"><b>${esc(moName(m))}</b>
      <span>${esc(m.task.active || m.task.next || (m.busy ? m.request : '') || 'idle')}</span><small>${m.task.open ? m.task.open + ' open' : ''}</small></div>`).join('') || '<p class="panel-text dim">No MO is running.</p>'}`;
  } else if (sel.type === 'review') {
    const done = (d.specialists || []).filter(s => s.last_verdict);
    html += `<div class="panel-kicker">Review gate</div><p class="panel-title">${(d.review || {}).verified || 0} verified · ${(d.review || {}).refused || 0} refused</p>
      <p class="panel-sub">Only reports the architect checked count toward a specialist's rank.</p>${done.map(s => `<div class="panel-kicker">${esc(s.name)}</div>
      <p class="panel-text">${s.last_verdict === 'accepted' ? '✓ verified' : '✗ refused'} · ${esc(clip(s.last_reason || s.last_report, 160))}</p>`).join('')}`;
  } else if (sel.type === 'owner') {
    const target = ((d.mos || []).find(m => m.id === state.focus && !m.desktop) || (d.mos || []).find(m => !m.desktop));
    html += `<div class="panel-kicker">Owner desk · only you</div><p class="panel-sub">From your private profile. Runs in ${target ? 'MO · ' + esc(moName(target)) : 'a running MO'}.</p>
      ${(d.owner_desk || []).map(c => `<div class="owner-row"><span>${esc(c.name)}<br><small class="panel-text dim">${esc(c.description)}</small></span>
      <button class="button" data-do="owner" data-command="${esc(c.name)}" ${target ? `data-mo="${esc(target.id)}"` : 'disabled'}>Run</button></div>`).join('')}`;
  } else if (sel.type === 'archive' || sel.type === 'learning' || sel.type === 'power') {
    const b = d.brain || {}, l = d.learning || {}, r = d.resources || {};
    html += sel.type === 'archive' ? `<div class="panel-kicker">Archive</div><p class="panel-title">${(d.archive || {}).recent ? 'Indexing now' : 'Quiet'}</p>
      <p class="panel-text">${(d.archive || {}).recent || 0} turns indexed into memory in the last 10 minutes.</p><p class="panel-text dim">Memory holds ${b.memory_turns ?? l.memory_turns ?? 0} turns · ${esc(b.recall || l.recall_mode || '')}</p>`
      : sel.type === 'learning' ? `<div class="panel-kicker">Learning</div><p class="panel-title">${b.lessons_adopted ?? 0} adopted · ${b.lessons_pending ?? 0} waiting for you</p>
      <p class="panel-text dim">Review waiting lessons with /learning in MO Terminal.</p>`
      : `<div class="panel-kicker">Power</div><p class="panel-title">System CPU ${pct(r.system_cpu)} · RAM ${pct(r.memory_percent)}</p><p class="panel-text">MO uses ${gb(r.mo_memory)} across ${(d.mos || []).length} MOs.</p>
      <p class="panel-text dim">This window: ${gb((r.app || {}).memory_bytes)}. Closing it stops its sampling and never stops MO's work.</p>`;
  } else {
    const b = d.brain || {}, goalMo = goalOwner(d);
    html += `<div class="panel-kicker">MO · the brain</div><p class="panel-title">One brain, shared by every MO</p>
      <p class="panel-sub">What MO learned from you, and how it is used.</p>
      <div class="stat-grid"><div class="stat"><b>${esc(b.profile || '—')}</b><small>profile</small></div><div class="stat"><b>${b.memory_turns ?? '—'}</b><small>memory</small></div>
      <div class="stat"><b>${b.lessons_adopted ?? '—'}</b><small>lessons</small></div><div class="stat"><b>${b.lessons_pending ?? '—'}</b><small>for you</small></div></div>
      ${b.lessons_pending ? `<p class="panel-text dim">${b.lessons_pending} lessons wait for your yes: /learning in MO Terminal.</p>` : ''}
      ${goalMo ? `<div class="panel-kicker">Goal loop · MO ${esc(moName(goalMo))}</div><p class="panel-text">${esc(goalMo.goal.objective)} · ${goalMo.goal.done}/${goalMo.goal.total} · ${esc(goalMo.goal.state)}</p>` : ''}
      <div class="panel-kicker">Just called</div>${calledList(d)}`;
  }
  $('#panel').innerHTML = html;
}

// ------------------------------------------------------------------ data and actions
async function call(method, ...args) {
  try { const r = await api()[method](...args); if (r && r.message) toast(r.message); return r; }
  catch (e) { toast(e.message || String(e)); return null; }
}
function toast(text) { const e = $('#toast'); e.textContent = String(text); e.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { e.hidden = true; }, 3600); }
function topbar(d) {
  const mos = d.mos || [], specs = d.specialists || [], cands = d.candidates || [];
  $('#floor-sub').textContent = `MO's operations floor · ${mos.length} MO${mos.length === 1 ? '' : 's'} · ${specs.length} specialist${specs.length === 1 ? '' : 's'} · ${cands.length} candidate${cands.length === 1 ? '' : 's'}`;
  const gm = goalOwner(d), pill = $('#goal-pill');
  if (gm && gm.goal.total) {
    const st = gm.goal.state, step = Math.min(gm.goal.done + 1, gm.goal.total);
    pill.hidden = false; pill.dataset.mo = gm.id; pill.dataset.state = st;
    pill.innerHTML = `Goal loop <b>step ${step} of ${gm.goal.total}</b> · ${st === 'running' ? '❚❚ pause after this step' : st === 'pausing' ? 'pausing after this step' : st === 'paused' ? '▶ resume' : esc(st)}`;
    pill.disabled = !['running', 'paused'].includes(st);
  } else pill.hidden = true;
  const r = d.resources || {}, u = $('#usage-pill');
  u.textContent = `system ${pct(r.system_cpu)} · MO ${gb(r.mo_memory)}`; u.className = 'usage-pill ' + (r.pressure || '');
  const empty = $('#floor-empty');
  empty.hidden = mos.length > 0; empty.innerHTML = '<strong>No MO is running</strong>Start one with + and its bay appears here.';
}
async function poll() {
  if (!state.visible || !api()) return;
  let d = null;
  try { d = await api().snapshot(); } catch (e) { return; }
  if (!d || !d.mos) return;
  if (d.error && Date.now() - state.toastAt > 30000) { state.toastAt = Date.now(); toast(d.error); }
  state.data = d;
  if (d.focus && !state.focus) state.focus = d.focus;
  if (!state.selected && state.focus && (d.mos || []).some(m => m.id === state.focus)) state.selected = {type: 'mo', id: state.focus};
  if (state.selected && state.selected.type === 'mo') fetchConversation(state.selected.id);
  topbar(d); renderPanel();
  if (reduced || !raf) { if (raf) cancelAnimationFrame(raf); frame(performance.now()); }
  if (!state.revealed) { state.revealed = true; try { await api().ui_ready(); } catch (e) { /* host closing */ } }
}
async function fetchConversation(id) {
  const now = Date.now(); if (fetchConversation.at && now - fetchConversation.at < 6000 && fetchConversation.id === id) return;
  fetchConversation.at = now; fetchConversation.id = id;
  try { state.conversation[id] = await api().conversation(id) || []; renderPanel(); } catch (e) { /* stays empty */ }
}
function select(hit) {
  if (!hit) return;
  state.selected = {type: hit.type, id: hit.id}; state.composer = null;
  if (hit.type === 'mo') { state.focus = hit.id; fetchConversation(hit.id); }
  renderPanel(true); if (!raf) raf = requestAnimationFrame(frame);
}
function hitAt(x, y) { for (let i = state.hits.length - 1; i >= 0; i--) { const h = state.hits[i]; if (x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h) return h; } return null; }
async function act(button) {
  const doIt = button.dataset.do, sel = state.selected || {}, d = state.data || {};
  const moId = button.dataset.mo || (sel.type === 'mo' ? sel.id : sel.type === 'specialist' ? (leadFor((find('specialist', sel.id) || {}).cwd) || {}).id :
    sel.type === 'candidate' ? (leadFor((find('candidate', sel.id) || {}).cwd) || {}).id : '');
  if (doIt === 'assign') { state.composer = {target: button.dataset.specialist ? 's:' + button.dataset.specialist : 'mo', text: ''}; renderPanel(true); $('#composer') && $('#composer').focus(); }
  else if (doIt === 'cancel') { state.composer = null; renderPanel(true); }
  else if (doIt === 'target') { state.composer.text = ($('#composer') || {}).value || ''; state.composer.target = button.dataset.target; renderPanel(true); }
  else if (doIt === 'send-assign') {
    const text = (($('#composer') || {}).value || '').trim(), target = state.composer.target, mo = find('mo', moId) || {};
    if (!text) { toast('Write the assignment first.'); return; }
    const r = target === 'new' ? await call('new_mo', mo.cwd || '', text) : target.startsWith('s:') ? await call('ask_specialist', moId, target.slice(2), text) : await call('assign', moId, text);
    if (r && r.ok) { state.composer = null; renderPanel(true); }
  }
  else if (doIt === 'terminal') call('open_terminal', moId);
  else if (doIt === 'pause') call('pause_goal', moId);
  else if (doIt === 'resume') call('resume_goal', moId);
  else if (doIt === 'hire') { const c = find('candidate', sel.id); if (c && moId) call('hire', moId, c.name); else toast('No MO is running in that project.'); }
  else if (doIt === 'investigate') call('investigate', button.dataset.id);
  else if (doIt === 'dismiss') { await call('dismiss', button.dataset.id); state.panelKey = ''; }
  else if (doIt === 'owner') call('run_owner_command', moId, button.dataset.command);
  void d;
}

// ------------------------------------------------------------------ wiring
function start() {
  canvas = $('#floor'); ctx = canvas.getContext('2d'); readTheme(); resize();
  // Setting the canvas size clears it: redraw at once, not on a frame a hidden window never gets.
  new ResizeObserver(() => { resize(); if (raf) cancelAnimationFrame(raf); frame(performance.now()); }).observe($('#stage'));
  canvas.addEventListener('mousemove', e => { const r = canvas.getBoundingClientRect(), h = hitAt(e.clientX - r.left, e.clientY - r.top);
    canvas.classList.toggle('pointing', !!h);
    const id = h ? h.id : null; if (id !== (state.hover && state.hover.id)) { state.hover = h; if (reduced || !raf) frame(performance.now()); } });
  canvas.addEventListener('click', e => { const r = canvas.getBoundingClientRect(); select(hitAt(e.clientX - r.left, e.clientY - r.top)); });
  canvas.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return; e.preventDefault();
    const order = state.hits.filter(h => ['mo', 'specialist', 'candidate', 'brain'].includes(h.type)).filter((h, i, a) => a.findIndex(x => x.type === h.type && x.id === h.id) === i);
    if (!order.length) return; const i = order.findIndex(h => state.selected && h.type === state.selected.type && h.id === state.selected.id);
    select(order[(i + (e.shiftKey ? -1 : 1) + order.length) % order.length]);
  });
  document.addEventListener('click', e => {
    const w = e.target.closest('[data-window]'); if (w) { call('window_control', w.dataset.window); return; }
    const b = e.target.closest('[data-do]'); if (b && !b.disabled) act(b);
  });
  $('#new-mo').addEventListener('click', () => call('new_mo', ((goalOwner(state.data || {}) || (state.data || {}).mos?.[0]) || {}).cwd || '', ''));
  $('#goal-pill').addEventListener('click', e => { const p = e.currentTarget; call(p.dataset.state === 'paused' ? 'resume_goal' : 'pause_goal', p.dataset.mo); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && state.composer) { state.composer = null; renderPanel(true); } });
  document.addEventListener('visibilitychange', () => { state.visible = !document.hidden; try { api().set_visible(state.visible); } catch (e) { /* not ready */ }
    if (state.visible) { poll(); if (!raf) raf = requestAnimationFrame(frame); } });
  renderPanel(true); poll(); setInterval(poll, 2000);
  setTimeout(() => { if (!state.revealed && api()) { state.revealed = true; api().ui_ready(); } }, 4000);
}
window.mologrthimFocus = id => { state.focus = id; if (id) select({type: 'mo', id}); };
window.mologrthimSelect = (type, id) => select({type, id});     // the host (or a review) selects any floor object
window.mologrthimApplyTheme = cssText => { const el = document.getElementById('mologrthim-theme'); if (el) el.textContent = cssText; readTheme(); renderPanel(true); };
if (window.pywebview && window.pywebview.api) start(); else window.addEventListener('pywebviewready', start, {once: true});
})();
