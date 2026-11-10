'use strict';
const $ = selector => document.querySelector(selector);
const state = {workflow:'application',config:null,apps:[],projectId:'',run:null,selected:0,view:'script',source:null,busy:false,follow:true,resetPreview:null,resetLoading:false,demoTag:null};
const descriptions = {
  governance:'Protect production promotion with real human roles: Member can change staging, Member is denied production, Admin can promote. Writable service keys keep CI access.',
  audit:'Make a controlled role change and correlate it with Langfuse’s persisted audit records. Prepares the dedicated governance project when needed.',
  instance:'Create a separate organization, bootstrap an organization administration key, provision a starter project, and verify the hierarchy.',
  application:'Create real application projects, issue their integration keys, and seed a prompt and evaluation dataset. Increase the application count to demonstrate the same blueprint across a fleet.',
  team:'Provision people through SCIM and apply project-specific roles. The team name is portal metadata; Langfuse receives individual users and memberships.',
  permissions:'Change an existing portal-managed person’s role and read the resulting membership back from Langfuse.',
  keys:'Switch this portal’s demo integration to a verified replacement key, revoke its previous key, and prove that the previous key is rejected.',
  dashboard:'Create an actual dashboard, a latency widget, and its placement through the OSS project API. The dashboard contract is currently unstable.',
  offboarding:'Remove the team’s project grants and verify their removal. Preserve application data and its independent integration key.',
  reset:'Clean up the objects created for this demo so you can start again. Review the live inventory, then confirm permanent deletion.'
};
const editionText = {enterprise:'Enterprise',oss:'OSS included',mixed:'Enterprise + OSS',orchestration:'Demo orchestration'};
const keyRoles={ADMIN:'Full access',VIEWER:'Viewer',INGEST:'Ingestion',SCORES_INGEST:'Scores ingestion'};
const keyRoleDescriptions={ADMIN:'Read and write project data and configuration.',VIEWER:'Read project data. Writes are denied.',INGEST:'Create traces, scores, and media. Reading project data is denied.',SCORES_INGEST:'Submit scores only. Reading project data is denied.'};
const pretty = value => JSON.stringify(value,null,2);
function badge(edition) {const span=document.createElement('span');span.className='badge '+edition;span.textContent=editionText[edition]||edition;return span;}
async function api(path,options={}) {
  const response=await fetch(path,options);const body=await response.json();
  if(!response.ok)throw new Error(body.error||'Request failed.');return body;
}
function app() {return state.apps.find(item=>item.id===state.projectId);}
function info() {return state.config.workflows.find(item=>item.id===state.workflow);}
function steps() {
  if(!state.run)return [];
  return state.run.events.filter(event=>event.type==='request').map(request=>({...request,response:state.run.events.find(event=>event.type==='response'&&event.stepId===request.stepId)}));
}
function setError(message) {$('#form-error').hidden=!message;$('#form-error').textContent=message||'';}
function fieldVisibility() {
  const action=info();
  const applicationWorkflow=state.workflow==='application',resetWorkflow=state.workflow==='reset';
  $('#new-application-fields').hidden=!applicationWorkflow;$('#existing-application-fields').hidden=!action.project;
  $('#reset-fields').hidden=!resetWorkflow;
  for(const [id,workflow] of [['governance','governance'],['instance','instance']]){
    const box=$('#'+id+'-fields');box.hidden=state.workflow!==workflow;
    box.querySelectorAll('input').forEach(input=>{input.disabled=state.busy||state.workflow!==workflow;input.required=state.workflow===workflow;});
  }
  $('#team-fields').hidden=state.workflow!=='team';$('#permission-fields').hidden=state.workflow!=='permissions';
  const keyWorkflow=['application','keys'].includes(state.workflow);
  $('#key-fields').hidden=!keyWorkflow;$('#key-role').required=keyWorkflow;$('#key-role').disabled=state.busy||!keyWorkflow;
  $('#key-role-description').textContent=keyRoleDescriptions[$('#key-role').value]||'Choose the access required by the integration.';
  $('#application-name').required=applicationWorkflow;$('#team-name').required=applicationWorkflow;
  $('#retention-days').required=applicationWorkflow;$('#application-count').required=applicationWorkflow;
  $('#member-one-email').required=state.workflow==='team';$('#member-two-email').required=state.workflow==='team';
  const days=Number($('#retention-days').value);
  $('#retention-days').setCustomValidity(state.workflow==='application'&&(days===1||days===2)?'Retention must be 0 or at least 3 days.':'');
  $('#workflow-title').textContent=action.title;$('#workflow-description').textContent=descriptions[state.workflow];
  const temporarySetup=state.workflow==='dashboard'&&app()?.keyRole&&app().keyRole!=='ADMIN';
  const displayEdition=temporarySetup?'mixed':action.edition;
  $('#workflow-edition').className='badge '+displayEdition;$('#workflow-edition').textContent=editionText[displayEdition];
  $('#run-workflow').className='primary '+action.edition;$('#run-workflow').textContent=state.busy?'Running real API calls…':action.button;
  $('#run-workflow').classList.toggle('danger',resetWorkflow);
  if(!state.busy&&state.workflow==='dashboard'&&app()?.dashboardId)$('#run-workflow').textContent='Verify existing dashboard';
  if(!state.busy&&state.workflow==='application'&&Number($('#application-count').value)===1&&state.apps.some(item=>item.name===$('#application-name').value.trim()))$('#run-workflow').textContent='Verify existing application';
  $('#parameters').querySelectorAll('input,select').forEach(input=>input.disabled=state.busy);
  $('#new-application-fields').querySelectorAll('input').forEach(input=>input.disabled=state.busy||!applicationWorkflow);
  $('#selected-project').disabled=state.busy||!action.project;
  $('#team-fields').querySelectorAll('input,select').forEach(input=>input.disabled=state.busy||state.workflow!=='team');
  $('#permission-fields').querySelectorAll('select').forEach(input=>input.disabled=state.busy||state.workflow!=='permissions');
  $('#key-role').disabled=state.busy||!keyWorkflow;
  $('#reset-confirmation').disabled=state.busy||!resetWorkflow;$('#reset-confirmation').required=resetWorkflow;
  $('#preview-reset').disabled=state.busy||state.resetLoading;$('#preview-reset').textContent=state.resetLoading?'Checking live inventory…':'Preview reset';
  const current=app();
  const activeMembers=(current?.members||[]).filter(member=>member.active);
  const emptyOffboarding=state.workflow==='offboarding'&&current&&activeMembers.length===0;
  $('#run-workflow').disabled=state.busy||(action.project&&!current)||(state.workflow==='permissions'&&!$('#selected-member').value)||emptyOffboarding;
  if(resetWorkflow)$('#run-workflow').disabled=state.busy||state.resetLoading||!state.resetPreview?.canReset||$('#reset-confirmation').value!=='RESET DEMO';
  if(!state.busy&&emptyOffboarding)$('#run-workflow').textContent='No team access to remove';
  const notes={team:'SCIM does not set a password. Use synthetic demo emails; use SSO or a separate password-reset flow for interactive sign-in.',keys:'This changes the demo integration managed by this portal. External applications are not reconfigured.',offboarding:'Only active memberships recorded in this portal’s manifest are removed. Organization membership and global user accounts remain.'};
  let note=action.project&&!current?'Create an application first.':notes[state.workflow]||'';
  if(state.workflow==='instance'&&!state.config.instanceConfigured)note='Setup required: generate private/instance.env and load its ADMIN_API_KEY into Langfuse using the Compose override. See the README.';
  if(state.workflow==='keys'&&current)note='Current key: '+(keyRoles[current.keyRole]||'Legacy key')+'. '+note;
  if((state.workflow==='application'&&$('#key-role').value&&$('#key-role').value!=='ADMIN')||temporarySetup)note='The integration keeps its selected permissions. Portal setup uses a separate temporary Full access key and revokes it afterward; those key-management calls are highlighted as Enterprise.';
  if(emptyOffboarding)note=current.members.length
    ?'All team memberships recorded for '+current.name+' have already been removed. Onboard the team again to restore project access.'
    :'No team members have been onboarded to '+current.name+'. Creating an application sets up its workspace and assets; add people and roles in Team before offboarding them.';
  $('#pre-run-note').textContent=note;
  $('#go-to-team').hidden=!emptyOffboarding;
  $('#go-to-team').disabled=state.busy;
  document.querySelectorAll('[data-workflow]').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.workflow===state.workflow));button.disabled=state.busy;});
}
function renderProjects() {
  const selector=$('#selected-project');selector.replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='Select a portal-created application';selector.append(empty);
  state.apps.forEach(item=>{const option=document.createElement('option');option.value=item.id;option.textContent=item.name;selector.append(option);});
  if(!state.apps.some(item=>item.id===state.projectId))state.projectId=state.apps[0]?.id||'';
  selector.value=state.projectId;
  const memberSelector=$('#selected-member'),previous=memberSelector.value;memberSelector.replaceChildren();
  for(const member of app()?.members||[])if(member.active){const option=document.createElement('option');option.value=member.userId;option.textContent=member.email+' ('+member.role+')';memberSelector.append(option);}
  if([...memberSelector.options].some(option=>option.value===previous))memberSelector.value=previous;
  const members=app()?.members||[];
  if(members[0]){$('#member-one-email').value=members[0].email;$('#member-one-role').value=members[0].role;}
  if(members[1]){$('#member-two-email').value=members[1].email;$('#member-two-role').value=members[1].role;}
  renderResources();
  renderOrganizations($('#resource-list'));
}
function renderResources() {
  const container=$('#resource-list');container.replaceChildren();
  if(!state.apps.length&&!state.organizations?.length){const p=document.createElement('p');p.className='hint';p.textContent='Create an application to see its real object IDs and links.';container.append(p);return;}
  for(const item of state.apps){
    const row=document.createElement('article');row.className='resource';
    const top=document.createElement('div');top.className='heading-row';
    const name=document.createElement('strong');name.textContent=item.name;
    const link=document.createElement('a');link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Open in Langfuse';top.append(name,link);
    const id=document.createElement('code');id.className='resource-id';id.textContent=item.id;
    const dl=document.createElement('dl');
    const values=[['Team',item.team],['Retention',item.retentionDays===0?'Unlimited':item.retentionDays+' days'],['Prompt',item.promptName],['Dataset',item.datasetName],['Key ID',item.keyId],['Key permissions',keyRoles[item.keyRole]||'Legacy key; permissions not selected by this portal'],['Members',(item.members||[]).filter(m=>m.active).map(m=>m.email+' / '+m.role).join('; ')||'No active team grants'],['Dashboard',item.dashboardId]];
    for(const [label,value] of values)if(value!==undefined&&value!==null){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=String(value);dl.append(dt,dd);}
    row.append(top,id,dl);container.append(row);
  }
}
function renderOrganizations(container) {
  for(const item of state.organizations||[]){
    const row=document.createElement('article');row.className='resource';
    const name=document.createElement('strong');name.textContent='Organization: '+item.name;
    const id=document.createElement('p');id.textContent=item.id;
    row.append(name,id);
    if(item.resetStatus){const note=document.createElement('p');note.textContent='Cleanup pending. Retry Reset after background project purging.';row.append(note);}
    else if(item.projectId){const a=document.createElement('a');a.href=state.config.host+'/project/'+item.projectId;a.target='_blank';a.rel='noopener noreferrer';a.textContent=item.projectName+' · '+item.projectId;row.append(a);}
    container.append(row);
  }
}
function renderResetPreview() {
  const container=$('#reset-preview');container.replaceChildren();
  const preview=state.resetPreview;
  if(!preview){const text=document.createElement('p');text.className='hint';text.textContent='Preview the exact projects and demo memberships before confirming.';container.append(text);return;}
  const heading=document.createElement('p');heading.className='reset-summary';heading.textContent=preview.projects.length+' project(s) to delete or clear from the portal, '+preview.users.length+' demo membership(s) and '+(preview.organizations||[]).length+' isolated organization(s) to remove. '+preview.preservedProjectCount+' other project(s) kept.';container.append(heading);
  const list=document.createElement('ul');list.className='reset-object-list';
  for(const project of preview.projects){const li=document.createElement('li'),name=document.createElement('strong'),detail=document.createElement('span');name.textContent=project.name;detail.textContent=project.id+' · '+(project.blocked?'Blocked: '+project.reason:project.present?'Delete project and all contents':'Already absent; clear local records');if(project.blocked)li.className='error';li.append(name,detail);list.append(li);}
  for(const user of preview.users){const li=document.createElement('li'),name=document.createElement('strong'),detail=document.createElement('span');name.textContent=user.email;detail.textContent=user.present?'Remove organization membership; global account remains':'Membership already absent; clear portal record';li.append(name,detail);list.append(li);}
  for(const org of preview.organizations||[]){const li=document.createElement('li');li.textContent='Organization '+org.name+' · '+org.id+' · '+(org.blocked?'Blocked: '+org.reason:'Delete organization and recorded starter project');if(org.blocked)li.className='error';list.append(li);}
  container.append(list);
  for(const user of preview.protectedUsers){const p=document.createElement('p');p.className='hint';p.textContent='Keep '+user.email+': '+user.reason;container.append(p);}
  const note=document.createElement('p');note.className='hint';note.textContent=preview.canReset?preview.deletionNote:'Nothing eligible to reset, or an ownership check needs review. No deletion can run.';container.append(note);
}
function renderSteps() {
  const list=$('#steps'),rows=steps(),scroll=list.scrollTop;list.replaceChildren();
  rows.forEach((step,index)=>{
    const li=document.createElement('li'),button=document.createElement('button');button.type='button';button.className='step-button '+step.edition+(step.response&&!step.response.ok?' failed':'');button.setAttribute('aria-current',index===state.selected?'step':'false');
    const number=document.createElement('span');number.className='step-number';number.textContent=String(index+1);
    const content=document.createElement('span'),label=document.createElement('span');label.className='step-label';label.append(document.createTextNode(step.label),badge(step.edition));
    const meta=document.createElement('div');meta.className='step-meta';meta.textContent=step.method+' '+step.path;
    content.append(label,meta);
    const status=document.createElement('span');status.className='step-status';status.textContent=step.response?String(step.response.status||'Failed')+' / '+(step.response.durationMs||0)+' ms':'Calling…';
    button.append(number,content,status);button.addEventListener('click',()=>{state.selected=index;state.follow=false;renderSteps();renderInspector();});li.append(button);list.append(li);
  });
  const summary=$('#run-summary');summary.replaceChildren();
  if(state.run){
    const label=document.createElement('span');label.textContent=state.run.status==='running'?'Live execution':state.run.status==='complete'?'Completed in Langfuse':state.run.status==='pending'?'Cleanup pending':'Run stopped';summary.append(label);
    for(const edition of ['enterprise','oss']){const count=rows.filter(row=>row.edition===edition).length;if(count){const b=badge(edition);b.textContent=count+' '+editionText[edition]+' '+(count===1?'call':'calls');summary.append(b);}}
  }
  list.scrollTop=state.follow?list.scrollHeight:scroll;
  renderEvidence();
  const result=$('#run-result');result.className='result';result.textContent='';
  const finish=state.run?.events.find(event=>event.type==='complete');const error=state.run?.events.find(event=>event.type==='error');
  if(finish){result.textContent=finish.result.message;result.classList.add(state.run.status==='pending'?'hint':'success');}
  else if(error){result.textContent=error.message;result.classList.add('error');}
}
function renderInspector() {
  const row=steps()[state.selected];
  document.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===state.view)));
  const b=$('#call-edition');b.hidden=!row;if(row){b.className='badge '+row.edition;b.textContent=editionText[row.edition];}
  $('#call-doc').hidden=!row;if(row)$('#call-doc').href=row.doc;
  $('#call-scope').textContent=row?(row.surface||row.scope)+' · '+row.source.file+':'+row.source.line:'';
  if(state.view==='script'){
    $('#inspector-label').textContent=row?'Executed command with credential-file path redacted; source from the running script.':'Actual script source. Run the workflow to see each executed command.';
    const input='# JSON parameters passed to the named script\n'+pretty(state.run?.params||parameters())+'\n\n';
    $('#inspector-code').textContent=input+(row?row.command+'\n\n# '+row.source.file+':'+row.source.line+'\n'+row.source.code:state.source?'# '+state.source.entryFile+'\n'+state.source.entry+'\n# '+state.source.file+':'+state.source.line+'\n'+state.source.code:'Loading script…');
  }else if(state.view==='request'){
    $('#inspector-label').textContent=row?'Actual request; authentication redacted before delivery.':'Run a workflow to see the real HTTP request.';
    $('#inspector-code').textContent=row?row.method+' '+state.config.host+row.path+'\nAuthentication: '+(row.auth||'Basic [redacted]')+'\n'+(row.body?'Content-Type: application/json\n\n'+pretty(row.body):'')+(row.query?.length?'\n\nCLI query arguments: '+pretty(row.query):''):'';
  }else{
    $('#inspector-label').textContent=row?.response?'Actual Langfuse response; secrets redacted.':row?'Waiting for Langfuse…':'Run a workflow to see the real response.';
    $('#inspector-code').textContent=row?.response?'HTTP '+row.response.status+'\n\n'+pretty(row.response.body):'';
  }
}
async function refreshState() {
  const data=await api('/api/state');state.apps=data.applications;state.organizations=data.organizations||[];renderProjects();
  if(state.demoTag!==data.demoTag&&!app()?.members?.length){$('#member-one-email').value='maya.demo+'+data.demoTag+'@example.invalid';$('#member-two-email').value='alex.demo+'+data.demoTag+'@example.invalid';}
  state.demoTag=data.demoTag;state.resetPreview=null;$('#reset-confirmation').value='';renderResetPreview();fieldVisibility();return data;
}
async function selectWorkflow(id) {
  if(id!==state.workflow&&['application','keys'].includes(id))$('#key-role').value='';
  state.workflow=id;state.run=null;state.selected=0;state.source=null;state.resetPreview=null;$('#reset-confirmation').value='';renderResetPreview();setError('');fieldVisibility();renderSteps();renderInspector();
  const source=await api('/api/script?workflow='+encodeURIComponent(id));if(state.workflow===id){state.source=source;renderInspector();}
}
function renderEvidence(){
  const container=$('#evidence');container.replaceChildren();
  const event=state.run?.events.find(e=>e.type==='audit'||e.type==='governance');container.hidden=!event;
  if(!event)return;
  const title=document.createElement('h3');title.textContent=event.type==='audit'?'Native Langfuse audit evidence':'Verified permission checks';container.append(title);
  if(event.checks){const list=document.createElement('ul');for(const check of event.checks){const item=document.createElement('li');item.textContent=check;list.append(item);}container.append(list);return;}
  const note=document.createElement('p');note.textContent=event.matchedIds.length+' record(s) match this run. Showing '+event.records.length+' of '+event.totalCount+' native records.';container.append(note);
  const a=document.createElement('a');a.href=event.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent='Open native audit logs';container.append(a);
  for(const record of event.records){const details=document.createElement('details'),summary=document.createElement('summary'),code=document.createElement('pre');
    summary.textContent=(event.matchedIds.includes(record.id)?'✓ This run · ':'')+record.createdAt+' · '+(record.actor?.body?.email||record.actor?.body?.id||record.type)+' · '+record.resourceType+' / '+record.action;
    code.textContent=pretty({id:record.id,actor:record.actor,before:record.before,after:record.after});details.append(summary,code);details.open=event.matchedIds.includes(record.id);container.append(details);}
}
function parameters() {
  if(state.workflow==='governance')return {governanceName:$('#governance-name').value};
  if(state.workflow==='instance')return {organizationName:$('#organization-name').value};
  if(state.workflow==='audit')return {};
  if(state.workflow==='reset')return {confirmation:$('#reset-confirmation').value,previewFingerprint:state.resetPreview?.fingerprint||''};
  if(state.workflow==='application')return {applicationName:$('#application-name').value,teamName:$('#team-name').value,retentionDays:Number($('#retention-days').value),applicationCount:Number($('#application-count').value),keyRole:$('#key-role').value};
  const p={projectId:state.projectId};
  if(state.workflow==='keys')p.keyRole=$('#key-role').value;
  if(state.workflow==='team')p.members=[{email:$('#member-one-email').value,role:$('#member-one-role').value},{email:$('#member-two-email').value,role:$('#member-two-role').value}];
  if(state.workflow==='permissions'){p.userId=$('#selected-member').value;p.role=$('#target-role').value;}
  return p;
}
async function poll(runId) {
  const run=await api('/api/runs/'+runId),count=steps().length;state.run=run;
  if(state.follow&&steps().length>count)state.selected=steps().length-1;
  renderSteps();renderInspector();
  if(run.status==='running'){setTimeout(()=>poll(runId).catch(runError),450);return;}
  state.busy=false;
  const complete=run.events.find(event=>event.type==='complete');if(complete?.result.projectIds?.length)state.projectId=complete.result.projectIds[0];
  await refreshState();fieldVisibility();
}
function runError(error) {state.busy=false;fieldVisibility();setError(error.message);}
$('#parameters').addEventListener('submit',async event=>{
  event.preventDefault();setError('');state.busy=true;state.run=null;state.selected=0;state.follow=true;fieldVisibility();renderSteps();renderInspector();
  try {const result=await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Request':'1'},body:JSON.stringify({workflow:state.workflow,params:parameters()})});await poll(result.runId);}catch(error){runError(error);}
});
$('#selected-project').addEventListener('change',()=>{state.projectId=$('#selected-project').value;renderProjects();fieldVisibility();renderInspector();});
$('#key-role').addEventListener('change',()=>{fieldVisibility();renderInspector();});
$('#selected-member').addEventListener('change',fieldVisibility);
$('#go-to-team').addEventListener('click',()=>selectWorkflow('team').catch(runError));
$('#preview-reset').addEventListener('click',async()=>{
  setError('');state.resetPreview=null;state.resetLoading=true;$('#reset-confirmation').value='';renderResetPreview();fieldVisibility();
  try{const preview=await api('/api/reset-preview');if(state.workflow==='reset'){state.resetPreview=preview;renderResetPreview();renderInspector();}}
  catch(error){setError(error.message);}
  finally{state.resetLoading=false;fieldVisibility();}
});
$('#parameters').addEventListener('input',()=>{if(!state.busy){fieldVisibility();renderInspector();}});
$('#show-api').addEventListener('change',()=>{$('#inspector').hidden=!$('#show-api').checked;$('#layout').classList.toggle('inspector-hidden',!$('#show-api').checked);});
$('#highlight-enterprise').addEventListener('change',()=>document.body.classList.toggle('highlight',$('#highlight-enterprise').checked));
$('#refresh-state').addEventListener('click',()=>refreshState().catch(runError));
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{state.view=button.dataset.view;renderInspector();}));
async function init() {
  state.config=await api('/api/config');
  for(const action of state.config.workflows){const button=document.createElement('button');button.type='button';button.dataset.workflow=action.id;button.append(document.createTextNode(action.label),badge(action.edition));button.addEventListener('click',()=>selectWorkflow(action.id).catch(runError));$('#workflows').append(button);}
  const initial=await refreshState();
  if(!app()?.members?.length){$('#member-one-email').value='maya.demo+'+initial.demoTag+'@example.invalid';$('#member-two-email').value='alex.demo+'+initial.demoTag+'@example.invalid';}
  document.body.classList.add('highlight');await selectWorkflow('application');
  const selectedRun=new URLSearchParams(location.search).get('run')||initial.activeRun;
  if(selectedRun){const running=await api('/api/runs/'+encodeURIComponent(selectedRun));await selectWorkflow(running.workflow);state.busy=running.status==='running';fieldVisibility();poll(selectedRun).catch(runError);}
  try{const preflight=await api('/api/preflight');$('#connection-status').textContent=preflight.ok?'Organization connected ('+preflight.organizationProjectCount+' projects)':'Organization API unavailable';}catch{$('#connection-status').textContent='Organization API unavailable';}
}
init().catch(runError);
