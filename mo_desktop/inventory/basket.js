(function () {
'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const api = () => window.pywebview && window.pywebview.api;
const reduced = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const icons = (window.INVENTORY_INITIAL || {}).icons || {};
const CHIPS = [['all', 'Everything'], ['today', 'Today'], ['conversation', 'Conversations'], ['task', 'Tasks'], ['file', 'Files'],
  ['picture', 'Pictures'], ['media', 'Videos & songs'], ['design', 'Designs'], ['action', 'Actions']];
const KIND = {conversation: ['Conversation', 'terminal'], task: ['Task', 'pulse'], goal: ['Goal', 'pulse'], file: ['File', 'file'],
  picture: ['Picture', 'details'], media: ['Video or song', 'details'], design: ['Design', 'pen'], lesson: ['Lesson', 'team'], commit: ['Commit', 'send']};
const MAX_CARDS = 70;
const state = {items: [], terminals: [], chip: 'all', query: '', picked: new Set(), cards: new Map(), revealed: false, visible: true, drag: null};
let raf = 0;

function matches(item) {
  const day = Date.now() / 1000 - 86400;
  const c = state.chip;
  if (c === 'today' && item.at < day) return false;
  if (c === 'action' && !['commit', 'goal', 'lesson'].includes(item.kind)) return false;
  if (!['all', 'today', 'action'].includes(c) && item.kind !== c) return false;
  return true;
}
function searchHit(item) { const q = state.query.trim().toLowerCase(); return q && `${item.title} ${item.sub} ${item.ref}`.toLowerCase().includes(q); }
function chips() {
  const counts = {}; for (const i of state.items) { counts[i.kind] = (counts[i.kind] || 0) + 1; }
  const today = state.items.filter(i => i.at > Date.now() / 1000 - 86400).length;
  $('#chips').innerHTML = CHIPS.filter(([k]) => k === 'all' || k === 'today' || k === 'action' ? true : counts[k]).map(([k, label]) => {
    const n = k === 'all' ? state.items.length : k === 'today' ? today : k === 'action' ? (counts.commit || 0) + (counts.goal || 0) + (counts.lesson || 0) : counts[k];
    return `<button data-chip="${k}" class="${state.chip === k ? 'on' : ''}">${esc(label)}<small>${n || 0}</small></button>`;
  }).join('');
}
function ago(at) { const s = Date.now() / 1000 - at; return s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : new Date(at * 1000).toLocaleDateString(); }
function cardHtml(item) {
  const [label, glyph] = KIND[item.kind] || [item.kind, 'details'];
  const img = item.kind === 'picture' && item.thumb ? `<img src="${esc(item.thumb)}" alt="">` : '';
  return `${img}<span class="kind ${esc(item.kind)}">${icons[glyph] || ''}${esc(label)}</span><b>${esc(item.title)}</b><small>${esc(item.sub)} · ${esc(ago(item.at))}</small>`;
}

// ------------------------------------------------------------------ physics: spring each card to its spot
function layout() {
  const stage = $('#stage').getBoundingClientRect(), basket = $('#basket').getBoundingClientRect();
  const left = basket.left - stage.left, top = basket.top - stage.top, width = basket.width, height = basket.height;
  const shown = state.items.filter(matches), hits = shown.filter(searchHit), rest = shown.filter(i => !searchHit(i));
  const visible = [...hits.slice(0, 6), ...rest].slice(0, MAX_CARDS);
  // Only what fits the basket is placed; the rest waits behind a filter or a search (never a messy pile).
  const placed = [];
  let x = left + 18, y = top + 18, rowH = 0, hx = left + width * 0.18;
  visible.forEach((item, n) => {
    const seed0 = hash(item.id), w0 = item.kind === 'picture' ? 180 : 210, h0 = item.kind === 'picture' ? 160 : 86;
    if (!(searchHit(item) && n < 6)) {
      let nx = x, ny = y, nh = rowH;
      if (nx + w0 > left + width - 12) { nx = left + 18 + (seed0 % 23); ny = y + rowH + 16; nh = 0; }
      if (ny + h0 > top + height - 8) return;
      x = nx; y = ny; rowH = nh;
    }
    placed.push(item.id);
    let card = state.cards.get(item.id);
    if (!card) {
      const el = document.createElement('div');
      el.className = 'card ' + item.kind; el.dataset.id = item.id; el.innerHTML = cardHtml(item);
      $('#cards').appendChild(el);
      card = {el, x: left + Math.random() * width, y: -160 - Math.random() * 300, r: (Math.random() - .5) * 30, vx: 0, vy: 0, vr: 0};
      state.cards.set(item.id, card);
    }
    const seed = hash(item.id), w = item.kind === 'picture' ? 180 : 210, h = item.kind === 'picture' ? 160 : 86;
    if (searchHit(item) && n < 6) {                       // search matches rise above the rim
      card.tx = hx; card.ty = top - h - 14 + (seed % 9); card.tr = ((seed % 7) - 3) * 0.9; hx += w + 26;
    } else {
      if (x + w > left + width - 12) { x = left + 18 + (seed % 23); y += rowH + 16; rowH = 0; }
      card.tx = x + (seed % 15) - 7; card.ty = Math.min(y + (seed % 11), top + height - h - 10) ; card.tr = ((seed % 9) - 4) * 0.7;
      x += w + 18 + (seed % 13); rowH = Math.max(rowH, h);
    }
    card.el.style.zIndex = String(searchHit(item) ? 3000 + n : 1000 - n);
    card.el.classList.toggle('match', searchHit(item));
    card.el.classList.toggle('dim', !!state.query.trim() && !searchHit(item));
    card.el.classList.toggle('picked', state.picked.has(item.id));
  });
  const keep = new Set(placed);
  for (const [id, card] of state.cards) if (!keep.has(id)) { card.el.remove(); state.cards.delete(id); }
  const more = $('#more'), hidden = shown.length - placed.length;
  more.hidden = hidden <= 0; more.textContent = `+${hidden} more · filter or search to see them`;
  $('#found').textContent = state.query.trim() ? `${hits.length} found` : '';
  const empty = $('#empty'); empty.hidden = placed.length > 0;
  empty.innerHTML = state.items.length ? 'Nothing here for this filter.' : 'Gathering your work with MO…';
  if (reduced) { for (const c of state.cards.values()) { c.x = c.tx; c.y = c.ty; c.r = c.tr; place(c); } } else wake();
}
function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return Math.abs(h); }
function place(c) { c.el.style.transform = `translate(${c.x.toFixed(1)}px, ${c.y.toFixed(1)}px) rotate(${c.r.toFixed(2)}deg)`; }
function step() {
  raf = 0; let moving = false;
  for (const c of state.cards.values()) {
    if (state.drag && state.drag.card === c) { place(c); continue; }
    c.vx = (c.vx + (c.tx - c.x) * 0.06) * 0.78; c.vy = (c.vy + (c.ty - c.y) * 0.06 + 0.4) * 0.78; c.vr = (c.vr + (c.tr - c.r) * 0.08) * 0.75;
    if (c.y > c.ty && c.vy > 0) { c.y = c.ty; c.vy *= -0.25; }                  // a soft landing on its spot
    c.x += c.vx; c.y += c.vy; c.r += c.vr;
    if (Math.abs(c.tx - c.x) + Math.abs(c.ty - c.y) > 0.4 || Math.abs(c.vy) > 0.05) moving = true;
    place(c);
  }
  if (moving && state.visible && !document.hidden) raf = requestAnimationFrame(step);
}
function wake() { if (!raf && state.visible && !document.hidden) raf = requestAnimationFrame(step); }

// ------------------------------------------------------------------ terminals and sending
function terminals() {
  const n = state.picked.size, el = $('#terminals');
  document.querySelector('.inv-app').classList.toggle('picking', n > 0);
  el.innerHTML = state.terminals.length ? state.terminals.map(t => `<div class="term" data-term="${esc(t.id)}"><span class="cube">${icons.team || ''}</span>
    <div><b>${esc(t.project)} · ${esc(t.slot)}</b><small>${esc(t.on)}</small></div><em>drop here · send ${n}</em></div>`).join('')
    : '<div class="term none">No MO terminal is running. Start one, and it appears here to receive what you pick.</div>';
}
async function send(termId, ids) {
  const list = [...new Set(ids)];
  if (!list.length) { toast('Pick something first: click cards, or drag one here.'); return; }
  try { const r = await api().send(termId, list); toast(r.message); if (r.ok) { state.picked.clear(); layout(); terminals(); } }
  catch (e) { toast(e.message || String(e)); }
}
function toast(text) { const e = $('#toast'); e.textContent = String(text); e.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { e.hidden = true; }, 4200); }

// ------------------------------------------------------------------ data
async function poll() {
  if (!state.visible || !api()) return;
  let d = null; try { d = await api().snapshot(); } catch (e) { return; }
  if (!d || !d.items) return;
  const sig = d.items.map(i => i.id + i.at).join('|') + '#' + (d.terminals || []).map(t => t.id + t.on).join('|');
  if (sig !== state.sig) { state.sig = sig; state.items = d.items; state.terminals = d.terminals || []; chips(); layout(); terminals(); }
  if (!state.revealed) { state.revealed = true; try { await api().ui_ready(); } catch (e) { /* closing */ } }
}

// ------------------------------------------------------------------ wiring
function start() {
  chips(); terminals(); layout();
  $('#chips').addEventListener('click', e => { const b = e.target.closest('[data-chip]'); if (b) { state.chip = b.dataset.chip; chips(); layout(); } });
  $('#search').addEventListener('input', e => { state.query = e.target.value; layout(); });
  $('#cards').addEventListener('pointerdown', e => {
    const el = e.target.closest('.card'); if (!el) return;
    const card = state.cards.get(el.dataset.id); state.drag = {card, id: el.dataset.id, sx: e.clientX, sy: e.clientY, ox: card.x, oy: card.y, moved: false};
    el.setPointerCapture(e.pointerId);
  });
  $('#cards').addEventListener('pointermove', e => {
    const d = state.drag; if (!d) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy; if (Math.abs(dx) + Math.abs(dy) > 6) d.moved = true;
    if (!d.moved) return;
    d.card.el.classList.add('dragging'); d.card.x = d.ox + dx; d.card.y = d.oy + dy; d.card.r = 0; place(d.card);
    document.querySelectorAll('.term').forEach(t => { const r = t.getBoundingClientRect(); t.classList.toggle('over', e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom); });
  });
  $('#cards').addEventListener('pointerup', e => {
    const d = state.drag; state.drag = null; if (!d) return;
    d.card.el.classList.remove('dragging');
    const over = document.querySelector('.term.over'); document.querySelectorAll('.term.over').forEach(t => t.classList.remove('over'));
    if (!d.moved) { state.picked.has(d.id) ? state.picked.delete(d.id) : state.picked.add(d.id); layout(); terminals(); return; }
    if (over && over.dataset.term) send(over.dataset.term, [d.id, ...state.picked]);
    wake();
  });
  $('#terminals').addEventListener('click', e => { const t = e.target.closest('[data-term]'); if (t) send(t.dataset.term, [...state.picked]); });
  document.addEventListener('click', e => { const w = e.target.closest('[data-window]'); if (w) api().window_control(w.dataset.window); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { if (state.query) { state.query = ''; $('#search').value = ''; } else state.picked.clear(); layout(); terminals(); } });
  document.addEventListener('visibilitychange', () => { state.visible = !document.hidden; try { api().set_visible(state.visible); } catch (e) { /* not ready */ } if (state.visible) { poll(); wake(); } });
  new ResizeObserver(() => layout()).observe($('#stage'));
  poll(); setInterval(poll, 4000);
  setTimeout(() => { if (!state.revealed && api()) { state.revealed = true; api().ui_ready(); } }, 4000);
}
window.inventoryApplyTheme = cssText => { const el = document.getElementById('inventory-theme'); if (el) el.textContent = cssText; };
if (window.pywebview && window.pywebview.api) start(); else window.addEventListener('pywebviewready', start, {once: true});
})();
