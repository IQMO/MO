(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  let state = null, lastFocus = null, feedbackTimer = null, toastTimer = null, pairingTimer = null, deviceSignature = '', drawerKind = '';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function notice(message) {
    clearTimeout(toastTimer);
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 2400);
  }
  async function call(method, ...args) {
    try {
      const result = await window.pywebview.api[method](...args);
      if (result?.accepted === false) notice('Wait for the current phone action to finish.');
      if (result?.devices) update(result);
      return result;
    } catch (error) {
      const message = error?.message || 'The phone action could not finish.';
      $('#context-note>span:not(.note-icon)').textContent = message;
      $('#context-note').classList.add('is-error');
      notice(message);
      return null;
    }
  }
  function feedback(kind = 'feedback') {
    clearTimeout(feedbackTimer);
    const art = $('#device-art');
    art.classList.remove('feedback','pen-feedback','frame-feedback');
    void art.offsetWidth;
    art.classList.add(kind);
    feedbackTimer = setTimeout(() => art.classList.remove(kind), 750);
  }
  function update(payload) {
    if (!payload || !Array.isArray(payload.devices)) return;
    const previous = state;
    state = payload;
    const ready = state.connection === 'device', busy = Boolean(state.busy);
    const mode = state.connection === 'unauthorized' ? 'authorize' : ready ? state.transport.startsWith('Wi-Fi') ? 'wifi' : 'usb' : state.connection;
    $('#phone-app').dataset.state = mode;
    const signature = JSON.stringify(state.devices);
    if (signature !== deviceSignature) {
      deviceSignature = signature;
      const select = $('#device-select');
      select.replaceChildren();
      for (const device of state.devices) {
        const option = document.createElement('option');
        option.value = device.serial;
        option.textContent = device.label + (device.state === 'device' ? ' · ' + (device.serial.includes(':') ? 'Wi-Fi' : 'USB') : '');
        select.appendChild(option);
      }
      if (!state.devices.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = state.discovered ? 'No phone connected' : 'Looking for connected phones…';
        select.appendChild(option);
      }
    }
    $('#device-select').value = state.serial;
    $('#device-select').disabled = busy || !state.devices.length;
    const paired = state.hub.inventory === 'Observed' ? `${state.hub.devices.length} paired` : state.hub.inventory === 'Not checked' ? 'Paired phones not checked' : 'Paired phones unavailable';
    $('#phone-count').textContent = `${paired} · ${state.devices.length} ADB ${state.devices.length === 1 ? 'connection' : 'connections'}`;
    $('#device-name').textContent = state.label;
    $('#transport').textContent = state.serial ? state.transport.startsWith('Wi-Fi') ? 'Wi-Fi' : 'USB' : 'No link';
    const descriptions = {
      loading:'Looking for connected phones…', none:'Connect your phone, then authorize this computer on its screen.',
      unauthorized:'Allow this computer on your phone’s screen.', offline:'Check the cable or reconnect your phone.',
      device:state.transport.startsWith('Wi-Fi') ? 'Connected wirelessly. Choose what you want to do.' : 'Connected by USB. Choose what you want to do.',
    };
    $('#device-description').textContent = descriptions[state.connection] || 'Refresh the connection to continue.';
    const connectionLabel = ready ? 'Connected' : state.connection === 'unauthorized' ? 'Needs authorization' : state.connection === 'offline' ? 'Offline' : state.discovered ? 'Not connected' : 'Looking for phones';
    $('#device-ready-label').textContent = connectionLabel;
    $('#footer-status').textContent = ready ? state.transport + ' connected' : connectionLabel;
    $('#header-status').textContent = busy ? ({refresh:'Looking for phones…',check_connection:'Checking MO connection…',pair_phone:'Creating one-use QR…',wifi:'Connecting over Wi-Fi…',mirror:'Updating mirroring…',trackpad:'Updating Trackpad…',frame:'Keeping a frame…'}[state.busy] || 'Working…') : state.error ? 'Phone needs attention' : ready ? 'Your phone is ready' : connectionLabel;
    $('#host-label').textContent = state.host_label;
    $('#host-status').dataset.tone = state.host_tone;
    $('#hub-label').textContent = state.hub.label;
    $('#hub-status').dataset.tone = state.hub.tone;
    $('#hub-status').title = state.hub.checked_at ? 'Last checked ' + new Date(state.hub.checked_at * 1000).toLocaleTimeString() : 'Not checked yet';
    $('#screen-caption').textContent = state.mirror ? 'MIRRORING' : state.trackpad ? 'TRACKPAD' : ready ? 'READY' : state.connection === 'unauthorized' ? 'AUTHORIZE' : state.connection === 'offline' ? 'OFFLINE' : 'WAITING';
    $('#mirror-button').disabled = busy || (!state.mirror && (!ready || !state.scrcpy_available));
    $('#trackpad-button').disabled = busy || (!state.trackpad && (!ready || !state.adb_available));
    $('#capture-button').disabled = busy || !ready || !state.adb_available;
    $('#fullscreen').checked = state.fullscreen;
    $('#fullscreen').disabled = busy || state.mirror;
    $('[data-action=refresh]').disabled = busy;
    $('[data-action=wifi]').disabled = busy;
    $('[data-action=files]').disabled = !state.files_available;
    for (const name of ['mirror','trackpad']) {
      const active = state[name], starting = state.busy === name;
      const available = ready && (name === 'mirror' ? state.scrcpy_available : state.adb_available);
      const connected = name !== 'trackpad' || state.trackpad_connection === 'connected';
      $('#'+name+'-state').textContent = starting ? 'Working…' : active ? `${connected ? 'Active' : 'Waiting'} · ${state[name+'_device']}` : available ? 'Ready' : state.discovered ? 'Unavailable' : 'Waiting';
      $('#'+name+'-state').title = active ? state[name+'_device'] : '';
      $('#'+name+'-card').classList.toggle('is-active',active);
      $('#'+name+'-card').classList.toggle('is-starting',starting);
      $('#'+name+'-button>span:not(.glyph):not(.button-trailing)').textContent = active ? 'Stop '+(name === 'mirror' ? 'mirroring' : 'trackpad') : starting ? 'Working…' : 'Start '+(name === 'mirror' ? 'mirroring' : 'trackpad');
    }
    $('#context-note>span:not(.note-icon)').textContent = state.message;
    $('#context-note').classList.toggle('is-error',Boolean(state.error));
    $('#capture-count').textContent = state.captures ? `${state.captures} kept this session · MO Files` : 'Open in MO Files';
    if (previous && previous.connection !== state.connection) feedback();
    if (previous && previous.captures < state.captures) feedback('frame-feedback');
    if (previous && !previous.trackpad && state.trackpad) feedback('pen-feedback');
    if (drawerKind === 'diagnostics' && !$('#drawer').hidden) renderDiagnostics();
    if (drawerKind === 'access' && !$('#drawer').hidden) renderAccess();
    if (!state.pairing_expires_at) clearPairing();
  }
  window.moPhoneUpdate = update;
  window.moPhoneApplyTheme = css => { $('#mo-phone-theme').textContent = css; };
  function dismiss() {
    if ($('#drawer').hidden) return;
    $('#drawer').hidden = true;
    $('#scrim').hidden = true;
    drawerKind = '';
    clearPairing();
    if (state?.pairing_expires_at) call('dismiss_pairing');
    lastFocus?.focus();
    lastFocus = null;
  }
  function drawer(title, content, eyebrow = 'PHONE') {
    if ($('#drawer').hidden) lastFocus = document.activeElement;
    drawerKind = '';
    clearPairing();
    if (state?.pairing_expires_at) call('dismiss_pairing');
    $('#drawer-title').textContent = title;
    $('#drawer-eyebrow').textContent = eyebrow;
    $('#drawer-content').innerHTML = content;
    $('#scrim').hidden = false;
    $('#drawer').hidden = false;
    $('#drawer [data-action=dismiss]').focus();
  }
  function row(title, detail, value, muted = false) {
    return `<div class="detail-row"><div><strong>${escape(title)}</strong><small>${escape(detail)}</small></div><span class="detail-value${muted?' muted':''}">${escape(value)}</span></div>`;
  }
  function connectionDetails() {
    drawer('Connection details', '<p>The selected phone and MO’s host connection are separate.</p>' +
      row('Selected phone',state.label,state.connection === 'device' ? 'Connected' : state.connection) +
      row('Phone transport','Screen, input and frame capture',state.transport) +
      row('MO host','Existing Desktop host connection',state.host_label,state.host_tone !== 'ok') +
      '<div class="drawer-caption">A working ADB or Hub link does not establish Android permission grants.</div><button class="button" data-action="wifi">Wi-Fi setup</button><button class="button" data-action="access">MO setup & pairing</button>', 'CONNECTION');
  }
  function accessDetails() {
    drawer('Phones & access','<section class="pairing-card"><div class="pairing-heading"><strong>Your phones</strong><span>CONNECTIONS</span></div><div id="phone-inventory"></div><p>To mirror another phone, connect it by USB, authorize this computer on that phone, then Refresh and select its connection above. Wi-Fi setup applies to the selected phone.</p><button class="button" data-action="refresh">Refresh ADB connections</button></section><div id="access-status"></div><section class="pairing-card"><div class="pairing-heading"><strong>Add to MO Everywhere</strong><span>ONE USE</span></div><p>For another phone, create a fresh QR and scan it in that phone’s MO Everywhere pairing screen. It receives its own identity; your other phones stay paired. USB mirroring needs no Hub QR. An existing phone only needs a new QR when replacing its Hub credentials.</p><div class="pairing-image" id="pairing-image" hidden></div><small id="pairing-caption">A QR is created only when you ask. It expires in five minutes.</small><div class="pairing-actions"><button class="button primary" data-action="pair-phone">Create pairing QR</button><button class="button" data-action="check-connection">Refresh paired phones</button></div></section><div id="access-grants"></div>', 'ACCESS');
    drawerKind = 'access';
    renderAccess();
  }
  function setupStep(title, detail, command = '') {
    return `<div class="setup-step"><strong>${escape(title)}</strong><p>${escape(detail)}</p>${command ? `<pre class="setup-command">${escape(command)}</pre>` : ''}</div>`;
  }
  function renderAccess() {
    const checked = state.hub.checked_at ? ' Last checked ' + new Date(state.hub.checked_at * 1000).toLocaleTimeString() + '.' : '';
    $('#phone-inventory').innerHTML = '<p class="access-caption">ADB connections · '+state.devices.length+'</p>' +
      (state.devices.length ? state.devices.map(device => row(device.label,device.serial.includes(':') ? 'Wi-Fi ADB' : 'USB ADB',device.state === 'device' ? 'Connected' : device.state,device.state !== 'device')).join('') : '<p>No ADB phone detected. Connect one and Refresh.</p>') +
      '<p class="access-caption">Paired with MO'+(state.hub.inventory === 'Observed' ? ' · '+state.hub.devices.length : '')+'</p>' +
      (state.hub.inventory === 'Observed' ? state.hub.devices.length ? state.hub.devices.map(device => {
        const hostGrant = device.capability === 'control' && ['remote_control','remote_host'].every(scope => device.scopes.includes(scope));
        return row(device.label,'ID …'+device.device_id.slice(-6)+' · Last seen '+new Date(device.last_seen_at * 1000).toLocaleString(),hostGrant ? 'Phone-host grant' : 'Companion');
      }).join('') : '<p>No Android phones paired with this Hub yet.</p>' : '<p>'+escape(state.hub.inventory)+'. Refresh paired phones to check.</p>') +
      '<p class="access-caption">USB and Wi-Fi may be two connections to the same phone. Paired entries are Hub identities, not proof of a live connection. Phone tools stay bound to the phone that sends the MO request.</p>';
    $('#drawer-content [data-action=refresh]').disabled = Boolean(state.busy);
    $('#access-status').innerHTML = row('Serving Hub',state.hub.detail + checked,state.hub.label,state.hub.tone !== 'ok') +
      row('Desktop coordinator','Required to create a phone QR',state.hub.coordinator,state.hub.coordinator !== 'Verified') +
      row('Desktop Live Control','Independent host credential',state.host_label,state.host_tone !== 'ok') +
      state.hub.steps.map(step => setupStep(step.title,step.detail,step.command)).join('') +
      (state.host_tone === 'ok' ? '' : hostSetup());
    const pairing = $('[data-action=pair-phone]');
    pairing.disabled = Boolean(state.busy) || !state.hub.can_pair;
    pairing.textContent = state.busy === 'pair_phone' ? 'Creating QR…' : state.pairing_expires_at ? 'Replace pairing QR' : 'Create pairing QR';
    $('[data-action=check-connection]').disabled = Boolean(state.busy);
    $('#access-grants').innerHTML = '<p class="access-caption">Pairing does not grant Android permissions automatically. Review them in the phone app.</p>' +
      row('Live Control','Exact pairing and phone consent','Check on phone',true) +
      row('Semantic control','Accessibility and text consent','Check on phone',true) +
      row('Phone files','Granted storage scopes in MO Files','Check on phone',true) +
      row('Privileged tools','Independent Shizuku authority','Check on phone',true);
    if (!state.hub.can_pair) $('#pairing-caption').textContent = state.busy ? 'Checking connection…' : 'Complete the setup steps above, then check connection to enable the QR.';
    else if (!state.pairing_expires_at) $('#pairing-caption').textContent = 'One-use companion QR · expires in five minutes. Phone-host control requires its separate explicit grant.';
  }
  function hostSetup() {
    if (state.host_status === 'disabled' || state.host_status === 'stopped') {
      return setupStep('Enable Desktop Live Control if needed','For remote control of this PC, enable consistent_everywhere.live_control.enabled and its host.enabled setting, then restart MO Desktop. USB mirroring, Trackpad and phone QR pairing remain independent.');
    }
    if (state.host_label === 'Host needs pairing') {
      return setupStep('Pair the separate Live Control host','The Hub can be online while this host credential is missing or rejected. On the serving Hub, issue its exact host grant:',
        'python -m mo_everywhere.cli pair --capability notify --scope remote_host') +
        setupStep('Join the Live Control host from this PC','Use that fresh host code. Keep the Desktop coordinator credential separate; the running host detects its replacement.',
        'python -m mo_everywhere.cli join --as-live-host --hub https://YOUR_HUB --code ONE_TIME_CODE --label "My MO host"');
    }
    return setupStep('Check the Live Control connection',state.host_tone === 'warn' ? 'The existing Desktop host is linking. Its own connection worker handles reconnects.' : 'Check the existing Hub service and WSS proxy, and confirm the Live Control host settings. The Hub health check and this host’s connection are separate.');
  }
  function clearPairing() {
    clearTimeout(pairingTimer);
    pairingTimer = null;
    const box = $('#pairing-image');
    if (box) { box.replaceChildren(); box.hidden = true; }
  }
  window.moPhonePairingImage = (uri, expiresAt) => {
    clearPairing();
    const box = $('#pairing-image'), lifetime = expiresAt * 1000 - Date.now();
    if (drawerKind !== 'access' || !box || lifetime <= 0) { call('dismiss_pairing'); return; }
    const image = document.createElement('img');
    image.alt = 'One-use MO Everywhere companion pairing QR';
    image.src = uri;
    box.appendChild(image);
    box.hidden = false;
    $('#pairing-caption').textContent = 'Scan before ' + new Date(expiresAt * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) + ' · valid once. MO’s permission review remains on the phone.';
    pairingTimer = setTimeout(() => {
      clearPairing();
      call('dismiss_pairing');
      if ($('#pairing-caption')) $('#pairing-caption').textContent = 'This QR expired. Create a new one to continue.';
    }, Math.min(lifetime, 2147483647));
  }
  function wifi() {
    drawer('Connect over Wi-Fi','<p>Connect and authorize your phone by USB once, with both devices on your private network. MO verifies the wireless endpoint before the cable can come out.</p><div class="connection-step"><span>1</span><p>Authorize this computer on the phone.</p></div><div class="connection-step"><span>2</span><p>Connect with the existing private Wi-Fi flow. Only the deliberately paired phone can be re-armed automatically.</p></div><button class="button primary" data-action="wifi-connect">Connect over Wi-Fi</button>', 'CONNECTION');
    $('[data-action=wifi-connect]').disabled = Boolean(state.busy) || !state.adb_available;
  }
  function renderDiagnostics() {
    const focused = document.activeElement?.dataset.action;
    $('#drawer-content').innerHTML = '<p>Current device and tool observations. Results update here when discovery finishes.</p>' +
      row('Device discovery',state.label,state.busy === 'refresh' ? 'Refreshing…' : state.discovered ? 'Observed' : 'Pending') +
      row('Phone connection',state.transport,state.connection) +
      row('Android platform-tools','Existing ADB transport',state.adb_available ? 'Found' : 'Unavailable',!state.adb_available) +
      row('Mirroring tool','scrcpy owns video and phone input',state.scrcpy_available ? 'Found' : 'Unavailable',!state.scrcpy_available) +
      row('Trackpad','Direct Android app and selected ADB tunnel',state.trackpad ? state.trackpad_connection === 'connected' ? 'Connected' : 'Waiting for phone' : 'Stopped',!state.trackpad) +
      row('Serving Hub',state.hub.detail,state.hub.label,state.hub.tone !== 'ok') +
      row('Desktop host','Independent Live Control connection',state.host_label,state.host_tone !== 'ok') +
      '<div class="drawer-caption">'+escape(state.error || state.message)+'</div><button class="button" data-action="diagnose">Refresh diagnostics</button>';
    if (focused === 'diagnose') $('#drawer-content [data-action=diagnose]').focus();
  }
  async function diagnose() {
    drawer('Phone diagnostics', '', 'DIAGNOSTICS');
    drawerKind = 'diagnostics';
    renderDiagnostics();
    await call('refresh');
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || button.disabled || !state) return;
    const action = button.dataset.action;
    if (['mirror','trackpad','frame'].includes(action)) { await call('perform',action); return; }
    if (action === 'files') { await call('open_files'); return; }
    if (action === 'refresh') { await call('refresh'); return; }
    if (action === 'connection') { connectionDetails(); return; }
    if (action === 'access' || action === 'pair') { accessDetails(); if (!state.hub.checked_at) await call('check_connection'); return; }
    if (action === 'check-connection') { await call('check_connection'); return; }
    if (action === 'pair-phone') { await call('pair_phone'); return; }
    if (action === 'wifi') { wifi(); return; }
    if (action === 'wifi-connect') { dismiss(); await call('perform','wifi'); return; }
    if (action === 'diagnose') { await diagnose(); return; }
    if (action === 'dismiss') { dismiss(); return; }
    if (['pin','minimize','close','maximize'].includes(action)) {
      const result = await call('window_control',action === 'maximize' ? 'toggle_maximize' : action);
      if (action === 'pin' && typeof result?.on_top === 'boolean') button.setAttribute('aria-pressed',String(result.on_top));
    }
  });
  $('#device-select').addEventListener('change', event => { call('select_device',event.target.value); });
  $('#fullscreen').addEventListener('change', event => { call('set_fullscreen',event.target.checked); });
  $('#scrim').addEventListener('click',dismiss);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { dismiss(); return; }
    if (event.key === 'Tab' && !$('#drawer').hidden) {
      const elements = [...$('#drawer').querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled)')];
      const first = elements[0], last = elements[elements.length-1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(toastTimer); clearTimeout(feedbackTimer); $('#toast').hidden = true; clearPairing(); if (state?.pairing_expires_at) call('dismiss_pairing'); }
  });
  let resizing = false, startX = 0, startY = 0, startW = 0, startH = 0;
  $('#resize-grip').addEventListener('pointerdown', event => {
    resizing = true; startX = event.screenX; startY = event.screenY; startW = innerWidth; startH = innerHeight;
    $('#resize-grip').setPointerCapture(event.pointerId);
  });
  $('#resize-grip').addEventListener('pointermove', event => {
    if (resizing) call('window_control','resize',startW+event.screenX-startX,startH+event.screenY-startY);
  });
  $('#resize-grip').addEventListener('pointerup', () => { resizing = false; });
  async function ready() {
    const payload = await call('snapshot');
    if (payload) await call('ui_ready');
  }
  window.addEventListener('pywebviewready',ready,{once:true});
})();
