// Presentation of the existing structural graph; no second graph or state store.
const DATA = JSON.parse(document.getElementById('graph-data').textContent);
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
}[c]));
const cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const THEME = {bg:cssVar('--bg','#101218'), line:cssVar('--line','#26365f'),
  text:cssVar('--text','#d7dee8'), muted:cssVar('--muted','#7d8996'),
  cyan:cssVar('--cyan','#3fe0e0'), gold:cssVar('--gold','#ffd166'),
  pink:cssVar('--pink','#bb86fc'), green:cssVar('--green','#68d391')};
function withAlpha(value, alpha) {
  const hex = String(value).replace(/^#/, '');
  if (!/^[0-9a-f]{6}$/i.test(hex)) return value;
  return `rgba(${parseInt(hex.slice(0,2),16)},${parseInt(hex.slice(2,4),16)},${parseInt(hex.slice(4,6),16)},${alpha})`;
}
const nodes = DATA.nodes || [], links = DATA.links || [], groups = DATA.groups || [];
const byId = new Map(nodes.map(n => [n.id,n]));
const members = new Map(), incident = new Map(), fileNode = new Map(), symbolsByFile = new Map();
const isSymbol = n => n.type !== 'file' && n.type !== 'brain' && n.type !== 'package';
function append(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}
for (const n of nodes) {
  append(members, n.group, n);
  if (n.type === 'file' && n.source_file) fileNode.set(n.source_file, n);
  if (isSymbol(n) && n.source_file) append(symbolsByFile, n.source_file, n);
  n._r = n.type === 'file' ? 11 : n.type === 'brain' ? 14 : 6;
}
// Keep the actual edges, including direction and multiple kinds between a pair.
for (const edge of links) {
  append(incident, edge.source, edge);
  if (edge.target !== edge.source) append(incident, edge.target, edge);
}
const groupColor = new Map();
function updateColors() {
  const palette = [THEME.cyan,THEME.gold,THEME.pink,THEME.green,THEME.text,THEME.muted];
  groups.forEach((g,i) => groupColor.set(g.name, g.brain
    ? ({Memory:THEME.gold,Profile:THEME.pink,Learning:THEME.green}[g.name] || THEME.cyan)
    : palette[i % palette.length]));
}
updateColors();
const boards = DATA.work?.boards || [], commits = DATA.work?.commits || [];
const insights = DATA.insights || {}, ops = DATA.fileOps || {}, pathTasks = new Map(), pathCommits = new Map();
for (const [task, paths] of Object.entries(DATA.annotations?.tasks || {}))
  for (const path of paths) append(pathTasks, path, task);
for (const commit of commits) for (const path of commit.files || []) append(pathCommits, path, commit);
const touchedPaths = new Set([...Object.keys(ops),...pathTasks.keys(),...pathCommits.keys()]);
function evidencePath(evidence) {
  const value = String(evidence || '').trim();
  const match = /^(?:read_file|write_file|edit_file|file):(.+)$/.exec(value);
  return (match ? match[1].trim() : /[\/\\].+\.[A-Za-z0-9]+$/.test(value) ? value : '').replace(/\\/g, '/');
}
function workPaths(selection) {
  if (selection.kind === 'commit') return new Set(commits[selection.id].files || []);
  if (selection.kind === 'board') return new Set(boards[selection.id].tasks.flatMap(
    task => (task.evidence || []).map(evidencePath).filter(Boolean)));
  return new Set();
}

// Package cells aggregate only existing cross-package edges. Detailed views keep
// the original edges, not the aggregate, and never truncate the canvas topology.
const GA = Math.PI * (3-Math.sqrt(5));
const packageNodes = groups.map((group,i) => {
  const radius = 340*Math.sqrt((i+.5)/Math.max(1,groups.length)), angle = i*GA;
  return {id:'package:'+group.name, label:group.name, group:group.name, type:'package',
    _x:Math.cos(angle)*radius*1.8, _y:Math.sin(angle)*radius*.8,
    _z:Math.sin(angle*.7)*90, _r:24, degree:0};
});
const packageByName = new Map(packageNodes.map(n => [n.group,n])), packagePairs = new Map();
for (const edge of links) {
  const a = byId.get(edge.source), b = byId.get(edge.target);
  if (!a || !b || a.group === b.group) continue;
  const source = packageByName.get(a.group), target = packageByName.get(b.group);
  if (!source || !target) continue;
  const key = source.id+'\0'+target.id;
  if (packagePairs.has(key)) packagePairs.get(key).weight++;
  else packagePairs.set(key, {source:source.id,target:target.id,weight:1});
}
const packageLinks = [...packagePairs.values()];
for (const edge of packageLinks) {
  packageByName.get(edge.source.slice(8)).degree += edge.weight;
  packageByName.get(edge.target.slice(8)).degree += edge.weight;
}
const packageLabels = new Set([...packageNodes].sort((a,b) => b.degree-a.degree).slice(0,8));

// All entry points use navigate(): menu, cube, search, source, relation and work.
// Back stores view/camera choices only; source data and graph truth stay above.
let view = {kind:'overview',level:'packages',touched:false};
const backStack = [], limits = new Map();
let query = '', detailsOpen = false, highlightPaths = new Set();
let scene = {nodes:[],links:[],index:new Map(),center:{x:0,y:0,z:0},radius:1};
let ax = .38, ay = .55, zoom = 1, spin=false, grid = false, hover = null;
let drag = false, moved = false, lx = 0, ly = 0;
let W = 0, H = 0, DPR = 1, frame = 0, parentVisible = true, cameraDirty = true;
let fitScale = 1, cameraDistance = 2050, transition = null, lastFrame = 0;
let rendered = [], matrix = {};
const canvas = $('canvas'), ctx = canvas.getContext('2d'), info = $('info');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
function selectedNode() { return view.kind === 'node' ? byId.get(view.id) : null; }
function selectedGroup() { return view.kind === 'package' ? view.id : selectedNode()?.group || null; }
function navigate(target, remember = true, animate = true) {
  if (target.kind === 'node' && !byId.has(target.id)) return;
  if (target.kind === 'package' && !members.has(target.id)) return;
  if (target.kind === 'commit' && !commits[target.id]) return;
  if (target.kind === 'board' && !boards[target.id]) return;
  if (target.kind === 'package' && groups.find(g => g.name === target.id)?.brain) {
    target = {kind:'node',id:members.get(target.id)[0].id};
  }
  const node = target.kind === 'node' ? byId.get(target.id) : null;
  const next = {...target,level:target.level || (target.kind === 'overview' ? 'packages' : node && isSymbol(node) ? 'symbols' : 'files'),touched:!!target.touched};
  if (remember && JSON.stringify(view) !== JSON.stringify(next))
    backStack.push({view:{...view},ax,ay,zoom});
  const point = target.kind === 'package' && view.level === 'packages'
    ? rendered.find(item => item.n.group === target.id)?.p : null;
  const previousBounds = canvas.getBoundingClientRect();
  const origin = point ? {sx:point.sx+previousBounds.left,sy:point.sy+previousBounds.top} : null;
  view = next;
  zoom = 1; hover = null; limits.clear(); query = ''; $('search').value = '';
  highlightPaths = workPaths(view);
  detailsOpen = ['node','commit','board'].includes(view.kind);
  setExplorer(false);
  rebuildScene();
  renderInspector();
  syncControls();
  if (origin) {const bounds=canvas.getBoundingClientRect();origin.sx-=bounds.left;origin.sy-=bounds.top;}
  transition = animate && !reducedMotion.matches ? {start:performance.now(),origin} : null;
  invalidate();
}
function goBack() {
  const previous = backStack.pop();
  if (!previous) return;
  navigate(previous.view, false, false);
  ax = previous.ax; ay = previous.ay; zoom = previous.zoom;
  cameraDirty = true; syncControls(); invalidate();
}
function scopeLabel() {
  if (view.kind === 'commit') return 'Commit '+commits[view.id].hash;
  if (view.kind === 'board') return boards[view.id].title || boards[view.id].board_id;
  return selectedGroup() || (view.level === 'packages' ? '' : 'Whole project');
}
function syncControls() {
  $('level').value = view.level;
  $('touched').setAttribute('aria-pressed', String(view.touched));
  $('touched').disabled = view.level === 'packages';
  $('back').disabled = !backStack.length;
  $('scope').textContent = scopeLabel();
  $('scope').title = scopeLabel();
  const kinds = new Map();
  for (const node of scene.nodes) {
    const kind = node.type === 'package' ? 'packages' : node.type === 'file' ? 'files' : node.type === 'brain' ? 'brain nodes' : 'symbols';
    kinds.set(kind, (kinds.get(kind) || 0)+1);
  }
  $('status').textContent = [...kinds].map(([kind,count]) => `${count} ${kind}`).join(' · ') || 'No matching nodes';
  $('status').title = `${scene.links.length} relationships in this view. Dashed lines are inferred; select a node for direction and type.`;
  $('tSpin').setAttribute('aria-pressed', String(spin));
  $('tSpin').disabled = reducedMotion.matches;
  $('tGrid').setAttribute('aria-pressed', String(grid));
}
function layoutMembers(list, center, radius) {
  list.forEach((node,i) => {
    const y = 1-2*((i+.5)/list.length), r = Math.sqrt(1-y*y), angle = i*GA;
    node._x = center.x+Math.cos(angle)*r*radius*1.4;
    node._y = center.y+y*radius;
    node._z = center.z+Math.sin(angle)*r*radius*.65;
  });
}
function rebuildScene() {
  const group = selectedGroup(), work = view.kind === 'commit' || view.kind === 'board';
  const candidates = view.level === 'packages' ? packageNodes : group ? members.get(group) || [] : nodes;
  const visible = candidates.filter(node => {
    if (view.level === 'packages') return true;
    if (work && !highlightPaths.has(node.source_file)) return false;
    if (view.touched && !touchedPaths.has(node.source_file)) return false;
    if (node.type === 'brain') return !work;
    if (view.level === 'files' && isSymbol(node)) return false;
    return view.level !== 'definitions' || node.type !== 'file';
  });
  if (view.level !== 'packages') {
    if (group || work) layoutMembers(visible, {x:0,y:0,z:0}, 120+Math.sqrt(visible.length)*9);
    else groups.forEach((g,i) => {
      const radius = 480*Math.sqrt((i+.5)/Math.max(1,groups.length)), angle = i*GA;
      const list = visible.filter(n => n.group === g.name);
      layoutMembers(list, {x:Math.cos(angle)*radius*1.7,y:Math.sin(angle)*radius,z:Math.sin(angle*.7)*100}, 35+Math.sqrt(list.length)*8);
    });
  }
  const index = new Map(visible.map(n => [n.id,n]));
  const edges = (view.level === 'packages' ? packageLinks : links).filter(e => index.has(e.source) && index.has(e.target));
  const low = {x:Infinity,y:Infinity,z:Infinity}, high = {x:-Infinity,y:-Infinity,z:-Infinity};
  for (const n of visible) for (const axis of ['x','y','z']) {
    low[axis] = Math.min(low[axis],n['_'+axis]); high[axis] = Math.max(high[axis],n['_'+axis]);
  }
  const center = visible.length ? {x:(low.x+high.x)/2,y:(low.y+high.y)/2,z:(low.z+high.z)/2} : {x:0,y:0,z:0};
  const radius = visible.reduce((r,n) => Math.max(r,Math.hypot(n._x-center.x,n._y-center.y,n._z-center.z)+n._r*2),1);
  scene = {nodes:visible,links:edges,index,center,radius};
  $('empty').hidden = !!visible.length;
  $('empty').textContent = work ? 'No evidence files from this work are indexed in this graph.' : 'No nodes match this view. Change the detail or touched filter.';
  cameraDirty = true;
}

// Camera and cells are centred on the visible scene, not the full project.
const F = 1050;
function rotXYZ(point) {
  const x = point.x*matrix.ca-point.z*matrix.sa, z = point.x*matrix.sa+point.z*matrix.ca;
  return {x,y:point.y*matrix.cb-z*matrix.sb,z:point.y*matrix.sb+z*matrix.cb};
}
function rot(node) { return rotXYZ({x:node._x-scene.center.x,y:node._y-scene.center.y,z:node._z-scene.center.z}); }
function proj(point) {
  const s = zoom*fitScale*F/(cameraDistance+point.z);
  return {sx:W/2+point.x*s,sy:H/2+point.y*s,s};
}
function fitCamera() {
  matrix = {ca:Math.cos(ay),sa:Math.sin(ay),cb:Math.cos(ax),sb:Math.sin(ax)};
  cameraDistance = F+scene.radius;
  let x = 1, y = 1;
  for (const n of scene.nodes) {
    const r = rot(n), s = F/(cameraDistance+r.z-n._r*2), padding = n._r*2+10;
    x = Math.max(x,(Math.abs(r.x)+padding)*s);
    y = Math.max(y,(Math.abs(r.y)+padding)*s);
  }
  fitScale = Math.max(.001,Math.min(Math.max(10,W/2-28)/x,Math.max(10,H/2-20)/y));
  cameraDirty = false;
}
function resize() {
  const bounds = canvas.getBoundingClientRect();
  const ratio = Math.min(2,devicePixelRatio || 1);
  if (W === bounds.width && H === bounds.height && DPR === ratio) return;
  W = bounds.width; H = bounds.height; DPR = ratio;
  canvas.width = Math.round(W*DPR); canvas.height = Math.round(H*DPR);
  cameraDirty = true; invalidate();
}
const cubeCorners = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
const cubeFaces = [
  {indices:[0,1,2,3],normal:{x:0,y:0,z:-1},shade:.68},
  {indices:[4,7,6,5],normal:{x:0,y:0,z:1},shade:.48},
  {indices:[0,3,7,4],normal:{x:-1,y:0,z:0},shade:.42},
  {indices:[1,5,6,2],normal:{x:1,y:0,z:0},shade:.58},
  {indices:[0,4,5,1],normal:{x:0,y:-1,z:0},shade:.9},
  {indices:[3,2,6,7],normal:{x:0,y:1,z:0},shade:.35}
];
function motionPoint(point, progress) {
  if (!transition) return point;
  const origin = transition.origin || {sx:W/2,sy:H/2};
  return {...point,sx:origin.sx+(point.sx-origin.sx)*progress,sy:origin.sy+(point.sy-origin.sy)*progress};
}
function cubeVertices(node, progress = 1) {
  return cubeCorners.map(([x,y,z]) => motionPoint(proj(rotXYZ({
    x:node._x-scene.center.x+x*node._r,
    y:node._y-scene.center.y+y*node._r,
    z:node._z-scene.center.z+z*node._r
  })),progress));
}
function highlighted(node) { return node === selectedNode() || node === hover || highlightPaths.has(node.source_file); }
function draw(now) {
  frame = 0;
  if (document.hidden || !parentVisible || !W || !H) return;
  const elapsed = lastFrame ? Math.min(50,now-lastFrame) : 0;
  lastFrame = now;
  if (spin && !drag) { ay += elapsed*.00008; cameraDirty = true; }
  if (cameraDirty) fitCamera();
  const time = transition ? Math.min(1,(now-transition.start)/220) : 1;
  const progress = 1-Math.pow(1-time,3);
  ctx.setTransform(DPR,0,0,DPR,0,0); ctx.clearRect(0,0,W,H);
  if (grid) {
    ctx.strokeStyle = withAlpha(THEME.line,.35); ctx.lineWidth = .6;
    for (let x = 24; x < W; x += 48) {ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke();}
    for (let y = 24; y < H; y += 48) {ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();}
  }
  const focus = hover || selectedNode(), near = new Set();
  if (focus) for (const edge of view.level === 'packages' ? scene.links : incident.get(focus.id) || []) {
    if (edge.source === focus.id) near.add(edge.target);
    if (edge.target === focus.id) near.add(edge.source);
  }
  rendered = scene.nodes.map(n => {const r=rot(n);return {n,r,p:motionPoint(proj(r),progress)};});
  const projected = new Map(rendered.map(item => [item.n.id,item]));
  const anyFocus = !!focus || highlightPaths.size > 0;
  for (const edge of scene.links) {
    const a = projected.get(edge.source), b = projected.get(edge.target);
    const hi = highlighted(a.n) || highlighted(b.n);
    const alpha = hi ? .72 : anyFocus ? .045 : view.level === 'packages' ? .19 : .3;
    ctx.strokeStyle = withAlpha(hi ? THEME.cyan : THEME.muted,alpha*progress);
    ctx.lineWidth = (hi ? 1.3 : .65)+Math.min(.8,Math.log2(1+(edge.weight || 1))*.12);
    ctx.setLineDash(edge.confidence === 'INFERRED' ? [3,5] : []);
    const dx=b.p.sx-a.p.sx,dy=b.p.sy-a.p.sy,length=Math.hypot(dx,dy)||1,bend=Math.min(22,length*.09);
    ctx.beginPath();ctx.moveTo(a.p.sx,a.p.sy);
    ctx.quadraticCurveTo((a.p.sx+b.p.sx)/2-dy/length*bend,(a.p.sy+b.p.sy)/2+dx/length*bend,b.p.sx,b.p.sy);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  rendered.sort((a,b) => b.r.z-a.r.z);
  const faces = cubeFaces.map(face => ({...face,z:rotXYZ(face.normal).z})).filter(face => face.z < 0).sort((a,b) => b.z-a.z);
  for (const item of rendered) {
    const {n,p} = item, hi = highlighted(n), visibleFocus = !anyFocus || hi || near.has(n.id);
    const color = hi ? THEME.cyan : groupColor.get(n.group) || THEME.text;
    ctx.globalAlpha = (visibleFocus ? 1 : .23)*progress;
    const radius = n._r*p.s;
    item.hit = Math.max(9,radius*1.8);
    if (radius < 2.5) { // Pixel-sized cells need no eight-vertex mesh.
      ctx.fillStyle=color;ctx.fillRect(p.sx-1.5,p.sy-1.5,3,3);
    } else {
      const vertices = cubeVertices(n,progress);
      ctx.lineWidth = hi ? 1.1 : .65;
      for (const face of faces) {
        ctx.beginPath();face.indices.forEach((index,i) => {
          const v=vertices[index];if(i)ctx.lineTo(v.sx,v.sy);else ctx.moveTo(v.sx,v.sy);
        });ctx.closePath();
        ctx.fillStyle=THEME.bg;ctx.fill();
        ctx.fillStyle=withAlpha(color,face.shade);ctx.fill();
        ctx.strokeStyle=withAlpha(hi?THEME.cyan:color,hi?.95:.65);ctx.stroke();
      }
    }
    if (n.type === 'file' && ops[n.source_file]?.modifies > 0 && visibleFocus) {
      ctx.strokeStyle=THEME.gold;ctx.lineWidth=1.2;ctx.beginPath();ctx.arc(p.sx,p.sy,item.hit+3,0,Math.PI*2);ctx.stroke();
    }
    const labelled = scene.nodes.length <= 24 || hi || packageLabels.has(n) || (near.has(n.id) && near.size < 30);
    if (labelled && visibleFocus) {
      ctx.globalAlpha = (hi ? 1 : .88)*progress;ctx.fillStyle=hi?THEME.text:color;
      ctx.font=`${hi?600:400} 11px ui-sans-serif,system-ui`;
      const width=Math.min(ctx.measureText(n.label).width,Math.max(30,W/2-24)),right=p.sx+item.hit+5;
      const x=right+width>W-8?p.sx-item.hit-width-5:right;
      ctx.fillText(n.label,Math.max(8,Math.min(W-width-8,x)),p.sy+4,width);
    }
  }
  ctx.textAlign='left';ctx.globalAlpha=1;
  if (time === 1) transition=null;
  if (spin || transition) frame=requestAnimationFrame(draw);
  else lastFrame=0;
}
function invalidate() {
  if (!document.hidden && parentVisible && !frame) frame=requestAnimationFrame(draw);
}
function pick(event) {
  const bounds=canvas.getBoundingClientRect(), x=event.clientX-bounds.left, y=event.clientY-bounds.top;
  for (let i=rendered.length-1;i>=0;i--) {
    const item=rendered[i];
    if (Math.hypot(item.p.sx-x,item.p.sy-y)<item.hit) return item.n;
  }
  return null;
}
canvas.onpointerdown = event => {
  if (event.button !== 0) return;
  drag=true;moved=false;lx=event.clientX;ly=event.clientY;
  canvas.setPointerCapture(event.pointerId);
};
canvas.onpointermove = event => {
  if (drag) {
    const dx=event.clientX-lx,dy=event.clientY-ly;
    if (Math.abs(dx)+Math.abs(dy)>2) moved=true;
    ay+=dx*.006;ax=Math.max(-1.45,Math.min(1.45,ax+dy*.006));lx=event.clientX;ly=event.clientY;
    transition=null;cameraDirty=true;invalidate();return;
  }
  const node=pick(event);canvas.style.cursor=node?'pointer':'grab';
  if (node!==hover) {hover=node;invalidate();}
};
canvas.onpointerup = event => {
  if (!drag) return;
  drag=false;canvas.releasePointerCapture(event.pointerId);
  if (moved) return;
  const node=pick(event);
  if (node) navigate(node.type==='package'?{kind:'package',id:node.group}:{kind:'node',id:node.id});
};
canvas.onpointercancel = () => {drag=false;};
canvas.onpointerleave = () => {if(hover){hover=null;invalidate();}};
canvas.onwheel = event => {event.preventDefault();transition=null;zoom=Math.max(.25,Math.min(4,zoom*(event.deltaY<0?1.1:1/1.1)));invalidate();};

// One scrolling menu, and one inspector. They never compete for canvas space.
function setExplorer(open) {
  $('explorer').hidden=!open;$('explore').setAttribute('aria-expanded',String(open));
  $('inspector').hidden=open || !detailsOpen;
  if (open) renderMenu();
}
function paged(key, items, render, label, initial=12) {
  const limit=limits.get(key)||initial,remaining=Math.max(0,items.length-limit);
  return items.slice(0,limit).map(render).join('')+(remaining
    ? `<button type="button" class="more" data-more="${esc(key)}">Show more ${esc(label)} (${remaining} remaining)</button>` : '');
}
function nodeLink(node, label=node.label) {
  return `<button type="button" class="link" data-node="${esc(node.id)}">${esc(label)}</button>`;
}
function fileLink(path) {
  const node=fileNode.get(path);
  return node?nodeLink(node,path):`<span>${esc(path)} <span class="muted">· not indexed</span></span>`;
}
function confidenceBadge(value) {
  const color={EXTRACTED:'green',INFERRED:'gold',AMBIGUOUS:'pink'}[value]||'';
  return value?`<span class="badge ${color}">${esc(value)}</span>`:'';
}
function workButton(kind,index,label,meta='') {
  return `<button type="button" class="witem" data-work="${kind}" data-index="${index}"><span class="n">${esc(label)}</span><span class="c">${esc(meta)}</span></button>`;
}
function searchResults() {
  const rank=node=>{
    const label=String(node.label||'').toLowerCase(),path=String(node.source_file||'').toLowerCase();
    if (label===query || (node.type==='file'&&path===query)) return 0;
    if (label.startsWith(query)) return 1;
    if (label.includes(query)) return 2;
    if (path.includes(query)) return 3;
    return String(node.id||'').toLowerCase().includes(query)?4:5;
  };
  return [...packageNodes,...nodes].map(node=>({node,rank:rank(node)})).filter(item=>item.rank<5)
    .sort((a,b)=>a.rank-b.rank).map(item=>item.node);
}
function renderMenu() {
  const menu=$('menu'),kind=$('menuKind').value;
  $('menuTitle').textContent=query?'Search results':'';
  if (query) {
    const results=searchResults();
    $('menuTitle').textContent=`${results.length} results`;
    menu.innerHTML=paged('menu',results,node=>node.type==='package'?packageButton(node.group):
      `<button type="button" class="node-link" data-node="${esc(node.id)}"><span class="n">${esc(node.label)} <span class="muted">${esc(node.source_file||node.group)}</span></span><span class="badge">${esc(node.type)}</span></button>`,'results',24)||'<p class="note">No matching package, file or symbol.</p>';
    return;
  }
  if (kind==='packages') {
    const group=selectedGroup();
    if (group) {
      const children=(members.get(group)||[]).filter(n=>!isSymbol(n)).sort((a,b)=>a.label.localeCompare(b.label));
      $('menuTitle').textContent=group+' · '+children.length+' sources';
      menu.innerHTML=`<div class="package-list">${paged('menu',children,node=>
        `<button type="button" class="node-link" data-node="${esc(node.id)}"><span class="n">${esc(node.label)}</span><span class="badge">${node.type==='brain'?'counts':(symbolsByFile.get(node.source_file)||[]).length+' symbols'}</span></button>`,'sources',18)}</div>`;
    } else {
      const ordered=[...groups].sort((a,b)=>Number(!!a.brain)-Number(!!b.brain)||a.name.localeCompare(b.name));
      menu.innerHTML=`<div class="package-list">${paged('menu',ordered,g=>packageButton(g.name),'packages',18)}</div>`;
    }
  } else if (kind==='work') {
    menu.innerHTML=(boards.length?'<div class="section-title">Taskboards</div>':'')+
      boards.map((b,i)=>workButton('board',i,b.title||b.board_id,`${b.state} · ${b.tasks.length} tasks`)).join('')+
      (commits.length?'<div class="section-title">Recent commits</div>':'')+
      commits.map((c,i)=>workButton('commit',i,c.subject,`${c.hash} · ${c.files.length} files`)).join('');
    if (!boards.length&&!commits.length) menu.innerHTML='<p class="note">No taskboards or recent commits in this snapshot.</p>';
  } else {
    menu.innerHTML=`<div id="stats">${Object.entries(insights.confidence||{}).filter(([,count])=>count>0).map(([label,count])=>`<span>${esc(label.toLowerCase())} ${count}</span>`).join('')}</div>`+
      '<div class="section-title">Connected owners <small>degree is not a quality score</small></div>'+
      (insights.godNodes||[]).map(n=>`<button type="button" class="qitem" data-node="${esc(n.id)}">${esc(n.label)} <span class="badge">${n.degree} connections</span></button>`).join('')+
      '<div class="section-title">Across communities</div>'+
      (insights.surprises||[]).map(edge=>`<div class="row">${byId.has(edge.source_id)?nodeLink(byId.get(edge.source_id)):esc(edge.source)} <span class="muted">${esc(edge.relation.replace(/_/g,' '))}</span> ${byId.has(edge.target_id)?nodeLink(byId.get(edge.target_id)):esc(edge.target)} ${confidenceBadge(edge.confidence)}</div>`).join('')+
      '<div class="section-title">Questions to verify in source</div>'+(insights.suggestedQuestions||[]).map(q=>`<p class="note">${esc(q)}</p>`).join('');
  }
}
function packageButton(name) {
  const group=groups.find(g=>g.name===name),parts=name.split('/'),tail=parts.pop();
  const count=(members.get(name)||[]).filter(n=>n.type==='file').length;
  return `<button type="button" class="grow" data-package="${esc(name)}" aria-current="${selectedGroup()===name}"><span class="dot" style="color:${groupColor.get(name)}"></span><span class="n">${parts.length?`<span class="prefix">${esc(parts.join('/'))}/</span>`:''}${esc(tail)}</span><span class="c">${group?.brain?'counts':count+' files'}</span></button>`;
}
function relationshipSection(node,direction) {
  const outgoing=direction==='outgoing';
  const structural=new Set(['contains','method','case_of']);
  const edges=(incident.get(node.id)||[]).filter(edge=>outgoing?edge.source===node.id:edge.target===node.id)
    .sort((a,b)=>Number(structural.has(a.relation))-Number(structural.has(b.relation)));
  if (!edges.length) return '';
  return `<section class="section"><div class="section-title">${outgoing?'Outgoing':'Incoming'} (${edges.length}) <small>${outgoing?'this node is the source':'this node is the target'}</small></div>`+
    paged(direction,edges,edge=>{
      const other=byId.get(outgoing?edge.target:edge.source);
      if (!other) return '';
      return `<button type="button" class="relationship" data-node="${esc(other.id)}" title="${esc(other.source_file||other.group)}"><span>${esc(other.label)}</span> <span class="relation">${other.group!==node.group?esc(other.group)+' · ':''}${esc((edge.relation||'related').replace(/_/g,' '))}</span> ${confidenceBadge(edge.confidence)}</button>`;
    },direction+' relationships',8)+'</section>';
}
function renderInspector() {
  let title='',html='';
  const node=selectedNode();
  if (node) {
    title=node.label;
    html=`<p class="note">${esc(node.type)} · ${node.source_file?(node.type==='file'?esc(node.source_file):'Defined in '+fileLink(node.source_file)):esc(node.group)}</p>`;
    if (node.type==='brain') {
      html+='<div class="inspector-columns"><section>'+Object.entries(node.stats||{}).map(([key,value])=>`<div class="row">${esc(key)} · ${esc(value)}</div>`).join('')+'</section></div>';
      html+=`<p class="note">Counts only, not live execution.${node.drill?' Open '+esc(node.drill)+' in MO for its controls.':''}</p>`;
    } else {
      const symbols=symbolsByFile.get(node.source_file)||[];
      html+='<div class="inspector-columns">';
      if (node.type==='file'&&symbols.length) html+=`<section class="section"><div class="section-title">Symbols (${symbols.length})</div>`+
        paged('symbols',symbols,child=>`<div class="row">${nodeLink(child)} <span class="badge">${esc(child.type)}</span></div>`,'symbols')+'</section>';
      html+=relationshipSection(node,'outgoing')+relationshipSection(node,'incoming');
      const tasks=pathTasks.get(node.source_file)||[],recent=pathCommits.get(node.source_file)||[],op=ops[node.source_file];
      if (tasks.length||recent.length||op) {
        html+='<section class="section"><div class="section-title">Work touching this file</div>';
        if (op) html+=`<div class="row">${op.reads} recorded reads · ${op.modifies} writes</div>`;
        html+=paged('tasks',tasks,task=>`<div class="row">${esc(task)}</div>`,'task references');
        html+=paged('commits',recent,commit=>workButton('commit',commits.indexOf(commit),commit.subject,commit.hash),'commits',5)+'</section>';
      }
      html+='</div>';
    }
  } else if (view.kind==='commit'||view.kind==='board') {
    const indexed=[...highlightPaths].filter(path=>fileNode.has(path)).length;
    html=`<p class="note">${indexed} of ${highlightPaths.size} referenced files indexed. The graph shows matching files only; unindexed paths remain listed below.</p>`;
    if (view.kind==='commit') {
      const commit=commits[view.id];title=commit.subject;
      html+=`<p class="note">${esc(commit.hash)} · ${esc(commit.when)}</p><div class="section-title">Files (${commit.files.length})</div>`+
        paged('files',commit.files,path=>`<div class="row">${fileLink(path)}</div>`,'files');
    } else {
      const board=boards[view.id];title=board.title||board.board_id;
      html+=`<p class="note">${esc(board.state)} · ${esc(board.board_id)}</p><div class="section-title">Tasks (${board.tasks.length})</div>`+
        paged('tasks',board.tasks,task=>`<section class="section"><div class="row">${esc(task.id)} · ${esc(task.title)} <span class="badge">${esc(task.status)}</span></div>`+
          (task.evidence||[]).map(evidence=>`<div class="row muted">${evidencePath(evidence)?fileLink(evidencePath(evidence)):esc(evidence)}</div>`).join('')+'</section>','tasks');
    }
  }
  $('infoTitle').textContent=title;info.innerHTML=html;
  $('inspector').hidden=!detailsOpen||!$('explorer').hidden;
}
function handleAction(event) {
  const button=event.target.closest('button');
  if (!button) return;
  if (button.dataset.node) navigate({kind:'node',id:button.dataset.node});
  else if (button.dataset.package) navigate({kind:'package',id:button.dataset.package});
  else if (button.dataset.work) navigate({kind:button.dataset.work,id:Number(button.dataset.index)});
  else if (button.dataset.more) {
    const key=button.dataset.more,container=key==='menu'?$('menu'):info,scroll=container.scrollTop;
    const initial=key==='menu'?(query?24:18):key==='incoming'||key==='outgoing'?8:key==='commits'?5:12;
    limits.set(key,(limits.get(key)||initial)+24);
    if (key==='menu') renderMenu(); else renderInspector();
    container.scrollTop=scroll;
    (container.querySelector(`[data-more="${key}"]`)||container.querySelector('button'))?.focus({preventScroll:true});
  }
}
$('menu').onclick=handleAction;info.onclick=handleAction;
$('explore').onclick=()=>setExplorer($('explorer').hidden);
$('closeMenu').onclick=()=>{setExplorer(false);$('explore').focus();};
$('closeInfo').onclick=()=>{detailsOpen=false;renderInspector();};
$('menuKind').onchange=()=>{query='';$('search').value='';limits.delete('menu');renderMenu();};
$('search').oninput=()=>{query=$('search').value.trim().toLowerCase();limits.delete('menu');setExplorer(true);};
$('search').onkeydown=event=>{if(event.key==='Enter'&&query)$('menu').querySelector('button[data-package],button[data-node]')?.click();};
$('back').onclick=goBack;
$('overview').onclick=()=>{const open=!$('explorer').hidden;navigate({kind:'overview'});if(open){$('menuKind').value='packages';setExplorer(true);}};
$('level').onchange=()=>{
  const level=$('level').value,node=selectedNode();
  if (level==='packages') navigate({kind:'overview'});
  else if (node&&isSymbol(node)&&level==='files') {
    const file=fileNode.get(node.source_file);
    navigate(file?{kind:'node',id:file.id,level}:{kind:'package',id:node.group,level});
  } else if (node?.type==='file'&&level==='definitions') navigate({kind:'package',id:node.group,level});
  else navigate({...view,level});
};
$('touched').onclick=()=>{
  const node=selectedNode(),touched=!view.touched;
  navigate(node&&touched&&!touchedPaths.has(node.source_file)
    ? {kind:'package',id:node.group,level:view.level,touched} : {...view,touched});
};
$('fit').onclick=()=>{zoom=1;cameraDirty=true;invalidate();};
$('tSpin').onclick=()=>{spin=!spin&&!reducedMotion.matches;lastFrame=0;syncControls();invalidate();};
$('tGrid').onclick=()=>{grid=!grid;syncControls();invalidate();};
addEventListener('keydown',event=>{
  if (event.key!=='Escape') return;
  if (!$('explorer').hidden) {query='';$('search').value='';setExplorer(false);$('explore').focus();}
  else if (detailsOpen) {detailsOpen=false;renderInspector();}
  else goBack();
});

// No idle animation or hidden-frame work; finite transitions honour motion settings.
function suspend() {cancelAnimationFrame(frame);frame=0;transition=null;lastFrame=0;drag=false;}
let parentTheme='';
window.addEventListener('message',event=>{
  if (event.source!==parent) return;
  if (event.data?.type==='mo-graph-visibility') {
    parentVisible=event.data.visible===true;
    if (parentVisible) invalidate(); else suspend();
    return;
  }
  if (event.data?.type!=='mo-graph-theme'||typeof event.data.css!=='string'||event.data.css.length>20000||parentTheme===event.data.css) return;
  parentTheme=event.data.css;
  // Replace the shared skin in its existing slot. Appending its base rules after
  // the local layout would restore the old full-viewport canvas dimensions.
  $('skin').textContent=parentTheme;
  for (const key of Object.keys(THEME)) THEME[key]=cssVar('--'+key,THEME[key]);
  updateColors();
  if (!$('explorer').hidden) renderMenu();
  invalidate();
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else invalidate();});
addEventListener('pagehide',suspend);
reducedMotion.addEventListener('change',()=>{if(reducedMotion.matches){spin=false;transition=null;}syncControls();invalidate();});
new ResizeObserver(resize).observe($('stage'));
$('meta').title=`${DATA.meta?.project||'Project'} · generated ${DATA.meta?.generated_at||'unknown'} · verify with source and tests`;
navigate({kind:'overview'},false,false);
resize();
let hash='';
try {hash=decodeURIComponent(location.hash.slice(1)).toLowerCase();} catch (_) { /* A malformed external fragment is not a graph selection. */ }
if (hash) {
  $('search').value=hash;query=hash;setExplorer(true);
  const match=searchResults()[0];
  if (match) navigate(match.type==='package'?{kind:'package',id:match.group}:{kind:'node',id:match.id},false,false);
}
