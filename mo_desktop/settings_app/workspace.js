(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const all = (query, root = document) => [...root.querySelectorAll(query)];
  const pending = new Map(), bindings = new Map(), unsaved = new Set();
  let closing = false, recordingVoice = false;
  let sequence = 0, state, page = 'appearance', selectedSkin, skinPicker, draftColors = {}, openPicker, modelTarget = 'desktop', chosenProject, modelPoll = 0;
  const descriptions = {
    general: ['General', 'Small preferences that shape your day.'],
    appearance: ['Appearance', 'Make MO feel like yours.'],
    models: ['Models & providers', 'Choose the model behind your conversations.'],
    voice: ['Voice & roles', 'A voice and perspective that fit the moment.'],
    connections: ['Tools & connections', 'Connect MO to the places you work.'],
    projects: ['Projects & checks', 'Keep project rules and diagnostic choices in their own scope.'],
    permissions: ['Permissions & privacy', 'Review where access is configured.'],
    memory: ['Memory & learning', 'Learning, skill delivery and recall have separate controls.'],
    automation: ['Automation', 'Review scheduled and background work.'],
    devices: ['Devices', 'Keep your connected surfaces close.'],
  };
  const scopes = {general:'This Desktop', appearance:'Shared skin · Desktop geometry', models:'Desktop · Terminal defaults · Live instances', voice:'Desktop conversation', connections:'Host integrations', projects:'Per project', permissions:'Host policy', memory:'Profile', automation:'Host services', devices:'Per device'};
  const destinations = [
    ['general', 'SystemCare preferences', 'Scan scope, notifications, retention, Game Mode and optional automatic scans are managed in SystemCare → Settings.', 'systemcare', 'Open SystemCare'],
    ['connections', 'Email accounts & notifications', 'Use Dashboard → Email for Gmail opt-in, private client setup, sign-in and notification preferences. Outlook uses its existing Connected Tab setup.', 'dashboard', 'Open Dashboard'],
    ['projects', 'Checks, rules & knowledge', 'Select the project in Dashboard → Workspace. Recorded checks, rules and knowledge keep their existing views. Language-server preferences are managed here.', 'dashboard', 'Open Dashboard'],
    ['memory', 'Review learning & skills', 'Dashboard → Learning shows active learning, pending suggestions and skill sources. Review, Undo and import remain explicit actions.', 'dashboard', 'Open Dashboard'],
    ['automation', 'Scheduled work', 'Dashboard → Life manages reminders and scheduled tasks. Jobs keep their existing pause, resume, cancellation and run evidence.', 'dashboard', 'Open Dashboard'],
    ['devices', 'Phone & device connections', 'Open Phone for device connection, pairing and trackpad controls. Android-local preferences stay on the phone; pairing never grants extra scopes automatically.', 'phone', 'Open Phone'],
  ];
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function status(message, error = false) {
    $('save-status').textContent = message;
    $('save-status').classList.toggle('error', error);
  }
  function request(action, payload = {}) {
    const id = String(++sequence);
    const response = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('MO did not respond. Refresh Settings to reconnect.')); }, action === 'media_install' ? 180000 : 20000);
      pending.set(id, {resolve, reject, timer});
      Promise.resolve(window.pywebview.api.request(id, action, payload)).catch(error => {
        clearTimeout(timer); pending.delete(id); reject(error);
      });
    });
    pending.get(id).response = response;
    return response;
  }
  window.moSettingsResult = ({request_id, result}) => {
    const entry = pending.get(request_id);
    if (!entry) return;
    clearTimeout(entry.timer); pending.delete(request_id);
    entry.resolve(result);
  };
  window.moSettingsTheme = css => { $('settings-theme').textContent = css; };
  window.moSettingsClose = async () => {
    if (closing) return;
    closing = true;
    document.activeElement?.blur();
    document.body.inert = true;
    modelPoll++;
    try {
      if (recordingVoice) { await request('record_cancel').catch(() => {}); recordingVoice = false; }   // never leave the mic open
      await Promise.all([...bindings.values()].map(binding => binding.lane.flush()));
      while (pending.size) await Promise.all([...pending.values()].map(entry => entry.response));
      if (unsaved.size) throw new Error('A preview has not been saved. Save or reset it before closing.');
      await window.pywebview.api.window_control('dispose');
    } catch (error) {
      status(error.message, true);
      closing = false;
      document.body.inert = false;
    }
  };
  async function act(action, payload = {}) {
    try {
      const result = await request(action, payload);
      if (!result.ok) { status(result.message || 'This change could not be saved.', true); return result; }
      if (result.state) render(result.state);
      if (result.chrome) showChrome(result.chrome);
      if (result.media_status) {
        const m = result.media_status;
        $('media-status').textContent = `Key ${m.credential_present ? 'ready' : 'missing'} · reference helper ${m.reference_helper_ready ? 'ready' : 'missing'} · ${m.reference_platform_ready ? 'reference sharing supported' : 'reference sharing requires Windows'} · audio/video tools ${m.audio_video_tools_ready ? 'ready' : 'missing'} · ${m.enabled ? 'creation enabled' : 'creation off'}`;
        $('media-install').disabled = m.reference_helper_ready || !m.reference_platform_ready;
      }
      if (result.model_state) { state.model_state = result.model_state; showRunningModels(); }
      if (result.overview) { state.overview = result.overview; }
      if (result.graph) { state.graph = result.graph; buildGraph(); }
      if (result.message) status(result.message);
      return result;
    } catch (error) { status(error.message, true); return {ok: false}; }
  }
  function closePicker() { if (openPicker) { openPicker.menu.hidden = true; openPicker.button.setAttribute('aria-expanded', 'false'); openPicker = null; } }
  function picker(label, options, value, changed) {
    const root = element('div', 'picker'), button = element('button', 'pick-button');
    const caption = element('span', 'pick-label'), menu = element('div', 'pick-menu');
    button.type = 'button'; button.setAttribute('aria-label', label); button.setAttribute('aria-haspopup', 'listbox'); button.setAttribute('aria-expanded', 'false');
    menu.hidden = true; menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', label);
    button.append(caption); root.append(button, menu);
    const api = {root, button, menu, value, options,
      update(next, items = api.options) {
        api.value = next; api.options = items;
        caption.textContent = items.find(item => item[0] === next)?.[1] ?? String(next || 'Default');
        menu.replaceChildren();
        items.forEach(([key, title]) => {
          const option = element('button', '', title); option.type = 'button'; option.setAttribute('role', 'option'); option.setAttribute('aria-selected', String(key === next));
          option.onclick = () => { closePicker(); api.update(key); button.focus(); changed(key); };
          menu.append(option);
        });
      },
    };
    button.onclick = () => {
      const wasOpen = !menu.hidden; closePicker(); if (wasOpen) return;
      menu.hidden = false; button.setAttribute('aria-expanded', 'true'); openPicker = api;
      const bounds = menu.getBoundingClientRect(), available = innerHeight - button.getBoundingClientRect().bottom - 48;
      menu.classList.toggle('above', bounds.height > available && button.getBoundingClientRect().top > bounds.height + 65);
      (menu.querySelector('[aria-selected=true]') || menu.firstElementChild)?.focus();
    };
    menu.onkeydown = event => {
      const items = [...menu.children], index = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
      } else if (event.key === 'Escape') { closePicker(); button.focus(); }
    };
    api.update(value); return api;
  }
  document.addEventListener('pointerdown', event => { if (openPicker && !openPicker.root.contains(event.target)) closePicker(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closePicker(); });
  function row(label, detail, id) {
    const root = element('div', 'setting-row'), text = element('div'), name = element('label', '', label), control = element('div', 'control');
    if (id) name.htmlFor = id;
    text.append(name, element('p', '', detail)); root.append(text, control);
    root.dataset.search = `${label} ${detail}`; return {root, control};
  }
  function group(title, targetPage) {
    const section = element('section', 'page-section'); section.dataset.section = targetPage;
    const card = element('section', 'setting-group'), heading = element('div', 'group-heading');
    heading.append(element('h2', '', title)); card.append(heading); section.append(card); return {section, card};
  }
  // Each slider has one ordered lane. A pending preview is replaced by the
  // latest value; release queues the final save after any in-flight preview.
  function liveControl(id, update, initial) {
    let latest, busy = false, timer, savedValue = initial, idle = Promise.resolve();
    function drain() {
      if (busy || !latest) return;
      busy = true;
      idle = (async () => {
        const next = latest; latest = null;
        const result = await act(next.save ? 'save' : 'preview', {id, value: next.value});
        if (result.ok && next.save) { savedValue = result.value; unsaved.delete(id); }
        if (!result.ok && next.save) {
          if (result.value !== undefined) unsaved.add(id);
          else if (!latest) { update(savedValue); unsaved.delete(id); }
        }
        $('unsaved').hidden = !unsaved.size;
        if (!latest && result.ok && next.save) update(result.value);
        if (id === 'voice.role' && result.ok) bindings.get('voice.role_active')?.update(result.role_active);
        busy = false; if (latest) drain();
      })();
    }
    return {queue(value, save) {
      latest = {value, save}; clearTimeout(timer);
      if (save) { status('Saving…'); drain(); } else { unsaved.add(id); $('unsaved').hidden = false; timer = setTimeout(drain, 33); }
    }, async flush() {
      clearTimeout(timer);
      if (latest) latest.save = true;
      drain();
      while (busy) await idle;
    }, get savedValue() { return savedValue; }};
  }
  function makeControl(field) {
    const {root, control} = row(field.label, field.detail, field.id);
    let update, input, choice;
    const lane = liveControl(field.id, value => update(value), field.value);
    if (field.kind === 'switch') {
      input = element('input', 'settings-switch'); input.type = 'checkbox'; input.onchange = () => lane.queue(input.checked, true);
      update = value => { input.checked = Boolean(value); };
    } else if (field.kind === 'range') {
      control.classList.add('range-control'); input = element('input'); input.type = 'range';
      const [low, high, step, unit] = field.constraints; input.min = low; input.max = high; input.step = step;
      const output = element('output'); output.htmlFor = field.id;
      const display = () => { output.textContent = `${Number(input.value)}${unit}`; };
      input.oninput = () => { display(); lane.queue(Number(input.value), false); };
      input.onchange = () => lane.queue(Number(input.value), true);
      update = value => { input.value = value; display(); }; control.append(output);
    } else if (['select', 'role', 'device', 'provider', 'choice'].includes(field.kind)) {
      let items = field.constraints;
      if (field.kind === 'role') items = roleOptions(field.value);
      if (field.kind === 'provider') items = providerOptions(field.value);
      if (field.kind === 'choice') items = choiceOptions(field.id);
      if (field.kind === 'device') items = [[field.value || 'default', field.value === 'default' ? 'System default' : field.value]];
      choice = picker(field.label, items, field.value, value => {
        if (field.kind === 'role' && value === '__custom__') { custom.hidden = false; custom.focus(); return; }
        if (custom) custom.hidden = true;
        lane.queue(value, true);
      });
      control.append(choice.root);
      let custom;
      if (field.kind === 'role') {
        control.classList.add('role-control'); custom = element('input', 'role-custom'); custom.type = 'text'; custom.maxLength = 500;
        custom.placeholder = 'Describe a custom role'; custom.setAttribute('aria-label', 'Custom role'); custom.hidden = true;
        custom.onchange = () => { const value = custom.value.trim(); choice.update(value, roleOptions(value)); lane.queue(value, true); };
        control.append(custom);
      }
      update = value => choice.update(value, field.kind === 'role' ? roleOptions(value) : field.kind === 'provider' ? providerOptions(value)
        : field.kind === 'choice' ? choiceOptions(field.id) : choice.options);
    } else if (field.kind === 'status') {         // shows state only; nothing to save
      const text = element('span', 'muted'); control.append(text);
      update = value => { text.textContent = value || ''; };
    } else if (field.kind === 'recorder') {       // Record my voice: one line at a time, MO records and checks it
      const line = element('p'), words = element('strong'), note = element('p', 'muted'), progress = element('p', 'muted');
      const actions = element('div', 'group-actions'), record = element('button', 'button', 'Record');
      const back = element('button', 'quiet', 'Back'), next = element('button', 'quiet', 'Next');
      [record, back, next].forEach(button => { button.type = 'button'; });
      line.append(words); actions.append(record, back, next); control.append(line, note, actions, progress);
      let view = {}, step = null;
      const show = () => {
        const steps = view.steps || [], current = steps[step];
        recordingVoice = view.recording !== null && view.recording !== undefined;
        words.textContent = current ? `${step + 1} of ${steps.length} · ${current.text}` : 'Every line is recorded.';
        record.textContent = recordingVoice ? 'Stop' : 'Record'; record.disabled = !current;
        back.disabled = recordingVoice || step === 0; next.disabled = recordingVoice || !current;
        if (recordingVoice) note.textContent = current?.kind === 'talk' ? 'Recording… talk freely, then press Stop.' : 'Recording… read the line, then press Stop.';
        const good = Math.round(view.good_seconds || 0);
        progress.textContent = `${Math.floor(good / 60)} min ${good % 60} s of good speech` + (view.ready ? ' · enough to make your voice' : ' · about 5 minutes are needed');
      };
      record.onclick = async () => {
        record.disabled = true;
        const result = await act(recordingVoice ? 'record_stop' : 'record_start', recordingVoice ? {} : {step});
        if (result.ok && result.recording) {
          view = result.recording;
          const last = view.last;
          if (last) { note.textContent = last.advice; if (last.status === 'ok') step = Math.min(step + 1, (view.steps || []).length); }
        }
        show();
      };
      back.onclick = () => { step = Math.max(0, step - 1); note.textContent = ''; show(); };
      next.onclick = () => { step = Math.min(step + 1, (view.steps || []).length); note.textContent = ''; show(); };
      update = value => { view = value || {}; if (step === null) step = view.next || 0; show(); };
    } else if (field.kind === 'color') {
      const follow = element('input', 'settings-switch'), label = element('label', 'muted', 'Follow skin'); follow.type = 'checkbox';
      follow.setAttribute('aria-label', 'Follow skin color'); input = element('input'); input.type = 'color';
      follow.onchange = () => { input.disabled = follow.checked; lane.queue(follow.checked ? 'skin' : input.value, true); };
      input.onchange = () => lane.queue(input.value, true);
      control.append(label, follow); update = value => { follow.checked = value === 'skin'; input.disabled = follow.checked; input.value = follow.checked ? (state.skins.find(s => s.id === state.skin)?.colors.accent || '#00c4cc') : value; };
    } else {
      input = element('input'); input.type = 'text'; input.maxLength = 500; input.onchange = () => lane.queue(input.value, true);
      update = value => { input.value = Array.isArray(value) ? value.join(', ') : value || ''; };
    }
    if (input) { input.id = field.id; input.setAttribute('aria-label', field.label); control.prepend(input); }
    update(field.value); bindings.set(field.id, {root, update, choice, input, lane}); return root;
  }
  function choiceOptions(id) { return (state.choices || {})[id] || []; }   // what exists on this computer
  function providerOptions(value) {
    const names = [...new Set([...(state.voice_providers || []), ...(value ? [value] : [])])];
    return [['', 'Same as MO'], ...names.map(name => [name, name])];
  }
  function roleOptions(value) {
    const values = [...new Set(['', ...state.roles, ...(value ? [value] : [])])];
    return [...values.map(role => [role, role || 'Default']), ['__custom__', 'Custom role…']];
  }
  function buildControls() {
    const groups = new Map();
    state.controls.forEach(field => {
      const key = `${field.page}/${field.group}`;
      if (!groups.has(key)) { const item = group(field.group, field.page); groups.set(key, item.card); $('generated-controls').append(item.section); }
      groups.get(key).append(makeControl(field));
    });
    const terminal = {show_reasoning: 'Show reasoning', show_tools: 'Show tools', hints: 'Show hints', activity: 'Show activity'};
    Object.entries(terminal).forEach(([key, label]) => {
      const item = row(label, 'Used by MO Terminal on its next load.', `terminal-${key}`), input = element('input', 'settings-switch');
      input.type = 'checkbox'; input.id = `terminal-${key}`; input.checked = Boolean(state.terminal[key]);
      input.onchange = async () => { input.disabled = true; const result = await act('terminal', {id: key, value: input.checked}); if (!result.ok) input.checked = !input.checked; input.disabled = false; };
      item.control.append(input); $('terminal-controls').append(item.root);
    });
  }
  function render(next) {
    const first = !state; state = next;
    if (next.page && descriptions[next.page]) page = next.page;   // opened at a section (Generate's Provider settings)
    if (first) buildControls();
    else state.controls.forEach(field => { const binding = bindings.get(field.id); if (!binding.root.contains(document.activeElement)) binding.update(field.value); });
    $('startup').checked = state.startup;
    Object.entries(state.terminal).forEach(([key, value]) => { $(`terminal-${key}`).checked = Boolean(value); });
    selectedSkin = state.skin; selectSkin(selectedSkin);
    skinPicker = picker('Skin', state.skins.map(s => [s.id, s.label]), selectedSkin, selectSkin);
    $('skin-picker').replaceChildren(skinPicker.root);
    buildModels(); buildProjects(); buildGraph(); buildOverviews(); buildSkinGallery(); $('loading').hidden = true; $('content').hidden = false; navigate(page, false);
  }
  function selectSkin(id) {
    selectedSkin = id; const skin = state.skins.find(s => s.id === id); if (!skin) return;
    skinPicker?.update(id, state.skins.map(s => [s.id, s.label]));
    draftColors = {...skin.colors}; $('skin-state').textContent = id === state.skin ? 'Current skin' : 'Preview';
    $('delete-skin').hidden = !skin.custom; $('skin-apply').disabled = id === state.skin;
    $('color-editor').replaceChildren();
    Object.entries(draftColors).forEach(([role, color]) => {
      const label = element('label', '', role.replaceAll('_', ' ')), input = element('input'); input.type = 'color'; input.value = color; input.dataset.role = role;
      input.setAttribute('aria-label', `${role.replaceAll('_', ' ')} color`);
      input.oninput = () => { draftColors[role] = input.value; paintSkin(); $('skin-state').textContent = 'Custom preview · save as new'; };
      label.append(input); $('color-editor').append(label);
    });
    paintSkin(); all('.skin-tile').forEach(tile => tile.setAttribute('aria-pressed', String(tile.dataset.skin === id)));
  }
  function paintSkin() {
    const mapping = {background:'bg', surface:'surface', accent:'accent', text:'text', user_message_background:'user-bg', user_message:'user', mo_response:'response', ok:'ok', warn:'warn', error:'error'};
    Object.entries(mapping).forEach(([key, token]) => $('terminal-preview').style.setProperty(`--preview-${token}`, draftColors[key]));
    Object.entries({background:'bg', surface:'surface', accent:'brand', text:'text'}).forEach(([key, token]) => $('desktop-preview').style.setProperty(`--${token}`, draftColors[key]));
  }
  function showRunningModels() {
    const live = state.model_state || {}, root = $('running-models'); root.replaceChildren();
    const format = choice => choice?.model ? `${choice.model} · ${choice.thinking || 'provider default'}` : 'No provider available';
    const desktop = row('MO Desktop', live.desktop_busy ? 'Working · selection below applies to the next request' : 'Ready · model for the next request');
    desktop.control.append(element('span', 'model-value', format(live.desktop))); root.append(desktop.root);
    if (live.desktop_observed?.selection) { const observed = row('Desktop · last observed request', `Observed ${live.desktop_observed.age}s ago`); observed.control.append(element('span', 'model-value', format(live.desktop_observed.selection))); root.append(observed.root); }
    (live.instances || []).forEach(instance => {
      const item = row(`Terminal · ${instance.instance}`, `${instance.slot || 'Current conversation'} · observed ${instance.age}s ago${instance.controllable ? '' : ' · reload to enable live selection'}`);
      item.control.append(element('span', 'model-value', format(instance.selection))); root.append(item.root);
    });
    if (!live.instances?.length) root.append(element('p', 'scope-note', 'No recent running Terminal was observed. Refresh after a Terminal starts or publishes its presence.'));
  }
  function buildModels() {
    showRunningModels();
    const live = state.model_state || {}, instances = live.instances || [], catalog = state.models || [];
    const targets = [['desktop', 'MO Desktop'], ['terminal_default', 'Terminal default'], ...instances.filter(i => i.controllable).map(i => [`instance:${i.pid}:${i.instance}`, `Terminal · ${i.instance}`])];
    if (!targets.some(t => t[0] === modelTarget)) modelTarget = 'desktop';
    const targetRow = row('Apply to', 'Choose one surface or running instance');
    targetRow.control.append(picker('Model target', targets, modelTarget, value => { modelTarget = value; buildModels(); }).root);
    $('model-target').replaceChildren(targetRow.root);
    const instance = instances.find(i => modelTarget === `instance:${i.pid}:${i.instance}`);
    const selection = {...(instance?.selection || (modelTarget === 'desktop' ? live.desktop : live.terminal_default) || {})};
    const source = catalog.find(item => item.source === selection.source) || catalog[0];
    $('model-controls').replaceChildren(); $('apply-model').disabled = !source;
    $('follow-model').hidden = modelTarget !== 'desktop';
    $('follow-model').disabled = Boolean(live.desktop_follows);
    $('follow-model').onclick = async () => { const result = await act('model', {target:'desktop', follow:true}); if (result.ok) buildModels(); };
    $('apply-model').textContent = instance ? 'Change this Terminal' : 'Save model choice';
    $('model-note').textContent = instance ? 'Requires an idle Terminal. Confirmation comes from that instance.' : modelTarget === 'desktop' ? (live.desktop_follows ? 'Following the Terminal provider’s Desktop variant.' : 'Independent Desktop selection · next request.') : 'Applies to new or reloaded Terminals.';
    if (modelTarget === 'terminal_default' && !live.terminal_default) $('model-note').textContent = 'No saved override · startup configuration still applies. Save below to choose a new default.';
    if (!source) { $('model-note').textContent = 'Configure a provider before choosing a model.'; return; }
    const sourceRow = row('Provider', source.description || 'Configured provider'), modelRow = row('Model', 'Available from this provider'), thinkingRow = row('Reasoning', 'Supported thinking level');
    function updateThinking() {
      const model = catalog.find(s => s.source === selection.source).models.find(m => m.id === selection.model), options = (model?.thinking || []).map(t => [t.value, t.label]);
      thinkingRow.root.hidden = !options.length;
      selection.thinking = options.some(t => t[0] === selection.thinking) ? selection.thinking : (options[0]?.[0] || 'none');
      thinkingRow.control.replaceChildren(picker('Reasoning', options, selection.thinking, value => { selection.thinking = value; }).root);
    }
    function updateModels() {
      const models = catalog.find(s => s.source === selection.source).models;
      selection.model = models.some(m => m.id === selection.model) ? selection.model : models[0]?.id;
      modelRow.control.replaceChildren(picker('Model', models.map(m => [m.id, m.id]), selection.model, value => { selection.model = value; updateThinking(); }).root);
      $('apply-model').disabled = !selection.model; updateThinking();
    }
    selection.source = source.source;
    sourceRow.control.append(picker('Provider', catalog.map(s => [s.source, s.label]), selection.source, value => { selection.source = value; updateModels(); }).root);
    updateModels(); $('model-controls').append(sourceRow.root, modelRow.root, thinkingRow.root);
    $('apply-model').onclick = async () => {
      $('apply-model').disabled = true;
      const result = await act('model', {target:instance ? 'instance' : modelTarget, selection, instance:instance?.instance, pid:instance?.pid});
      if (result.request_id) {
        const poll = ++modelPoll;
        for (let attempt = 0; attempt < 15 && poll === modelPoll; attempt++) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          const observed = await act('models_status');
          const receipt = observed.model_state?.instances?.find(i => i.instance === instance.instance && i.pid === instance.pid)?.receipt;
          if (receipt?.request_id === result.request_id) { status(receipt.message, !receipt.ok); break; }
          if (attempt === 14) status('Not confirmed yet · refresh instances to inspect the actual model.', true);
        }
      }
      if (result.ok && result.model_state) buildModels();
      $('apply-model').disabled = false;
    };
  }
  $('refresh-models').onclick = async () => { const result = await act('models_status'); if (result.ok) buildModels(); };
  function buildProjects() {
    const projects = state.projects || [], root = $('lsp-controls'); root.replaceChildren();
    let project = projects.find(p => p.id === chosenProject) || projects[0];
    if (!project) { root.append(element('p', 'scope-note', 'Open MO in a project to choose its language-server policy.')); return; }
    $('lsp-servers').textContent = state.language_servers?.length ? `Configured languages: ${state.language_servers.join(', ')}. Server availability is checked when diagnostics run.` : 'No servers configured. Nothing is installed automatically.';
    chosenProject = project.id;
    const projectRow = row('Project', project.path);
    projectRow.control.append(picker('Project', projects.map(p => [p.id, p.label]), project.id, value => { chosenProject = value; buildProjects(); }).root);
    root.append(projectRow.root);
    const policy = row('Language-server policy', project.lsp ? `${project.lsp.state === 'ready' ? 'Configured · starts on demand' : project.lsp.state} · ${(project.lsp.languages || []).join(', ') || 'No configured languages'}` : 'Reload Desktop to start its LSP manager');
    if (project.lsp) {
      const globalPolicy = state.overview.flatMap(group => group.values).find(field => field.key === 'lsp.enabled');
      const select = picker('Language-server policy', [['default', `${globalPolicy.current} · global default`], ['on', 'On'], ['off', 'Off']], project.lsp.selection, async value => {
        select.button.disabled = true; const result = await act('lsp', {project:project.id, selection:value});
        if (result.ok) project.lsp = result.lsp; buildProjects();
      }); policy.control.append(select.root);
    }
    root.append(policy.root);
  }
  $('lsp-server-form').onsubmit = async event => {
    event.preventDefault(); const args = $('lsp-args').value.split('\n').map(s => s.trim()).filter(Boolean);
    const result = await act('lsp_server', {language:$('lsp-language').value, command:$('lsp-command').value, args});
    if (result.ok) { state.language_servers = result.language_servers; buildProjects(); $('lsp-server-form').reset(); }
  };
  function buildGraph() {
    const labels = {enabled:['Structural graph', 'Read and query MO’s native project graph.'], auto_build:['Automatic initial build', 'Allow existing lifecycle checks to build a missing graph.'], context:['Graph context in turns', 'Include relevant project orientation when the turn needs it.']};
    $('graph-controls').replaceChildren();
    Object.entries(state.graph || {}).forEach(([key, setting]) => {
      const item = row(labels[key][0], setting.managed_by ? `Controlled by ${setting.managed_by} · ${setting.value ? 'On' : 'Off'}` : `${labels[key][1]} Currently ${setting.value ? 'on' : 'off'}.`);
      const choice = picker(labels[key][0], [[null, `${setting.default ? 'On' : 'Off'} · default`], [true, 'On'], [false, 'Off']], setting.saved, async value => {
        choice.button.disabled = true; const result = await act('graph', {id:key, value}); if (!result.ok) buildGraph();
      }); choice.button.disabled = Boolean(setting.managed_by); item.control.append(choice.root); $('graph-controls').append(item.root);
    });
  }
  function buildSkinGallery() {
    $('skin-gallery').replaceChildren();
    state.skins.forEach(skin => {
      const tile = element('button', 'skin-tile'); tile.dataset.skin = skin.id; tile.setAttribute('aria-label', `${skin.label} skin preview`); tile.setAttribute('aria-pressed', String(skin.id === selectedSkin));
      tile.style.setProperty('--tile-bg', skin.colors.background); tile.style.setProperty('--tile-surface', skin.colors.surface); tile.style.setProperty('--tile-accent', skin.colors.accent); tile.style.setProperty('--tile-text', skin.colors.text);
      const swatch = element('span', 'skin-swatch'); swatch.append(element('i'), element('i'), element('i')); tile.append(swatch, element('span', '', skin.label));
      tile.onclick = () => selectSkin(skin.id); $('skin-gallery').append(tile);
    });
  }
  function buildOverviews() {
    $('owner-overviews').replaceChildren(); const groups = new Map();
    destinations.forEach(([page, title, detail, action, label]) => {
      const part = group(title, page), actions = element('div', 'group-actions'), link = element('button', 'button', label);
      part.card.append(element('p', 'group-description', detail));
      link.onclick = () => act(action); actions.append(link); part.card.append(actions); $('owner-overviews').append(part.section);
    });
    (state.overview || []).forEach(item => {
      if (!item.values.length) return;
      const part = group(item.label, item.page);
      part.card.append(element('p', 'group-description', item.detail), element('p', 'apply-note', 'Current values are loaded in this Desktop process. Saved changes apply after reload or restart.'));
      item.values.forEach(field => {
        const entry = row(field.label, field.detail); entry.root.dataset.search += ' ' + field.key;
        const status = element('p', 'setting-state'); entry.root.firstElementChild.append(status);
        const updateStatus = () => { status.textContent = `Current: ${field.current}${field.pending ? ' · saved change needs reload' : ''}`; status.classList.toggle('pending', field.pending); };
        updateStatus();
        let control;
        const save = async value => {
          const previous = field.selection;
          const result = await act('configuration', {id:field.key, value});
          if (result.ok) {
            const saved = result.overview.flatMap(group => group.values).find(item => item.key === field.key);
            Object.assign(field, saved); updateStatus();
            if (control?.update) control.update(field.selection);
            else control.value = field.selection ?? '';
          }
          else if (control?.update) control.update(previous);
          else if (control) control.value = previous ?? '';
        };
        if (field.kind === 'bool' || Array.isArray(field.kind)) {
          const options = [[null, `${field.default_value} · default`], ...(field.kind === 'bool' ? [[true,'On'],[false,'Off']] : field.kind.map(value => [value, value.replaceAll('_', ' ')]))];
          control = picker(field.label, options, field.selection, save); entry.control.append(control.root);
        } else {
          control = element('input', 'configuration-number'); control.type = field.kind === 'number_auto' ? 'text' : 'number'; control.placeholder = `${field.default_value} · default`; control.setAttribute('aria-label', field.label); control.value = field.selection ?? '';
          if (field.limits) [control.min, control.max, control.step] = field.limits;
          control.onchange = () => { const raw = control.value.trim(); save(!raw ? null : raw === 'auto' ? 'auto' : Number(raw)); }; entry.control.append(control);
          const reset = element('button', 'quiet', 'Reset'); reset.setAttribute('aria-label', `Reset ${field.label}`); reset.onclick = () => { control.value = ''; save(null); }; entry.control.append(reset);
        }
        part.card.append(entry.root);
      });
      $('owner-overviews').append(part.section);
    });
  }

  function navigate(next, scroll = true) {
    page = next; closePicker(); const query = $('search').value.trim().toLowerCase();
    $('page-title').textContent = query ? 'Search settings' : descriptions[page][0];
    $('page-description').textContent = query ? `Results for “${$('search').value.trim()}”` : descriptions[page][1];
    document.querySelector('.page-scope').textContent = query ? 'All settings' : scopes[page];
    all('[data-page]').forEach(button => button.setAttribute('aria-current', !query && button.dataset.page === page ? 'page' : 'false'));
    let count = 0;
    all('[data-section]').forEach(section => {
      section.hidden = query ? !section.textContent.toLowerCase().includes(query) && !(section.dataset.section + ' ' + all('[data-search]', section).map(n => n.dataset.search).join(' ')).toLowerCase().includes(query) : section.dataset.section !== page;
      if (!section.hidden) count++;
    });
    $('no-results').hidden = count > 0;
    if (scroll) $('main').scrollTop = 0;
    if (page === 'voice' && !query && !bindings.get('voice.output_device')?.loaded) loadDevices();
  }
  async function loadDevices() {
    const binding = bindings.get('voice.output_device'); if (!binding) return;
    binding.loaded = true; const result = await act('devices');
    if (result.ok) { const current = binding.choice.value; binding.choice.update(current, [...new Set([current, ...result.devices])].map(name => [name, name === 'default' ? 'System default' : name])); }
    else binding.loaded = false;
  }
  function showChrome(chrome) {
    $('chrome-status').textContent = chrome.extension_connected ? 'Tab connected' : chrome.bridge_live ? 'Bridge ready' : chrome.installed ? 'Installed · waiting for Chrome' : 'Bridge not installed';
    $('chrome-repair').disabled = chrome.installed;
    $('chrome-repair').textContent = chrome.installed ? 'Bridge ready' : 'Repair bridge';
    $('chrome-remove').disabled = !chrome.installed;
  }
  async function confirmAction(title, detail, action, payload = {}) {
    const dialog = $('confirm'); $('confirm-title').textContent = title; $('confirm-detail').textContent = detail; dialog.returnValue = ''; dialog.showModal();
    dialog.addEventListener('close', () => { if (dialog.returnValue === 'confirm') act(action, payload); }, {once:true});
  }
  all('[data-page]').forEach(button => { button.onclick = () => { $('search').value = ''; navigate(button.dataset.page); }; });
  all('[data-action]').forEach(button => { button.onclick = async () => { button.disabled = true; const result = await act(button.dataset.action); button.disabled = result.chrome ? result.chrome.installed : false; }; });
  all('[data-window]').forEach(button => { button.onclick = () => window.pywebview.api.window_control(button.dataset.window); });
  $('search').oninput = () => navigate(page);
  $('startup').onchange = async () => { const input = $('startup'); input.disabled = true; const result = await act('startup', {value:input.checked}); if (!result.ok) input.checked = !input.checked; input.disabled = false; };
  $('skin-apply').onclick = () => act('skin_apply', {id:selectedSkin});
  $('edit-colors').onclick = () => { $('color-editor').hidden = !$('color-editor').hidden; };
  all('[data-color-role]').forEach(button => { button.onclick = () => { $('color-editor').hidden = false; $('color-editor').querySelector(`[data-role="${button.dataset.colorRole}"]`)?.click(); }; });
  $('save-skin').onclick = () => { $('skin-name-form').hidden = false; $('skin-name').focus(); };
  $('cancel-skin-name').onclick = () => { $('skin-name-form').hidden = true; };
  $('skin-name-form').onsubmit = async event => { event.preventDefault(); const result = await act('skin_save', {id:selectedSkin, label:$('skin-name').value, colors:draftColors}); if (result.ok) $('skin-name-form').hidden = true; };
  $('delete-skin').onclick = () => confirmAction('Delete this custom skin?', 'MO will return to its default skin if this skin is in use.', 'skin_delete', {id:selectedSkin});
  $('chrome-refresh').onclick = () => act('chrome_status');
  $('media-refresh').onclick = () => act('media_status');
  $('media-install').onclick = () => confirmAction('Install the optional reference helper?', 'MO will download Cloudflare’s helper from its official release and verify its checksum. No tunnel, upload or paid job starts now.', 'media_install');
  $('media-key-save').onclick = async () => {
    let value = $('media-key').value; $('media-key').value = '';
    try {
      const pendingSave = window.pywebview.api.save_media_key(value); value = '';
      const result = await pendingSave; status(result.message, !result.ok);
      if (result.ok) await act('media_status');
    } catch (_error) { status('The key was not saved. Reopen Settings and try again.', true); }
  };
  $('chrome-remove').onclick = () => confirmAction('Remove the Chrome bridge?', 'Connected Tab will be unavailable until the bridge is installed again. MO may reinstall it on its next startup.', 'chrome_remove');
  $('restart').onclick = () => confirmAction('Restart MO Desktop?', 'Your Desktop windows will close and the companion will start again.', 'restart');
  $('reset').onclick = () => confirmAction('Reset Desktop preferences?', 'Cube, panel, movement and voice preferences will return to their defaults. Other MO configuration stays as it is.', 'reset');
  $('refresh-audio').onclick = loadDevices;
  $('about').onclick = () => status(`MO Settings${state?.version ? ' · ' + state.version : ''} · preferences are saved by their existing MO owners.`);
  let refreshInFlight = false;
  window.moSettingsRefresh = async () => { if (refreshInFlight) return; refreshInFlight = true; $('refresh').disabled = true; try { await act('snapshot'); } finally { refreshInFlight = false; $('refresh').disabled = false; } };
  $('refresh').onclick = window.moSettingsRefresh;
  $('resize-grip').onpointerdown = event => {
    const start = {x:event.screenX, y:event.screenY, width:innerWidth, height:innerHeight}; let latest, frame;
    const grip = event.currentTarget; grip.setPointerCapture(event.pointerId);
    grip.onpointermove = next => { latest = next; if (!frame) frame = requestAnimationFrame(() => { frame = null; window.pywebview.api.window_control('resize', Math.round(start.width + latest.screenX - start.x), Math.round(start.height + latest.screenY - start.y)); }); };
    const stop = () => { grip.onpointermove = null; }; grip.onpointerup = stop; grip.onlostpointercapture = stop;
  };
  async function start() { await window.moSettingsRefresh(); window.pywebview.api.ui_ready(); }
  if (window.pywebview?.api) start(); else window.addEventListener('pywebviewready', start, {once:true});
})();
