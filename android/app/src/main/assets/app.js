'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=name=>`<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const paths={back:'<path d="m14 5-7 7 7 7"/>',next:'<path d="m9 5 7 7-7 7"/>',plus:'<path d="M12 5v14M5 12h14"/>',close:'<path d="m6 6 12 12M18 6 6 18"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',send:'<path d="m5 12 7-7 7 7M12 5v15"/>',check:'<path d="m5 12 4 4 10-10"/>',chat:'<path d="M21 11a8 8 0 0 1-8 8H7l-4 3V11a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z"/>',tools:'<path d="m12 2 9 5-9 5-9-5 9-5ZM3 7v11l9 5 9-5V7M12 12v11"/>',file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6ZM14 3v6h6M8 13h8M8 17h5"/>',folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',cloud:'<path d="M6 18a5 5 0 1 1 1-10 6 6 0 0 1 11 0 5 5 0 0 1 0 10H6Z"/>',spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',screen:'<rect x="3" y="3" width="18" height="14" rx="3"/><path d="M8 21h8M12 17v4m-2-13 6 4-3 1-1 3-2-8Z"/>',mail:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/>',moon:'<path d="M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10Z"/>',copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',edit:'<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14v6Z"/>',code:'<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',bell:'<path d="M5 10a7 7 0 0 1 14 0c0 7 3 7 3 7H2s3 0 3-7Zm4 10a3 3 0 0 0 6 0"/>',settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',vault:'<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>'};
Object.assign(paths,{pin:'<path d="m8 3 8 0-1 6 4 4H5l4-4-1-6ZM12 13v8"/>',eye:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',hide:'<path d="m3 3 18 18M10.6 5.1 12 5c6.5 0 10 7 10 7a23 23 0 0 1-3 3.5M6.5 6.5A24 24 0 0 0 2 12s3.5 7 10 7a11 11 0 0 0 5.5-1.5M10 10a3 3 0 0 0 4 4"/>'});
Object.assign(paths,{camera:'<path d="M8 6 9.5 3h5L16 6h4a2 2 0 0 1 2 2v11H2V8a2 2 0 0 1 2-2h4Z"/><circle cx="12" cy="12" r="4"/>'});
$('iconSymbols').innerHTML=Object.entries(paths).map(([name,path])=>`<symbol id="i-${name}" viewBox="0 0 24 24">${path}</symbol>`).join('');
const cloudNames=['read_file','write_file','edit_file','run_bash','web_fetch','web_search'];
const defaultBots=[{id:'companion',name:'白kawa',description:'聊聊近况，把挂心的事理清楚。',instructions:'',engine:'small',color:'#7C5CFF',shape:'flower',tools:cloudNames,notify:true},{id:'research',name:'研究助手',description:'查资料、写文件，带着成果回来。',instructions:'',engine:'big',color:'#FF8A3D',shape:'circle',tools:cloudNames,notify:true}];
const state={settings:window.bootstrapPreferences||{},view:'home',stack:[],botId:'companion',bots:defaultBots,primaryBotId:'companion',messages:[],pending:{},status:{},online:false,busy:false,busyBot:null,optimistic:null,mode:'auto',reply:null,files:[],folder:'all',category:'all',tools:[],routines:[],privilege:{},note:null,noteOriginal:'',configGroup:'connection',configReturn:'settings',toolId:null,botDraft:null,routineId:null};
const inflight=new Map();let sequence=0,foreground=true,refreshing=false,lastRefresh=0,toastTimer,lastMessages='',sheetActions={};
const navigationVisuals=[];
let draftSaveTimer;
PulseMotion.configure(()=>state.settings,kind=>{if(window.Native)action('haptic',{kind}).catch(()=>{});});
function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),4500);}
function bridge(kind,args){return new Promise((resolve,reject)=>{if(!window.Native){reject(new Error('请安装 APK 后连接你的节点。'));return;}const id=String(++sequence),timer=setTimeout(()=>{inflight.delete(id);reject(new Error('请求超时，请检查网络或模型配置。'));},kind==='action'&&['uploadFile','downloadFile','chooseAvatar'].includes(args[0])?600000:160000);inflight.set(id,{resolve,reject,timer});try{Native[kind](id,...args);}catch(e){clearTimeout(timer);inflight.delete(id);reject(e);}});}
window.onNativeReply=packet=>{const p=inflight.get(packet.id);if(!p)return;clearTimeout(p.timer);inflight.delete(packet.id);packet.ok?p.resolve(packet.data):p.reject(new Error(packet.error));};
const api=(method,path,body=null,query=null)=>bridge('request',[method,path,body===null?'':JSON.stringify(body),query===null?'':JSON.stringify(query)]);
const action=(name,args={})=>bridge('action',[name,JSON.stringify(args)]);
const localSettings=data=>bridge('settings',[data?JSON.stringify(data):'']);
const paired=()=>Boolean(state.settings.deviceToken);
const bot=()=>state.bots.find(b=>b.id===state.botId)||state.bots[0];
const botFor=id=>state.bots.find(b=>b.id===id)||state.bots[0];
const avatar=(b,size='')=>renderBotAvatar(b,size);
const timeOf=t=>new Date(t*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'});
const dayOf=t=>new Date(t*1000).toLocaleDateString('zh-CN',{month:'long',day:'numeric',timeZone:'Asia/Shanghai'});
function group(label,rows,footer=''){return `${label?`<div class="group-label">${esc(label)}</div>`:''}<div class="settings-group">${rows}</div>${footer?`<p class="field-hint">${esc(footer)}</p>`:''}`;}
function row(title,subtitle,ic,attrs,value=''){return `<button class="settings-row" ${attrs}>${icon(ic)}<span class="row-content"><strong>${esc(title)}</strong>${subtitle?`<small>${esc(subtitle)}</small>`:''}</span>${value?`<span class="row-value">${esc(value)}</span>`:''}${icon('next').replace('<svg','<svg class="chevron"')}</button>`;}
function toggleRow(title,subtitle,ic,attrs,on,id=''){return `<button class="settings-row" ${attrs}>${icon(ic)}<span class="row-content"><strong>${esc(title)}</strong>${subtitle?`<small>${esc(subtitle)}</small>`:''}</span><i ${id?`id="${id}"`:''} class="toggle ${on?'on':''}"></i></button>`;}
const pageTitles={settings:'设置',config:'连接',profile:'助手',botSettings:'助手设置',tools:'工具',toolDetail:'工具详情',vault:'记忆库',editor:'笔记',routines:'例行任务',routineDetail:'例行任务',root:'手机与虚拟屏',appearance:'外观',local:'此手机',computer:'云电脑',library:'资料库',hiddenBots:'已隐藏助手',identity:'编辑助手',avatarCrop:'裁剪头像',primaryBot:'主助手'};
function routeSnapshot(){return {view:state.view,botId:state.botId,configGroup:state.configGroup,toolId:state.toolId,routineId:state.routineId,scrollTop:(state.view==='chat'?$('chatScroll'):document.querySelector('.page.active'))?.scrollTop||0};}
function saveChatDraft(){if(state.view==='chat'){state.settings.drafts||={};state.settings.drafts[state.botId]=$('chatInput').value;state.settings.attachmentDrafts||={};state.settings.attachmentDrafts[state.botId]=state.attachments||[];}}
function restoreChatDraft(){if(state.view==='chat'){$('chatInput').value=state.settings.drafts?.[state.botId]||'';state.attachments=state.settings.attachmentDrafts?.[state.botId]||[];resizeComposer();}}
function go(view,{replace=false,prepare=null}={}){
  if(window.Native&&['local','root'].includes(view)){saveChatDraft();action('openLocal').catch(e=>toast(e.message));return;}
  saveChatDraft();const outgoing=PulseMotion.capture();
  document.activeElement?.blur();
  if(state.view!==view&&!replace){state.stack.push(routeSnapshot());navigationVisuals.push(outgoing);if(navigationVisuals.length>8)navigationVisuals[navigationVisuals.length-9]=null;}
  prepare?.();state.view=view;showRoute();
  const page=document.querySelector('.page.active');if(page&&view!=='chat')page.scrollTop=0;
  PulseMotion.route(outgoing,'forward');
}
function showRoute(){
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id==='page-'+state.view));
  $('mainHeader').hidden=state.view!=='home';$('subHeader').hidden=['home','chat'].includes(state.view);
  $('subTitle').textContent=pageTitles[state.view]||'';$('subAction').textContent='';$('subAction').onclick=null;
  if(state.view==='home'){renderBots();renderAttention();}
  if(state.view==='hiddenBots')renderHiddenBots();
  if(state.view==='chat'){renderChatHeader();renderMessages(true);ackVisible();}
  if(state.view==='settings')renderSettings();
  if(state.view==='profile')renderProfile();
  if(state.view==='identity')renderIdentity();
  if(state.view==='avatarCrop')renderAvatarCrop();
  if(state.view==='primaryBot')renderPrimaryPicker();
  if(state.view==='botSettings')renderBotSettings();
  if(state.view==='tools')renderTools();
  if(state.view==='toolDetail')renderToolDetail();
  if(state.view==='vault'){loadVault();$('subAction').textContent='新建';$('subAction').onclick=newNote;}
  if(state.view==='editor'){$('subTitle').textContent=state.note?.split('/').pop()||'笔记';$('subAction').textContent='保存';$('subAction').onclick=saveNote;}
  if(state.view==='routines'){renderRoutines();$('subAction').textContent='新建';$('subAction').onclick=()=>editRoutine();}
  if(state.view==='routineDetail')renderRoutineDetail();
  if(state.view==='root'){renderRoot();refreshPrivilege();$('subAction').textContent='检测';$('subAction').onclick=()=>refreshPrivilege(true);}
  if(state.view==='appearance')renderAppearance();
  if(state.view==='config')renderConfig();
  if(state.view==='local')renderLocal();
  if(state.view==='computer')renderComputer();
  if(state.view==='library')renderLibrary();
  devicesRouteChanged();
}
function back(){
  if($('sheet').open){closeSheet();return;}
  if(state.view==='identity'){identityBack();return;}
  if(state.view==='editor'&&$('markdownEditor').value!==state.noteOriginal){sheet('保留这份修改？',`<p class="sheet-description">这份笔记还没有保存到云端。</p><button class="primary full" data-sheet-action="save">保存并返回</button><button class="plain-button full" data-sheet-action="discard">放弃修改</button>`,{save:async()=>{await saveNote();if($('markdownEditor').value===state.noteOriginal){closeSheet();popRoute();}},discard:()=>{closeSheet();popRoute();}});return;}
  if(window.nativeEntry?.view===state.view&&!state.stack.length){Native.finishEntry();return;}
  popRoute();
}
function popRoute({animated=true}={}){
  if(window.nativeEntry?.view===state.view&&!state.stack.length){Native.finishEntry();return;}
  saveChatDraft();const outgoing=animated?PulseMotion.capture():null;
  const previous=state.stack.pop();navigationVisuals.pop();if(previous)Object.assign(state,previous);else state.view='home';
  lastMessages='';restoreChatDraft();showRoute();
  if(previous)requestAnimationFrame(()=>{const scroll=state.view==='chat'?$('chatScroll'):document.querySelector('.page.active');if(scroll)scroll.scrollTop=previous.scrollTop||0;});
  if(animated)PulseMotion.route(outgoing,'back');
}
function sheet(title,html,actions={}){const height=$('sheet').offsetHeight;$('sheetTitle').textContent=title;$('sheetBody').innerHTML=html;sheetActions=actions;PulseMotion.openSheet(height);}
function closeSheet(){return PulseMotion.closeSheet(()=>{sheetActions={};if(window.nativeEntry?.action==='createBot'&&state.view==='home')Native.finishEntry();});}
function menu(title,items){sheet(title,`<div class="sheet-menu">${items.map((m,i)=>`<button data-sheet-action="${i}">${icon(m.icon)}<span><strong>${esc(m.title)}</strong>${m.description?`<small>${esc(m.description)}</small>`:''}</span></button>`).join('')}</div>`,Object.fromEntries(items.map((m,i)=>[String(i),()=>{closeSheet();return m.run();}])));}
function requirePair(){if(paired())return true;openConfig('connection');toast('先连接你的私人节点。');return false;}
function setOnline(online,latency=''){
  state.online=online;$('connectionDot').classList.toggle('live',online);const node=(state.settings.serverUrl||'').includes('216.23.82.196')?'日本节点':'私人节点';$('connectionLabel').textContent=online?node+' · 加密连接':paired()?'暂时离线':'连接你的助理';
  $('connectionHint').textContent=online?latency:paired()?'点此检查连接':'开始配对';
  $('connectionBanner').hidden=online&&state.messages.length>0;
  $('accountSubtitle').textContent=online?node+'已连接':paired()?'连接暂不可用':'连接你的私人节点';
  updateComposer();
}
async function refresh(force=false){
  await syncPhoneHistory();
  if(window.nativeEntry?.view==='root'&&state.view==='root')return;
  if(!paired()||refreshing||(!force&&Date.now()-lastRefresh<1000))return;
  refreshing=true;lastRefresh=Date.now();const started=performance.now(),revision=botRevision;
  try{
    const [status,history,bots]=await Promise.all([api('GET','/api/status'),api('GET','/api/messages'),api('GET','/api/bots')]);
    state.status=status;state.messages=fusePhoneMessages(history.messages||[]);state.pending=history.pending||{};if(revision===botRevision)acceptBotRoster(bots);
    setOnline(true,Math.round(performance.now()-started)+' ms');renderBots();renderAttention();if(state.view==='chat'){renderMessages();ackVisible();}renderExecution();
    const snoozed=status.snooze_until*1000>Date.now();$('snoozeHome').textContent=snoozed?'恢复主动联系':'安静一小时';
  }catch(e){setOnline(false);if(force)toast(e.message);}finally{refreshing=false;}
}
async function loadMetadata(){
  if(window.nativeEntry?.view==='root'&&state.view==='root')return;
  if(!paired())return;
  try{const revision=botRevision;const [b,t,r,c]=await Promise.all([api('GET','/api/bots'),api('GET','/api/tools'),api('GET','/api/routines'),api('GET','/api/config')]);if(revision===botRevision)acceptBotRoster(b);state.tools=t.tools;state.routines=r.routines;
    const local=state.settings.config||{};state.settings.config={...c,small_brain:{...c.small_brain,api_key:local.small_brain?.api_key||''},big_brain:{...c.big_brain,api_key:local.big_brain?.api_key||''},smtp:{...c.smtp,password:local.smtp?.password||''}};
    renderBots();$('routineCount').textContent=state.routines.filter(r=>r.enabled).length||'';
  }catch(e){toast(e.message);}
}
function renderAttention(){
  const pending=state.messages.filter(m=>state.pending[m.id]&&m.kind==='result').slice(-2);
  $('attentionList').innerHTML=pending.map(m=>`<button class="attention-row" data-bot="${esc(m.bot_id||'companion')}"><strong>有一份成果等你确认</strong>${esc(m.text.slice(0,80))}</button>`).join('');
}
function openChat(id){go('chat',{prepare:()=>{state.mode='auto';state.reply=null;state.botId=id;lastMessages='';$('chatInput').value=state.settings.drafts?.[id]||'';state.attachments=state.settings.attachmentDrafts?.[id]||[];resizeComposer();}});updateComposer();ackVisible();}
function renderChatHeader(){const b=bot();$('chatIdentity').innerHTML=avatar(b,'small')+`<span>${esc(b.name)}</span>`;$('chatInput').placeholder='发消息给 '+b.name;$('conversationEmpty').innerHTML=avatar(b,'large')+`<h2>${esc(b.name)}</h2><p>${esc(b.description||'给它一件事情，带着结果回来。')}</p><div class="suggestions"><button data-suggest="帮我把今天的事情理一理。">理一理今天的事</button><button data-suggest="我有一件卡住的事，想跟你聊聊。">聊聊卡住的事</button><button data-suggest="我想查一些资料，先帮我明确问题。">开始一个小研究</button></div>`;updateComposer();}
// Markdown rendering follows OpenBot's marked + DOMPurify pattern; see THIRD_PARTY_NOTICES.md.
marked.setOptions({breaks:true,gfm:true});
function markdown(text){const source=String(text||'').replace(/\[\[([^\]\n]+)\]\]/g,(_,target)=>{const [path,label]=target.split('|');return `[${label||path}](#vault:${encodeURIComponent(path)})`;});return DOMPurify.sanitize(marked.parse(source),{USE_PROFILES:{html:true},FORBID_TAGS:['style','script','iframe','object','embed','form','img','video','audio','input'],FORBID_ATTR:['style','onerror','onclick','onload']});}
const toolInfo={read_file:{title:'读取文件',icon:'file',description:'查看工作区里的文本文件。',example:'请读取 research/opportunities-snapshot.md，整理关键内容。'},write_file:{title:'写入文件',icon:'edit',description:'将研究结果、清单或草稿保存为文件。',example:'请把下一步要做的事情写成 research/next-step.md。'},edit_file:{title:'编辑文件',icon:'edit',description:'精确替换工作区文件中的一段文字。',example:'我想修改一份工作区文件，请先问我路径和要替换的内容。'},run_bash:{title:'运行命令',icon:'code',description:'在隔离工作区执行命令并查看输出。',example:'请列出工作区文件，并说明每份文件的用途。'},web_fetch:{title:'读取网页',icon:'cloud',description:'打开公开网页，提取正文和来源。',example:'请先问我要阅读的网址，再提炼重点并保存摘要。'},web_search:{title:'联网搜索',icon:'search',description:'搜索公开资料，整理带来源的结果。',example:'请先问清楚我的研究主题，再搜索并整理三个可信来源。'}};
function traceHTML(trace,id){return `<div class="tool-trace">${trace.map((t,i)=>{const info=toolInfo[t.tool]||{title:t.tool,icon:'tools'},failed=!!t.result?.error||(typeof t.result?.exit_code==='number'&&t.result.exit_code!==0);return `<details class="tool-step"><summary>${icon(info.icon)}<strong>${esc(info.title)}</strong><small>${t.duration_ms!=null?t.duration_ms+' ms':''}</small><span class="${failed?'fail':'ok'}">${failed?'失败':'✓'}</span></summary><div class="trace-content"><h4>${esc(t.tool)} · 输入</h4><pre>${esc(JSON.stringify(t.arguments||{},null,2))}</pre><h4>输出</h4><pre>${esc(typeof t.result==='string'?t.result:JSON.stringify(t.result,null,2))}</pre><button class="trace-copy" data-trace-copy="${esc(id)}:${i}">复制执行记录</button></div></details>`;}).join('')}</div>`;}
function visibleMessages(){const messages=state.messages.filter(m=>(m.bot_id||'companion')===state.botId);if(state.optimistic&&state.optimistic.bot_id===state.botId&&!messages.some(m=>m.role==='user'&&m.text===state.optimistic.text&&m.created_at>=state.optimistic.created_at-3))return [...messages,state.optimistic];return messages;}
function messageVersion(m){const source=JSON.stringify([m.id,m.text,m.decision,m.trace]);let hash=2166136261;for(let i=0;i<source.length;i++)hash=Math.imul(hash^source.charCodeAt(i),16777619);return String(hash>>>0);}
function messageNode(m){
    const user=m.role==='user',computer=m.kind==='computer_result'||m.kind==='computer_question',result=m.kind==='result',stopped=m.kind==='stopped',label=m.kind==='curiosity'?'好奇心':m.engine==='local_prompt'?'本地引导':m.kind==='error'?'连接提示':'';
    let body=user?esc(m.text):`<div class="markdown">${markdown(m.text)}</div>`;
    if(computer){body=`<div class="result-heading">${icon(m.kind==='computer_question'?'screen':'check')}<span>${m.kind==='computer_question'?'需要接管':'云电脑成果'}</span></div><div class="result-body markdown">${markdown(m.text)}</div><button class="secondary full" data-route="computer">${m.kind==='computer_question'?'接管云电脑':'查看云电脑与文件'}</button>`;}
    else if(result||stopped){const trace=Array.isArray(m.trace)?m.trace:[];body=`<div class="result-heading">${icon(stopped?'close':'check')}<span>${stopped?'执行已停止':'成果已就绪'} · ${trace.length} 次工具调用</span></div><div class="result-body markdown">${markdown(m.text)}</div>${trace.length?traceHTML(trace,m.id):''}`;
      if(result)body+=m.decision?`<div class="decision">${m.decision==='accepted'?'✓ 已加入例行任务 · 每 24 小时执行':'已记下反馈，这次不继续。'}</div>`:`<div class="result-controls"><button class="accept" data-decision="yes" data-id="${esc(m.id)}">正是我要的，以后接着干</button><button class="reject" data-decision="no" data-id="${esc(m.id)}">方向不对，重新对齐</button></div>`;
    }
    if(m.kind==='escalation_demo')body+=`<button class="plain-button accent" data-ack="${esc(m.id)}">我看完了，结束演练</button>`;
    const template=document.createElement('template');template.innerHTML=`<article data-message-id="${esc(m.id)}" data-message-version="${messageVersion(m)}" class="message ${user?'user':''} ${result||computer?'result':''} ${stopped?'stopped':''}">${!user?`<div class="message-meta"><strong>${esc(botFor(m.bot_id||'companion').name)}</strong>${label?`<span class="kind-label">${label}</span>`:''}</div>`:''}<div class="message-bubble">${body}</div>${!m.optimistic?`<div class="message-actions"><button data-copy="${esc(m.id)}" aria-label="复制消息">${icon('copy')}</button><button data-reply="${esc(m.id)}">引用</button><time>${timeOf(m.created_at)}</time></div>`:''}</article>`;
    return template.content.firstElementChild;
}
function renderMessages(force=false){
  const messages=visibleMessages(),signature=state.botId+JSON.stringify(messages.map(m=>[m.id,m.decision,m.text,m.trace]));if(!force&&signature===lastMessages)return;lastMessages=signature;
  const scroll=$('chatScroll'),oldTop=scroll.scrollTop,atBottom=scroll.scrollHeight-oldTop-scroll.clientHeight<150;
  const oldNodes=new Map([...$('messages').querySelectorAll(':scope > [data-message-id]')].map(node=>[node.dataset.messageId,node]));
  const fragment=document.createDocumentFragment(),entering=[];let day='';
  $('conversationEmpty').hidden=messages.length>0;$('messages').hidden=!messages.length;
  for(const m of messages){
    const current=dayOf(m.created_at);
    if(current!==day){day=current;const divider=document.createElement('div');divider.className='date-divider';divider.textContent=current+' '+timeOf(m.created_at);fragment.append(divider);}
    const old=oldNodes.get(m.id);let node=old;
    if(!old||old.dataset.messageVersion!==messageVersion(m)){
      node=messageNode(m);
      if(old){const open=[...old.querySelectorAll('details')].map(detail=>detail.open);node.querySelectorAll('details').forEach((detail,index)=>detail.open=!!open[index]);}
      const replacesPending=!m.optimistic&&m.role==='user'&&[...oldNodes.values()].some(item=>item.dataset.messageId.startsWith('pending-')&&item.querySelector('.message-bubble')?.textContent===m.text);
      if(!old&&!force&&!replacesPending)entering.push([node,m.role==='user']);
    }
    fragment.append(node);
  }
  $('messages').replaceChildren(fragment);
  for(const [node,user]of entering)PulseMotion.reveal(node,{user});
  if(force||atBottom||state.optimistic)requestAnimationFrame(()=>scroll.scrollTo({top:scroll.scrollHeight,behavior:force||PulseMotion.reduced()?'instant':'smooth'}));
  else scroll.scrollTop=oldTop;
}
function renderExecution(){const execution=state.status.execution,here=state.view==='chat'&&execution?.bot_id===state.botId;$('executionLive').hidden=!here;if(here)$('executionLive').innerHTML=`<h4>正在执行</h4>${execution.trace.map(t=>`<div>✓ ${esc(toolInfo[t.tool]?.title||t.tool)}</div>`).join('')}<div>${execution.tool?'◌ '+esc(toolInfo[execution.tool]?.title||execution.tool):'正在梳理下一步…'}</div>`;updateComposer();}
function updateComposer(){const b=bot(),big=state.mode==='execute'||state.mode==='auto'&&b.engine==='big';const config=state.settings.config||{},model=(big?config.big_brain:config.small_brain)?.model;
  const place=state.mode==='phone'?'此手机':state.mode==='computer'?'云电脑':null;
  $('modeButton').innerHTML=icon(place==='此手机'?'screen':place?'cloud':big?'tools':'spark')+`<span>${place|| (big?'执行任务':'对话')}${!place&&model?' · '+esc(model):''} ▾</span>`;
  const computer=state.status.computer,cloudRunning=computer?.bot_id===state.botId&&['running','paused','waiting_user'].includes(computer.job?.state);
  const isBusy=state.busy&&state.busyBot===state.botId,executing=state.status.execution?.bot_id===state.botId||isBusy&&(big||state.mode==='computer')||cloudRunning;
  $('sendButton').disabled=state.busy||!$('chatInput').value.trim()&&!state.attachments?.length;$('sendButton').hidden=!!executing;$('stopButton').hidden=!executing;$('thinking').hidden=!isBusy||!!state.status.execution;$('thinkingText').textContent=big?'正在处理你的任务':'正在思考';
  $('composerStatus').textContent=state.online?(state.status.snooze_until*1000>Date.now()?'免打扰中':big?(state.status.big_ready?'执行模型已就绪':'待配置执行模型'):(state.status.small_ready?'在线':'本地引导')):'尚未连接';
  $('replyContext').hidden=!state.reply&&!state.attachments?.length;if(state.attachments?.length)$('replyContext').innerHTML=`<span>附件：${state.attachments.map(a=>esc(a.name)).join('、')}</span><button id="clearAttachments" aria-label="移除附件">×</button>`;if(state.reply)$('replyContext').innerHTML=`<span>引用：${esc(state.reply.text)}</span><button id="clearReply" aria-label="取消引用">×</button>`;
}
async function ackVisible(){if(state.view!=='chat'||!foreground||document.hidden||!state.online)return;const visible=new Set(visibleMessages().map(m=>m.id)),ids=Object.keys(state.pending).filter(id=>visible.has(id)&&!state.pending[id].demo);if(ids.length){try{await api('POST','/api/ack',{ids});ids.forEach(id=>delete state.pending[id]);renderBots();}catch(e){}}}
async function sendMessage(event){event.preventDefault();let text=$('chatInput').value.trim()||(state.attachments?.length?'请查看这些附件。':'');if(!text||state.busy)return;if(state.mode==='phone'){if(state.attachments?.length){toast('手机助理内可直接选择附件，请在手机助理中添加。');return;}try{await sendToPhone(text);}catch(e){toast(e.message);}return;}if(!requirePair())return;const selected=state.botId,originalDraft=$('chatInput').value;
  if(state.attachments?.length){const expected=state.mode==='computer'?'computer':'workspace';if(state.attachments.some(a=>a.source!==expected)){toast('附件在另一处工作区，请选择对应的云电脑或对话处理方式。');return;}text+='\n\n附件（工作区相对路径，可用文件或 Bash 工具读取）：\n'+state.attachments.map(a=>a.path.replace(/^workspace\//,'')).join('\n');}
  if(state.reply)text='> '+state.reply.text.slice(0,600).replace(/\n/g,'\n> ')+'\n\n'+text;
  state.busy=true;state.busyBot=selected;state.optimistic={id:'pending-'+Date.now(),bot_id:selected,role:'user',text,created_at:Date.now()/1000,optimistic:true};state.reply=null;$('chatInput').value='';state.settings.drafts||={};state.settings.drafts[selected]='';resizeComposer();renderMessages();updateComposer();
  try{if(state.mode==='computer'){await api('POST','/api/computer/run',{goal:text,bot_id:selected,max_steps:24});toast('云电脑开始执行，可从首页查看或接管。');}else await api('POST','/api/chat',{text,bot_id:selected,mode:state.mode});state.optimistic=null;state.settings.attachmentDrafts||={};state.settings.attachmentDrafts[selected]=[];if(state.botId===selected)state.attachments=[];await refresh(true);await loadRoutines();}
  catch(e){state.optimistic=null;state.settings.drafts[selected]=originalDraft;if(state.botId===selected)$('chatInput').value=originalDraft;toast(e.message);await refresh();}
  finally{state.busy=false;state.busyBot=null;state.optimistic=null;renderMessages();updateComposer();}
}
function resizeComposer(){$('chatInput').style.height='auto';$('chatInput').style.height=Math.min(135,$('chatInput').scrollHeight)+'px';updateComposer();}
function modeMenu(){menu('选择处理方式',[{icon:'screen',title:'此手机',description:'在 Root 独立屏上执行任务',run:()=>{state.mode='phone';updateComposer();}},{icon:'cloud',title:'云电脑',description:'浏览网页、完成工作，随时看画面或接管',run:cloudComposerMenu},{icon:'chat',title:'对话',description:state.settings.config?.small_brain?.model||'使用常驻小脑，整理思路与记忆',run:()=>{state.mode='chat';updateComposer();}},{icon:'tools',title:'执行任务',description:state.settings.config?.big_brain?.model||'使用执行大脑与此助手启用的工具',run:()=>{state.mode='execute';updateComposer();}}]);}
function chatActions(){menu('对话工具',[{icon:'cloud',title:'打开云电脑',description:'查看实时画面、接管或下载文件',run:()=>go('computer')},{icon:'screen',title:'打开手机助理',description:'Root 工作区、执行过程与接管',run:()=>go('local')},{icon:'spark',title:'触发好奇心追问',description:'让主助手问一个具体的问题',run:()=>runJob('curiosity')},{icon:'search',title:'闲时信息搜集试探',description:'先完成一步，再把成果交给你',run:()=>runJob('research')},{icon:'tools',title:'查看可用工具',description:'文件、联网、命令与手机能力',run:()=>go('tools')},{icon:'file',title:'添加附件',description:'上传到当前处理方式的工作区',run:()=>uploadFile(state.mode==='computer'?'computer':'workspace',true)},{icon:'folder',title:'资料库',run:()=>go('library')},{icon:'vault',title:'打开透明记忆库',run:()=>go('vault')},{icon:'bell',title:'演练三级催单',description:'通知 → 邮件检查 → 全局提示卡',run:()=>runJob('escalation')},{icon:'moon',title:'别吵我 1 小时',run:()=>runJob('snooze')}]);}
async function runJob(job){if(!requirePair()||state.busy)return;state.busy=true;state.busyBot=state.botId;updateComposer();try{
  if(job==='curiosity'){state.botId=state.primaryBotId;renderChatHeader();await api('POST','/api/trigger-curiosity');}
  if(job==='research')await api('POST','/api/research',{bot_id:state.botId});
  if(job==='snooze'||job==='wake'){await api('POST','/api/snooze',{minutes:job==='snooze'?60:0});toast(job==='snooze'?'好，安静一小时。':'已恢复主动联系。');}
  if(job==='escalation'){await api('POST','/api/snooze',{minutes:0});state.settings.background=true;await localSettings(state.settings);await action('background',{enabled:true});await api('POST','/api/test-escalation');toast('演练已开始：4 秒检查邮件，8 秒请求提示卡。');}
  await refresh(true);
}catch(e){toast(e.message);}finally{state.busy=false;state.busyBot=null;updateComposer();}}
function renderProfile(){renderBotProfile();}
function renderBotSettings(){const b=state.botDraft?.id===state.botId?state.botDraft:{...bot(),tools:[...bot().tools]};state.botDraft=b;$('botSettings').innerHTML=`<form id="botForm"><div class="bot-profile-identity">${avatar(b,'large')}</div><label>名称<input id="botName" maxlength="40" value="${esc(b.name)}" required></label><label>简介<input id="botDescription" maxlength="160" value="${esc(b.description)}"></label><label>角色设定<textarea id="botInstructions" rows="6" maxlength="12000" placeholder="它负责什么，应该怎样与你工作？">${esc(b.instructions)}</textarea></label><label>默认处理方式<select id="botEngine"><option value="small" ${b.engine==='small'?'selected':''}>对话 · 常驻小脑</option><option value="big" ${b.engine==='big'?'selected':''}>执行 · 主理人大脑</option></select></label><div class="group-label">可用工具</div><p class="field-hint">执行模式仅能调用已勾选的工具。</p><div class="tool-checks">${cloudNames.map(t=>`<label><input type="checkbox" name="botTool" value="${t}" ${b.tools.includes(t)?'checked':''}>${esc(toolInfo[t].title)}</label>`).join('')}</div><button class="primary full" type="submit" id="saveBot">保存助手</button><p id="botSaveFeedback" class="field-feedback"></p></form>`;$('botForm').onsubmit=saveBot;}
function readBotDraft(){if(!$('botName'))return;Object.assign(state.botDraft,{name:$('botName').value.trim(),description:$('botDescription').value.trim(),instructions:$('botInstructions').value,engine:$('botEngine').value,tools:[...document.querySelectorAll('[name=botTool]:checked')].map(i=>i.value)});}
async function saveBot(event){event.preventDefault();readBotDraft();if(!state.botDraft.name){toast('给助手起一个名字。');return;}$('saveBot').disabled=true;try{const b=await api('PUT','/api/bots/'+state.botId,state.botDraft);acceptBotUpdate(b);state.botDraft=null;toast('助手已保存。');back();}catch(e){$('botSaveFeedback').textContent=e.message;$('saveBot').disabled=false;}}
const shapes=['circle','oval','square','pill','triangle','diamond','flower','drop'];
const colors=['#F3F3F5','#996B39','#FF3154','#FF742E','#FFA000','#00C982','#02B8AD','#1688FF','#9254FF','#FF2BA4','#858585'];
function avatarPicker(create=true){if(create)state.botDraft={name:'',description:'',instructions:'',engine:'small',color:'#FF742E',shape:'flower',tools:[...cloudNames],notify:true};const b=state.botDraft;
  sheet(create?'创建新助手':'编辑头像',`<div class="bot-editor-avatar" id="draftAvatar">${avatar(b,'large')}</div>${create?'<label><input id="createBotName" maxlength="40" placeholder="助手名称" aria-label="助手名称"></label>':''}<div id="shapeOptions" class="shape-options">${shapes.map(shape=>`<button data-shape="${shape}" class="${b.shape===shape?'selected':''}" aria-label="${shape} 头像">${avatar({...b,shape})}</button>`).join('')}</div><div id="colorOptions" class="color-options">${colors.map(color=>`<button data-color="${color}" style="background:${color}" class="${b.color===color?'selected':''}" aria-label="头像颜色 ${color}"></button>`).join('')}</div><button class="primary full" id="commitAvatar">${create?'创建':'完成'}</button><p id="createBotFeedback" class="field-feedback"></p>`,{});
  $('commitAvatar').onclick=async()=>{if(!create){closeSheet();renderBotSettings();return;}const name=$('createBotName').value.trim();if(!name){$('createBotName').focus();return;}if(!paired()){$('createBotFeedback').textContent='先在设置中连接私人节点，再创建助手。';return;}$('commitAvatar').disabled=true;try{const created=await api('POST','/api/bots',{...b,name});acceptBotUpdate(created);closeSheet();state.botDraft=null;if(window.nativeEntry?.action==='createBot'){Native.createdBot(created.id);return;}openChat(created.id);toast('助手已创建。点击顶部名字，继续设置角色。');}catch(e){$('createBotFeedback').textContent=e.message;$('commitAvatar').disabled=false;}};
}
function updateDraftAvatar(){const b=state.botDraft;$('draftAvatar').innerHTML=avatar(b,'large');document.querySelectorAll('[data-shape]').forEach(el=>{el.classList.toggle('selected',el.dataset.shape===b.shape);el.innerHTML=avatar({...b,shape:el.dataset.shape});});document.querySelectorAll('[data-color]').forEach(el=>el.classList.toggle('selected',el.dataset.color===b.color));}
function renderSettings(){const c=state.settings.config||{},small=c.small_brain||{},big=c.big_brain||{},smtp=c.smtp||{};
  $('settingsRows').innerHTML=group('连接与模型',row('云端节点',state.settings.serverUrl||'连接你的私人节点','cloud','data-config="connection"',state.online?'已连接':paired()?'离线':'未配对')+row('常驻小脑',small.model||'对话、记忆与主动追问','spark','data-config="small"')+row('执行大脑',big.model||'工具调用与视觉操作','tools','data-config="big"')+row('邮件',smtp.recipient||'催单邮件与 SMTP 配置','mail','data-config="mail"'))+group('工作空间',row('资料库','上传的资料与助手的成果文件','folder','data-route="library"')+row('云电脑','查看画面、接管与成果文件','cloud','data-route="computer"')+row('此手机','Root 独立屏与本机执行','screen','data-route="local"')+row('连接工具','','tools','data-route="tools"')+row('透明记忆库','','vault','data-route="vault"')+row('例行任务','','clock','data-route="routines"')+row('手机与虚拟屏','','screen','data-route="root"'))+group('偏好',row('已隐藏助手','保留对话与例行任务','hide','data-route="hiddenBots"',String(state.bots.filter(b=>b.hidden).length))+row('外观','','spark','data-route="appearance"',({dark:'深色',light:'浅色',system:'跟随系统'})[state.settings.appearance||'system'])+row('主动节奏','追问间隔、升级时间与夜间安静','clock','data-config="rhythm"')+row('免打扰一小时','','moon','data-snooze="toggle"',state.status.snooze_until*1000>Date.now()?'已开启':'未开启'));
}
const input=(id,label,value='',placeholder='',type='text',attrs='')=>`<label>${esc(label)}<input id="${id}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${attrs} autocomplete="off"></label>`;
const secret=(id,label,value='',placeholder='')=>`<label>${esc(label)}<div class="secret-field"><input id="${id}" type="password" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off"><button type="button" data-eye="${id}">显示</button></div></label>`;
function openConfig(groupName){state.configGroup=groupName;go('config');}
function renderConfig(){const c=state.settings.config||{},name=state.configGroup,brain=name==='small'?c.small_brain:c.big_brain,b=brain||{},mail=c.smtp||{};
  const copy={connection:['云端节点','连接你自己的助理。默认节点已填好，只需复制私人报告中的配对码。'],small:['常驻小脑','负责日常对话、整理记忆，和主动问一个小问题。'],big:['执行大脑','负责调用工具完成任务。视觉操作需要支持图片输入的模型。'],mail:['邮件','用于通知长时间未读时的第二级提醒。'],rhythm:['主动节奏','让它主动做事，也给生活留一点安静。']};
  $('subTitle').textContent=copy[name][0];$('configIntro').textContent=copy[name][1];$('configFeedback').textContent='';
  if(name==='connection')$('configFields').innerHTML=input('serverUrl','Server URL',state.settings.serverUrl||'http://216.23.82.196/proactive-agent','','url','required')+secret('deviceToken','私人设备配对码',state.settings.deviceToken||'','从私人交付报告复制')+`<details class="advanced"><summary>自建节点公钥</summary><label>RSA 公钥 · Base64<textarea id="publicKey" rows="4" placeholder="默认节点无需填写">${esc(state.settings.publicKey||'')}</textarea></label></details><p class="field-hint">配对码不会包含在公开 APK 中。</p>`;
  if(name==='small'||name==='big')$('configFields').innerHTML=`<div class="presets"><button type="button" data-preset="deepseek">DeepSeek</button><button type="button" data-preset="openai">OpenAI 兼容</button></div>`+input('modelBase','API Base URL',b.base_url||'https://api.openai.com/v1','https://api.example.com/v1','url')+secret('modelKey','API Key',b.api_key||'',b.has_api_key?'云端已保存，留空保留':'填入你的 API Key')+input('modelName','Model',b.model||'','模型名称')+`<p class="field-hint">支持 OpenAI 兼容接口。密钥以加密载荷同步到私人节点。</p>`;
  if(name==='mail')$('configFields').innerHTML=input('recipient','收件邮箱',mail.recipient||'','you@example.com','email')+`<div class="form-row">${input('smtpHost','SMTP 主机',mail.host||'','smtp.example.com')}${input('smtpPort','端口',mail.port||587,'','number','min="1" max="65535"')}</div>`+input('smtpUser','SMTP 用户名',mail.username||'')+secret('smtpPassword','SMTP 授权码',mail.password||'',mail.has_password?'云端已保存，留空保留':'邮箱授权码或应用专用密码')+input('smtpSender','发件地址',mail.sender||'','留空使用 SMTP 用户名','email')+`<label>加密方式<select id="smtpSecurity"><option value="starttls" ${mail.security!=='ssl'?'selected':''}>STARTTLS · 587</option><option value="ssl" ${mail.security==='ssl'?'selected':''}>SSL / TLS · 465</option></select></label><button type="button" class="secondary full" id="testEmail">发送测试邮件</button><p class="field-hint" style="margin-top:12px">先保存，再测试。每个待办最多 3 封；已读或免打扰后停止。</p>`;
  if(name==='rhythm')$('configFields').innerHTML=`<div class="form-row">${input('curiosityMinutes','追问间隔 · 分钟',c.curiosity_interval_minutes||90,'','number','min="5" max="1440"')}${input('idleMinutes','闲时研究 · 分钟',c.idle_research_minutes||30,'','number','min="5" max="1440"')}</div><div class="form-row">${input('emailMinutes','邮件升级 · 分钟',c.email_after_minutes||15,'','number','min="1" max="1440"')}${input('overlayMinutes','提示卡升级 · 分钟',c.overlay_after_minutes||30,'','number','min="2" max="2880"')}</div><div class="form-row">${input('quietStart','夜间安静 · 开始',c.quiet_start_hour??0,'','number','min="0" max="23"')}${input('quietEnd','结束小时',c.quiet_end_hour??8,'','number','min="0" max="23"')}</div><p class="field-hint">使用北京时间。夜间暂停主动追问和闲时研究；进行中的催单可随时免打扰。</p>`;
}
function configUpdate(){const name=state.configGroup,num=id=>Number($(id).value);if(name==='small'||name==='big'){const key=name==='small'?'small_brain':'big_brain',old=state.settings.config?.[key]||{},value={base_url:$('modelBase').value.trim(),model:$('modelName').value.trim()};if($('modelKey').value.trim()||!old.has_api_key)value.api_key=$('modelKey').value.trim();return {[key]:value};}
  if(name==='mail'){const smtp={host:$('smtpHost').value.trim(),port:num('smtpPort'),username:$('smtpUser').value.trim(),recipient:$('recipient').value.trim(),sender:$('smtpSender').value.trim(),security:$('smtpSecurity').value};if($('smtpPassword').value||!state.settings.config?.smtp?.has_password)smtp.password=$('smtpPassword').value;return {smtp};}
  if(name==='rhythm')return {curiosity_interval_minutes:num('curiosityMinutes'),idle_research_minutes:num('idleMinutes'),email_after_minutes:num('emailMinutes'),overlay_after_minutes:num('overlayMinutes'),quiet_start_hour:num('quietStart'),quiet_end_hour:num('quietEnd')};return null;}
function mergeConfig(update){const config=state.settings.config||{};for(const [key,value] of Object.entries(update)){config[key]=typeof value==='object'?{...config[key],...value}:value;}state.settings.config=config;}
async function saveConfig(event){event.preventDefault();const button=$('saveConfig');button.disabled=true;button.textContent='正在同步…';$('configFeedback').textContent='';try{
    if(state.configGroup==='connection'){const token=$('deviceToken').value.trim();if(!token)throw new Error('请填入私人设备配对码。');Object.assign(state.settings,{serverUrl:$('serverUrl').value.trim().replace(/\/+$/,''),deviceToken:token,publicKey:$('publicKey').value.trim()});}
    const update=configUpdate();if(update)mergeConfig(update);await localSettings(state.settings);
    if(!paired()){throw new Error('本机已保存。请先连接云端节点，再同步。');}
    if(update)await api('POST','/api/config',update);else await api('GET','/api/status');
    state.settings.background=true;await localSettings(state.settings);await action('reconnect');await loadMetadata();await refresh(true);$('configFeedback').textContent='✓ 云端已保存';toast('已保存并同步。');
  }catch(e){$('configFeedback').textContent=e.message;toast(e.message);}finally{button.disabled=false;button.textContent='保存并同步';}}
async function syncAll(){if(!requirePair())return;$('syncButton').disabled=true;$('syncFeedback').textContent='正在同步…';try{await localSettings(state.settings);const config=JSON.parse(JSON.stringify(state.settings.config||{}));for(const key of ['small_brain','big_brain']){if(config[key]){delete config[key].has_api_key;if(!config[key].api_key)delete config[key].api_key;}}if(config.smtp){delete config.smtp.has_password;if(!config.smtp.password)delete config.smtp.password;}config.enabled=true;await api('POST','/api/config',config);state.settings.background=true;await localSettings(state.settings);await action('reconnect');await refresh(true);$('syncFeedback').textContent='✓ 云端已保存，Harness 循环已唤醒。';}catch(e){$('syncFeedback').textContent=e.message;}finally{$('syncButton').disabled=false;}}
function applyAppearance(){const appearance=state.settings.appearance||'system',systemDark=state.settings.systemDark??matchMedia('(prefers-color-scheme: dark)').matches,dark=appearance==='dark'||appearance==='system'&&systemDark;document.documentElement.dataset.theme=dark?'dark':'light';document.documentElement.dataset.motion=state.settings.motion===false?'off':'on';if(window.Native)action('appearance',{dark,appearance,motion:state.settings.motion!==false,haptics:state.settings.haptics!==false}).catch(()=>{});}
function renderAppearance(){$('appearanceContent').innerHTML=group('外观',[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([value,title])=>row(title,'','spark',`data-appearance="${value}"`,(state.settings.appearance||'system')===value?'✓':'')).join(''))+group('交互',toggleRow('动态效果','页面、面板与消息的自然过渡','spark','data-preference="motion"',state.settings.motion!==false)+toggleRow('触觉反馈','轻触确认与开关反馈','settings','data-preference="haptics"',state.settings.haptics!==false),'动态效果始终尊重系统的减弱动画设置。');}
const extraTools=[{id:'cloud_computer',title:'云电脑',icon:'cloud',description:'独立桌面与浏览器，交代任务、实时接管和下载文件。',category:'cloud',route:'computer'},{id:'root_phone',title:'本机手机助理',icon:'screen',description:'在独立屏操作应用，共用执行模型与任务记录。',category:'phone',route:'local'},{id:'virtual_display',title:'独立虚拟屏',icon:'screen',description:'创建独立屏幕、截图与坐标点击。',category:'phone',route:'root'},{id:'notifications',title:'通知与全局提示卡',icon:'bell',description:'从轻提醒到第三级催单提示。',category:'phone',route:'root'},{id:'smtp',title:'邮件催单',icon:'mail',description:'未读时通过你的 SMTP 邮箱提醒。',category:'automation',config:'mail'},{id:'routines',title:'例行任务',icon:'clock',description:'按设定的间隔重复一项任务。',category:'automation',route:'routines'},{id:'memory',title:'透明记忆库',icon:'vault',description:'查看、编辑助理记住的 Markdown。',category:'automation',route:'vault'}];
function allTools(){return [...cloudNames.map(id=>({id,...toolInfo[id],category:'cloud',parameters:state.tools.find(t=>t.id===id)?.parameters})),...extraTools];}
function renderTools(){const query=$('toolSearch').value.toLowerCase(),list=allTools().filter(t=>(state.category==='all'||state.category===t.category)&&(!query||(t.title+' '+t.description+' '+t.id).toLowerCase().includes(query)));$('toolList').innerHTML=list.length?list.map(t=>`<button class="tool-row" data-tool="${t.id}"><span class="tool-icon">${icon(t.icon)}</span><span class="tool-text"><strong>${esc(t.title)}</strong><small>${esc(t.description)}</small></span><span class="tool-status ${t.category==='cloud'&&state.online?'ready':''}">${t.category==='cloud'?'云端':t.category==='phone'?'手机':'设置'}</span></button>`).join(''):'<div class="empty-state">没有匹配的工具</div>';}
function openTool(id){state.toolId=id;go('toolDetail');}
function renderToolDetail(){const t=allTools().find(t=>t.id===state.toolId);if(!t)return;const conditions=t.category==='cloud'?t.id==='run_bash'?'命令运行在服务器的隔离工作区，最长 25 秒。':'由执行模型发起调用；只使用此助手启用的工具。':t.id==='virtual_display'?'创建独立屏、截图与点击需要 Root 与 Android 14+。视觉分析需要执行模型支持图片。':t.id==='smtp'?'填写收件邮箱和可用的 SMTP 授权码后启用。':'在对应设置中查看当前状态。';$('toolDetail').innerHTML=`<div class="tool-detail-heading"><span class="entry-icon">${icon(t.icon)}</span><h1>${esc(t.title)}</h1><small>${t.category==='cloud'?esc(t.id):'你的私人助理工具'}</small></div><p class="intro-text">${esc(t.description)}</p>${group('使用要求',row(conditions,'',t.icon,'data-noop="true"'))}${t.parameters?`<div class="group-label">调用参数</div><pre class="tool-schema">${esc(JSON.stringify(t.parameters,null,2))}</pre>`:''}<button id="useTool" class="primary full">${t.category==='cloud'?'在对话中使用':'打开设置'}</button>`;$('useTool').onclick=()=>{if(t.route){go(t.route);return;}if(t.config){openConfig(t.config);return;}const researcher=state.bots.find(b=>b.engine==='big')||state.bots[0];openChat(researcher.id);state.mode='execute';$('chatInput').value=t.example;resizeComposer();toast('补充你的目标，然后发送。');};}
async function loadVault(){if(!paired()){$('vaultFiles').innerHTML=`<div class="empty-state">${icon('vault')}连接私人节点后查看记忆库<button class="secondary" data-config="connection">连接节点</button></div>`;return;}try{state.files=(await api('GET','/api/vault')).files;$('vaultCount').textContent=state.files.length;renderVaultFiles();}catch(e){$('vaultFiles').innerHTML=`<div class="error-card">${esc(e.message)}</div>`;}}
const folderNames={profile:'个人档案',tasks:'任务',timeline:'时间轴',playbooks:'常驻手册',notes:'我的笔记','首页':'首页'};
function renderVaultFiles(){const query=$('fileSearch').value.toLowerCase(),files=state.files.filter(f=>(state.folder==='all'||f.path.startsWith(state.folder+'/'))&&f.path.toLowerCase().includes(query));let previous='';$('vaultFiles').innerHTML=files.length?files.map(f=>{const folder=f.path.includes('/')?f.path.split('/')[0]:'首页',heading=previous!==folder?`<div class="file-group">${esc(folderNames[folder]||folder)}</div>`:'';previous=folder;return heading+`<button class="file-row" data-file="${esc(f.path)}">${icon('file')}<span><strong>${esc(f.path.split('/').pop())}</strong><small>${(f.bytes/1024).toFixed(1)} KB · Markdown</small></span>${icon('next')}</button>`;}).join(''):'<div class="empty-state">没有匹配的记忆文件</div>';}
async function openNote(path,isNew=false){try{const data=isNew?{content:'# '+path.split('/').pop().replace(/\.md$/,'')+'\n\n'}:await api('GET','/api/vault',null,{path});state.note=path;state.noteOriginal=data.content;$('markdownEditor').value=data.content;$('markdownPreview').innerHTML=markdown(data.content);go('editor');setEditorMode(false);$('editorHint').textContent=isNew?'新笔记 · 保存后同步到云端':'云端已同步';}catch(e){toast(e.message);}}
function setEditorMode(edit){$('markdownPreview').hidden=edit;$('markdownEditor').hidden=!edit;$('previewTab').classList.toggle('selected',!edit);$('editTab').classList.toggle('selected',edit);if(!edit)$('markdownPreview').innerHTML=markdown($('markdownEditor').value);}
async function saveNote(){if(!state.note)return;$('subAction').disabled=true;try{await api('PUT','/api/vault',{path:state.note,content:$('markdownEditor').value});state.noteOriginal=$('markdownEditor').value;$('editorHint').textContent='✓ 已同步 · '+timeOf(Date.now()/1000);toast('记忆已保存。');}catch(e){toast(e.message);}finally{$('subAction').disabled=false;}}
function newNote(){if(!requirePair())return;sheet('新建记忆',`<form id="newNoteForm">${input('newNotePath','Markdown 路径','','notes/我的想法.md','text','required')}<button class="primary full" type="submit">创建</button></form>`);$('newNoteForm').onsubmit=e=>{e.preventDefault();const path=$('newNotePath').value.trim();if(!path.endsWith('.md')||path.includes('..')||/^[\\/]|:/.test(path)){toast('使用相对路径，例如 notes/我的想法.md');return;}closeSheet();openNote(path,true);};}
async function loadRoutines(){if(!paired())return;try{state.routines=(await api('GET','/api/routines')).routines;$('routineCount').textContent=state.routines.filter(r=>r.enabled).length||'';if(state.view==='routines')renderRoutines();}catch(e){toast(e.message);}}
function renderRoutines(){const selected=state.stack.at(-1)?.view==='profile'?state.botId:null,items=state.routines.filter(r=>!selected||(r.bot_id||'companion')===selected);$('routineList').innerHTML=items.length?items.map(r=>`<button class="routine-card" data-routine="${esc(r.id)}"><span class="entry-icon">${icon('clock')}</span><span><strong>${esc(r.goal)}</strong><small>${r.enabled?'每 '+r.interval_hours+' 小时':'已暂停'} · ${esc(botFor(r.bot_id||'companion').name)}</small></span></button>`).join(''):`<div class="empty-state">${icon('clock')}把需要重复的事，交给助手。<button class="secondary" id="emptyRoutineCreate">创建例行任务</button></div>`;if($('emptyRoutineCreate'))$('emptyRoutineCreate').onclick=()=>editRoutine();loadRoutinesOnce();}
let routineLoadedAt=0;function loadRoutinesOnce(){if(Date.now()-routineLoadedAt<5000)return;routineLoadedAt=Date.now();loadRoutines();}
function openRoutine(id){state.routineId=id;go('routineDetail');}
function renderRoutineDetail(){const r=state.routines.find(r=>r.id===state.routineId);if(!r)return;const next=new Date((r.last_run+r.interval_hours*3600)*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'});$('routineDetail').innerHTML=`<div class="tool-detail-heading"><span class="entry-icon">${icon('clock')}</span><h1>例行任务</h1></div><h2 class="routine-title">${esc(r.goal)}</h2>`+group('',row('负责的助手','','chat',`data-bot="${esc(r.bot_id||'companion')}"`,botFor(r.bot_id||'companion').name)+row('重复间隔','','clock','data-noop="true"','每 '+r.interval_hours+' 小时')+row('下次执行','','clock','data-noop="true"',r.enabled?next:'已暂停')+toggleRow('启用任务','免打扰与夜间安静时顺延','bell',`data-routine-toggle="${esc(r.id)}"`,r.enabled))+`<p class="section-foot">每次执行都会在对应助手的对话中留下工具记录与结果。</p><div class="routine-actions"><button class="primary" id="runRoutine">立即执行一次</button></div>`;$('runRoutine').onclick=async()=>{if(state.busy)return;state.busy=true;$('runRoutine').disabled=true;try{await api('POST','/api/routines/'+r.id+'/run');await refresh(true);openChat(r.bot_id||'companion');}catch(e){toast(e.message);}finally{state.busy=false;if($('runRoutine'))$('runRoutine').disabled=false;}};}
function editRoutine(){if(!requirePair())return;sheet('创建例行任务',`<form id="routineForm"><label>让助手做什么<textarea id="routineGoal" rows="4" required maxlength="3000" placeholder="例如：整理任务池，给出今天最值得先做的一步"></textarea></label><label>负责的助手<select id="routineBot">${state.bots.map(b=>`<option value="${esc(b.id)}" ${b.id==='research'?'selected':''}>${esc(b.name)}</option>`).join('')}</select></label>${input('routineHours','每隔多少小时',24,'','number','min="1" max="720" required')}<button class="primary full" id="createRoutine" type="submit">创建</button><p id="routineFeedback" class="field-feedback"></p></form>`);$('routineForm').onsubmit=async e=>{e.preventDefault();$('createRoutine').disabled=true;try{const r=await api('POST','/api/routines',{goal:$('routineGoal').value.trim(),bot_id:$('routineBot').value,interval_hours:Number($('routineHours').value)});state.routines.push(r);closeSheet();renderRoutines();toast('例行任务已创建。');}catch(error){$('routineFeedback').textContent=error.message;$('createRoutine').disabled=false;}};}
function renderRoot(){$('rootContent').innerHTML=deviceHero('screen','ON THIS PHONE','手机工作区','与对话共用模型、任务和执行记录。')+'<button class="primary full" id="ownRootWorkspace">打开 Root 工作区</button>';$('ownRootWorkspace').onclick=()=>openPhone();}
async function refreshPrivilege(){if(window.Native)try{state.privilege=await action('status');}catch(e){toast(e.message);}}
document.addEventListener('click',async event=>{
  const el=event.target.closest('button,a');if(!el)return;
  try{
    if(el.dataset.sheetAction!==undefined){if(!PulseMotion.sheetClosing)await sheetActions[el.dataset.sheetAction]?.();return;}
    if(el.dataset.restoreBot){await setBotPlacement(el.dataset.restoreBot,{hidden:false});return;}
    if(el.dataset.bot){openChat(el.dataset.bot);return;}
    if(el.dataset.route){go(el.dataset.route);return;}
    if(el.dataset.config){openConfig(el.dataset.config);return;}
    if(el.dataset.snooze){runJob(state.status.snooze_until*1000>Date.now()?'wake':'snooze');return;}
    if(el.dataset.suggest){$('chatInput').value=el.dataset.suggest;resizeComposer();$('chatInput').focus();return;}
    if(el.dataset.eye){const input=$(el.dataset.eye);input.type=input.type==='password'?'text':'password';el.textContent=input.type==='password'?'显示':'隐藏';return;}
    if(el.dataset.preset){$('modelBase').value=el.dataset.preset==='deepseek'?'https://api.deepseek.com/v1':'https://api.openai.com/v1';if(el.dataset.preset==='deepseek')$('modelName').value='deepseek-chat';return;}
    if(el.dataset.shape){state.botDraft.shape=el.dataset.shape;updateDraftAvatar();return;}
    if(el.dataset.color){state.botDraft.color=el.dataset.color;updateDraftAvatar();return;}
    if(el.dataset.tool){openTool(el.dataset.tool);return;}
    if(el.dataset.category){state.category=el.dataset.category;document.querySelectorAll('[data-category]').forEach(b=>b.classList.toggle('selected',b===el));renderTools();return;}
    if(el.dataset.folder){state.folder=el.dataset.folder;document.querySelectorAll('[data-folder]').forEach(b=>b.classList.toggle('selected',b===el));renderVaultFiles();return;}
    if(el.dataset.file){openNote(el.dataset.file);return;}
    if(el.dataset.routine){openRoutine(el.dataset.routine);return;}
    if(el.dataset.routineToggle){const r=state.routines.find(r=>r.id===el.dataset.routineToggle);el.disabled=true;const updated=await api('PUT','/api/routines/'+r.id,{enabled:!r.enabled});Object.assign(r,updated);renderRoutineDetail();return;}
    if(el.dataset.decision){el.disabled=true;await api('POST','/api/confirm',{id:el.dataset.id,accepted:el.dataset.decision==='yes'});await refresh(true);await loadRoutines();return;}
    if(el.dataset.ack){await api('POST','/api/ack',{ids:[el.dataset.ack]});toast('演练已结束。');await refresh(true);return;}
    if(el.dataset.copy){await action('copy',{text:state.messages.find(m=>m.id===el.dataset.copy)?.text||''});toast('已复制。');return;}
    if(el.dataset.traceCopy){const [id,index]=el.dataset.traceCopy.split(':');await action('copy',{text:JSON.stringify(state.messages.find(m=>m.id===id)?.trace?.[Number(index)],null,2)});toast('执行记录已复制。');return;}
    if(el.dataset.reply){state.reply=state.messages.find(m=>m.id===el.dataset.reply);updateComposer();$('chatInput').focus();return;}
    if(el.dataset.appearance){state.settings.appearance=el.dataset.appearance;await localSettings(state.settings);applyAppearance();renderAppearance();return;}
    if(el.dataset.preference){const key=el.dataset.preference;state.settings[key]=state.settings[key]===false;await localSettings(state.settings);applyAppearance();el.querySelector('.toggle')?.classList.toggle('on',state.settings[key]);return;}
    if(el.dataset.permission){await action(el.dataset.permission);return;}
    if(el.id==='clearAttachments'){state.attachments=[];updateComposer();return;}
    if(el.id==='clearReply'){state.reply=null;updateComposer();return;}
    if(el.id==='testEmail'){el.disabled=true;try{const r=await api('POST','/api/test-email');toast(r.sent?'SMTP 已接受邮件，请检查收件箱。':r.reason);}finally{el.disabled=false;}return;}
    if(el.id==='botNotify'){const current=bot(),updated=await api('PUT','/api/bots/'+current.id,{...current,notify:current.notify===false});acceptBotUpdate(updated);renderProfile();return;}
    if(el.id==='editIdentity'){openIdentity();return;}
    if(el.tagName==='A'){event.preventDefault();const href=el.getAttribute('href')||'';if(href.startsWith('#vault:')){let path=decodeURIComponent(href.slice(7));if(!path.endsWith('.md'))path+='.md';await openNote(path);}else if(/^https?:\/\//i.test(href))await action('openUrl',{url:href});}
  }catch(e){if(el.isConnected)el.disabled=false;toast(e.message);}
});
$('profileButton').onclick=()=>go('settings');$('newBot').onclick=()=>menu('新建',[{icon:'chat',title:'创建新助手',description:'给它一个角色，和一件要负责的事',run:()=>avatarPicker(true)},{icon:'clock',title:'创建例行任务',run:()=>editRoutine()},{icon:'file',title:'新建记忆',run:newNote}]);$('addAssistant').onclick=()=>avatarPicker(true);
$('openSearch').onclick=()=>{$('botSearchBox').hidden=false;$('botSearch').focus();};$('closeSearch').onclick=()=>{$('botSearch').value='';$('botSearchBox').hidden=true;renderBots();};$('botSearch').oninput=renderBots;$('openRoutines').onclick=()=>go('routines');$('snoozeHome').onclick=()=>runJob(state.status.snooze_until*1000>Date.now()?'wake':'snooze');
$('subBack').onclick=back;$('chatBack').onclick=back;$('chatIdentity').onclick=()=>go('profile');$('chatMenu').onclick=()=>botOptions();
$('chatActions').onclick=chatActions;$('modeButton').onclick=modeMenu;$('chatForm').onsubmit=sendMessage;$('chatInput').oninput=()=>{resizeComposer();saveChatDraft();clearTimeout(draftSaveTimer);draftSaveTimer=setTimeout(()=>localSettings(state.settings).catch(()=>{}),600);};$('stopButton').onclick=async()=>{try{const cloud=state.status.computer?.bot_id===state.botId&&['running','paused','waiting_user'].includes(state.status.computer?.job?.state);const result=await api('POST',cloud?'/api/computer/stop':'/api/stop');toast(cloud?'云电脑执行已停止。':result.stopping?'正在停止执行…':'当前没有正在执行的工具任务。');}catch(e){toast(e.message);}};
$('closeSheet').onclick=closeSheet;$('sheet').addEventListener('click',e=>{if(e.target===$('sheet')){const r=$('sheet').getBoundingClientRect();if(e.clientY<r.top||e.clientX<r.left||e.clientX>r.right)closeSheet();}});
$('configForm').onsubmit=saveConfig;$('syncButton').onclick=syncAll;$('toolSearch').oninput=renderTools;$('fileSearch').oninput=renderVaultFiles;$('refreshVault').onclick=loadVault;$('previewTab').onclick=()=>setEditorMode(false);$('editTab').onclick=()=>setEditorMode(true);
window.onAppBack=()=>{if($('sheet').open){closeSheet();return true;}if(state.view!=='home'){back();return true;}return false;};
window.onSystemBackInvoked=()=>PulseMotion.systemBackFinish(true)||window.onAppBack();
window.onNativeEvent=(type,payload)=>{if(type==='preferences'){Object.assign(state.settings,payload);applyAppearance();if(state.view==='appearance')renderAppearance();return;}if(type==='backStart'){PulseMotion.systemBackStart();return;}if(type==='backProgress'){PulseMotion.systemBackProgress(payload.progress);return;}if(type==='backCancel'){PulseMotion.systemBackFinish(false);return;}if(type==='visibility'){foreground=payload.foreground;if(foreground){refresh();if(state.view==='root')refreshPrivilege();if(state.view==='local')renderLocal();}else PulseMotion.cancel();devicesRouteChanged();return;}if(type==='error')toast(payload.message);if(type==='refresh'){refresh();if(state.view==='root')refreshPrivilege();}};
PulseMotion.install({canStart:()=>state.stack.length>0&&!$('sheet').open&&!(state.view==='identity'&&(identitySaving||identityDirty()))&&!(state.view==='editor'&&$('markdownEditor').value!==state.noteOriginal),previous:()=>navigationVisuals.at(-1),commit:()=>popRoute({animated:false})});
let chatPinnedToBottom=true,lastChatHeight=0;
$('chatScroll').addEventListener('scroll',()=>{const scroll=$('chatScroll');chatPinnedToBottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<100;},{passive:true});
new ResizeObserver(entries=>{const height=entries[0].contentRect.height;if(lastChatHeight&&height!==lastChatHeight&&state.view==='chat'&&chatPinnedToBottom)$('chatScroll').scrollTop=$('chatScroll').scrollHeight;lastChatHeight=height;}).observe($('chatScroll'));
document.addEventListener('visibilitychange',()=>{foreground=!document.hidden;if(foreground)refresh();else{saveChatDraft();localSettings(state.settings).catch(()=>{});}});
(async()=>{applyAppearance();renderBots();renderTools();renderChatHeader();try{state.settings=await localSettings();applyAppearance();setOnline(false);if(window.nativeEntry?.view==='root'){state.view='root';showRoute();window.nativeEntry.applied=true;}await loadMetadata();await refresh(true);if(state.settings.background&&paired())await action('background',{enabled:true});}catch(e){setOnline(false);}if(!window.nativeEntry?.applied)showRoute();
  const entry=window.nativeEntry;
  if(entry?.view&&!entry.applied&&document.getElementById('page-'+entry.view)){
    if(state.bots.some(b=>b.id===entry.botId))state.botId=entry.botId;
    state.view=entry.view;state.mode=['auto','chat','execute','phone','computer'].includes(entry.mode)?entry.mode:'auto';
    if(entry.action==='connection')state.configGroup='connection';
    showRoute();restoreChatDraft();
    if(entry.action==='createBot')avatarPicker(true);
    if(entry.action==='openNote'){await openNote(entry.path);if(state.view==='editor'){state.stack=[];navigationVisuals.length=0;window.nativeEntry.view='editor';}}
    if(entry.action==='createRoutine')editRoutine();
    if(entry.action==='createNote')newNote();
    if(entry.action==='primary')openPrimaryPicker(state.botId);
    if(entry.action==='upload')uploadFile(state.mode==='computer'?'computer':'workspace',true);
    if(entry.action==='escalation')runJob('escalation');
  }
})();
setInterval(()=>{if(!document.hidden&&foreground&&paired())refresh();},2500);
