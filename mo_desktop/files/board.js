(function () {
  'use strict';
  const api = () => window.pywebview.api;
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const fmt = bytes => { let n = Number(bytes) || 0, i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return `${n.toFixed(i && n < 10 ? 1 : 0)} ${['B','KB','MB','GB','TB'][i]}`; };
  const state = { panes: [{source_id: '', location_id: '', path: '', data: null, selected: [], search: '', sort: 'name'}], active: 0, split: false, view: 'graph', transfers: [], transferError: '', clipboard: null };
  let dropTargetKey = '';
  let boardRevealed = false;
  const pane = () => state.panes[state.active];
  const accent = index => `hsl(calc(var(--mo-accent-hue, 195) + ${index * 53}) var(--mo-accent-sat, 66%) var(--mo-accent-light, 62%))`;
  const folderAccent = path => `hsl(calc(var(--mo-accent-hue, 195) + ${[...String(path)].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 42 - 21}) var(--mo-accent-sat, 66%) var(--mo-accent-light, 62%))`;
  const iconPaths = {
    back:'<path d="M19 12H5m7 7-7-7 7-7"/>', forward:'<path d="M5 12h14m-7-7 7 7-7 7"/>',
    close:'<path d="M5 5 19 19M19 5 5 19"/>', refresh:'<path d="M20 11a8 8 0 1 1-2-5.5M20 4v6h-6"/>',
    go:'<path d="M4 5h11a4 4 0 0 1 4 4v6m-4-4 4 4-4 4"/>', chevron:'<path d="m6 9 6 6 6-6"/>',
    new:'<path d="M3 7h7l2 2h9v10H3zM12 12v6m-3-3h6"/>', paste:'<path d="M8 4h8v3H8zM6 6H4v15h16V6h-2M8 12h8m-8 4h6"/>',
    copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    cut:'<circle cx="6" cy="7" r="2"/><circle cx="6" cy="17" r="2"/><path d="m8 8 11 11M8 16 19 5"/>',
    move:'<path d="M4 12h15m-6-6 6 6-6 6M4 5v14"/>', send:'<path d="m3 11 18-8-8 18-2-8-8-2zM11 13 21 3"/>',
    graph:'<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 12h5l5-6m-5 6 5 6"/>', list:'<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    rename:'<path d="m4 16 10-10 4 4L8 20H4zM13 7l4 4"/>', details:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    edit:'<path d="M4 6h16M4 11h16M4 16h10"/>', delete:'<path d="M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13M10 11v6m4-6v6"/>',
    sort:'<path d="M7 4v16m-3-3 3 3 3-3M17 20V4m-3 3 3-3 3 3"/>'
  };
  const icon = name => `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${iconPaths[name]}</svg>`;
  let toastTimer;
  let quickShareOpen = false;
  let quickShareStopTask = null;
  function syncBrandHue() {
    const brand = getComputedStyle(document.documentElement).getPropertyValue('--mo-brand').trim();
    const rgb = brand.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
    if (!rgb) return;
    const [r,g,b] = rgb.slice(1).map(v => parseInt(v,16)/255), hi=Math.max(r,g,b), lo=Math.min(r,g,b), delta=hi-lo;
    let hue=0;
    if (delta) hue = hi === r ? ((g-b)/delta)%6 : hi === g ? (b-r)/delta+2 : (r-g)/delta+4;
    const light=(hi+lo)/2, saturation=delta ? delta/(1-Math.abs(2*light-1)) : 0;
    const style=document.documentElement.style;
    style.setProperty('--mo-accent-hue',String(Math.round((hue*60+360)%360)));
    style.setProperty('--mo-accent-sat',`${Math.round(saturation*100)}%`);
    style.setProperty('--mo-accent-light',`${Math.round(light*100)}%`);
  }

  function notice(message) {
    const box = $('#toast'); box.textContent = String(message); box.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { box.hidden = true; }, 4500);
  }
  async function call(method, ...args) {
    try { return await api()[method](...args); }
    catch (error) { notice(error.message || String(error)); throw error; }
  }
  function operations(p) {
    return new Set((p.data?.locations || []).find(row => row.location_id === p.location_id)?.operations || []);
  }
  async function browse(index = state.active, source = '', location = '', path = '', recordHistory = true) {
    const p = state.panes[index];
    const selectedSource = source || p.source_id;
    const selectedLocation = location || (source && source !== p.source_id ? '' : p.location_id);
    const request = (p.request || 0) + 1;
    p.request = request;
    p.loadingSource = selectedSource;
    render();
    let data;
    try { data = await call('browse', selectedSource, selectedLocation, path); }
    catch (error) {
      if (p.request === request) { p.loadingSource = ''; render(); }
      throw error;
    }
    if (p.request !== request || !state.panes.includes(p)) return;
    Object.assign(p, {data, source_id: data.source_id, location_id: data.location_id, path: data.listing.path || '', selected: []});
    p.loadingSource = '';
    const address = {source_id:p.source_id,location_id:p.location_id,path:p.path};
    p.history ||= [];
    p.historyIndex ??= -1;
    const current = p.history[p.historyIndex];
    if (recordHistory && (!current || current.source_id !== address.source_id || current.location_id !== address.location_id || current.path !== address.path)) {
      p.history = [...p.history.slice(0,p.historyIndex+1),address];
      p.historyIndex = p.history.length-1;
    }
    render();
    if (!boardRevealed) { boardRevealed = true; await call('ui_ready'); }
    if (index === state.active) { await call('set_drop_target', p.source_id, p.location_id, p.path); dropTargetKey = `${p.source_id}\0${p.location_id}\0${p.path}`; }
  }
  function sourceColor(p) { return accent((p.data?.sources || []).findIndex(row => row.source_id === p.source_id)); }
  function sourceIcon(kind) {
    if (kind === 'hub') return window.MO_FILES_MARK || '';
    if (kind === 'phone') return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 18h4"/></svg>';
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="3" width="20" height="15" rx="2"/><path d="M8 22h8M12 18v4"/></svg>';
  }
  function renderPlaces() {
    const p = pane(), data = p.data; if (!data) return;
    const shown = [...data.sources];
    if (!shown.some(s => s.kind === 'phone')) shown.push({label:'MO Phone',kind:'phone',online:false,source_id:''});
    $('#sources').innerHTML = shown.map((s, i) => `<button class="source-button ${s.source_id && s.source_id === (p.loadingSource || p.source_id) ? 'active' : ''} ${s.online ? '' : 'offline'}" style="--source-accent:${accent(i)}" ${s.source_id ? `data-source="${esc(s.source_id)}"` : ''} ${s.online ? '' : 'disabled'}><span class="source-symbol">${sourceIcon(s.kind)}</span><span><strong>${esc(s.label)}</strong><small>${s.online ? esc(s.kind || 'Connected') : 'Not connected'}</small></span></button>`).join('');
    $('#locations').innerHTML = data.locations.map(l => `<button class="location-button ${l.location_id === p.location_id ? 'active' : ''}" style="--source-accent:${sourceColor(p)}" data-location="${esc(l.location_id)}"><span class="location-dot"></span>${esc(l.label || l.name || l.location_id)}</button>`).join('');
    const currentLocation = data.locations.find(l => l.location_id === p.location_id);
    $('#location-switch').innerHTML = `<span>${esc(currentLocation?.label || currentLocation?.name || 'CURRENT LOCATION')}</span>${icon('chevron')}`;
    $('#source-notice').textContent = data.notice || '';
    $('#trash').disabled = !(data.sources.find(s => s.source_id === p.source_id)?.operations || []).includes('trash');
    $('#header-status').textContent = `${data.sources.filter(s => s.online).length} connected places · ${data.listing.entries.length} visible items`;
  }
  function selectedItems(p) { return p.selected.map(path => p.data.listing.entries.find(e => e.path === path)).filter(Boolean); }
  function halo(p, mode = 'selection') {
    const ops = operations(p), selected = selectedItems(p), count = selected.length, allFiles = selected.every(e => e.kind === 'file');
    const buttons = [
      ['new', 'new', 'New folder', (mode === 'folder' || !count) && ops.has('create_folder')],
      ['paste', 'paste', 'Paste into this folder', (mode === 'folder' || !count) && state.clipboard && state.clipboard.source_id === p.source_id && (ops.has(state.clipboard.operation))],
      ['focus_copy', 'copy', 'Copy this folder', mode === 'folder' && !!p.path && ops.has('copy')],
      ['focus_move', 'move', 'Move this folder', mode === 'folder' && !!p.path && ops.has('move')],
      ['focus_rename', 'rename', 'Rename this folder', mode === 'folder' && !!p.path && ops.has('rename')],
      ['focus_delete', 'delete', 'Move this folder to Trash', mode === 'folder' && !!p.path && ops.has('delete')],
      ['copy', 'copy', 'Copy selection', mode !== 'folder' && count && ops.has('copy')],
      ['cut', 'cut', 'Cut selection', mode !== 'folder' && count && ops.has('move')],
      ['move', 'move', 'Move or copy', mode !== 'folder' && count && (ops.has('move') || ops.has('copy'))],
      ['send', 'send', 'Send to a connected place', mode !== 'folder' && count && allFiles && ops.has('send')],
      ['rename', 'rename', 'Rename', mode !== 'folder' && count === 1 && ops.has('rename')],
      ['details', 'details', 'Item details', mode !== 'folder' && count === 1],
      ['edit', 'edit', 'Edit text', mode !== 'folder' && count === 1 && selected[0]?.editable && ops.has('edit_text')],
      ['delete', 'delete', 'Move to Trash', mode !== 'folder' && count && ops.has('delete')]
    ].filter(row => row[3]);
    return `<div class="action-halo"><span class="halo-label">${mode === 'folder' || !count ? 'THIS FOLDER' : `${count} SELECTED`}</span>${buttons.map(row => `<button class="circle-action" data-action="${row[0]}" title="${row[2]}" aria-label="${row[2]}">${icon(row[1])}</button>`).join('')}</div>`;
  }
  function entryMarkup(e, p, maxSize) {
    const folder = e.kind === 'folder', color = folder ? folderAccent(e.path) : sourceColor(p);
    const value = Number(e.bytes) || 0;
    const width = Math.max(3, Math.round(value / Math.max(1, maxSize) * 100));
    const proportion = Math.sqrt(value / Math.max(1, maxSize));
    const orb = 25 + Math.round(proportion * 19);
    const fileWidth = 230 + Math.round(proportion * 190);
    const glyph = (e.name.split('.').pop() || 'F').slice(0,3).toUpperCase();
    const selected = p.selected.includes(e.path);
    const node = `<button class="graph-node entry ${folder ? 'folder-entry' : 'file-entry'} ${selected ? 'selected' : ''}" style="--entry-accent:${color};--folder-accent:${color};--size-fraction:${width}%;--orb-size:${orb}px;--file-width:${fileWidth}px" data-entry="${esc(e.path)}" title="${esc(e.name)}"><span class="${folder ? 'folder-glyph' : 'file-orb'}">${folder ? '' : esc(glyph)}</span><span class="entry-copy"><strong>${esc(e.name)}</strong><small>${folder ? 'Folder · open' : fmt(value)}</small></span>${folder ? '' : '<span class="entry-bar"><i></i></span>'}</button>`;
    return selected && p.selected.length === 1 ? `<div class="selected-node">${halo(p)}${node}</div>` : node;
  }
  function displayedEntries(p) {
    const search = p.search.toLocaleLowerCase();
    const filtered = (p.data?.listing.entries || []).filter(e => !search || e.name.toLocaleLowerCase().includes(search));
    const sorter = (a,b) => {
      const byName = a.name.localeCompare(b.name);
      if (p.sort === 'size') return (Number(b.bytes || 0) - Number(a.bytes || 0)) || byName;
      if (p.sort === 'date') return (Number(b.modified_at || 0) - Number(a.modified_at || 0)) || byName;
      if (p.sort === 'type') return (a.name.split('.').pop() || '').localeCompare(b.name.split('.').pop() || '') || byName;
      return byName;
    };
    return {
      folders:filtered.filter(e => e.kind === 'folder').sort(sorter),
      files:filtered.filter(e => e.kind === 'file').sort(sorter),
    };
  }
  function paneMarkup(p, index) {
    if (!p.data) return '<div class="pane">Opening this place…</div>';
    const data = p.data, entries = data.listing.entries || [], parts = p.path.split('/').filter(Boolean);
    const label = data.locations.find(l => l.location_id === p.location_id)?.label || 'Home';
    const sourceLabel=data.sources.find(s => s.source_id === p.source_id)?.label || 'Place';
    const breadcrumbs = [{name: label, path: ''}, ...parts.map((name, i) => ({name, path: parts.slice(0,i+1).join('/')}))];
    const {folders,files} = displayedEntries(p);
    const allFiles = entries.filter(e => e.kind === 'file');
    const maxSize = Math.max(1, ...allFiles.map(e => Number(e.bytes) || 0));
    const total = allFiles.reduce((sum,e) => sum + (Number(e.bytes) || 0), 0);
    const color = sourceColor(p), folderColor = folderAccent(p.path);
    const listRows = [...folders,...files].map(e => `<button class="list-row entry ${p.selected.includes(e.path) ? 'selected' : ''}" data-entry="${esc(e.path)}" style="--entry-accent:${e.kind === 'folder' ? folderAccent(e.path) : color}"><span class="list-name"><span class="${e.kind === 'folder' ? 'folder-glyph' : 'file-orb'}">${e.kind === 'folder' ? '' : esc((e.name.split('.').pop() || 'F').slice(0,3).toUpperCase())}</span><strong>${esc(e.name)}</strong></span><span>${e.kind === 'folder' ? 'Folder' : esc(e.name.includes('.') ? e.name.split('.').pop().toUpperCase() : 'File')}</span><span>${e.kind === 'folder' ? '—' : fmt(e.bytes)}</span><span>${e.modified_at ? new Date(Number(e.modified_at)*1000).toLocaleDateString() : '—'}</span></button>`).join('');
    return `<section class="pane ${state.split && state.active === index ? 'is-active' : ''} ${p.loadingSource ? 'is-loading' : ''}" data-pane="${index}" style="--source-accent:${color};--folder-accent:${folderColor}">
      <div class="pane-top"><button class="history-button" data-history="-1" title="Back" aria-label="Back" ${p.historyIndex > 0 ? '' : 'disabled'}>${icon('back')}</button><button class="history-button" data-history="1" title="Forward" aria-label="Forward" ${p.historyIndex < (p.history?.length || 0)-1 ? '' : 'disabled'}>${icon('forward')}</button><button class="pane-source" data-pane-source="${index}" title="Choose a connected place for this pane">${sourceIcon(data.sources.find(s=>s.source_id===p.source_id)?.kind || 'desktop')}<span>${esc(sourceLabel)}</span>${icon('chevron')}</button><div class="crumbs">${breadcrumbs.map((b,i) => `${i ? '<span class="crumb-separator"></span>' : ''}<button class="${i < breadcrumbs.length-1 ? 'ancestor-crumb' : ''}" data-path="${esc(b.path)}">${esc(b.name)}</button>`).join('')}</div><button class="path-edit" data-go title="Go to a folder path" aria-label="Go to a folder path">${icon('go')}</button><input class="pane-search" type="search" placeholder="Find here" value="${esc(p.search)}" aria-label="Find in this folder"><button class="sort-button" data-sort aria-label="Sort files">${icon('sort')} ${esc(({name:'Name',size:'Size',date:'Date',type:'Type'})[p.sort] || 'Name')} ${icon('chevron')}</button></div>
      ${p.loadingSource ? `<div class="pane-loading" role="status">Opening ${esc(data.sources.find(s=>s.source_id===p.loadingSource)?.label || 'place')}…</div>` : ''}
      ${data.listing.truncated ? '<div class="pane-notice">More items exist here. Open a folder to narrow the board.</div>' : ''}
      ${state.view === 'list' ? `<div class="list-view"><div class="list-toolbar">${halo(p,p.selected.length > 1 ? 'selection' : 'folder')}</div><div class="list-header" data-drop-directory="${esc(p.path)}"><span>Name</span><span>Type</span><span>Size</span><span>Modified</span></div><div class="list-rows">${listRows || '<p class="list-empty">This folder is empty.</p>'}</div></div>` : `<div class="map ${folders.length ? '' : 'without-folders'} ${files.length ? '' : 'without-files'}"><svg class="map-lines" aria-hidden="true"></svg>
        <div class="focus-zone">${halo(p,p.selected.length > 1 ? 'selection' : 'folder')}<div class="focus-card" data-drop-directory="${esc(p.path)}"><span class="folder-glyph"></span><strong>${esc(parts.at(-1) || label)}</strong><small>${entries.filter(e => e.kind === 'folder').length} folders · ${allFiles.length} files</small><span class="focus-size">${allFiles.length ? `${fmt(total)} directly visible` : 'No files directly here'}</span></div></div>
        ${folders.length ? `<div class="branches"><div class="map-label">FOLDERS · ${folders.length}</div><div class="entry-list">${folders.map(e => entryMarkup(e,p,maxSize)).join('')}</div></div>` : ''}
        ${files.length ? `<div class="files-column"><div class="map-label">FILES BY SIZE · ${files.length}</div><div class="entry-list">${files.map(e => entryMarkup(e,p,maxSize)).join('')}</div><p class="size-explain">Bars compare directly visible files in this folder.</p></div>` : ''}
      </div>`}
    </section>`;
  }
  function drawLines() {
    document.querySelectorAll('.map').forEach(map => {
      const svg=map.querySelector('.map-lines'), focus=map.querySelector('.focus-card');
      if (!svg || !focus) return;
      const base=map.getBoundingClientRect(), fr=focus.getBoundingClientRect();
      const x1=fr.left+fr.width/2-base.left, y1=fr.bottom-base.top;
      const paths=[];
      map.querySelectorAll('.folder-entry,.file-entry').forEach(entry => {
        const r=entry.getBoundingClientRect(), x2=r.left+r.width/2-base.left, y2=r.top-base.top;
        const bend=Math.max(12,(y2-y1)*.42);
        paths.push(`M ${x1} ${y1} C ${x1} ${y1+bend} ${x2} ${y2-bend} ${x2} ${y2}`);
      });
      svg.setAttribute('viewBox',`0 0 ${base.width} ${base.height}`);
      svg.innerHTML=paths.map(d=>`<path d="${d}"/>`).join('');
    });
  }
  function render() {
    syncBrandHue();
    renderPlaces();
    const boards = $('#boards'); boards.classList.toggle('split',state.split);
    boards.innerHTML = state.panes.map(paneMarkup).join('');
    $('#split').classList.toggle('active',state.split);
    $('#view').classList.toggle('active',state.view === 'list');
    $('#view').title = state.view === 'list' ? 'Switch to graph view' : 'Switch to list view';
    $('#view').setAttribute('aria-label',$('#view').title);
    $('#view').innerHTML = icon(state.view === 'list' ? 'graph' : 'list');
    renderTransfers();
    drawLines();
  }
  function activatePane(index) {
    if (state.active === index) return;
    state.active = index;
    renderPlaces();
    document.querySelectorAll('.pane').forEach((section, at) => section.classList.toggle('is-active',state.split && at === index));
  }
  function renderTransfers() {
    const rows = state.transfers.filter(t=>!['done','cancelled','expired','failed'].includes(t.state)).slice(0,4);
    const routes=$('#transfer-routes'); routes.hidden=!rows.length;
    $('#boards').classList.toggle('with-routes',!!rows.length);
    $('#transfers').classList.toggle('active',!!rows.length);
    $('#transfers').title=state.transferError ? `Transfer status unavailable: ${state.transferError}` : rows.length ? `${rows.length} active transfer${rows.length===1?'':'s'}` : 'Transfers';
    $('#transfers').setAttribute('aria-label',$('#transfers').title);
    routes.innerHTML=rows.map(t=>`<div class="route-card" style="--dot-color:${t.direction === 'incoming' ? 'var(--mo-ok)' : 'var(--mo-brand)'};--progress:${t.progress}%"><span class="dot"></span><strong>${esc(t.name)}</strong><span class="route-destination">${icon('forward')} ${esc(t.route_label)}</span><em>${state.transferError ? 'Last known: ' : ''}${esc(t.state)} ${t.progress}%</em><span class="bar"><i></i></span>${t.can_cancel ? `<button data-transfer="cancel" data-id="${esc(t.id)}" title="Cancel">${icon('close')}</button>` : t.can_retry ? `<button data-transfer="retry" data-id="${esc(t.id)}" title="Retry">${icon('refresh')}</button>` : '<span style="width:17px"></span>'}</div>`).join('');
  }
  async function refreshTransfers() {
    try { state.transfers = await api().transfers(); state.transferError = ''; }
    catch (error) {
      const message = error.message || String(error);
      if (message !== state.transferError) notice(`Transfer status unavailable: ${message}`);
      state.transferError = message;
    }
    renderTransfers();
  }
  async function refreshAfterBatch(p, result, message, path = p.path, destination = null) {
    const refreshErrors = [];
    for (const [target, directory] of [[p, path], ...(destination && destination !== p ? [[destination, destination.path]] : [])]) {
      const index = state.panes.indexOf(target);
      if (index < 0) continue;
      try { await browse(index,target.source_id,target.location_id,directory); }
      catch (error) { refreshErrors.push(error.message || String(error)); }
    }
    const outcome = result.error ? `${result.completed.length} item(s) completed. ${result.error}` : message;
    notice(outcome + (refreshErrors.length ? ` Folder refresh failed: ${[...new Set(refreshErrors)].join('; ')}` : ''));
  }
  function closeOverlay() {
    $('#dialog').hidden = true;
    if (quickShareStopTask) return quickShareStopTask;
    if (!quickShareOpen) { $('#popover').hidden = true; return Promise.resolve(); }
    quickShareOpen = false;
    const box = $('#popover');
    box.dataset.quickShareStopping = '1';
    box.innerHTML = '<h3>Stopping quick transfer…</h3>';
    box.hidden = false;
    quickShareStopTask = api().quick_share_stop().then(() => {
      if (box.dataset.quickShareStopping) {
        box.hidden = true;
        box.innerHTML = '';
        delete box.dataset.quickShareStopping;
      }
      const p = pane();
      browse(state.active,p.source_id,p.location_id,p.path).catch(()=>{});
    }).catch(error => {
      quickShareOpen = true;
      notice('Could not stop quick transfer. Retry Stop sharing or close MO Files.');
      if (box.dataset.quickShareStopping) {
        box.innerHTML = `<h3>Quick transfer is still open</h3><p>${esc(error?.message || error)}</p><div class="popover-actions"><button data-retry-stop>Retry stop</button></div>`;
        $('[data-retry-stop]',box).onclick = closeOverlay;
        delete box.dataset.quickShareStopping;
      }
    }).finally(() => { quickShareStopTask = null; });
    return quickShareStopTask;
  }
  function popover(title, body, controls = '', point = null) {
    const box = $('#popover'); box.classList.toggle('at-point',!!point);
    delete box.dataset.quickShareStopping;
    box.innerHTML = `<h3>${esc(title)}</h3>${body}<div class="popover-actions"><button data-dismiss>Cancel</button>${controls}</div>`; box.hidden = false;
    if (point) {
      const bounds = box.getBoundingClientRect();
      box.style.left = `${Math.max(8,Math.min(point.x+8,innerWidth-bounds.width-8))}px`;
      box.style.top = `${Math.max(65,Math.min(point.y+8,innerHeight-bounds.height-8))}px`;
    } else { box.style.left=''; box.style.top=''; }
    $('[data-dismiss]',box).onclick = closeOverlay; return box;
  }
  function toggleSourceMenu(anchor) {
    const menu = $('#source-menu');
    const key = anchor.dataset.paneSource;
    if (!menu.hidden && menu.dataset.anchor === key) { menu.hidden = true; return; }
    const bounds = anchor.getBoundingClientRect();
    menu.hidden = false;
    menu.style.left = `${Math.max(12,Math.min(innerWidth-menu.offsetWidth-12,bounds.left))}px`;
    menu.style.top = `${Math.min(innerHeight-75,bounds.bottom+6)}px`;
    menu.dataset.anchor = key;
  }
  function dialog(title, body, buttons) {
    const box = $('#dialog'); box.innerHTML = `<div class="dialog-card"><h2>${esc(title)}</h2>${body}<div class="dialog-buttons"><button data-dismiss>Cancel</button>${buttons}</div></div>`; box.hidden = false; $('[data-dismiss]',box).onclick = closeOverlay; return box;
  }
  function placeOnClipboard(p, items, operation) {
    state.clipboard = {source_id:p.source_id,location_id:p.location_id,items:[...items],operation};
    notice(`${items.length} item(s) ${operation === 'move' ? 'cut' : 'copied'}. Open a folder and paste.`);
    render();
  }
  async function pasteInto(p, directory) {
    const clip = state.clipboard;
    if (!clip || clip.source_id !== p.source_id) return notice('Copy or cut files from this connected place first.');
    const result = await call('organize',clip.operation,clip.source_id,clip.location_id,clip.items,p.location_id,directory);
    if (clip.operation === 'move' && !result.error) state.clipboard = null;
    await refreshAfterBatch(p,result,`${result.completed.length} item(s) ${clip.operation === 'move' ? 'moved' : 'copied'}.`);
  }
  async function focusAction(name) {
    const p = pane(), sid=p.source_id, lid=p.location_id, path=p.path;
    if (name === 'paste') return pasteInto(p,path);
    if (!path) return;
    const item = await call('focus_item',sid,lid,path);
    if (name === 'focus_copy') return placeOnClipboard(p,[item],'copy');
    if (name === 'focus_rename') {
      const box=popover('Rename folder',`<input id="focus-name" value="${esc(item.name)}">`,'<button class="primary" id="focus-confirm">Rename</button>');
      $('#focus-name').focus(); $('#focus-confirm').onclick=async()=>{const result=await call('rename',sid,lid,item,$('#focus-name').value);closeOverlay();await browse(state.active,sid,lid,result.path);};
    } else if (name === 'focus_delete') {
      const box=dialog('Move folder to Trash',`<p>Move ${esc(item.name)} and its contents to recoverable Trash?</p>`,'<button class="primary" id="focus-confirm">Move to Trash</button>');
      $('#focus-confirm').onclick=async()=>{const result=await call('delete',sid,lid,[item]);closeOverlay();await refreshAfterBatch(p,result,'Folder moved to Trash.',result.error ? path : path.split('/').slice(0,-1).join('/'));};
    } else if (name === 'focus_move') {
      const locations=p.data.locations.filter(l=>(l.operations||[]).includes('move'));
      const box=popover('Move folder',`<p>Choose a destination for ${esc(item.name)}.</p><select id="destination-location">${locations.map(l=>`<option value="${esc(l.location_id)}">${esc(l.label||l.location_id)}</option>`).join('')}</select><input id="destination-path" placeholder="Folder path (optional)">`,'<button class="primary" id="focus-confirm">Move</button>');
      $('#focus-confirm').onclick=async()=>{const result=await call('organize','move',sid,lid,[item],$('#destination-location').value,$('#destination-path').value.trim());closeOverlay();await refreshAfterBatch(p,result,'Folder moved.',result.error ? path : path.split('/').slice(0,-1).join('/'));};
    }
  }
  async function action(name, directory = null) {
    const p = pane(), selected = selectedItems(p), sid = p.source_id, lid = p.location_id;
    if (name === 'paste') return pasteInto(p,p.path);
    if (name.startsWith('focus_')) return focusAction(name);
    if ((name === 'copy' || name === 'cut') && selected.length) return placeOnClipboard(p,selected,name === 'cut' ? 'move' : 'copy');
    if (name === 'new') {
      const parent = directory ?? p.path;
      const box = popover('New folder', `<p>Inside ${esc(parent || 'this place')}</p><input id="new-name" placeholder="Folder name">`, '<button class="primary" id="new-confirm">Create</button>');
      $('#new-name').focus(); $('#new-confirm').onclick = async () => { await call('create_folder',sid,lid,parent,$('#new-name').value); closeOverlay(); await browse(state.active,sid,lid,p.path); };
    } else if (name === 'rename' && selected.length === 1) {
      const box = popover('Rename', `<input id="rename-name" value="${esc(selected[0].name)}">`, '<button class="primary" id="rename-confirm">Rename</button>');
      $('#rename-name').focus(); $('#rename-confirm').onclick = async () => { await call('rename',sid,lid,selected[0],$('#rename-name').value); closeOverlay(); await browse(state.active,sid,lid,p.path); };
    } else if (name === 'delete' && selected.length) {
      const box = dialog('Move to Trash', `<p>Move ${selected.length} selected item(s) to recoverable Trash?</p>`, '<button class="primary" id="delete-confirm">Move to Trash</button>');
      $('#delete-confirm').onclick = async () => { const result = await call('delete',sid,lid,selected); closeOverlay(); await refreshAfterBatch(p,result,`${result.completed.length} item(s) moved to Trash.`); };
    } else if (name === 'details' && selected.length === 1) {
      const item = selected[0], date = item.modified_at ? new Date(Number(item.modified_at)*1000).toLocaleString() : 'Unknown';
      popover(item.name, `<p>${esc(item.kind === 'folder' ? 'Folder' : `File · ${fmt(item.bytes)}`)}</p><p>Path: ${esc(item.path)}<br>Modified: ${esc(date)}</p>`);
    } else if (name === 'edit' && selected.length === 1) {
      const doc = await call('read_text',sid,lid,selected[0]);
      const box = dialog(`Edit ${selected[0].name}`, `<p>UTF-8 text · saves only if the file has not changed.</p><textarea id="text-editor"></textarea>`, '<button class="primary" id="save-text">Save</button>');
      $('#text-editor').value = doc.text; $('#save-text').onclick = async () => { await call('write_text',sid,lid,doc.path,$('#text-editor').value,doc.sha256); closeOverlay(); await browse(state.active,sid,lid,p.path); notice('Saved.'); };
    } else if (name === 'send' && selected.length) {
      const data = await call('targets',sid);
      const box = popover('Send selected files', `<p>${selected.length} file(s). The receiver uses its existing MO transfer route.</p>${data.targets.map(t => `<button class="choice" data-target="${esc(t.device_id)}">${esc(t.label)}<small>${esc(t.kind)}</small></button>`).join('') || '<p>No paired transfer target is available.</p>'}`);
      box.querySelectorAll('[data-target]').forEach(button => button.onclick = async () => { const result = await call('send',sid,lid,selected,button.dataset.target,''); closeOverlay(); await refreshTransfers(); notice(result.error || `Sending ${result.completed.length} file(s).`); });
    } else if (name === 'move' && selected.length) {
      const ops = operations(p); const destinations = p.data.locations.filter(l => (l.operations || []).includes('move') || (l.operations || []).includes('copy'));
      const box = popover('Move or copy', `<p>Choose a destination. Drag onto a folder for a direct move.</p><select id="destination-location">${destinations.map(l => `<option value="${esc(l.location_id)}">${esc(l.label || l.location_id)}</option>`).join('')}</select><input id="destination-path" placeholder="Folder path inside location (optional)"><select id="operation">${ops.has('move') ? '<option value="move">Move</option>' : ''}${ops.has('copy') ? '<option value="copy">Copy</option>' : ''}</select>`, '<button class="primary" id="organize-confirm">Continue</button>');
      $('#organize-confirm').onclick = async () => { const result = await call('organize',$('#operation').value,sid,lid,selected,$('#destination-location').value,$('#destination-path').value.trim()); closeOverlay(); await refreshAfterBatch(p,result,`${result.completed.length} item(s) ${$('#operation').value === 'copy' ? 'copied' : 'moved'}.`); };
    }
  }
  async function openItem(p,item) {
    if (item.kind === 'folder') await browse(state.panes.indexOf(p),p.source_id,p.location_id,item.path);
    else if (p.source_id === 'desktop-local') await call('open_local',p.source_id,p.location_id,item);
    else if (item.editable) { p.selected=[item.path]; await action('edit'); }
  }
  async function showTrash() {
    const p = pane(), result = await call('trash',p.source_id), rows = result.items || [];
    const box = dialog('Trash', rows.map(row => `<div class="trash-row"><span><strong>${esc(row.name || row.original_path || 'Item')}</strong><small>${esc(row.original_path || row.path || '')}</small></span><button data-restore="${esc(row.trash_id)}">Restore</button></div>`).join('') || '<p>Trash is empty.</p>', '');
    box.querySelectorAll('[data-restore]').forEach(button => button.onclick = async () => { await call('restore',p.source_id,button.dataset.restore); closeOverlay(); await browse(state.active,p.source_id,p.location_id,p.path); notice('Restored to its original location.'); });
  }
  function showSort(p, point) {
    const choices = [['name','Name'],['type','Type'],['size','Size'],['date','Date']];
    const box = popover('Sort this folder',choices.map(([value,label]) => `<button class="choice" data-sort-choice="${value}">${icon('sort')} ${label}${p.sort === value ? '<small>Current</small>' : ''}</button>`).join(''),'',point);
    box.querySelectorAll('[data-sort-choice]').forEach(button => button.onclick = () => { p.sort=button.dataset.sortChoice; closeOverlay(); render(); });
  }
  async function showQuickShare() {
    if (quickShareOpen || quickShareStopTask) await closeOverlay();
    if (quickShareOpen) return;
    const p=pane();
    if (p.source_id !== 'desktop-local') return notice('QR browser transfer currently uses this computer. Switch this pane to a local folder.');
    const selected=selectedItems(p);
    if (selected.some(item=>item.kind !== 'file')) return notice('Select files to offer, or clear the selection to receive files only.');
    const addresses=await call('quick_share_addresses');
    if (!addresses.length) return notice('No active private Wi-Fi or LAN address is available.');
    const box=popover('Quick transfer',`<p>Scan from any browser on the same Wi-Fi or LAN. Use a trusted private network; this local connection is not encrypted. ${selected.length ? `${selected.length} file(s) available to take.` : 'The device can send files into this folder.'}</p>${addresses.map(row=>`<button class="choice" data-quick-address="${esc(row.address)}">${esc(row.label)} · ${esc(row.address)}</button>`).join('')}`);
    box.querySelectorAll('[data-quick-address]').forEach(button=>button.onclick=async()=>{
      try {
        const result=await call('quick_share_start',p.source_id,p.location_id,p.path,selected,button.dataset.quickAddress);
        const qr=popover('Quick transfer',`<div class="quick-qr"><img src="${result.image}" alt="Scan to open MO Files on this device"><p>Scan with any camera or QR reader on the same trusted Wi-Fi or LAN. This local connection is not encrypted.</p><strong>${esc(result.offered.length ? `${result.offered.length} file(s) ready to download` : 'Ready to receive files')}</strong><small id="quick-status">Waiting for a browser · expires in ${result.expires_seconds} seconds</small></div>`);
        $('[data-dismiss]',qr).textContent='Stop sharing';
        quickShareOpen=true;
        const poll=async()=>{
          if (!quickShareOpen) return;
          try { const status=await api().quick_share_status(); if (!quickShareOpen) return; if (!status.active) { closeOverlay(); notice('Quick transfer expired.'); return; } const upload=status.upload_state==='receiving'?' · Receiving upload':status.upload_state==='rejected'?' · Upload rejected':''; $('#quick-status').textContent=`${status.connected ? 'Browser connected' : 'Waiting for a browser'} · ${status.expires_seconds}s left · ${status.received.length} received${status.received.length ? ` (${status.received.join(', ')})` : ''}${upload}`; } catch (_) {}
          if (quickShareOpen) setTimeout(poll,2000);
        };
        setTimeout(poll,2000);
      } catch (_) {}
    });
  }
  function changeSelection(p, item, event) {
    if (event.shiftKey && p.selected.length) {
      const {folders,files} = displayedEntries(p), rows = [...folders,...files];
      const from = rows.findIndex(e => e.path === p.selected.at(-1)), to = rows.findIndex(e => e.path === item.path);
      p.selected = from < 0 || to < 0 ? [item.path] : [...new Set([...p.selected, ...rows.slice(Math.min(from,to),Math.max(from,to)+1).map(e => e.path)])];
    } else if (event.ctrlKey || event.metaKey) p.selected = p.selected.includes(item.path) ? p.selected.filter(v => v !== item.path) : [...p.selected,item.path];
    else p.selected = [item.path];
    render();
  }
  async function travel(direction, index = state.active) {
    activatePane(index);
    const p = state.panes[index], next = (p.historyIndex ?? 0) + direction;
    if (!p.history || next < 0 || next >= p.history.length) return;
    const target = p.history[next];
    p.historyIndex = next;
    try { await browse(index,target.source_id,target.location_id,target.path,false); }
    catch (_) { if (p.historyIndex === next) p.historyIndex -= direction; }
  }
  let pendingDrag = null, lastClick = null;
  function dragTarget(x,y) {
    const hit = document.elementFromPoint(x,y)?.closest('[data-entry],[data-drop-directory],[data-location],[data-source]');
    if (!hit) return null;
    const targetPane = hit.closest('[data-pane]');
    if (hit.dataset.entry) {
      const p = state.panes[Number(targetPane?.dataset.pane || state.active)], item = p.data.listing.entries.find(e => e.path === hit.dataset.entry);
      return item?.kind === 'folder' ? {pane:p, path:item.path, kind:'folder', element:hit} : null;
    }
    if (hit.dataset.dropDirectory !== undefined) return {pane:state.panes[Number(targetPane?.dataset.pane || state.active)], path:hit.dataset.dropDirectory, kind:'folder', element:hit};
    if (hit.dataset.location) return {pane:pane(), location_id:hit.dataset.location, path:'', kind:'location', element:hit};
    if (hit.dataset.source) return {source_id:hit.dataset.source, kind:'source', element:hit};
    return null;
  }
  function dragDescription(items) {
    const files=items.filter(item=>item.kind==='file'), folders=items.length-files.length;
    const size=files.reduce((sum,item)=>sum+(Number(item.bytes)||0),0);
    return `${files.length ? fmt(size) : 'No file bytes'}${folders ? ` · ${folders} folder${folders===1?'':'s'} (size not calculated)` : ''}`;
  }
  async function finishDrag(drag,target,point) {
    const selected = drag.items, origin = drag.pane;
    if (!target) return;
    const destination=target.pane || null, sourceId=target.source_id || destination?.source_id;
    if (sourceId === origin.source_id && !destination) return;
    const crossSource=sourceId !== origin.source_id;
    if (crossSource && selected.some(item=>item.kind!=='file')) return notice('Send files only between connected places.');
    if (!crossSource && selected.some(item=>item.path===target.path || (item.kind==='folder' && target.path.startsWith(item.path+'/')))) return notice('Choose another folder for this move.');
    const destinationLabel=crossSource ? (origin.data.sources.find(s=>s.source_id===sourceId)?.label || destination?.data.sources.find(s=>s.source_id===sourceId)?.label || 'connected place') : (target.path || destination?.data.locations.find(l=>l.location_id===(target.location_id||destination.location_id))?.label || 'this location');
    const allowed=operations(origin);
    const destinationOps=new Set((destination?.data.locations || []).find(l=>l.location_id===(target.location_id || destination?.location_id))?.operations || []);
    const verbs=crossSource ? (allowed.has('send') ? ['send'] : []) : ['move','copy'].filter(op=>allowed.has(op)&&destinationOps.has(op));
    if (!verbs.length) return notice('This place does not allow that operation.');
    const box=popover(`${selected.length} item${selected.length===1?'':'s'} to ${destinationLabel}`,`<p>${esc(dragDescription(selected))}</p><p>${crossSource ? 'Send through the connected MO route.' : 'Choose what happens in this folder.'}</p>`,verbs.map(op=>`<button class="${op===verbs[0]?'primary':''}" data-drop-action="${op}">${op==='send'?'Send':op==='move'?'Move':'Copy'}</button>`).join(''),point);
    box.querySelectorAll('[data-drop-action]').forEach(button=>button.onclick=async()=>{
      const op=button.dataset.dropAction; closeOverlay();
      try {
        if (op==='send') {
          const result=await call('send',origin.source_id,origin.location_id,selected,'',sourceId);
          await refreshTransfers(); notice(result.error || `Sending ${result.completed.length} file(s).`);
        } else {
          const location=target.location_id || destination.location_id;
          const result=await call('organize',op,origin.source_id,origin.location_id,selected,location,target.path);
          await refreshAfterBatch(origin,result,`${op==='copy'?'Copied':'Moved'} ${result.completed.length} item(s).`,origin.path,destination);
        }
      } catch (_) { /* call() displays the owner error. */ }
    });
  }
  document.addEventListener('pointerdown',event => {
    const section=event.target.closest('[data-pane]');
    if (section) activatePane(Number(section.dataset.pane));
    const row = event.target.closest('.entry'); if (!row || event.button !== 0) return;
    const index = Number(row.closest('[data-pane]').dataset.pane), p = state.panes[index], item = p.data.listing.entries.find(e => e.path === row.dataset.entry); if (!item) return;
    state.active = index;
    pendingDrag = {x:event.clientX,y:event.clientY, pane:p, item, started:false, items:null, menuWasOpen:!$('#source-menu').hidden};
  });
  document.addEventListener('pointermove',event => {
    if (!pendingDrag) return;
    const d = pendingDrag;
    if (!d.started && Math.hypot(event.clientX-d.x,event.clientY-d.y)>7) {
      d.started = true; d.items = d.pane.selected.includes(d.item.path) ? selectedItems(d.pane) : [d.item];
      const ghost = $('#drag-ghost'); ghost.innerHTML = `<strong>${esc(d.items.length === 1 ? d.item.name : `${d.items.length} items`)}</strong><small>${esc(dragDescription(d.items))}</small>`; ghost.hidden = false;
    }
    if (d.started) {
      $('#source-menu').hidden=false;
      const ghost = $('#drag-ghost'); ghost.style.left = `${event.clientX+15}px`; ghost.style.top = `${event.clientY+15}px`;
      document.querySelectorAll('.drop-ready').forEach(el => el.classList.remove('drop-ready'));
      const target=dragTarget(event.clientX,event.clientY);
      target?.element?.classList.add('drop-ready');
      ghost.dataset.intent=target ? (target.kind==='source' || (target.pane && target.pane.source_id!==d.pane.source_id) ? 'Send to' : 'Move or copy to') : 'Moving';
      const atEdge = event.clientX < 18 || event.clientY < 18 || event.clientX > innerWidth-18 || event.clientY > innerHeight-18;
      if (atEdge && d.pane.source_id === 'desktop-local' && d.items.every(item => item.kind === 'file')) {
        const point={x:event.clientX,y:event.clientY};
        pendingDrag = null; ghost.hidden = true; if (!d.menuWasOpen) $('#source-menu').hidden=true;
        call('start_external_drag',d.pane.source_id,d.pane.location_id,d.items,false).then(result => {
          if (result.error) return notice(result.error);
          if (!result.copied || !result.outside) return;
          const box=popover('Copied to Explorer',`<p>${esc(d.items.length)} file(s) · ${esc(dragDescription(d.items))}</p><p>Move the originals to recoverable MO Trash?</p>`,'<button class="primary" id="external-move">Move originals</button>',point);
          $('[data-dismiss]',box).textContent='Keep originals';
          $('#external-move').onclick=async()=>{closeOverlay();const moved=await call('moved_outside',d.pane.source_id,d.pane.location_id,d.items);await refreshAfterBatch(d.pane,moved,`Moved ${moved.completed.length} original file(s) to Trash.`);};
        }).catch(()=>{});
      }
    }
  });
  document.addEventListener('pointerup',async event => {
    if (!pendingDrag) return;
    const d = pendingDrag, target=dragTarget(event.clientX,event.clientY); pendingDrag = null; $('#drag-ghost').hidden = true; if (!d.menuWasOpen) $('#source-menu').hidden=true; document.querySelectorAll('.drop-ready').forEach(el => el.classList.remove('drop-ready'));
    if (!d.started) {
      const now=Date.now(), twice=lastClick && lastClick.path===d.item.path && lastClick.pane===d.pane && now-lastClick.at<400;
      lastClick={path:d.item.path,pane:d.pane,at:now};
      if (d.item.kind === 'folder' && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
        lastClick = null;
        try { await openItem(d.pane,d.item); } catch (_) {}
      } else if (twice) { lastClick=null; try { await openItem(d.pane,d.item); } catch (_) {} }
      else changeSelection(d.pane,d.item,event);
      return;
    }
    try {
      await finishDrag(d,target,{x:event.clientX,y:event.clientY});
    } catch (_) { /* call() displays the owner error. */ }
  });
  document.addEventListener('contextmenu',event => {
    if (event.target.closest('input,textarea,select')) return;
    const section=event.target.closest('[data-pane]'); if (!section) return;
    event.preventDefault();
    const p=state.panes[Number(section.dataset.pane)], row=event.target.closest('.entry'), focus=event.target.closest('.focus-card');
    const item=row ? p.data.listing.entries.find(e=>e.path===row.dataset.entry) : null;
    state.active=state.panes.indexOf(p);
    if (item && !p.selected.includes(item.path)) p.selected=[item.path];
    render();
    const ops=operations(p), selected=selectedItems(p), single=selected.length===1, allFiles=selected.length&&selected.every(e=>e.kind==='file');
    const folder=item?.kind==='folder' ? item.path : focus ? p.path : null;
    const actions=item ? [
      ['open','Open',single],['details','Details',single],['edit','Edit text',single&&item.editable&&ops.has('edit_text')],
      ['new','New folder inside',folder!==null&&ops.has('create_folder')],['paste','Paste into folder',folder!==null&&!!state.clipboard&&state.clipboard.source_id===p.source_id],
      ['copy','Copy',ops.has('copy')],['cut','Cut',ops.has('move')],['move','Move or copy',ops.has('move')||ops.has('copy')],
      ['send','Send to',allFiles&&ops.has('send')],['rename','Rename',single&&ops.has('rename')],['delete','Move to Trash',ops.has('delete')]
    ] : [
      ['new','New folder',ops.has('create_folder')],['paste','Paste here',!!state.clipboard&&state.clipboard.source_id===p.source_id],
      ['focus_copy','Copy this folder',!!p.path&&ops.has('copy')],['focus_move','Move this folder',!!p.path&&ops.has('move')],
      ['focus_rename','Rename this folder',!!p.path&&ops.has('rename')],['focus_delete','Move this folder to Trash',!!p.path&&ops.has('delete')],
      ['sort','Sort',true],['refresh','Refresh',true],['trash','Trash',(p.data.sources.find(s=>s.source_id===p.source_id)?.operations||[]).includes('trash')]
    ];
    const point={x:event.clientX,y:event.clientY};
    const box=popover(item?.name || (focus ? p.path.split('/').at(-1) : 'This place') || 'This place',actions.filter(a=>a[2]).map(a=>`<button class="choice" data-context="${a[0]}">${a[1]}</button>`).join(''),'',point);
    box.querySelectorAll('[data-context]').forEach(button=>button.onclick=async()=>{const choice=button.dataset.context;closeOverlay();try{
      if (choice==='open'&&item) await openItem(p,item);
      else if (choice==='paste') await pasteInto(p,folder ?? p.path);
      else if (choice==='new') await action('new',folder ?? p.path);
      else if (choice==='sort') showSort(p,point);
      else if (choice==='refresh') await browse(state.active,p.source_id,p.location_id,p.path);
      else if (choice==='trash') await showTrash();
      else await action(choice);
    }catch(_){}});
  });
  document.addEventListener('click',async event => {
    const p = pane();
    try {
      const control = event.target.closest('[data-window]'); if (control) {const result=await call('window_control',control.dataset.window);if(control.dataset.window==='toggle_pin'&&result?.ok)control.setAttribute('aria-pressed',String(result.pinned));return;}
      const source = event.target.closest('[data-source]'); if (source) { $('#source-menu').hidden=true; return await browse(state.active,source.dataset.source,'',''); }
      const paneSource = event.target.closest('[data-pane-source]');
      if (paneSource) { activatePane(Number(paneSource.dataset.paneSource)); toggleSourceMenu(paneSource); return; }
      const loc = event.target.closest('[data-location]'); if (loc) { $('#locations').hidden = true; return await browse(state.active,p.source_id,loc.dataset.location,''); }
      if (event.target.closest('#location-switch')) { $('#locations').hidden = !$('#locations').hidden; return; }
      const path = event.target.closest('[data-path]'); if (path) { const targetPane = path.closest('[data-pane]'); const index=Number(targetPane?.dataset.pane ?? state.active); activatePane(index); const selectedPane=state.panes[index]; return await browse(index,selectedPane.source_id,selectedPane.location_id,path.dataset.path); }
      const history = event.target.closest('[data-history]'); if (history) return await travel(Number(history.dataset.history),Number(history.closest('[data-pane]')?.dataset.pane ?? state.active));
      const button = event.target.closest('[data-action]'); if (button) return await action(button.dataset.action);
      const sort = event.target.closest('[data-sort]'); if (sort) return showSort(p,{x:sort.getBoundingClientRect().left,y:sort.getBoundingClientRect().bottom});
      if (event.target.closest('[data-go]')) {
        const box=popover('Go to folder',`<p>Path inside ${esc(p.data.locations.find(l=>l.location_id===p.location_id)?.label||'this place')}</p><input id="go-path" value="${esc(p.path)}" placeholder="Folder/subfolder">`,'<button class="primary" id="go-confirm">Open</button>');
        $('#go-path').focus(); $('#go-confirm').onclick=async()=>{await browse(state.active,p.source_id,p.location_id,$('#go-path').value.trim());closeOverlay();}; return;
      }
      const transfer = event.target.closest('[data-transfer]'); if (transfer) { await call('transfer_action',transfer.dataset.transfer,transfer.dataset.id); return refreshTransfers(); }
      if (event.target.closest('#transfers') || event.target.closest('[data-all-transfers]')) {
        const warning = state.transferError ? `<p>Transfer status unavailable: ${esc(state.transferError)}. Showing last known status.</p>` : '';
        popover('Transfers',warning+(state.transfers.slice(0,40).map(t=>`<div class="trash-row"><span><strong>${esc(t.name)}</strong><small>${esc(t.direction)} · ${esc(t.route_label)} · ${esc(t.state)} ${t.progress}%</small></span>${t.can_cancel ? `<button data-transfer="cancel" data-id="${esc(t.id)}">Cancel</button>` : t.can_retry ? `<button data-transfer="retry" data-id="${esc(t.id)}">Retry</button>` : ''}</div>`).join('') || '<p>No transfers yet.</p>'),'<button data-clear>Clear history</button>'); return;
      }
      if (event.target.closest('[data-clear]')) { const n = await call('clear_transfer_history'); closeOverlay(); await refreshTransfers(); return notice(`Cleared ${n} completed transfers.`); }
      if (!event.target.closest('#source-menu')) $('#source-menu').hidden=true;
    } catch (_) {}
  });
  document.addEventListener('input',event => { if (event.target.matches('.pane-search')) { const p = state.panes[Number(event.target.closest('[data-pane]').dataset.pane)]; p.search = event.target.value; const start=event.target.selectionStart; render(); const next=$(`.pane[data-pane="${state.panes.indexOf(p)}"] .pane-search`); next.focus(); next.setSelectionRange(start,start); } });
  document.addEventListener('mousedown',event => {
    if (event.button === 3 || event.button === 4) { event.preventDefault(); travel(event.button === 3 ? -1 : 1).catch(()=>{}); }
  });
  document.addEventListener('auxclick',event => { if (event.button === 3 || event.button === 4) event.preventDefault(); });
  document.addEventListener('keydown',event => {
    const editing = !!event.target.closest?.('input,textarea,select,[contenteditable]');
    const command = event.ctrlKey || event.metaKey;
    if (command && !editing && event.key.toLowerCase() === 'a') { event.preventDefault(); const p=pane(), {folders,files}=displayedEntries(p);p.selected=[...folders,...files].slice(0,100).map(e=>e.path);render(); }
    else if (command && !editing && event.key.toLowerCase() === 'c') { event.preventDefault(); action('copy').catch(()=>{}); }
    else if (command && !editing && event.key.toLowerCase() === 'x') { event.preventDefault(); action('cut').catch(()=>{}); }
    else if (command && !editing && event.key.toLowerCase() === 'v') { event.preventDefault(); action('paste').catch(()=>{}); }
    else if (!editing && event.key === 'Delete') { event.preventDefault(); action('delete').catch(()=>{}); }
    else if (!editing && event.key === 'Enter' && selectedItems(pane()).length === 1) { event.preventDefault(); openItem(pane(),selectedItems(pane())[0]).catch(()=>{}); }
    else if ((event.altKey && event.key === 'ArrowLeft') || (event.key === 'Backspace' && !editing)) { event.preventDefault(); travel(-1).catch(()=>{}); }
    else if (event.altKey && event.key === 'ArrowRight') { event.preventDefault(); travel(1).catch(()=>{}); }
    else if (event.key === 'Escape') { closeOverlay(); $('#source-menu').hidden=true; }
  });
  $('#refresh').onclick = () => browse(state.active,pane().source_id,pane().location_id,pane().path).then(refreshTransfers).catch(() => {});
  $('#quick-share').onclick = () => showQuickShare().catch(() => {});
  $('#split').onclick = () => { state.split = !state.split; if (state.split && state.panes.length === 1) { const current=pane(); state.panes.push({source_id:current.source_id,location_id:current.location_id,path:current.path,data:current.data,selected:[],search:'',sort:current.sort,history:[{source_id:current.source_id,location_id:current.location_id,path:current.path}],historyIndex:0}); } else if (!state.split) { state.panes = [pane()]; state.active=0; } render(); };
  $('#view').onclick = () => { state.view = state.view === 'graph' ? 'list' : 'graph'; render(); };
  $('#trash').onclick = () => showTrash().catch(() => {});
  const resizeGrip = $('#resize-grip');
  let resizeStart = null;
  resizeGrip.addEventListener('pointerdown',event => {
    resizeStart = {x:event.screenX,y:event.screenY,width:innerWidth,height:innerHeight};
    resizeGrip.setPointerCapture(event.pointerId);
  });
  resizeGrip.addEventListener('pointermove',event => {
    if (!resizeStart) return;
    api().window_control('resize',resizeStart.width+event.screenX-resizeStart.x,resizeStart.height+event.screenY-resizeStart.y).catch(()=>{});
  });
  resizeGrip.addEventListener('pointerup',() => { resizeStart = null; });
  resizeGrip.addEventListener('pointercancel',() => { resizeStart = null; });
  document.addEventListener('pointerenter',event => { const section = event.target.closest?.('[data-pane]'); if (section) { const p=state.panes[Number(section.dataset.pane)], key=`${p.source_id}\0${p.location_id}\0${p.path}`; if (key !== dropTargetKey) { dropTargetKey=key; api().set_drop_target(p.source_id,p.location_id,p.path).catch(()=>{dropTargetKey='';}); } } },true);
  window.addEventListener('resize',() => requestAnimationFrame(drawLines));
  window.moFilesApplyTheme = css => { $('#mo-files-theme').textContent = css; render(); };
  window.moFilesNavigate = target => browse(state.active,target.source_id || pane().source_id,target.location_id || pane().location_id,'').catch(()=>{});
  window.moFilesExternalDropResult = result => { notice(result.message); if (result.ok) browse(state.active,pane().source_id,pane().location_id,pane().path).catch(()=>{}); };
  let booted=false;
  async function boot() {
    if (booted || !window.pywebview?.api) return;
    booted=true;
    const initial=window.MO_FILES_INITIAL || {};
    try {
      await browse(0,initial.source_id || '',initial.location_id || '','');
      async function pollTransfers() {
        await refreshTransfers();
        const active = state.transfers.some(row => !['done','cancelled','expired','failed'].includes(row.state));
        setTimeout(pollTransfers,active ? 3000 : 15000);
      }
      pollTransfers();
    }
    catch (_) {
      $('#header-status').textContent='This place is unavailable';
      $('#boards').innerHTML='<div class="boot-failure"><strong>Could not open this place</strong><span>Use Refresh to try again.</span></div>';
      if (!boardRevealed) { boardRevealed=true; await api().ui_ready(); }
    }
  }
  window.addEventListener('pywebviewready',boot,{once:true});
  if (window.pywebview?.api) boot();
})();
