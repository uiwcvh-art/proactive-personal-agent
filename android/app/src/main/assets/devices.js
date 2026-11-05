'use strict';
// Real device engines share the portal's navigation and appearance.
let cloudTimer=null,cloudInFlight=false,cloudSnapshot=null,cloudZoom=false,localSnapshot=null;
function deviceHero(mark,kicker,title,description){return `<div class="device-hero"><span class="device-mark">${icon(mark)}</span><small>${esc(kicker)}</small><h1>${esc(title)}</h1><p>${esc(description)}</p></div>`;}
function hasPhoneModel(){const c=state.settings.config||{};return [c.big_brain,c.small_brain].some(m=>m?.api_key&&m?.model);}
async function renderLocal(){
  $('localContent').innerHTML=deviceHero('screen','ON THIS PHONE','手机工作区','与对话共用模型和任务记录，用 Root 操作独立屏。')+'<button class="primary full" data-local-route="workspace">打开手机工作区</button>';
}
async function openPhone(){
  if(!window.Native){toast('请安装 APK 使用手机工作区。');return;}
  await action('openLocal');
}
async function sendToPhone(text){
  if(text.length>6000)throw new Error('手机任务最多 6000 字，请分成几步交代。');
  const original=$('chatInput').value;await action('phoneRun',{goal:text,botId:state.botId});
  if($('chatInput').value===original){$('chatInput').value='';saveChatDraft();await localSettings(state.settings);resizeComposer();}
  await syncPhoneHistory();toast('手机任务已开始，可随时查看或停止。');
}
let localPhoneMessages=[];
async function syncPhoneHistory(){
  if(!window.Native)return;
  try{localPhoneMessages=(await action('phoneHistory')).messages||[];state.messages=fusePhoneMessages(state.messages);renderBots();if(state.view==='chat')renderMessages();}catch(e){}
}
function fusePhoneMessages(messages){return [...new Map([...messages.filter(m=>m.engine!=='root-phone'),...localPhoneMessages].map(m=>[m.id,m])).values()].sort((a,b)=>a.created_at-b.created_at);}
function renderComputer(){
  $('computerContent').innerHTML=`<div class="computer-heading"><span class="device-mark small">${icon('screen')}</span><div><h1>你的云电脑</h1><p id="computerState">正在连接…</p></div><span id="computerControl" class="subtle-pill">云端</span></div>
    <div class="computer-monitor"><div id="computerViewport" class="computer-viewport"><img id="computerFrame" alt="云电脑实时画面" draggable="false" hidden><div id="computerEmpty" class="computer-empty">${icon('screen')}<span>正在获取桌面…</span></div></div><div class="computer-frame-bar"><span id="frameTime">1280 × 800</span><button id="computerZoom" aria-label="放大云电脑画面">放大</button><button id="refreshComputer" aria-label="刷新云电脑画面">${icon('spark')}</button></div></div>
    <div class="computer-main-actions"><button id="computerTakeover" class="primary">接管电脑</button><button id="computerHandback" class="secondary" disabled>交还助手</button><button id="computerStop" class="stop-outline" disabled>停止</button></div>
    <div id="computerQuestion" class="computer-question" hidden></div><p id="computerError" class="field-feedback" role="status"></p>
    <div class="segmented computer-tabs"><button data-computer-tab="task" class="selected">交代任务</button><button data-computer-tab="control">手动操作</button><button data-computer-tab="files">成果文件</button></div>
    <div id="computer-tab-task"><form id="computerTaskForm"><label>让它完成什么<textarea id="computerGoal" rows="4" maxlength="6000" required placeholder="例如：比较这三款产品，把参数和来源整理成一份表格"></textarea></label><button id="computerRun" class="primary full" type="submit" disabled>开始云端任务 ${icon('send')}</button><p id="computerModelHint" class="field-hint"></p></form><form id="computerSteerForm" hidden><label>补充一句<textarea id="computerSteerText" rows="2" maxlength="4000" required placeholder="修改方向，或告诉它你已经处理好登录"></textarea></label><button class="secondary full" type="submit">发送补充说明</button></form><div id="computerJob"></div></div>
    <div id="computer-tab-control" hidden><p class="intro-text">先接管，再点画面操作。放大后可拖动查看；输入网址或用下方键盘输入。</p><form id="computerUrlForm"><label>打开网页<div class="inline-form"><input id="computerUrl" type="url" required placeholder="https://example.com" aria-label="云电脑网址"><button class="secondary" type="submit">前往</button></div></label></form><div id="computerKeys" class="computer-keys">${[['Escape','Esc'],['Tab','Tab'],['Return','Enter'],['BackSpace','⌫'],['ctrl+a','全选'],['ctrl+l','地址栏'],['ctrl+v','粘贴']].map(([key,label])=>`<button data-computer-key="${key}">${label}</button>`).join('')}</div><form id="computerTypeForm"><label>键盘输入<div class="inline-form"><input id="computerTypeText" type="password" autocomplete="off" maxlength="16000" placeholder="输入后发送至云电脑" aria-label="发送到云电脑的文字"><button class="secondary" type="submit">输入</button></div></label></form><div class="computer-scroll"><button data-computer-scroll="up">↑ 向上滚动</button><button data-computer-scroll="down">↓ 向下滚动</button></div></div>
    <div id="computer-tab-files" hidden><div class="list-caption"><span>下载与工作区</span><button id="refreshComputerFiles" class="plain-button">刷新</button></div><div id="computerFiles"></div></div><p class="section-foot">任务可以在你离开 App 后继续。<br>需要登录或验证时，接管处理后再交还。</p>`;
  cloudZoom=false;
  $('computerTakeover').onclick=()=>cloudCommand('takeover');$('computerHandback').onclick=()=>cloudCommand('handback');$('computerStop').onclick=()=>cloudCommand('stop');
  $('refreshComputer').onclick=()=>pollComputer(true);
  $('computerZoom').onclick=()=>{cloudZoom=!cloudZoom;$('computerViewport').classList.toggle('zoomed',cloudZoom);$('computerZoom').textContent=cloudZoom?'缩小':'放大';};
  $('computerFrame').onclick=async e=>{if(!cloudSnapshot?.control_ready){toast('先点「接管电脑」，再操作画面。');return;}const r=e.currentTarget.getBoundingClientRect();await cloudInput({kind:'click',x:Math.min(1279,Math.max(0,Math.floor((e.clientX-r.left)/r.width*1280))),y:Math.min(799,Math.max(0,Math.floor((e.clientY-r.top)/r.height*800)))});};
  $('computerTaskForm').onsubmit=async e=>{e.preventDefault();if(!requirePair())return;const goal=$('computerGoal').value.trim();if(!goal)return;$('computerRun').disabled=true;try{await api('POST','/api/computer/run',{goal,bot_id:state.botId,max_steps:24});$('computerGoal').value='';await refresh(true);await pollComputer(true);toast('任务已开始，可以离开这页。');}catch(err){toast(err.message);$('computerRun').disabled=false;}};
  $('computerSteerForm').onsubmit=async e=>{e.preventDefault();try{await api('POST','/api/computer/steer',{text:$('computerSteerText').value});$('computerSteerText').value='';toast('已发给执行中的助手。');}catch(err){toast(err.message);}};
  $('computerUrlForm').onsubmit=async e=>{e.preventDefault();await cloudCommand('navigate',{url:$('computerUrl').value.trim()});};
  $('computerTypeForm').onsubmit=async e=>{e.preventDefault();const text=$('computerTypeText').value;if(text&&await cloudInput({kind:'type',text}))$('computerTypeText').value='';};
  $('refreshComputerFiles').onclick=loadComputerFiles;
  devicesRouteChanged();
}
function updateComputerStatus(s){
  cloudSnapshot=s;if(state.view!=='computer')return;
  const job=s.job,active=job&&['running','paused','waiting_user'].includes(job.state);
  $('computerState').textContent=!s.available?(s.error||'云电脑暂不可用'):active?'任务进行中 · 第 '+(job.step||0)+' 步':job?.state==='done'?'任务已完成':job?.state==='stopped'?'执行已停止':'桌面已就绪';
  $('computerControl').textContent=s.control==='user'?'你在操作':s.control==='pausing'?'正在交接':'助手';
  $('computerTakeover').disabled=!s.available||s.control==='pausing'||s.control==='user';$('computerHandback').disabled=!s.available||s.control!=='user';$('computerStop').disabled=!active;
  $('computerRun').disabled=!s.available||!s.model_ready||!!active;
  $('computerModelHint').innerHTML=s.model_ready?'使用执行大脑 · 最多 24 步，可随时停止。':'<button class="plain-button" data-config="big">先配置支持视觉的执行模型</button> · 也可以接管后自己操作。';
  $('computerSteerForm').hidden=!active;$('computerTaskForm').hidden=!!active;
  $('computerQuestion').hidden=!job?.question;if(job?.question)$('computerQuestion').textContent=job.question;
  const version=JSON.stringify(job);if($('computerJob').dataset.version!==version){$('computerJob').dataset.version=version;$('computerJob').innerHTML=job?`<div class="group-label">本轮任务</div><div class="computer-job"><strong>${esc(job.goal)}</strong>${job.result?`<div class="markdown">${markdown(job.result)}</div>`:''}${job.trace?.length?`<details><summary>查看 ${job.trace.length} 步执行记录</summary>${job.trace.map(t=>`<div class="computer-step"><small>STEP ${t.step}</small><pre>${esc(JSON.stringify(t.actions,null,2))}</pre></div>`).join('')}</details>`:''}</div>`:'';}
}
async function pollComputer(frame=true){
  clearTimeout(cloudTimer);if(cloudInFlight||state.view!=='computer'||document.hidden||!foreground)return;
  if(!paired()){$('computerState').textContent='先连接私人节点';$('computerEmpty').innerHTML=`${icon('cloud')}<button class="secondary" data-config="connection">连接节点</button>`;return;}
  cloudInFlight=true;
  try{const s=await api('GET','/api/computer/status');if(state.view!=='computer')return;updateComputerStatus(s);
    if(s.available&&frame){const f=await api('GET','/api/computer/frame');if(state.view!=='computer')return;$('computerFrame').src='data:'+f.mime+';base64,'+f.image;$('computerFrame').hidden=false;$('computerEmpty').hidden=true;$('frameTime').textContent='实时画面 · '+timeOf(f.captured_at);$('computerError').textContent='';}
    else if(!s.available){$('computerEmpty').hidden=false;$('computerEmpty').innerHTML=icon('screen')+'<span>'+esc(s.error||'桌面尚未就绪')+'</span>';}
  }catch(e){if(state.view==='computer'){$('computerError').textContent=e.message;if(cloudSnapshot)cloudSnapshot.control_ready=false;}}
  finally{cloudInFlight=false;if(state.view==='computer'&&!document.hidden&&foreground)cloudTimer=setTimeout(()=>pollComputer(true),2500);}
}
function devicesRouteChanged(){clearTimeout(cloudTimer);if(!document.hidden&&foreground){syncPhoneHistory();if(state.view==='computer')cloudTimer=setTimeout(()=>pollComputer(true),50);}}
async function cloudCommand(command,body=null){try{const s=await api('POST','/api/computer/'+command,body);if(s.available!==undefined){s.model_ready=cloudSnapshot?.model_ready;updateComputerStatus(s);}await pollComputer(true);return true;}catch(e){toast(e.message);return false;}}
async function cloudInput(body){if(!cloudSnapshot?.control_ready){toast('先接管云电脑，等当前一步结束。');return false;}return cloudCommand('input',body);}
async function loadComputerFiles(){try{const data=await api('GET','/api/computer/files');if(state.view!=='computer')return;$('computerFiles').innerHTML=data.files.length?data.files.map(f=>`<button class="file-row" data-download-source="computer" data-download-path="${esc(f.path)}">${icon('file')}<span><strong>${esc(f.path.split('/').pop())}</strong><small>${esc(f.path)} · ${formatBytes(f.bytes)}</small></span>${icon('next')}</button>`).join(''):'<div class="empty-state">浏览器下载与任务生成的文件会出现在这里。</div>';}catch(e){if(state.view==='computer')$('computerFiles').innerHTML=`<div class="error-card">${esc(e.message)}</div>`;}}
function formatBytes(bytes){return bytes<1024?bytes+' B':bytes<1048576?(bytes/1024).toFixed(1)+' KB':(bytes/1048576).toFixed(1)+' MB';}
async function renderLibrary(){
  libraryFilter='all';
  $('libraryContent').innerHTML=`<p class="intro-text">发给助手的资料，和它交回来的成果，都在这里。</p><div class="library-actions"><button id="uploadLibrary" class="primary">${icon('plus')} 添加文件</button><button id="refreshLibrary" class="plain-button">刷新</button></div><div class="filter-pills"><button data-library-filter="all" class="selected">全部</button><button data-library-filter="workspace">任务文件</button><button data-library-filter="computer">云电脑</button></div><div id="libraryFiles"><div class="empty-state">正在读取文件…</div></div>`;
  $('uploadLibrary').onclick=()=>menu('添加到哪里',[{icon:'file',title:'任务工作区',description:'对话、研究与文件工具可读取',run:()=>uploadFile('workspace')},{icon:'cloud',title:'云电脑',description:'让云端电脑使用这份资料',run:()=>uploadFile('computer')}]);
  $('refreshLibrary').onclick=loadLibrary;await loadLibrary();
}
let libraryItems=[],libraryFilter='all',libraryErrors={};
async function loadLibrary(){
  if(!paired()){$('libraryFiles').innerHTML=`<div class="empty-state">连接你的私人节点后，文件会同步到这里。<button class="secondary" data-config="connection">连接节点</button></div>`;return;}
  const results=await Promise.allSettled([api('GET','/api/files'),api('GET','/api/computer/files')]);libraryItems=[];libraryErrors={};
  for(let i=0;i<results.length;i++){const source=i?'computer':'workspace';if(results[i].status==='fulfilled')libraryItems.push(...results[i].value.files.map(f=>({...f,source})));else libraryErrors[source]=results[i].reason.message;}
  if(state.view!=='library')return;renderLibraryFiles();
  if(results.every(r=>r.status==='rejected'))$('libraryFiles').innerHTML=`<div class="error-card">${esc(results[0].reason.message)}</div>`;
}
function renderLibraryFiles(){
  if(libraryErrors[libraryFilter]){$('libraryFiles').innerHTML=`<div class="error-card">${esc(libraryErrors[libraryFilter])}</div>`;return;}
  const items=libraryItems.filter(f=>libraryFilter==='all'||f.source===libraryFilter).sort((a,b)=>(b.modified_at||0)-(a.modified_at||0));$('libraryFiles').innerHTML=items.length?items.map(f=>`<button class="file-row" data-download-source="${f.source}" data-download-path="${esc(f.path)}">${icon('file')}<span><strong>${esc(f.path.split('/').pop())}</strong><small>${f.source==='computer'?'云电脑':'任务工作区'} · ${formatBytes(f.bytes)}</small></span>${icon('next')}</button>`).join(''):'<div class="empty-state">还没有文件。添加一份资料，或让助手为你生成一份成果。</div>';
  if(libraryFilter==='all')for(const [source,error] of Object.entries(libraryErrors))$('libraryFiles').innerHTML+=`<p class="field-hint">${source==='computer'?'云电脑':'任务工作区'}：${esc(error)}</p>`;
}
async function uploadFile(source='workspace',attach=false){
  if(!requirePair())return;
  try{const receipt=await action('uploadFile',{source});toast('已上传：'+receipt.name);if(attach){state.attachments||=[];state.attachments.push({...receipt,source});if(source==='workspace')state.mode='execute';saveChatDraft();await localSettings(state.settings);updateComposer();}if(state.view==='library')await loadLibrary();if(state.view==='computer')await loadComputerFiles();}
  catch(e){if(!e.message.includes('取消'))toast(e.message);}
}
function cloudComposerMenu(){state.mode='computer';updateComposer();}
document.addEventListener('DOMContentLoaded',()=>{
  document.addEventListener('click',async e=>{const el=e.target.closest('button');if(!el)return;try{
    if(el.dataset.localRoute){await openPhone(el.dataset.localRoute);return;}
    
    if(el.dataset.libraryFilter){libraryFilter=el.dataset.libraryFilter;document.querySelectorAll('[data-library-filter]').forEach(b=>b.classList.toggle('selected',b===el));renderLibraryFiles();return;}
    if(el.dataset.computerTab){document.querySelectorAll('[data-computer-tab]').forEach(b=>b.classList.toggle('selected',b===el));for(const tab of ['task','control','files'])$('computer-tab-'+tab).hidden=tab!==el.dataset.computerTab;if(el.dataset.computerTab==='files')await loadComputerFiles();return;}
    if(el.dataset.computerKey){await cloudInput({kind:'key',key:el.dataset.computerKey});return;}
    if(el.dataset.computerScroll){await cloudInput({kind:'scroll',direction:el.dataset.computerScroll,x:640,y:400});return;}
    if(el.dataset.downloadPath){el.disabled=true;const old=el.innerHTML;try{const file=await action('downloadFile',{source:el.dataset.downloadSource,path:el.dataset.downloadPath});toast('已保存到下载 / Pulse Bot：'+file.name);}finally{el.disabled=false;el.innerHTML=old;}return;}
  }catch(err){toast(err.message);if(el.isConnected)el.disabled=false;}});
  document.addEventListener('visibilitychange',devicesRouteChanged);
});
