// Local presentation only; business mutations return to their existing MO owners.
let state=initial, project='0', view='now', editor=null, selectedInstance='';
let poll=null, noticeTimer=null, graphRequest=0, selectionRevision=0;
let ruleRequest=0, knowledgeRequest=0, skillsRequest=0, skillReadRequest=0, checksRequest=0, pendingAction=null, usageFetched=0;
let actionBusy=false, refreshing=null, learningBusy=false;
let mailRequest=0, mailProvider='gmail', selectedMail=null, mailQuery='', pendingMailMove=null;
let mailSetupRequest=0;
let mailMessages=[], lifeItems=[], lifeLinkedCounts={}, lifeDraft=null, lifeScheduled=undefined, lifeScheduledTotal=0, lifeSchedulerEnabled=false, mailFolderRequest=0;
let moneyEntries=[], moneySummary=null, moneyTimeline=null, moneyDraft=null, moneyRequest=0;
const lifeReview={};
const rendered=new Map();
const $=id=>document.getElementById(id);
function text(id,value){
  const next=String(value??'');
  if($(id).textContent!==next)$(id).textContent=next;
}
let token=location.hash.slice(1)||sessionStorage.getItem('mo-dashboard-connection')||'';
if(initial.connected&&location.hash){
  sessionStorage.setItem('mo-dashboard-connection',token);
  history.replaceState(null,'',location.pathname);
}
function notice(value){
  text('notice',value);$('notice').hidden=false;
  clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').hidden=true,8000);
}
async function api(path,body){
  if(!initial.connected)throw Error('Read-only export. Use /dashboard show for connected controls.');
  const response=await fetch('/api/'+path,{
    method:body?'POST':'GET',
    headers:{'Authorization':'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},
    body:body?JSON.stringify(body):undefined,cache:'no-store'
  });
  const result=await response.json();
  if(!response.ok)throw Error(result.error||'Source unavailable');
  return result;
}
function row(title,detail,control){
  const line=document.createElement('div'),content=document.createElement('div');
  const heading=document.createElement('h3'),sub=document.createElement('p');
  line.className='line';heading.textContent=title;sub.textContent=detail;
  content.append(heading,sub);line.append(content);
  if(control)line.append(control);
  return line;
}
function button(label,fn){
  const b=document.createElement('button');b.textContent=label;b.onclick=fn;return b;
}
function updateList(id,data,fill){
  const signature=JSON.stringify(data);
  if(rendered.get(id)===signature)return;
  const target=$(id),opened=new Set([...target.querySelectorAll('details[open]')].map(d=>d.dataset.key));
  target.replaceChildren();fill(target);
  for(const detail of target.querySelectorAll('details'))detail.open=opened.has(detail.dataset.key);
  rendered.set(id,signature);
}
function graphVisibility(){
  $('map').contentWindow?.postMessage({type:'mo-graph-visibility',visible:view==='now'&&!document.hidden},'*');
}
function show(next){
  document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!=='view-'+next);
  const main=['work','rules','knowledge','checks'].includes(next)?'now':next;
  document.querySelectorAll('nav button').forEach(el=>el.setAttribute('aria-selected',String(el.dataset.view===main)));
  view=next;document.body.classList.remove('graph-expanded');text('expand-map','Expand graph');
  graphVisibility();
  if(next==='work')loadUsage();
  if(next==='learning'){loadLearning();loadSkills();}
  if(next==='rules')rules();
  if(next==='knowledge')knowledge();
  if(next==='checks')loadChecks();
  if(next==='controls')renderControls();
  if(next==='now'&&!$('map').srcdoc)loadGraph();
  if(next==='email')return loadMail(mailProvider);
  if(next==='life'){loadLife();loadMoney();loadLifeReview();}
}
async function action(id,instance=selectedInstance){
  if(!initial.connected){notice('Read-only export. Use /dashboard show in MO for connected controls.');return;}
  if(actionBusy||pendingAction){notice('Waiting for the selected terminal');return;}
  const revision=selectionRevision;actionBusy=true;
  try{
    const result=await api('action',{id,project,instance});
    if(revision!==selectionRevision)return;
    notice(result.message||'Opened in MO');
    if(result.instance)selectedInstance=result.instance;
    if(result.pending&&result.instance){
      pendingAction={id,instance:result.instance,started:Date.now()};
      clearTimeout(poll);poll=setTimeout(refresh,1000);
    }
    if(result.terminals){
      const choices=$('terminal-choices');choices.replaceChildren();
      for(const t of result.terminals)choices.append(button(t.label+' · '+t.instance,()=>{
        choices.replaceChildren();selectedInstance=t.instance;action(id,t.instance);
      }));
      choices.scrollIntoView({block:'nearest'});
    }
  }catch(e){if(revision===selectionRevision)notice(e.message);}
  finally{actionBusy=false;}
}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>show(b.dataset.view));
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>action(b.dataset.action));

function render(){
  const s=state.snapshot||{},env=s.environment||{},runtime=s.runtime||{},work=s.work||{};
  const projects=state.projects||[{id:'0',name:(env.project||'Current project').split(/[\\/]/).pop(),available:true}];
  updateList('project',projects,list=>{
    for(const p of projects){
      const option=document.createElement('option');option.value=p.id;option.textContent=p.name;option.disabled=!p.available;list.append(option);
    }
  });
  $('project').value=project;
  text('runtime-state',runtime.provider?'connected · '+runtime.provider:'not observed');
  text('updated',s.generated_at?'Observed '+new Date(s.generated_at).toLocaleTimeString():'');
  const resources=state.resources||{};
  const percent=n=>typeof n==='number'?n.toFixed(1)+'%':'not measured';
  text('machine-state','CPU '+percent(resources.system_cpu_percent)+' · RAM '+percent(resources.memory_percent));
  document.querySelectorAll('[data-action]').forEach(b=>b.disabled=!initial.connected);
  renderLiveWork();
  renderHistory(work.recent_boards||[]);
  const evidence=[...(state.evidence||[])];
  updateList('source-evidence',[evidence,s.graph],list=>{
    for(const section of evidence){
      const detail=document.createElement('details'),title=document.createElement('summary');
      detail.className='detail';detail.dataset.key=section.title;title.textContent=section.title;detail.append(title);
      for(const item of section.rows){
        const p=document.createElement('p');p.textContent=[item.text,item.sub,item.meta].filter(Boolean).join(' · ');detail.append(p);
      }
      list.append(detail);
    }
    const graph=s.graph||{},counts=value=>Object.entries(value||{}).filter(([,n])=>n).map(([k,n])=>k+' '+n).join(', ');
    list.append(row('Graph confidence',counts(graph.confidence_breakdown)||'not measured'),
      row('Graph provenance',counts(graph.provenance_breakdown)||'not measured'),
      row('Graph freshness',graph.available?(graph.stale?'Stale orientation':'Current at last inspection')+' · '+(graph.observed_at?new Date(graph.observed_at*1000).toLocaleTimeString():'snapshot time'):'Not built'));
  });
  const health=state.health||[],provider=health.find(h=>h.label==='Selected provider');
  text('system-runtime',provider?.detail||[runtime.provider||'Not observed',runtime.model,runtime.session_slot].filter(Boolean).join(' · '));
  for(const [label,key,meter]of [['cpu','system_cpu_percent','cpu-meter'],['memory','memory_percent','memory-meter']]){
    const value=resources[key];text('system-'+label,percent(value));
    $(meter).hidden=!Number.isFinite(value);if(Number.isFinite(value))$(meter).value=value;
  }
  const notices=activeSystemNotices(health);
  const diagnostics=health.filter(h=>h!==provider&&!notices.includes(h));
  updateList('system-summary',diagnostics,list=>{
    for(const h of diagnostics)list.append(row(h.label,h.detail));
  });
  updateList('system-notices',notices,list=>{
    for(const h of notices)list.append(row(h.label,h.detail));
  });
  const learning=s.learning||{},counts=[['behavior_rules','profile rule'],['profile_facts','fact'],
    ['operator_terms','term'],['generated_learning_skills','learned skill'],['memory_turns','remembered turn']];
  text('learning-state','Learning & skills');
  updateList('learning-overview',counts.map(([key,label])=>[learning[key],label]),list=>{
    for(const [key,label]of counts)if(learning[key]>0){
      const span=document.createElement('span'),number=document.createElement('b');number.textContent=Number(learning[key]).toLocaleString();
      span.append(number,' '+label+(learning[key]===1?'':'s'));list.append(span);
    }
    if(!list.children.length)list.textContent='No saved learning counts reported. Skills and source availability are listed below.';
  });
  if(view==='controls')renderControls();
}
async function loadMail(provider){
  if(!initial.connected||!['gmail','outlook'].includes(provider))return;
  mailProvider=provider;selectedMail=null;mailMessages=[];
  loadMailSetup(provider);
  pendingMailMove=null;mailFolderRequest++;$('mail-move-form').hidden=true;
  $('mail-detail').replaceChildren(row('Select a message','Read it here without sending its content to a model.'));
  for(const control of document.querySelectorAll('[data-provider]'))
    control.setAttribute('aria-pressed',String(control.dataset.provider===provider));
  const request=++mailRequest,label=provider==='gmail'?'Gmail':'Outlook.com';
  text('mail-glance-status','Loading '+label+'…');$('mail-glance-rows').replaceChildren();
  try{
    const result=await api('mail/glance',{provider,query:mailQuery});
    if(request!==mailRequest)return;
    const messages=Array.isArray(result.messages)?result.messages:[];
    mailMessages=messages;
    rememberLifeReview(provider,messages);
    const count=provider==='gmail'
      ?[(Number.isInteger(result.unread)?result.unread+' unread':''),
        (Number.isInteger(result.inbox_estimate)?result.inbox_estimate+(mailQuery?' estimated matches':' estimated in Inbox'):'')].filter(Boolean).join(' · ')
      :'Connected Tab · counts unavailable';
    text('mail-glance-status',label+(count?' · '+count:''));
    const setting=$('mail-notification-setting');setting.replaceChildren();
    if(provider==='gmail'){
      const toggle=button(result.notifications_enabled?'Notifications on':'Notifications off',async()=>{
        toggle.disabled=true;
        try{await api('mail/notifications',{enabled:!result.notifications_enabled});loadMail('gmail');}
        catch(error){notice(error.message);toggle.disabled=false;}
      });
      toggle.setAttribute('aria-pressed',String(result.notifications_enabled));setting.append(toggle);
    }
    renderMailRows();
  }catch(error){
    if(request===mailRequest){text('mail-glance-status',label+' unavailable');text('mail-candidate-summary','');$('mail-glance-rows').append(row('Could not load mail',error.message));}
  }
}
async function loadMailSetup(provider){
  const request=++mailSetupRequest;
  try{
    const result=await api('mail/setup',{provider,action:'status'});
    if(request===mailSetupRequest&&provider===mailProvider)renderMailSetup(provider,result);
  }catch(error){if(request===mailSetupRequest&&provider===mailProvider)
    renderMailSetup(provider,{error:error.message});}
}
function setupLink(label,url){
  const link=document.createElement('a');link.textContent=label;link.href=url;
  link.target='_blank';link.rel='noopener noreferrer';return link;
}
async function mailSetupAction(action,payload={}){
  const provider=mailProvider,controls=$('mail-setup-actions');
  for(const control of controls.querySelectorAll('button'))control.disabled=true;
  text('mail-setup-detail','Working… complete any account consent in your browser.');
  try{
    await api('mail/setup',{provider,action,...payload});
    $('mail-client-id').value='';$('mail-client-secret').value='';
    await loadMailSetup(provider);
    if(provider===mailProvider&&(action==='connect'||action==='prepare'))loadMail(provider);
  }catch(error){notice(error.message);await loadMailSetup(provider);}
}
function renderMailSetup(provider,status){
  const section=$('mail-setup'),actions=$('mail-setup-actions'),form=$('mail-client-form');
  actions.replaceChildren();form.hidden=true;text('mail-setup-note','');
  if(provider==='gmail'){
    if(['connected','sync_unknown'].includes(status.state)){section.hidden=true;return;}
    section.hidden=false;text('mail-setup-title','Connect Gmail');
    if(status.state==='disabled'){
      text('mail-setup-detail','Enable Gmail for this MO profile to start setup.');
      actions.append(button('Enable Gmail',()=>mailSetupAction('enable')));
    }else if(status.state==='client_missing'){
      text('mail-setup-detail','Create a Google Desktop OAuth client for your account, then enter its ID and secret below. MO saves them only in your private profile and starts browser consent next.');
      form.hidden=false;
      actions.append(setupLink('Google setup steps','https://developers.google.com/workspace/gmail/api/quickstart/python'),
        setupLink('Google Cloud credentials','https://console.cloud.google.com/apis/credentials'));
      text('mail-setup-note','In your Google project, enable Gmail API, configure OAuth consent for your account and gmail.modify, then create a Desktop app client. For a personal account in Testing, add yourself as a test user. Follow only the Google project/client steps in the guide; MO handles the OAuth callback.');
    }else if(['disconnected','reconnect_required'].includes(status.state)){
      text('mail-setup-detail','Your app client is ready. Google will ask you to approve Gmail access in the browser. Dashboard reading stays local; mail you ask Agent chat to read is sent to your configured model for its reply.');
      actions.append(button('Continue with Google',()=>mailSetupAction('connect')));
    }else text('mail-setup-detail',status.next_action||status.error||'Gmail setup is unavailable on this device.');
    return;
  }
  if(status.extension_connected&&status.bridge_live){section.hidden=true;return;}
  section.hidden=false;text('mail-setup-title','Connect Outlook.com');
  if(!status.installed){
    text('mail-setup-detail','Prepare MO’s native Connected Tab bridge, then load the extension in Chrome.');
    actions.append(button('Prepare Connected Tab',()=>mailSetupAction('prepare')));
  }else text('mail-setup-detail','MO’s bridge is ready. Load MO Connected Tab in Chrome, then sign in to Outlook Mail.');
  if(status.extension_dir){
    const path=document.createElement('input');path.readOnly=true;path.value=status.extension_dir;
    path.setAttribute('aria-label','MO Connected Tab extension folder');path.onclick=()=>path.select();
    actions.append(path);
  }
  actions.append(setupLink('Open Outlook Mail','https://outlook.live.com/mail/'));
  text('mail-setup-note','In chrome://extensions, enable Developer mode and Load unpacked using the folder above. MO attaches to the signed-in tab when needed.');
}
$('mail-client-form').onsubmit=async event=>{
  event.preventDefault();
  const client_id=$('mail-client-id').value,client_secret=$('mail-client-secret').value;
  await mailSetupAction('save_client',{client_id,client_secret});
};
function renderMailRows(){
  const target=$('mail-glance-rows');target.replaceChildren();
  const counts={payment:0,subscription:0,appointment:0,issue:0,case:0,other:0};
  for(const message of mailMessages)counts[message.candidate_group in counts?message.candidate_group:'other']++;
  const groups=Object.entries(counts).filter(([,count])=>count).map(([group,count])=>count+' '+group);
  const tracked=mailProvider==='gmail'&&mailMessages.length&&
    mailMessages.every(message=>typeof message.tracked==='boolean')
    ?mailMessages.filter(message=>message.tracked).length:null;
  text('mail-candidate-summary',[mailMessages.length+' visible',...groups,
    ...(tracked===null?[]:[tracked+' tracked in Life']),
    'wording hints only'].join(' · '));
  const filter=$('mail-group-filter').value;
  const visible=mailMessages.filter(message=>filter==='all'||(message.candidate_group||'other')===filter);
  for(const message of visible){
    const controls=document.createElement('div');controls.className='actions';
    for(const [label,action] of [['Open','open'],['Move','move'],['Delete','delete'],['Send','compose']]){
      const control=button(label,()=>action==='open'?readMail(message):mailAction(action,message));
      control.title=({open:'Open message',move:'Move message',delete:'Choose message and approve deletion in MO chat',compose:'Compose and send in MO'})[action];
      control.setAttribute('aria-label',control.title);controls.append(control);
    }
    const gmailMarkers=mailProvider==='gmail'?
      [message.provider_important?'Gmail Important':'',message.provider_unread?'Unread':'',
        message.provider_spam?'Gmail Spam':''].filter(Boolean):[];
    const line=row(String(message.title||'(no subject)'),
      [String(message.detail||''),...gmailMarkers].filter(Boolean).join(' · '),controls);
    line.classList.add('mail-row');line.dataset.messageId=String(message.id||'');
    if(message.candidate_group&&message.candidate_group!=='other'){
      const signal=document.createElement('small');signal.className='mail-signal';
      signal.dataset.time=String(Boolean(message.candidate_signal));
      signal.textContent='Possible '+message.candidate_group+
        (message.candidate_signal?' · time wording':'')+' · review message';
      line.firstChild.append(signal);
    }
    if(message.tracked===true){
      const badge=document.createElement('small');badge.className='mail-signal';
      badge.textContent='Tracked in Life';line.firstChild.append(badge);
    }
    target.append(line);
  }
  if(!visible.length)target.append(row(mailMessages.length?'No messages in this wording group':'No recent messages',''));
}
async function readMail(message){
  if(!message.id)return;
  const provider=mailProvider,request=++mailRequest;
  const previousStatus=$('mail-glance-status').textContent;
  text('mail-glance-status','Opening message…');
  try{
    const result=await api('mail/read',{provider,id:message.id,identity:message.identity});
    if(request!==mailRequest)return;
    selectedMail={provider,id:message.id,identity:message.identity};
    for(const line of document.querySelectorAll('.mail-row'))
      line.dataset.selected=String(line.dataset.messageId===String(message.id));
    const pane=$('mail-detail');pane.replaceChildren();
    const heading=document.createElement('h2');heading.textContent=result.subject||message.title||'(no subject)';
    const meta=document.createElement('p');meta.className='muted';meta.textContent=[result.from,result.date].filter(Boolean).join(' · ');
    const controls=document.createElement('div');controls.className='actions';
    controls.append(button('Archive',()=>mailAction('archive')));
    controls.append(button('Move',()=>mailAction('move')));
    const deleteButton=button('Delete in MO',()=>mailAction('delete'));
    deleteButton.title='Choose message and approve deletion in MO chat';controls.append(deleteButton);
    controls.append(button('Compose / send',()=>mailAction('compose')));
    if(message.tracked===true)controls.append(button('View Life',()=>show('life')));
    else controls.append(button('Track in Life',()=>openLifeForm(null,{
      title:result.subject||message.title||'',category:message.candidate_group||'other',
      source_provider:provider,source_id:provider==='gmail'?message.id:'',
    })));
    const body=document.createElement('pre');body.className='mail-body';body.textContent=result.body||'(empty message)';
    pane.append(heading,meta,controls,body);
    text('mail-glance-status',previousStatus);
  }catch(error){if(request===mailRequest){text('mail-glance-status',previousStatus);notice(error.message);}}
}
async function mailAction(action,message=null,folder=''){
  const target=message?(message.provider?message:{provider:mailProvider,id:message.id,identity:message.identity}):selectedMail;
  if(!target)return;
  if(action==='move'&&!folder){
    pendingMailMove=target;const request=++mailFolderRequest;
    text('mail-move-label','Move to existing '+(target.provider==='gmail'?'Gmail label':'Outlook folder'));
    const picker=$('mail-move-folder');picker.replaceChildren();picker.disabled=true;
    const waiting=document.createElement('option');waiting.value='';waiting.textContent='Loading destinations…';
    picker.append(waiting);$('mail-move-form').hidden=false;
    try{
      const result=await api('mail/folders',{provider:target.provider});
      if(request!==mailFolderRequest)return;
      const folders=Array.isArray(result.folders)?result.folders.filter(name=>typeof name==='string'&&name):[];
      picker.replaceChildren();
      const choose=document.createElement('option');choose.value='';choose.textContent='Choose destination';picker.append(choose);
      for(const name of folders){const option=document.createElement('option');option.value=name;option.textContent=name;picker.append(option);}
      if(!folders.length){pendingMailMove=null;$('mail-move-form').hidden=true;notice('No existing move destinations are available');return;}
      picker.disabled=false;picker.focus();
    }catch(error){if(request===mailFolderRequest){pendingMailMove=null;$('mail-move-form').hidden=true;notice(error.message);}}
    return;
  }
  try{
    const result=await api('mail/action',{...target,action,folder});
    const done=result.message||result.state||'Done';
    notice(done);
    text('mail-last-action','Last email action · '+done);
    if(['archive','move'].includes(action))loadMail(mailProvider);
  }catch(error){notice(error.message);}
}
$('mail-move-form').onsubmit=event=>{event.preventDefault();const target=pendingMailMove;
  const folder=$('mail-move-folder').value.trim();if(!target||!folder)return;
  pendingMailMove=null;$('mail-move-form').hidden=true;mailAction('move',target,folder);};
$('mail-move-cancel').onclick=()=>{pendingMailMove=null;mailFolderRequest++;$('mail-move-form').hidden=true;};
$('mail-gmail').onclick=()=>{mailQuery='';$('mail-search').value='';loadMail('gmail');};
$('mail-outlook').onclick=()=>{mailQuery='';$('mail-search').value='';loadMail('outlook');};
$('mail-refresh').onclick=()=>loadMail(mailProvider);
$('mail-compose').onclick=()=>mailAction('compose',{id:'',identity:''});
$('mail-search-form').onsubmit=event=>{event.preventDefault();mailQuery=$('mail-search').value.trim();loadMail(mailProvider);};
$('mail-inbox').onclick=()=>{mailQuery='';$('mail-search').value='';loadMail(mailProvider);};
$('mail-group-filter').onchange=renderMailRows;
const LIFE_GROUPS=[['payment','Payments and plans'],['subscription','Subscriptions'],
  ['appointment','Appointments'],['issue','Problems'],['case','Cases and paperwork'],['other','Other']];
function rememberLifeReview(provider,messages){
  const counts={},timeCounts={},trackedCounts={};
  for(const message of messages){const group=message.candidate_group;
    if(group&&group!=='other'){
      counts[group]=(counts[group]||0)+1;
      if(message.candidate_signal)timeCounts[group]=(timeCounts[group]||0)+1;
      if(message.tracked===true)trackedCounts[group]=(trackedCounts[group]||0)+1;
    }}
  lifeReview[provider]={checked:Date.now(),total:messages.length,counts,timeCounts,trackedCounts};
  if(view==='life')renderLifeReview();
}
function openLifeMail(provider,group='all'){
  mailProvider=provider;mailQuery='';$('mail-search').value='';$('mail-group-filter').value=group;
  show('email');
}
function renderLifeReview(){
  const target=$('life-review-rows');target.replaceChildren();
  for(const provider of ['gmail','outlook']){
    const label=provider==='gmail'?'Gmail':'Outlook.com',entry=lifeReview[provider];
    if(!entry||entry.loading){target.append(row(label,'Checking recent mail…'));continue;}
    if(entry.error){target.append(row(label,'Recent mail unavailable',
      button('Open Email',()=>openLifeMail(provider))));continue;}
    const groups=LIFE_GROUPS.filter(([group])=>group!=='other'&&entry.counts[group]);
    if(!groups.length){target.append(row(label,entry.total+' recent visible · no matching wording',
      button('Review Email',()=>openLifeMail(provider))));continue;}
    for(const [group,heading] of groups)target.append(row(label+' · '+heading,
      [entry.counts[group]+' possible in '+entry.total+' recent visible',
       entry.timeCounts[group]?(entry.timeCounts[group]+' with time wording'):null,
       provider==='gmail'&&entry.trackedCounts[group]?(entry.trackedCounts[group]+' tracked'):null,
       'wording only'].filter(Boolean).join(' · '),
      button('Review in Email',()=>openLifeMail(provider,group))));
  }
}
function loadLifeReview(force=false){
  for(const provider of ['gmail','outlook']){
    const current=lifeReview[provider];
    if(current?.loading||(!force&&current?.checked&&Date.now()-current.checked<60000))continue;
    const pending={loading:true};lifeReview[provider]=pending;renderLifeReview();
    api('mail/glance',{provider}).then(result=>{
      if(lifeReview[provider]===pending)
        rememberLifeReview(provider,Array.isArray(result.messages)?result.messages:[]);
    }).catch(()=>{
      if(lifeReview[provider]===pending){lifeReview[provider]={error:true};renderLifeReview();}
    });
  }
}
function localToday(){
  const now=new Date();return [now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),
    String(now.getDate()).padStart(2,'0')].join('-');
}
function openLifeForm(item=null,source=null){
  show('life');
  lifeDraft=item?{id:item.id,revision:item.revision}:(source||{source_provider:'operator',source_id:''});
  text('life-form-heading',item?'Edit tracked item':'Track an item');
  text('life-source',item?'Source · '+item.source_provider:
    source?'Source · '+source.source_provider+' · confirm details before saving':'Entered by you');
  $('life-title').value=item?.title||source?.title||'';
  $('life-category').value=item?.category||source?.category||'other';
  $('life-due').value=item?.due_date||'';
  $('life-expected-amount').value=item?.expected_amount||'';
  $('life-currency').value=item?.currency||'';
  $('life-frequency').value=item?.frequency||'once';
  $('life-installments').value=item?.installments_total||'';
  $('life-case-area').value=item?.case_area||'';
  $('life-reference').value=item?.reference||'';
  updateLifePlanFields();
  updateLifeCaseFields();
  $('life-notes').value=item?.notes||'';
  $('life-form').hidden=false;
  $('life-title').focus();
}
function closeLifeForm(){lifeDraft=null;$('life-form').hidden=true;}
function updateLifePlanFields(){
  const available=['payment','subscription'].includes($('life-category').value);
  $('life-plan-note').hidden=!available;
  for(const id of ['life-expected-amount','life-currency','life-frequency','life-installments']){
    const field=$(id);field.closest('label').hidden=!available;field.disabled=!available;
    if(!available)field.value=id==='life-frequency'?'once':'';
  }
}
function updateLifeCaseFields(){
  const available=$('life-category').value==='case';
  const group=document.querySelector('.life-case-fields');group.hidden=!available;
  for(const id of ['life-case-area','life-reference']){
    $(id).disabled=!available;if(!available)$(id).value='';
  }
}
$('life-category').onchange=()=>{updateLifePlanFields();updateLifeCaseFields();};
let caseUpdateDraft=null;
function openCaseUpdateForm(item){
  caseUpdateDraft={id:item.id,revision:item.revision};
  text('life-update-heading','Update · '+item.title);
  $('life-update-date').value=localToday();
  $('life-update-kind').value='conversation';
  $('life-update-reference').value='';
  $('life-update-summary').value='';
  $('life-update-form').hidden=false;
  $('life-update-summary').focus();
}
$('life-update-cancel').onclick=()=>{caseUpdateDraft=null;$('life-update-form').hidden=true;};
$('life-update-form').onsubmit=async event=>{
  event.preventDefault();if(!caseUpdateDraft)return;
  try{
    await api('life/items',{action:'add_update',...caseUpdateDraft,
      date:$('life-update-date').value,kind:$('life-update-kind').value,
      summary:$('life-update-summary').value,reference:$('life-update-reference').value});
    caseUpdateDraft=null;$('life-update-form').hidden=true;
    await loadLife();notice('Case update saved');
  }catch(error){notice(error.message);await loadLife();}
};
function openMoneyForm(entry=null,linkedItem=null){
  moneyDraft=entry?{id:entry.id,revision:entry.revision}:null;
  text('life-money-form-heading',entry?'Edit money entry':'Record money');
  $('money-title').value=entry?.title||linkedItem?.title||'';
  $('money-kind').value=entry?.kind||'expense';
  $('money-amount').value=entry?.amount||linkedItem?.expected_amount||'';
  $('money-currency').value=entry?.currency||linkedItem?.currency||moneyEntries[0]?.currency||'';
  $('money-date').value=entry?.date||localToday();
  $('money-category').value=entry?.category||(linkedItem?.category==='subscription'?'Subscriptions':linkedItem?'Debt':'');
  $('money-notes').value=entry?.notes||'';
  const link=$('money-life-item');link.replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='No linked commitment';link.append(empty);
  for(const item of lifeItems.filter(item=>['payment','subscription'].includes(item.category))){
    const option=document.createElement('option');option.value=item.id;option.textContent=item.title;link.append(option);
  }
  link.value=entry?.life_item_id||linkedItem?.id||'';
  if(!link.value&&entry?.life_item_id){
    const former=document.createElement('option');former.value=entry.life_item_id;
    former.textContent='Former commitment · choose another or unlink';link.append(former);link.value=former.value;
  }
  link.disabled=$('money-kind').value!=='expense';
  $('life-money-form').hidden=false;
  $('money-title').focus();
}
function closeMoneyForm(){moneyDraft=null;$('life-money-form').hidden=true;}
$('money-kind').onchange=()=>{
  const link=$('money-life-item');link.disabled=$('money-kind').value!=='expense';
  if(link.disabled)link.value='';
};
async function loadMoney(){
  if(!initial.connected)return;
  const request=++moneyRequest;
  try{
    const result=await api('life/money',{action:'list',month:$('life-money-month').value});
    if(request!==moneyRequest)return;
    moneyEntries=Array.isArray(result.entries)?result.entries:[];
    moneySummary=result.summary||null;
    moneyTimeline=result.timeline||null;
    renderMoney();
  }catch(error){if(request===moneyRequest){moneySummary=null;moneyTimeline=null;renderMoney();notice(error.message);}}
}
function renderMoneyTimeline(){
  const target=$('life-money-timeline');target.replaceChildren();
  if(!moneyTimeline){target.append(row('Timeline unavailable','Refresh to retry.'));return;}
  const months=moneyTimeline.months||[];
  const finalMonth=months[months.length-1];
  text('money-timeline-range','Six months through '+(finalMonth?
    new Date(finalMonth+'-01T12:00:00').toLocaleDateString(undefined,{month:'short',year:'numeric'}):''));
  const currencies=moneyTimeline.currencies||[];
  if(!currencies.length){
    const empty=document.createElement('p');empty.className='money-timeline-empty';
    empty.textContent='0 recorded entries · no currency yet.';target.append(empty);
  }
  for(const group of currencies.length?currencies:[{currency:'',points:months.map(month=>({month,income:'0',expense:'0'}))}]){
    const section=document.createElement('div');section.className='money-trend';
    if(group.currency){
      const heading=document.createElement('div');heading.className='money-trend-title';
      const currency=document.createElement('b');currency.textContent=group.currency;heading.append(currency);
      for(const [kind,label] of [['income','Income'],['expense','Outgoing']]){
        const legend=document.createElement('span'),swatch=document.createElement('i');
        swatch.dataset.kind=kind;legend.append(swatch,label);heading.append(legend);
      }
      section.append(heading);
    }
    const grid=document.createElement('div');grid.className='money-trend-grid';
    const maximum=Math.max(0,...group.points.flatMap(point=>[Number(point.income),Number(point.expense)]));
    for(const point of group.points){
      const tick=document.createElement('div'),bars=document.createElement('div'),label=document.createElement('time');
      tick.className='money-tick';bars.className='money-bars';
      for(const kind of ['income','expense']){
        const bar=document.createElement('i'),value=Number(point[kind]);bar.dataset.kind=kind;
        bar.style.setProperty('--height',(maximum&&value?Math.max(3,value/maximum*100):0)+'%');
        bar.title=kind+' '+point[kind]+(group.currency?' '+group.currency:'');bars.append(bar);
      }
      label.dateTime=point.month;label.textContent=point.month.slice(5)+'/'+point.month.slice(2,4);
      tick.append(bars,label);grid.append(tick);
    }
    section.append(grid);target.append(section);
  }
}
function renderMoney(){
  const summary=$('life-money-summary'),categories=$('life-money-categories'),entries=$('life-money-entries');
  summary.replaceChildren();categories.replaceChildren();entries.replaceChildren();
  renderMoneyTimeline();
  if(!moneySummary){entries.append(row('Money entries unavailable','Refresh to retry.'));return;}
  if(!moneySummary.count){entries.append(row('No recorded money this month',
    'Record an income or outgoing entry when you have confirmed it.'));return;}
  for(const group of moneySummary.currencies||[]){
    const card=document.createElement('div');card.className='money-card';
    const heading=document.createElement('h3');heading.textContent=group.currency;card.append(heading);
    for(const [label,value,kind] of [['Income',group.income,'income'],
      ['Outgoing',group.expense,'expense'],['Net recorded',group.net,'net']]){
      const line=document.createElement('div'),name=document.createElement('span'),amount=document.createElement('b');
      line.className='money-metric';line.dataset.kind=kind;name.textContent=label;
      amount.textContent=value+' '+group.currency;line.append(name,amount);card.append(line);
    }
    summary.append(card);
    const expenses=(group.categories||[]).filter(category=>category.kind==='expense');
    if(expenses.length){
      const section=document.createElement('div');section.className='money-category-group';
      const title=document.createElement('h3');title.textContent='Outgoing by category · '+group.currency;section.append(title);
      const total=Number(group.expense);
      for(const category of expenses){
        const line=document.createElement('div'),label=document.createElement('span'),value=document.createElement('b'),bar=document.createElement('i');
        line.className='money-category';label.textContent=category.category;
        value.textContent=category.total+' '+group.currency;
        bar.style.width=(total>0?Math.max(1,Math.min(100,Number(category.total)/total*100)):0)+'%';
        line.append(label,value,bar);section.append(line);
      }
      categories.append(section);
    }
  }
  for(const entry of moneyEntries){
    const controls=document.createElement('div');controls.className='actions';
    controls.append(button('Edit',()=>openMoneyForm(entry)));
    controls.append(button('Forget',()=>{
      controls.replaceChildren(button('Confirm forget',()=>forgetMoney(entry)),
        button('Cancel',renderMoney));
    }));
    const linked=entry.life_item_id?lifeItems.find(item=>item.id===entry.life_item_id):null;
    const detail=[entry.date,entry.kind==='income'?'Income':'Outgoing',
      entry.category,entry.amount+' '+entry.currency,
      entry.life_item_id?(linked?'For '+linked.title:'Former commitment'):null,
      entry.notes].filter(Boolean).join(' · ');
    const line=row(entry.title,detail,controls);line.className='line money-entry';
    line.dataset.kind=entry.kind;entries.append(line);
  }
}
async function forgetMoney(entry){
  try{await api('life/money',{action:'forget',id:entry.id,revision:entry.revision});
    await loadMoney();await loadLife();notice('Money entry forgotten');}
  catch(error){notice(error.message);await loadMoney();}
}
async function loadLife(){
  if(!initial.connected)return;
  try{
    const result=await api('life/items',{action:'list'});
    lifeItems=Array.isArray(result.items)?result.items:[];
    lifeLinkedCounts=result.linked_counts||{};
    lifeScheduled=Array.isArray(result.scheduled)?result.scheduled:null;
    lifeScheduledTotal=Number.isInteger(result.scheduled_total)?result.scheduled_total:null;
    lifeSchedulerEnabled=result.scheduler_enabled===true;
    renderLife();
  }catch(error){notice(error.message);}
}
function renderLife(){
  const open=lifeItems.filter(item=>item.status==='open'),today=localToday();
  const soon=new Date();soon.setDate(soon.getDate()+7);
  const soonDate=[soon.getFullYear(),String(soon.getMonth()+1).padStart(2,'0'),
    String(soon.getDate()).padStart(2,'0')].join('-');
  const overdue=open.filter(item=>item.due_date&&item.due_date<today).length;
  const dueSoon=open.filter(item=>item.due_date&&item.due_date>=today&&item.due_date<=soonDate).length;
  const summary=$('life-summary');summary.replaceChildren();
  for(const [value,label,state] of [[open.length,'Open','open'],[overdue,'Overdue','overdue'],
    [dueSoon,'Due soon','soon'],[lifeItems.length-open.length,'Done','done']]){
    const stat=document.createElement('span'),number=document.createElement('b'),caption=document.createElement('small');
    stat.className='life-stat';stat.dataset.state=state;number.textContent=value;caption.textContent=label;
    stat.append(number,caption);summary.append(stat);
  }
  const target=$('life-items');target.replaceChildren();
  renderLifeScheduled();
  if(!lifeItems.length){target.append(row('Nothing confirmed yet',
    'Review the mail hints above or add an item you want MO to remember.'));return;}
  for(const [category,heading] of LIFE_GROUPS){
    const items=lifeItems.filter(item=>item.category===category);
    if(!items.length)continue;
    const group=document.createElement('h2');group.className='life-group';group.textContent=heading;
    target.append(group);
    for(const item of items){
      const controls=document.createElement('div');controls.className='actions';
      if(item.source_provider==='gmail'&&item.source_id){
        controls.append(button('Open Gmail source',async()=>{
          mailProvider='gmail';mailQuery='';$('mail-search').value='';
          await show('email');await readMail({id:item.source_id,title:item.title});
        }));
      }else if(item.source_provider==='outlook'){
        controls.append(button('Search Outlook',()=>{
          mailProvider='outlook';mailQuery=item.title;$('mail-search').value=mailQuery;show('email');
        }));
      }
      controls.append(button('Edit',()=>openLifeForm(item)));
      if(item.status==='open')controls.append(button('Remind',()=>openScheduleForm(item)));
      if(item.category==='case')controls.append(button('Add update',()=>openCaseUpdateForm(item)));
      if(['payment','subscription'].includes(item.category))
        controls.append(button('Record outgoing',()=>openMoneyForm(null,item)));
      controls.append(button(item.status==='done'?'Reopen':item.category==='case'?'Resolve':'Done',
        ()=>changeLife(item,{status:item.status==='done'?'open':'done'})));
      const forget=button('Forget',()=>{
        controls.replaceChildren(button('Confirm forget',()=>forgetLife(item)),
          button('Cancel',()=>renderLife()));
      });controls.append(forget);
      const due=item.due_date?(item.status==='open'&&item.due_date<today?'Overdue '+item.due_date:
        'Due '+item.due_date):'No confirmed date';
      const plan=item.expected_amount?item.expected_amount+' '+item.currency+' expected per payment':'';
      const cadence=item.frequency&&item.frequency!=='once'?item.frequency:'';
      const recorded=Number(lifeLinkedCounts[item.id]||0);
      const progress=item.installments_total?recorded+'/'+item.installments_total+' recorded outgoings':
        recorded?recorded+' recorded outgoings':'';
      const detail=[item.category==='case'?(item.status==='done'?'Resolved':'Open'):item.status,
        item.case_area,item.reference?'Ref '+item.reference:null,due,plan,cadence,progress,item.source_provider==='operator'?'Entered by you':
        item.source_provider+' source',item.notes].filter(Boolean).join(' · ');
      const line=row(item.title,detail,controls);line.className='line life-item';
      line.dataset.state=item.status==='done'?'done':item.due_date&&item.due_date<today?'overdue':
        item.due_date&&item.due_date<=soonDate?'soon':'open';target.append(line);
      if(item.category==='case'){
        const updates=Array.isArray(item.updates)?item.updates:[];
        const history=document.createElement('details');history.className='case-history';
        const caption=document.createElement('summary');
        caption.textContent=updates.length?updates.length+' updates · latest '+updates[updates.length-1].date:
          'No updates yet';history.append(caption);
        for(const event of [...updates].reverse()){
          const entry=document.createElement('p');entry.textContent=[event.date,event.kind,event.summary,
            event.reference?'Ref '+event.reference:''].filter(Boolean).join(' · ');history.append(entry);
        }
        target.append(history);
      }
    }
  }
}
function renderLifeScheduled(){
  const target=$('life-scheduled-rows');target.replaceChildren();
  if(lifeScheduled===undefined){target.append(row('Checking scheduled tasks',''));return;}
  if(lifeScheduled===null){target.append(row('Scheduled tasks unavailable',''));return;}
  if(!lifeSchedulerEnabled)target.append(row('Scheduler is off',
    'Saved tasks will run when the scheduler is enabled in MO settings.'));
  if(!lifeScheduled.length){target.append(row('No scheduled tasks','Explicit reminders will appear here when you add them.'));return;}
  for(const job of lifeScheduled){
    const when=Number(job.next_run_at);
    const next=Number.isFinite(when)&&when>0?new Date(when*1000).toLocaleString():'No next run';
    const controls=document.createElement('div');controls.className='actions';
    controls.append(button('Run now',()=>changeSchedule(job,'run')),
      button(job.enabled?'Pause':'Resume',()=>changeSchedule(job,job.enabled?'pause':'resume')));
    controls.append(button('Remove',()=>{
      controls.replaceChildren(button('Confirm remove',()=>changeSchedule(job,'remove')),
        button('Cancel',renderLifeScheduled));
    }));
    const status=[job.kind==='reminder'?'Reminder':'Agent task',job.enabled?'Active':'Paused',next,
      job.last_status?'Last '+job.last_status:''].filter(Boolean).join(' · ');
    target.append(row(job.name||'Scheduled task',status,controls));
  }
  if(lifeScheduledTotal>lifeScheduled.length)target.append(row('More scheduled tasks',
    'Showing '+lifeScheduled.length+' of '+lifeScheduledTotal));
}
function openScheduleForm(item=null){
  const form=$('life-schedule-form');form.reset();
  $('life-schedule-name').value=item?item.title:'';
  $('life-schedule-prompt').value=item?'Remind me about '+item.title:'';
  form.hidden=false;form.scrollIntoView({block:'nearest'});
  $('life-schedule-when').focus();
}
function closeScheduleForm(){$('life-schedule-form').hidden=true;}
async function changeSchedule(job,action){
  try{
    await api('life/schedule',{action,job_id:job.id});
    await loadLife();notice(action==='run'?'Task queued for the next scheduler check':'Task '+action+'d');
  }catch(error){notice(error.message);await loadLife();}
}
async function changeLife(item,changes){
  try{await api('life/items',{action:'update',id:item.id,revision:item.revision,changes});await loadLife();}
  catch(error){notice(error.message);await loadLife();}
}
async function forgetLife(item){
  try{await api('life/items',{action:'forget',id:item.id,revision:item.revision});await loadLife();}
  catch(error){notice(error.message);await loadLife();}
}
$('life-new').onclick=()=>openLifeForm();
$('life-refresh').onclick=()=>{loadLife();loadMoney();loadLifeReview(true);};
$('life-cancel').onclick=closeLifeForm;
$('life-schedule-new').onclick=()=>openScheduleForm();
$('life-schedule-cancel').onclick=closeScheduleForm;
$('life-schedule-form').onsubmit=async event=>{
  event.preventDefault();
  try{
    const result=await api('life/schedule',{action:'create',name:$('life-schedule-name').value,
      kind:$('life-schedule-kind').value,schedule:$('life-schedule-when').value,
      prompt:$('life-schedule-prompt').value});
    closeScheduleForm();await loadLife();
    notice(result.scheduler_enabled?'Scheduled task saved':'Task saved; scheduler is off in MO settings');
  }catch(error){notice(error.message);}
};
$('life-money-month').value=localToday().slice(0,7);
$('life-money-month').onchange=loadMoney;
$('life-money-new').onclick=()=>openMoneyForm();
$('life-money-cancel').onclick=closeMoneyForm;
$('life-money-form').onsubmit=async event=>{
  event.preventDefault();
  const fields={title:$('money-title').value,kind:$('money-kind').value,
    amount:$('money-amount').value,currency:$('money-currency').value,
    date:$('money-date').value,category:$('money-category').value,notes:$('money-notes').value,
    life_item_id:$('money-life-item').value};
  try{
    if(moneyDraft)await api('life/money',{action:'update',id:moneyDraft.id,
      revision:moneyDraft.revision,changes:fields});
    else await api('life/money',{action:'create',...fields});
    closeMoneyForm();$('life-money-month').value=fields.date.slice(0,7);
    await loadMoney();await loadLife();notice('Money entry saved');
  }catch(error){notice(error.message);}
};
$('life-form').onsubmit=async event=>{
  event.preventDefault();
  const fields={title:$('life-title').value,category:$('life-category').value,
    due_date:$('life-due').value,notes:$('life-notes').value,
    case_area:$('life-case-area').value,reference:$('life-reference').value,
    expected_amount:$('life-expected-amount').value,currency:$('life-currency').value,
    frequency:$('life-frequency').value,installments_total:$('life-installments').value};
  const draft=lifeDraft||{source_provider:'operator',source_id:''};
  try{
    if(draft.id)await api('life/items',{action:'update',id:draft.id,revision:draft.revision,changes:fields});
    else await api('life/items',{action:'create',...fields,
      source_provider:draft.source_provider,source_id:draft.source_id});
    closeLifeForm();await loadLife();notice('Tracked item saved');
  }catch(error){notice(error.message);}
};
function activeSystemNotices(health){
  return (Array.isArray(health)?health:[]).filter(h=>['Provider notice','Interrupted work','SystemCare'].includes(h.label)&&
    !/^(clear|off|unqueried)(?:$| · | \()/i.test(h.detail));
}
function renderAttention(s,health){
  const target=$('attention-summary');if(!target)return;
  target.replaceChildren();
  if(!initial.connected){target.hidden=true;return;}
  const items=[];
  const terminals=(state.terminals||[]).filter(t=>t.project===project&&t.live);
  const chosen=terminals.find(t=>t.instance===selectedInstance)||(terminals.length===1?terminals[0]:null);
  const blocked=Number((s.work_learning||{}).task_blocked);
  if(project==='0'&&chosen?.host&&Number.isFinite(blocked)&&blocked>0)
    items.push([`${blocked} blocked task${blocked===1?'':'s'}`,'work']);
  const learning=s.learning||{},workLearning=s.work_learning||{};
  const directPending=Number(workLearning.pending_review);
  const suggestions=Number(learning.pending_suggestions),workflows=Number(learning.workflow_candidates);
  const pending=Number.isFinite(directPending)?directPending:
    Number.isFinite(suggestions)||Number.isFinite(workflows)?(Number.isFinite(suggestions)?suggestions:0)+(Number.isFinite(workflows)?workflows:0):NaN;
  if(Number.isFinite(pending)&&pending>0)
    items.push([`${pending} learning review${pending===1?'':'s'}`,'learning']);
  const notices=activeSystemNotices(health);
  const life=s.life||{},due=Number(life.overdue||0)+Number(life.due_soon||0);
  if(life.available&&due>0)items.push([`${due} life item${due===1?'':'s'} due`,'life']);
  if(notices.length)items.push([`${notices.length} system notice${notices.length===1?'':'s'}`,'system']);
  if(!items.length){target.hidden=true;return;}
  const heading=document.createElement('span');heading.className='attention-heading';heading.textContent='Needs attention';target.append(heading);
  for(const [label,viewName]of items){const control=button(label,()=>show(viewName));control.className='attention-item';target.append(control);}
  target.hidden=false;
}
function renderLiveWork(){
  const work=state.snapshot?.work||{},boards=[work.live_taskboard,work.latest_taskboard,work.resumable_board].filter(b=>b?.title);
  const terminals=(state.terminals||[]).filter(t=>t.project===project&&t.live);
  let chosen=terminals.find(t=>t.instance===selectedInstance);
  if(selectedInstance&&!chosen&&!pendingAction){selectedInstance='';notice('Selected terminal ended or moved; choose the current destination');}
  chosen=chosen||(terminals.length===1?terminals[0]:null);
  const board=chosen?.host?(boards.find(b=>b.open>0)||boards[0]):null;
  const name=(state.projects||[]).find(p=>p.id===project)?.name||'Project';
  text('work-label',chosen?'Live terminal · '+name:terminals.length?'Choose live work':'Workspace · '+name);
  text('work-title',board?.title||chosen?.title||(terminals.length?terminals.length+' live terminals':'Ready for your next task'));
  text('work-detail',chosen?[chosen.state,'instance '+chosen.instance,board?board.open+'/'+board.total+' tasks open':''].filter(Boolean).join(' · '):
    'Sources and project controls are available here. Open terminal starts normal MO only when you choose it.');
  document.querySelector('[data-action="stop"]').disabled=!initial.connected||!terminals.length;
  const tasks=board?.open_tasks||chosen?.tasks||[];
  updateList('current-work',tasks,list=>{
    for(const t of tasks)if(t.title){
      const line=row(t.title,[t.id,t.status,t.blocker].filter(Boolean).join(' · '));line.dataset.state=t.status||'';list.append(line);
    }
  });
  // A single live terminal is already represented by the heading; do not repeat it.
  const choices=terminals.length>1?terminals.map(({age,...t})=>t):[];
  updateList('live-work',[choices,selectedInstance],list=>{
    for(const t of choices){
      const line=row(t.title,t.state+' · '+t.instance,button('Use this terminal',()=>{
        selectedInstance=t.instance;renderLiveWork();
      }));
      line.dataset.selected=String(t.instance===selectedInstance);line.dataset.state=t.state;list.append(line);
    }
  });
  renderAttention(state.snapshot||{},state.health||[]);
}
function renderHistory(boards){
  const recent=boards.filter(b=>b.board_id!==state.snapshot?.work?.live_taskboard?.board_id);
  document.querySelector('.recent-heading').hidden=!recent.length;
  updateList('work-history',recent,list=>{
    for(const board of recent.slice(0,3))list.append(button(board.title+' · '+board.open+'/'+board.total+' open',()=>{
      show('work');
      const detail=[...$('work-records').children].find(d=>d.dataset.key===board.board_id);
      if(detail){detail.open=true;detail.scrollIntoView({block:'nearest'});}
    }));
  });
  updateList('work-records',boards,list=>{
    if(!boards.length)list.append(row('No retained taskboards','A task becomes recorded work through MO’s task and evidence owners.'));
    for(const board of boards){
      const detail=document.createElement('details'),summary=document.createElement('summary');
      detail.dataset.key=board.board_id;summary.textContent=[board.state,board.title,board.open+'/'+board.total+' open'].filter(Boolean).join(' · ');
      detail.append(summary);
      for(const task of board.tasks||[]){
        const p=document.createElement('p');p.textContent=[task.id,task.status,task.title,task.blocked_reason].filter(Boolean).join(' · ');detail.append(p);
      }
      const origin=document.createElement('p');
      origin.textContent='Board '+(board.board_id||'unrecorded')+' · session '+(board.session_id||'unrecorded')+' · turn '+(board.turn_id||'unrecorded');
      detail.append(origin);list.append(detail);
    }
  });
}
async function loadLearning(){
  if(!initial.connected||learningBusy)return;
  learningBusy=true;
  try{
    const data=await api('learning',{});
    updateList('learning-summary',data,list=>{
      if(!data.pending?.length&&!data.active?.length){
        const p=document.createElement('p');p.className='muted detail';p.textContent='Review queue clear · saved knowledge and skills remain available below.';list.append(p);return;
      }
      for(const [kind,label]of [['pending','Awaiting review'],['active','Active when relevant']]){
        if(!data[kind]?.length)continue;
        let target=list;
        const heading=document.createElement(kind==='active'?'summary':'h2');heading.textContent=label+' · '+data[kind+'_total'];
        if(kind==='active'){target=document.createElement('details');target.className='detail';target.dataset.key='active-learning';list.append(target);}
        target.append(heading);
        for(const item of data[kind])target.append(row(item.summary,item.label+' · '+item.state,button('Inspect',()=>inspectLearning(item))));
      }
    });
  }catch(e){notice(e.message);}
  finally{learningBusy=false;}
}
function inspectLearning(item){
  text('learning-text',item.details);$('learning-detail').hidden=false;
  const controls=$('learning-actions');controls.replaceChildren();
  const review=async action=>{
    for(const b of controls.children)b.disabled=true;
    try{
      const result=await api('learning/review',{ref:item.ref,action});
      notice(result.message);$('learning-detail').hidden=true;await loadLearning();loadSkills();refresh();
    }catch(e){notice(e.message);}
    finally{for(const b of controls.children)b.disabled=false;}
  };
  if(item.state==='pending')controls.append(button('Approve',()=>review('confirm')));
  controls.append(button(item.state==='active'?'Undo learning':'Dismiss',()=>review('dismiss')),
    button('Close details',()=>$('learning-detail').hidden=true));
  $('learning-detail').scrollIntoView({block:'nearest'});
}
async function loadSkills(){
  if(!initial.connected){text('skills-count','Connect to inspect sources');return;}
  const request=++skillsRequest,revision=selectionRevision;
  text('skills-count','Reading sources…');
  try{
    const data=await api('skills',{project,query:$('skills-query').value});
    if(request!==skillsRequest||revision!==selectionRevision)return;
    text('skills-count',data.items.length+' of '+data.total+' sources'+(data.total>data.items.length?' · narrow the search to find more':''));
    updateList('skills-list',data,list=>{
      if(!data.items.length){const p=document.createElement('p');p.className='muted detail';p.textContent='No matching skills in this project and profile.';list.append(p);}
      for(const item of data.items)list.append(row(item.name,[item.kind,item.ownership,item.state==='unavailable'?'Unavailable':item.description].filter(Boolean).join(' · '),
        button('Inspect source',()=>inspectSkill(item.id))));
    });
  }catch(e){if(request===skillsRequest&&revision===selectionRevision){text('skills-count','Sources unavailable');notice(e.message);}}
}
async function inspectSkill(id){
  const request=++skillReadRequest,revision=selectionRevision;
  $('skill-detail').hidden=true;
  try{
    const data=await api('skill/read',{project,id});
    if(request!==skillReadRequest||revision!==selectionRevision)return;
    text('skill-text',data.details);$('skill-detail').hidden=false;$('skill-detail').scrollIntoView({block:'nearest'});
  }catch(e){if(request===skillReadRequest&&revision===selectionRevision)notice(e.message);}
}
$('skills-form').onsubmit=event=>{event.preventDefault();loadSkills();};
$('close-skill').onclick=()=>{skillReadRequest++;$('skill-detail').hidden=true;};
async function loadLsp(){
  if(!initial.connected)return;
  const revision=selectionRevision;
  try{
    const status=await api('lsp',{project});if(revision!==selectionRevision)return;
    text('lsp-state','LSP · '+status.state);
    text('lsp-detail',[status.state,...(status.languages||[])].join(' · ')+(status.configured?' · Servers start on demand. This host has '+(status.running||0)+' started.':' · Configure servers in normal MO settings; nothing is installed automatically.'));
  }catch(e){if(revision===selectionRevision)text('lsp-state','LSP · unavailable');}
}
async function loadChecks(){
  if(!initial.connected){
    $('reload-checks').disabled=true;
    text('checks-summary','Recorded checks require the connected dashboard');return;
  }
  const request=++checksRequest,revision=selectionRevision,instance=selectedInstance;loadLsp();
  try{
    const data=await api('checks',{project,instance});
    if(request!==checksRequest||revision!==selectionRevision||instance!==selectedInstance)return;
    updateList('checks-summary',data,list=>{
      const graph=data.graph||{};
      list.append(row('Structural graph',graph.available?(graph.stale?'Stale orientation':'Current orientation')+' · '+graph.nodes+' nodes · '+graph.edges+' relationships':'Not built'),
        row('Check scope',data.scope+(data.session?' · session '+data.session:'')));
      for(const check of data.evidence?.checks||[])list.append(row(check.label,check.value));
      if(!data.evidence?.checks?.length)list.append(row('No recorded checks for this selection','Not a passing result. Open a live conversation to inspect its recorded checks.'));
    });
  }catch(e){if(request===checksRequest&&revision===selectionRevision&&instance===selectedInstance)notice(e.message);}
}
$('reload-checks').onclick=loadChecks;
function renderControls(){
  const filter=$('control-search').value.toLowerCase(),controls=(state.controls||[]).filter(c=>(c.label+' '+c.detail).toLowerCase().includes(filter));
  updateList('controls-list',controls,list=>{
    for(const c of controls)list.append(row(c.label,c.detail,button('Open in MO',()=>action(c.id))));
  });
}
$('control-search').oninput=renderControls;
function refresh(){
  if(refreshing)return refreshing;
  refreshing=refreshState().finally(()=>{refreshing=null;});
  return refreshing;
}
async function refreshState(){
  if(!initial.connected){text('connection','Read-only export');text('usage-scope','Usage requires the connected dashboard');render();return;}
  try{
    state=await api('state');
    text('connection','Connected · local');$('connection').dataset.state='live';render();
    await refreshNativeVisuals();
    if(view==='learning')loadLearning();
    if(view==='checks')await loadChecks();else loadLsp();
    if(pendingAction){
      const pending=pendingAction;
      if((state.terminals||[]).some(t=>t.live&&t.instance===pending.instance&&t.project===project)){
        pendingAction=null;await action(pending.id,pending.instance);
      }else if(Date.now()-pending.started>45000){
        pendingAction=null;selectedInstance='';notice('Terminal readiness was not observed. Check its window before retrying.');
      }
    }
    if(view==='work')loadUsage();
  }catch(e){text('connection','Disconnected · reopen in MO');$('connection').dataset.state='offline';notice(e.message);}
  finally{clearTimeout(poll);if(!document.hidden)poll=setTimeout(refresh,pendingAction?1000:10000);}
}
async function loadUsage(){
  if(!initial.connected||Date.now()-usageFetched<=60000)return;
  usageFetched=Date.now();
  try{
    const usage=await api('usage');
    updateList('usage-grid',usage.days||[],grid=>{
      const days=usage.days||[],peak=usage.peak_day||1;
      const first=days.length?new Date(days[0].date+'T12:00:00Z'):null;
      const leading=first&&!isNaN(first)?first.getUTCDay():0;
      for(let i=0;i<leading;i++){const gap=document.createElement('span');gap.dataset.outside='';grid.append(gap);}
      for(const [index,day]of days.entries()){
        const cell=document.createElement('span');cell.className='activity-cell';cell.tabIndex=index===days.length-1?0:-1;
        cell.style.setProperty('--intensity',day.tokens?String(20+80*Math.sqrt(day.tokens/peak))+'%':'0%');
        cell.title=day.date+' · '+day.tokens.toLocaleString()+' recorded tokens';cell.setAttribute('aria-label',cell.title);grid.append(cell);
      }
      for(let i=(leading+days.length)%7;i>0&&i<7;i++){const gap=document.createElement('span');gap.dataset.outside='';grid.append(gap);}
      text('usage-range',days.length?days[0].date+' — '+days.at(-1).date:'No retained receipts');
    });
    text('usage-scope',(usage.available?'':'No recorded usage available · ')+(usage.scope||'Retained local sessions')+(usage.skipped_snapshots?' · '+usage.skipped_snapshots+' snapshots unavailable/over scan limit':''));
    updateList('usage-totals',[usage.available,usage.total_tokens,usage.peak_day,usage.current_streak,usage.longest_streak],totals=>{
      if(!usage.available)return;
      const number=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1});
      for(const [value,label]of [[number.format(usage.total_tokens),'Recorded tokens'],[number.format(usage.peak_day),'Peak day'],[usage.current_streak+'d','Current streak'],[usage.longest_streak+'d','Longest streak']]){
        const item=document.createElement('div'),n=document.createElement('b'),caption=document.createElement('small');
        n.textContent=value;caption.textContent=label;item.append(n,caption);totals.append(item);
      }
    });
  }catch(e){text('usage-scope','Recorded usage unavailable');}
}
$('usage-grid').onkeydown=event=>{
  const offset={ArrowUp:-1,ArrowDown:1,ArrowLeft:-7,ArrowRight:7}[event.key];
  if(!offset)return;
  const cells=[...$('usage-grid').querySelectorAll('.activity-cell')],index=cells.indexOf(event.target),next=cells[index+offset];
  if(next){event.preventDefault();event.target.tabIndex=-1;next.tabIndex=0;next.focus();}
};
async function loadGraph(){
  const request=++graphRequest,revision=selectionRevision;
  // Keep an existing map painted while its source is reloaded.
  if(!$('map').srcdoc){$('map').hidden=true;$('map-empty').hidden=false;}
  text('map-state','Loading source…');
  if(!initial.connected){text('map-empty','Read-only export · use /dashboard map for the graph');return;}
  try{
    const result=await api('graph',{project});if(request!==graphRequest||revision!==selectionRevision)return;
    text('map-state',result.state);
    if(result.html){
      if($('map').srcdoc!==result.html)$('map').srcdoc=result.html;
      $('map').hidden=false;$('map-empty').hidden=true;
    }else{$('map').hidden=true;$('map').removeAttribute('srcdoc');$('map-empty').hidden=false;text('map-empty',result.state);}
  }catch(e){if(request===graphRequest)text('map-state',e.message);}
}
function selectProject(next){
  if(editor&&$('rule-text').value!==editor.text){$('project').value=project;notice('Save or close the open rule editor before changing project');return false;}
  selectionRevision++;ruleRequest++;knowledgeRequest++;skillsRequest++;skillReadRequest++;
  project=next;selectedInstance='';pendingAction=null;editor=null;
  $('rules-list').replaceChildren();text('knowledge-result','');$('rule-editor').hidden=true;$('terminal-choices').replaceChildren();
  $('skills-list').replaceChildren();rendered.delete('skills-list');$('skill-detail').hidden=true;
  $('map').removeAttribute('srcdoc');render();loadGraph();loadLsp();
  if(view==='rules')rules();
  if(view==='knowledge')knowledge();
  if(view==='checks')loadChecks();
  if(view==='learning')loadSkills();
  return true;
}
$('project').onchange=()=>selectProject($('project').value);
$('refresh-map').onclick=loadGraph;

$('expand-map').onclick=()=>{const expanded=document.body.classList.toggle('graph-expanded');text('expand-map',expanded?'Restore workspace':'Expand graph');};
function graphTheme(){
  // Share theme tokens, never overwrite the graph's layout/control CSS.
  const css=[...($('native-theme')?.textContent||'').matchAll(/:root\s*\{[^}]*\}/g)].map(match=>match[0]).join('\n');
  const radius=getComputedStyle(document.documentElement).getPropertyValue('--button-radius');
  $('map').contentWindow?.postMessage({type:'mo-graph-theme',css:css+'\n:root{--button-radius:'+radius+'}'},'*');
}
$('map').onload=()=>{graphTheme();graphVisibility();};
async function refreshNativeVisuals(){
  if(!window.pywebview?.api?.window_control)return;
  const css=await window.pywebview.api.window_control('visuals');
  let style=$('native-theme');
  if(!style){style=document.createElement('style');style.id='native-theme';document.head.append(style);}
  if(style.textContent!==css){style.textContent=css;graphTheme();}
}
window.addEventListener('pywebviewready',async()=>{document.documentElement.classList.add('native-host');$('window-controls').hidden=false;$('resize-grip').hidden=false;await firstState;await refreshNativeVisuals();window.pywebview.api.window_control(matchMedia('(prefers-reduced-motion:reduce)').matches?'reveal':'ready');});
let resizeStart=null;
$('resize-grip').onpointerdown=event=>{resizeStart={x:event.screenX,y:event.screenY,w:innerWidth,h:innerHeight};$('resize-grip').setPointerCapture(event.pointerId);};
$('resize-grip').onpointermove=event=>{if(resizeStart)window.pywebview.api.window_control('resize',resizeStart.w+event.screenX-resizeStart.x,resizeStart.h+event.screenY-resizeStart.y);};
$('resize-grip').onlostpointercapture=()=>{resizeStart=null;};
document.querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>window.pywebview?.api.window_control(b.dataset.window));

async function rules(){
  if(!initial.connected){notice('Rule sources require the connected dashboard');return;}
  const request=++ruleRequest,revision=selectionRevision,scope=$('rules-scope').value,sourceProject=project;
  try{
    const result=await api('rules',{project:sourceProject,scope});
    if(request!==ruleRequest||revision!==selectionRevision)return;
    $('rules-list').replaceChildren();
    for(const r of result.rules)$('rules-list').append(row(r.source,r.scope+(r.editable?' · editable source':' · read-only inherited/unauthorized source'),r.readable?button('Open source',()=>openRule(r,sourceProject,scope,revision)):null));
    if(!result.rules.length)$('rules-list').append(row('No applicable AGENTS.md found','No rule file is created automatically.'));
  }catch(e){if(request===ruleRequest&&revision===selectionRevision)notice(e.message);}
}
$('scope-form').onsubmit=e=>{e.preventDefault();if(editor){notice('Close the current source before changing scope');return;}rules();};
async function openRule(rule,sourceProject=project,scope=$('rules-scope').value,revision=selectionRevision){
  if(revision!==selectionRevision)return;
  if(editor&&$('rule-text').value!==editor.text){notice('Save or close the current source first');return;}
  const request=++ruleRequest;
  try{
    const result=await api('rule/read',{project:sourceProject,scope,rule:rule.id});
    if(request!==ruleRequest||revision!==selectionRevision)return;
    editor={...result,rule:rule.id,scope,project:sourceProject,source:rule.source};
    $('rule-text').value=editor.text;$('rule-text').readOnly=!rule.editable;$('save-rule').disabled=!rule.editable;
    text('source-name',rule.source);text('rule-result','Source loaded · revision '+editor.sha256.slice(0,10));$('rule-editor').hidden=false;
  }catch(e){if(request===ruleRequest&&revision===selectionRevision)notice(e.message);}
}
$('save-rule').onclick=async()=>{
  if(!editor)return;
  const source=editor,draft=$('rule-text').value;
  $('save-rule').disabled=true;
  try{
    const result=await api('rule/save',{project:source.project,scope:source.scope,rule:source.rule,text:draft,sha256:source.sha256});
    if(editor!==source)return;
    Object.assign(source,result);text('rule-result','Saved and reread from source · '+result.sha256.slice(0,10));
  }catch(e){if(editor===source)text('rule-result',e.message);}
  finally{if(editor===source)$('save-rule').disabled=false;}
};
$('close-rule').onclick=()=>{if(editor&&$('rule-text').value!==editor.text&&!confirm('Discard this unsaved rule edit?'))return;ruleRequest++;editor=null;$('rule-editor').hidden=true;};
document.addEventListener('visibilitychange',()=>{clearTimeout(poll);graphVisibility();if(!document.hidden)refresh();});
window.addEventListener('pagehide',()=>{clearTimeout(poll);clearTimeout(noticeTimer);});
async function knowledge(){
  if(!initial.connected){text('knowledge-status','Open /dashboard show for source-linked queries');return;}
  const revision=selectionRevision;
  text('knowledge-status','Checking selected project…');text('knowledge-result','');
  try{
    const s=await api('knowledge',{project});if(revision!==selectionRevision)return;
    text('knowledge-status',!s.available?'Automatic index maintenance unavailable':s.manifest_current?'Sources current · graph '+(s.graph_current?'current':'not current'):'Sources changed · automatic refresh will retry');
  }catch(e){if(revision===selectionRevision)text('knowledge-status',e.message);}
}
$('knowledge-form').onsubmit=async e=>{
  e.preventDefault();const revision=selectionRevision,request=++knowledgeRequest;
  text('knowledge-result','Searching selected project…');
  try{
    const result=await api('knowledge/query',{project,query:$('knowledge-query').value});
    if(revision===selectionRevision&&request===knowledgeRequest)text('knowledge-result',result.result);
  }catch(error){if(revision===selectionRevision&&request===knowledgeRequest)text('knowledge-result',error.message);}
};
$('replay').onclick=async()=>{
  if(matchMedia('(prefers-reduced-motion:reduce)').matches)return;
  const root=document.documentElement;
  root.classList.add('replaying');
  const animations=document.getAnimations().filter(a=>a.effect?.target===$('entrance')||$('entrance').contains(a.effect?.target)||a.effect?.target===document.body||a.effect?.target?.parentElement===document.body);
  animations.forEach(a=>{a.cancel();a.play();});
  await Promise.allSettled(animations.map(a=>a.finished));
  root.classList.remove('replaying');
};
const firstState=refresh();firstState.then(loadGraph);
