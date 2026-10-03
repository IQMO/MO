/* Preview runtime and manual editor. Composed inline by the native renderer. */
function previewRuntime(initial) {
  const root=document.getElementById('design-root'), style=document.getElementById('design-style');
  style.textContent=initial.css+(initial.editing?'\n*,*::before,*::after{animation-play-state:paused!important;transition:none!important}':'');root.innerHTML=initial.html;
  let edits=initial.edits||[], selected=null, gesture=null, queued=0, last='', lastMissing=-1,resizeTimer=0;
  const originals=new Map(), addresses=new WeakMap(), identities=new WeakMap();
  const send=(type,payload={})=>parent.postMessage({type,token:initial.token,...payload},'*');
  const identity=node=>node.dataset.moId?'key:'+node.dataset.moId:node.id?'id:'+node.id:[node.tagName,node.getAttribute('class')||'',node.getAttribute('aria-label')||'',node.children.length?'':node.textContent.trim().slice(0,160)].join('|').slice(0,512);
  function selector(node){
    if(addresses.has(node))return addresses.get(node);
    if(node.dataset.moSelector){addresses.set(node,node.dataset.moSelector);identities.set(node,node.dataset.moIdentity);return node.dataset.moSelector;}
    let result='';
    for(let current=node;current&&current!==root;current=current.parentElement){
      const key=current.dataset.moId, id=current.id;
      if(key||id){const attr=key?'data-mo-id':'id',value=key||id,part=`[${attr}="${CSS.escape(value)}"]`;if(root.querySelectorAll(part).length===1){result=part+(result?' > '+result:'');break;}}
      const siblings=[...current.parentElement.children].filter(row=>row.tagName===current.tagName), part=current.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(current)+1)+')';
      result=part+(result?' > '+result:'');
    }
    addresses.set(node,result);identities.set(node,identity(node));return result;
  }
  function find(address){
    for(const node of root.querySelectorAll('[data-mo-selector]'))if(node.dataset.moSelector===address)return node;
    try{return root.querySelector(address);}catch(_error){return null;}
  }
  function remember(node,row){
    if(!originals.has(node))originals.set(node,{style:node.getAttribute('style'),text:node.children.length?null:node.textContent,identity:identity(node)});
    addresses.set(node,row.selector);identities.set(node,row.identity);
  }
  function matches(node,row){return node&&node.tagName.toLowerCase()===row.tag&&(node.dataset.moIdentity===row.identity||identity(node)===row.identity||originals.get(node)?.identity===row.identity);}
  function apply(){
    observer.disconnect();let missing=0;
    for(const node of originals.keys())if(!node.isConnected)originals.delete(node);
    for(const row of edits){
      const node=find(row.selector);if(!matches(node,row)){missing++;continue;}remember(node,row);
      for(const [key,value] of Object.entries(row.style||{}))if(node.style.getPropertyValue(key)!==value)node.style.setProperty(key,value,'important');
      if(Object.hasOwn(row,'text')&&!node.children.length&&node.textContent!==row.text)node.textContent=row.text;
    }
    if(!initial.editing)observer.observe(root,{subtree:true,childList:true,attributes:true,characterData:true});
    if(missing!==lastMissing){lastMissing=missing;send('mo-preview-status',{missing});}
    outline();
  }
  const observer=new MutationObserver(()=>{if(!queued)queued=requestAnimationFrame(()=>{queued=0;apply();});});
  function restore(){for(const [node,base] of originals){if(!node.isConnected)continue;if(base.style===null)node.removeAttribute('style');else node.setAttribute('style',base.style);if(base.text!==null)node.textContent=base.text;}}
  const overlay=document.createElement('div');
  if(initial.editing){
    overlay.style.cssText='position:fixed;pointer-events:none;z-index:2147483647;box-sizing:border-box;border:2px solid '+initial.brand+';display:none';
    const handle=document.createElement('button');handle.ariaLabel='Resize selected element';handle.dataset.moResize='true';handle.style.cssText='position:absolute;right:-7px;bottom:-7px;width:14px;height:14px;border:2px solid '+initial.brand+';background:'+initial.surface+';padding:0;cursor:nwse-resize;pointer-events:auto';overlay.append(handle);document.body.append(overlay);
  }
  function outline(){if(!selected||!selected.isConnected){overlay.style.display='none';return;}const rect=selected.getBoundingClientRect();Object.assign(overlay.style,{display:'block',left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'});}
  function describe(){
    if(!selected)return;const css=getComputedStyle(selected), rect=selected.getBoundingClientRect(), values={};
    for(const key of initial.properties||[])values[key]=css.getPropertyValue(key);
    const nodes=[...root.querySelectorAll('*')].filter(node=>!['SCRIPT','STYLE','SVG','PATH'].includes(node.tagName)).slice(0,512);
    send('mo-edit-selection',{selection:{selector:selector(selected),tag:selected.tagName.toLowerCase(),identity:identities.get(selected)||identity(selected),text:selected.children.length||selected.matches('input,textarea,select')?null:selected.textContent,style:values,width:rect.width,height:rect.height},layers:nodes.map(node=>({selector:selector(node),label:(node.getAttribute('aria-label')||node.id||node.tagName.toLowerCase()+' '+(node.children.length?'':node.textContent.trim())).slice(0,70)}))});outline();
  }
  function change(values){
    if(!selected)return;const address=selector(selected);let row=edits.find(item=>item.selector===address);
    if(!row){if(edits.length>=512)return;row={selector:address,tag:selected.tagName.toLowerCase(),identity:identities.get(selected)||identity(selected),style:{}};edits.push(row);}
    remember(selected,row);Object.assign(row.style,values.style||{});restore();
    for(const [key,value] of Object.entries(row.style))if(!value)delete row.style[key];
    if(Object.hasOwn(values,'text')&&!selected.children.length&&!selected.matches('input,textarea,select'))row.text=String(values.text).slice(0,8000);
    if(!Object.keys(row.style).length&&!Object.hasOwn(row,'text'))edits=edits.filter(item=>item!==row);
    apply();send('mo-edit-change',{edits});describe();
  }
  window.addEventListener('message',event=>{
    if(event.source!==parent||event.data?.token!==initial.token)return;const data=event.data;
    if(data.type==='mo-preview-capture'){
      const copy=root.cloneNode(true), nodes=[...root.querySelectorAll('*')], clones=[...copy.querySelectorAll('*')];
      nodes.forEach((node,index)=>{const clone=clones[index];clone.dataset.moSelector=selector(node);clone.dataset.moIdentity=identities.get(node)||identity(node);const base=originals.get(node);if(base){if(base.style===null)clone.removeAttribute('style');else clone.setAttribute('style',base.style);if(base.text!==null)clone.textContent=base.text;}});
      send('mo-preview-captured',{html:copy.innerHTML});return;
    }
    if(!initial.editing)return;
    if(data.type==='mo-edit-flush'){send('mo-edit-flushed',{request:data.request,edits});return;}
    if(data.type==='mo-edit-select'){selected=find(data.selector);describe();}
    if(data.type==='mo-edit-set')change(data.values||{});
    if(data.type==='mo-edit-history'){restore();edits=data.edits||[];apply();describe();}
    if(data.type==='mo-edit-reset'&&selected){const address=selector(selected);restore();edits=edits.filter(row=>row.selector!==address);apply();send('mo-edit-change',{edits});describe();}
  });
  if(initial.editing){
    document.addEventListener('pointerdown',event=>{
      const resize=event.target.dataset.moResize==='true';if(!resize&&!root.contains(event.target))return;
      event.preventDefault();event.stopImmediatePropagation();if(!resize)selected=event.target;
      if(selected===root||selected.matches('script,style,svg,path')){selected=null;return;}
      selector(selected);describe();const css=getComputedStyle(selected), offset=css.translate==='none'?[0,0]:css.translate.split(' ').map(parseFloat),rect=selected.getBoundingClientRect();
      gesture={id:event.pointerId,x:event.clientX,y:event.clientY,resize,offset,width:parseFloat(css.width)||rect.width,height:parseFloat(css.height)||rect.height,style:selected.getAttribute('style'),changed:false};event.target.setPointerCapture(event.pointerId);
    },true);
    document.addEventListener('pointermove',event=>{
      if(!gesture)return;const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y;if(!gesture.changed&&Math.hypot(dx,dy)<4)return;
      gesture.changed=true;const values=gesture.resize?{width:Math.max(8,Math.round(gesture.width+dx))+'px',height:Math.max(8,Math.round(gesture.height+dy))+'px'}:{translate:Math.round((gesture.offset[0]||0)+dx)+'px '+Math.round((gesture.offset[1]||0)+dy)+'px'};
      gesture.values=values;for(const [key,value] of Object.entries(values))selected.style.setProperty(key,value,'important');outline();
    },true);
    function finish(cancel){if(!gesture)return;const active=gesture;gesture=null;if(active.style===null)selected.removeAttribute('style');else selected.setAttribute('style',active.style);if(active.changed&&!cancel)change({style:active.values});outline();}
    document.addEventListener('pointerup',()=>finish(false),true);document.addEventListener('pointercancel',()=>finish(true),true);
    document.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();},true);
    document.addEventListener('keydown',event=>{if(event.key==='Escape')finish(true);if((event.ctrlKey||event.metaKey)&&['z','y','s'].includes(event.key.toLowerCase())){event.preventDefault();send('mo-edit-shortcut',{key:event.key.toLowerCase(),shift:event.shiftKey});}},true);
    window.addEventListener('resize',()=>{outline();clearTimeout(resizeTimer);resizeTimer=setTimeout(describe,120);});document.addEventListener('scroll',outline,true);
  }else{
    document.addEventListener('click',event=>{const node=event.target.closest('button,a,input,select,textarea,[role="button"]');if(node)last=(node.getAttribute('aria-label')||node.textContent||node.tagName).trim().slice(0,120);},true);
    window.addEventListener('error',event=>send('mo-preview-error',{detail:String(event.message||'Preview script failed').slice(0,600),interaction:last}));
  }
  apply();
  if(!initial.editing&&initial.script){const script=document.createElement('script');script.textContent=initial.script;document.body.appendChild(script);apply();}
  send('mo-preview-ready');
}

function createPreviewEditor() {
  let mode=false,capturing=false,source='',base=null,target=null,edits=[],saved='[]',undo=[],redo=[],selection=null,timer=0,saving=null,failure='',flushId=0,captureId=0;
  const flushes=new Map();
  const pendingFields=new Set();
  const properties=['translate','width','height','padding','margin','gap','border-radius','font-size','font-weight','color','background-color','text-align','opacity'];
  const frame=()=>$('preview-frame'), message=(type,payload={})=>frame().contentWindow?.postMessage({type,token:state.previewToken,...payload},'*');
  const dirty=()=>JSON.stringify(edits)!==saved;
  function status(text){const unsaved=dirty()||pendingFields.size>0;$('edit-status').textContent=text||failure||(unsaved?'Unsaved changes':'Saved');$('edit-save').disabled=!unsaved||!!saving;$('edit-undo').disabled=!undo.length;$('edit-redo').disabled=!redo.length;}
  function history(stack,value){stack.push(clone(value));while(stack.length>40||JSON.stringify(stack).length>4000000)stack.shift();}
  function changed(next){if(JSON.stringify(next)===JSON.stringify(edits)){status();return;}history(undo,edits);redo=[];edits=clone(next);status();clearTimeout(timer);timer=setTimeout(()=>save(false),1200);}
  async function save(flush=true){
    if(mode&&flush){
      const field=document.activeElement;if(field?.matches('#edit-text,[data-edit-property]'))field.blur();
      if(pendingFields.size){for(const input of pendingFields)input.reportValidity();return false;}
      if(!document.hidden){const request=++flushId;const flushed=await new Promise(resolve=>{const timeout=setTimeout(()=>{flushes.delete(request);resolve(false);},1500);flushes.set(request,()=>{clearTimeout(timeout);resolve(true);});message('mo-edit-flush',{request});});if(!flushed){failure='The editor is not ready. Try Save again.';status();return false;}}
    }
    clearTimeout(timer);if(saving&&!await saving)return false;if(!dirty())return true;
    const candidate=clone(edits), serialized=JSON.stringify(candidate),bound={...target};
    saving=(async()=>{try{const result=await window.pywebview.api.preview_save(bound.id,bound.revision,candidate);if(!result.ok)throw new Error(result.message||'Could not save edits');target={id:result.meta.id,revision:result.meta.revision};saved=serialized;failure='';state.revision=result.revision;update(result);return true;}catch(error){failure=error.message;showRevisionError(error);return false;}finally{saving=null;status();}})();
    return saving;
  }
  function close(){mode=false;capturing=false;source='';base=null;selection=null;failure='';pendingFields.clear();clearTimeout(timer);$('preview-inspector').hidden=true;$('edit-tools').hidden=true;$('preview-refresh').disabled=false;$('preview-edit').setAttribute('aria-pressed','false');$('preview-edit').textContent='Edit design';document.body.classList.remove('editing-preview');state.rendered='';if(state.payload)renderArtifact(state.payload.design);}
  async function leave(){if(!mode)return true;do{if(!await save())return false;}while(dirty()||saving);close();return true;}
  $('preview-edit').addEventListener('click',async()=>{
    if(mode){await leave();return;}if(capturing||state.working||!state.payload?.history?.is_current)return;
    if(state.boardDraft){showRevisionError(new Error('Accept or reject the Board draft before editing Preview.'));return;}
    if(state.boardDirty&&!(await checkpointBoard()).ok)return;
    capturing=true;target={id:state.payload.meta.id,revision:state.payload.meta.revision};message('mo-preview-capture');status('Opening editor…');
    const request=++captureId;setTimeout(()=>{if(capturing&&request===captureId){capturing=false;showRevisionError(new Error('The preview is not ready to edit. Reload it and try again.'));}},1500);
  });
  $('edit-save').addEventListener('click',save);
  $('edit-reload').addEventListener('click',()=>{if((dirty()||pendingFields.size)&&!confirm('Discard unsaved edits and reload the saved design?'))return;edits=[];saved='[]';close();});
  document.addEventListener('keydown',event=>{if(!mode||!(event.ctrlKey||event.metaKey))return;const key=event.key.toLowerCase();if(key==='s'){event.preventDefault();save();}else if(!event.target.matches('input,textarea')&&['z','y'].includes(key)){event.preventDefault();key==='y'||event.shiftKey?travel(redo,undo):travel(undo,redo);}});
  function travel(from,to){if(!from.length)return;history(to,edits);edits=from.pop();message('mo-edit-history',{edits});status();clearTimeout(timer);timer=setTimeout(()=>save(false),1200);}
  $('edit-undo').addEventListener('click',()=>travel(undo,redo));$('edit-redo').addEventListener('click',()=>travel(redo,undo));
  $('edit-reset').addEventListener('click',()=>message('mo-edit-reset'));
  $('edit-layers').addEventListener('change',event=>message('mo-edit-select',{selector:event.target.value}));
  for(const input of document.querySelectorAll('#edit-text,[data-edit-property]'))input.addEventListener('input',()=>{pendingFields.add(input);input.setCustomValidity('');status();});
  for(const input of document.querySelectorAll('[data-edit-property]'))input.addEventListener('change',()=>{
    const key=input.dataset.editProperty,value=input.value.trim();if(value&&!CSS.supports(key,value)){input.setCustomValidity('Enter a valid value, such as 24px, 50%, or auto.');input.reportValidity();return;}input.setCustomValidity('');pendingFields.delete(input);message('mo-edit-set',{values:{style:{[key]:value}}});
  });
  $('edit-text').addEventListener('change',event=>{pendingFields.delete(event.target);message('mo-edit-set',{values:{text:event.target.value}});});
  window.addEventListener('message',event=>{
    const data=event.data;if(event.source!==frame().contentWindow||data?.token!==state.previewToken)return;
    if(data.type==='mo-edit-flushed'){flushes.get(data.request)?.();flushes.delete(data.request);return;}
    if(data.type==='mo-preview-captured'&&capturing){
      capturing=false;if(!sameBoardTarget(target,boardTargetValue())||String(data.html||'').length>600000){showRevisionError(new Error('This preview changed or is too large to edit. Reload it first.'));return;}
      source=sanitizeMarkup(String(data.html||''));base=clone(state.payload.design);edits=clone(base.edits||[]);saved=JSON.stringify(edits);undo=[];redo=[];mode=true;failure='';$('preview-refresh').disabled=true;
      $('preview-inspector').hidden=false;$('edit-tools').hidden=false;$('edit-empty').hidden=false;$('edit-fields').hidden=true;$('preview-edit').setAttribute('aria-pressed','true');$('preview-edit').textContent='Done';document.body.classList.add('editing-preview');state.rendered='';renderArtifact(state.payload.design);status();
    }
    if(data.type==='mo-preview-status'){state.previewMissing=Number(data.missing)||0;$('preview-edit-note').textContent=state.previewMissing?`${state.previewMissing} saved edit${state.previewMissing===1?' is':'s are'} not present on this screen.`:'';if(state.previewMissing&&!mode){$('toast').className='toast error';$('toast').textContent=$('preview-edit-note').textContent;}}
    if(!mode)return;
    if(data.type==='mo-edit-change'&&Array.isArray(data.edits))changed(data.edits);
    if(data.type==='mo-edit-selection'){
      selection=data.selection;$('edit-empty').hidden=true;$('edit-fields').hidden=false;$('edit-element').textContent=selection.tag;
      const select=$('edit-layers');select.replaceChildren();for(const row of data.layers||[]){const option=document.createElement('option');option.value=row.selector;option.textContent=row.label;select.append(option);}select.value=selection.selector;
      for(const input of document.querySelectorAll('[data-edit-property]'))if(document.activeElement!==input)input.value=selection.style[input.dataset.editProperty]||'';
      $('edit-text').disabled=selection.text===null;if(document.activeElement!==$('edit-text'))$('edit-text').value=selection.text??'';
    }
    if(data.type==='mo-edit-shortcut'){if(data.key==='s')save();else if(data.key==='y'||data.shift)travel(redo,undo);else travel(undo,redo);}
  });
  return {
    active:()=>mode, save, leave, properties,
    content:design=>mode?{...base,html:source,script:'',edits}:design,
    sync(payload){$('preview-edit').disabled=!mode&&(!!payload.conversation?.pending||!payload.history?.is_current);$('preview-refresh').disabled=mode;if(mode&&!sameBoardTarget(target,boardTargetValue(payload)))failure='The saved design changed. Reload before editing further.';status();},
  };
}
