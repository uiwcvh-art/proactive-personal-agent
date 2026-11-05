'use strict';

const avatarImages = new Map(), avatarLoading = new Set(), avatarRetry = new Map();
let avatarCrop = null, primaryChoice = null, identitySaving = false, avatarGenerating = false;

function renderBotAvatar(b, size = '') {
  const photo = b._image !== undefined ? b._image : b.avatar_version ? avatarImages.get(b.id)?.version === b.avatar_version ? avatarImages.get(b.id).image : '' : '';
  const isPhoto = b._image !== undefined ? !!b._image : !!b.avatar_version;
  return `<span class="bot-avatar ${isPhoto ? 'photo-avatar' : esc(b.shape || 'circle')} ${size}" data-avatar-id="${esc(b.id || '')}" data-avatar-draft="${b._image !== undefined}" style="--bot-color:${/^#[0-9a-f]{6}$/i.test(b.color) ? b.color : '#7c5cff'}" aria-hidden="true">${photo ? `<img src="${esc(photo)}" alt="">` : ''}</span>`;
}

function loadAvatarImages() {
  if (!paired()) return;
  for (const b of state.bots) {
    if (avatarLoading.size >= 2) break;
    if (!b.avatar_version || avatarImages.get(b.id)?.version === b.avatar_version || avatarLoading.has(b.id) || (avatarRetry.get(b.id) || 0) > Date.now()) continue;
    avatarLoading.add(b.id);
    api('GET', '/api/bots/' + encodeURIComponent(b.id) + '/avatar').then(result => {
      if (!/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(result.image)) throw new Error('头像格式无效');
      avatarImages.set(b.id, result);
      document.querySelectorAll('.bot-avatar.photo-avatar').forEach(el => {
        if (el.dataset.avatarDraft !== 'true' && el.dataset.avatarId === b.id && state.bots.find(item => item.id === b.id)?.avatar_version === result.version) {
          el.innerHTML = `<img src="${esc(result.image)}" alt="">`;
        }
      });
    }).catch(() => avatarRetry.set(b.id, Date.now() + 30000)).finally(() => {avatarLoading.delete(b.id); loadAvatarImages();});
  }
}

function acceptBotRoster(roster) {
  const previous = state.bots.find(b => b.id === state.botId), previousPrimary = state.primaryBotId;
  state.bots = roster.bots;
  state.primaryBotId = roster.primary_bot_id || 'companion';
  const current = state.bots.find(b => b.id === state.botId);
  const identityChanged = previous && current && ['name','title','color','shape','avatar_version'].some(key => previous[key] !== current[key]);
  if (state.view === 'chat' && identityChanged) renderChatHeader();
  if (state.view === 'profile' && (identityChanged || previousPrimary !== state.primaryBotId)) renderProfile();
  loadAvatarImages();
}

function renderBotProfile() {
  const b = bot(), routines = state.routines.filter(r => (r.bot_id || 'companion') === b.id);
  $('subTitle').textContent = b.name;
  $('botProfile').innerHTML = `<div class="bot-profile-identity"><button class="profile-picture" id="profileAvatar" aria-label="修改头像">${avatar(b, 'large')}</button><h1>${esc(b.name)}</h1><p class="bot-title">${esc(b.title || '')}</p>${state.primaryBotId === b.id ? '<span class="primary-label">主助手</span>' : ''}<button id="editIdentity">编辑</button></div>` +
    group('', row('助手设置', '角色设定、模型与工具', 'settings', 'data-route="botSettings"') +
      row('例行任务', '', 'clock', 'data-route="routines"', String(routines.length)) +
      row('记忆与文件', '', 'folder', 'data-route="vault"') + row('手机与虚拟屏', '', 'screen', 'data-route="root"')) +
    group('', row('主助手', state.primaryBotId === b.id ? '主动联系你，帮你协调当前的事' : '选择会主动联系你的助手', 'spark', 'id="profilePrimary"', state.primaryBotId === b.id ? '当前助手' : '选择') +
      toggleRow('通知', '完成任务或需要你时提醒', 'bell', 'id="botNotify"', b.notify !== false), '关闭后，此助手不触发消息通知与催单升级。');
  $('profileAvatar').onclick = () => {openIdentity(); openIdentityAvatar();};
  $('profilePrimary').onclick = () => openPrimaryPicker(b.id);
  loadAvatarImages();
}

function openIdentity() {
  state.identityDraft = {...bot()};
  go('identity');
}
function readIdentity() {
  if (!$('identityName') || !state.identityDraft) return;
  Object.assign(state.identityDraft, {name: $('identityName').value.trim(), title: $('identityTitle').value.trim()});
}
function identityDirty() {
  readIdentity();
  const d = state.identityDraft, current = bot();
  return !!d && (d.name !== current.name || d.title !== (current.title || '') || d.color !== current.color || d.shape !== current.shape || Object.hasOwn(d, 'image') && (d.image !== '' || !!current.avatar_version));
}
function renderIdentity() {
  const d = state.identityDraft;
  $('subAction').textContent = '完成';
  $('subAction').disabled = identitySaving;
  $('subAction').onclick = () => $('identityForm').requestSubmit();
  $('identityContent').innerHTML = `<form id="identityForm"><div class="bot-profile-identity identity-picture"><button type="button" id="identityAvatar" class="profile-picture" aria-label="修改头像">${avatar({...d, _image: d.image}, 'large')}<span class="picture-edit">${icon('edit')}</span></button><button type="button" id="identityAvatarLabel">更换头像</button></div><div class="identity-fields"><label>名称<input id="identityName" value="${esc(d.name)}" maxlength="40" required autocomplete="off"></label><label>Title · 标签<input id="identityTitle" value="${esc(d.title || '')}" maxlength="80" placeholder="添加一句简短的标签" autocomplete="off"></label></div><p id="identityFeedback" class="field-feedback" role="status"></p></form>`;
  $('identityForm').onsubmit = saveIdentity;
  $('identityAvatar').onclick = $('identityAvatarLabel').onclick = () => {readIdentity(); openIdentityAvatar();};
  loadAvatarImages();
}
async function saveIdentity(event) {
  event.preventDefault(); if (identitySaving || !requirePair()) return;
  readIdentity(); const d = state.identityDraft;
  const changes = {name:d.name, title:d.title, shape:d.shape, color:d.color};
  if (Object.hasOwn(d, 'image')) changes.image = d.image;
  identitySaving = true; $('subAction').disabled = true;
  try {
    const updated = await api('PATCH', '/api/bots/' + encodeURIComponent(d.id) + '/identity', changes);
    if (d.image && updated.avatar_version) avatarImages.set(d.id, {version:updated.avatar_version, image:d.image});
    if (d.image === '') avatarImages.delete(d.id);
    acceptBotUpdate(updated); state.identityDraft = null; state.botDraft = null;
    popRoute(); renderBots(); toast('身份已同步。');
  } catch (error) {$('identityFeedback').textContent = error.message;}
  finally {identitySaving = false; $('subAction').disabled = false;}
}
function identityBack() {
  if (identitySaving) return;
  if (!identityDirty()) {state.identityDraft = null; popRoute(); return;}
  sheet('放弃这次修改？', '<p class="sheet-description">名称、标签和头像还没有保存。</p><button class="primary full" data-sheet-action="keep">继续编辑</button><button class="plain-button full" data-sheet-action="discard">放弃修改</button>', {
    keep:closeSheet, discard:() => {closeSheet(); state.identityDraft = null; popRoute();}
  });
}

function openIdentityAvatar(tab = 'shape') {
  const d = state.identityDraft;
  let content;
  if (tab === 'shape') {
    content = `<div class="shape-options">${shapes.map(shape => `<button data-identity-shape="${shape}" aria-label="${shape} 头像" class="${d.shape === shape && !d.avatar_version && !d.image ? 'selected' : ''}">${avatar({...d, _image:'', shape})}</button>`).join('')}</div><div class="color-options">${colors.map(color => `<button data-identity-color="${color}" aria-label="头像颜色 ${color}" style="background:${color}" class="${d.color === color ? 'selected' : ''}"></button>`).join('')}</div>`;
  } else if (tab === 'upload') {
    content = `<div class="sheet-menu"><button data-avatar-photo="library">${icon('folder')}<span><strong>从照片中选择</strong><small>选择后可以拖动、缩放和裁剪</small></span></button><button data-avatar-photo="camera">${icon('camera')}<span><strong>拍摄照片</strong><small>使用手机相机</small></span></button></div><p class="field-hint">支持不超过 25 MB 的图片。</p>`;
  } else {
    content = `<label>描述头像<textarea id="avatarPrompt" rows="3" maxlength="2000" placeholder="例如：深蓝夜空中，一颗温暖的橙色小星球"></textarea></label><label>图像模型<input id="avatarModel" value="${esc(state.settings.avatarModel || '')}" placeholder="填写支持 Images API 的图像模型" maxlength="200" autocomplete="off"></label><p class="field-hint">使用执行大脑的接口与密钥，模型需支持返回图片数据。</p><button id="generateAvatar" class="secondary full" ${avatarGenerating ? 'disabled' : ''}>${avatarGenerating ? '正在生成…' : '生成头像'}</button><p id="avatarGenerateFeedback" class="field-feedback" role="status"></p>`;
  }
  sheet('头像', `<div class="bot-editor-avatar" id="identityAvatarPreview">${avatar({...d, _image:d.image}, 'large')}</div><div class="segmented">${[['shape','形状'],['generate','生成'],['upload','上传']].map(([key,label]) => `<button data-avatar-tab="${key}" class="${tab === key ? 'selected' : ''}">${label}</button>`).join('')}</div>${content}<button class="primary full avatar-done" data-sheet-action="done">完成</button>`, {done:() => {closeSheet(); renderIdentity();}});
  if (tab === 'generate') $('generateAvatar').onclick = generateIdentityAvatar;
}
async function generateIdentityAvatar() {
  if (avatarGenerating) return;
  const prompt = $('avatarPrompt').value.trim(), model = $('avatarModel').value.trim(), draft = state.identityDraft;
  if (!prompt || !model) {$('avatarGenerateFeedback').textContent = '填写头像描述和图像模型。'; return;}
  avatarGenerating = true; $('generateAvatar').disabled = true; $('generateAvatar').textContent = '正在生成…';
  try {
    state.settings.avatarModel = model; await localSettings(state.settings);
    const source = await api('POST', '/api/bots/' + encodeURIComponent(draft.id) + '/avatar/generate', {prompt,model});
    if (state.identityDraft !== draft) return;
    await closeSheet();
    avatarCrop = {source:source.image, width:source.width, height:source.height, zoom:1, x:0, y:0};
    go('avatarCrop');
  } catch (error) {if ($('avatarGenerateFeedback')) $('avatarGenerateFeedback').textContent = error.message; else toast(error.message);}
  finally {avatarGenerating = false; if ($('generateAvatar')) {$('generateAvatar').disabled = false; $('generateAvatar').textContent = '生成头像';}}
}
async function chooseIdentityPhoto(camera) {
  readIdentity(); await closeSheet();
  const source = await action('chooseAvatar', {camera});
  if (!source.image || !state.identityDraft) return;
  avatarCrop = {source:source.image, width:source.width, height:source.height, zoom:1, x:0, y:0};
  go('avatarCrop');
}
function renderAvatarCrop() {
  const c = avatarCrop;
  $('subAction').textContent = '使用照片'; $('subAction').onclick = useCroppedAvatar;
  $('avatarCropContent').innerHTML = `<div id="avatarCropViewport" class="avatar-crop-viewport"><img id="avatarCropImage" src="${esc(c.source)}" alt="待裁剪的头像" draggable="false"><div class="crop-grid" aria-hidden="true"></div></div><p class="crop-hint">拖动照片调整位置，双指或滑块缩放。</p><label class="crop-zoom-label">缩放<input id="avatarZoom" type="range" min="1" max="3" step="0.01" value="${c.zoom}" aria-label="头像缩放"></label>`;
  const viewport = $('avatarCropViewport'), points = new Map(); let pinch = null;
  const distance = () => {const p = [...points.values()]; return Math.hypot(p[0].x-p[1].x, p[0].y-p[1].y);};
  viewport.onpointerdown = event => {event.preventDefault(); viewport.setPointerCapture(event.pointerId); points.set(event.pointerId, {x:event.clientX, y:event.clientY}); if (points.size === 2) pinch = {distance:distance(), zoom:c.zoom};};
  viewport.onpointermove = event => {
    const previous = points.get(event.pointerId); if (!previous) return;
    points.set(event.pointerId, {x:event.clientX, y:event.clientY});
    if (points.size === 2 && pinch) {c.zoom = Math.max(1, Math.min(3, pinch.zoom*distance()/Math.max(1,pinch.distance))); $('avatarZoom').value = c.zoom;}
    else if (points.size === 1) {c.x += event.clientX-previous.x; c.y += event.clientY-previous.y;}
    layoutAvatarCrop();
  };
  viewport.onpointerup = viewport.onpointercancel = event => {points.delete(event.pointerId); if (points.size < 2) pinch = null;};
  $('avatarZoom').oninput = event => {c.zoom = Number(event.target.value); layoutAvatarCrop();};
  $('avatarCropImage').onload = layoutAvatarCrop;
  requestAnimationFrame(layoutAvatarCrop);
}
function layoutAvatarCrop() {
  const c = avatarCrop, viewport = $('avatarCropViewport'); if (!c || !viewport) return;
  c.side = viewport.clientWidth;
  c.scale = Math.max(c.side/c.width, c.side/c.height)*c.zoom;
  const maxX = Math.max(0, (c.width*c.scale-c.side)/2), maxY = Math.max(0, (c.height*c.scale-c.side)/2);
  c.x = Math.max(-maxX, Math.min(maxX,c.x)); c.y = Math.max(-maxY, Math.min(maxY,c.y));
  const image = $('avatarCropImage');
  image.style.width = c.width*c.scale+'px'; image.style.height = c.height*c.scale+'px';
  image.style.transform = `translate(-50%,-50%) translate(${c.x}px,${c.y}px)`;
}
function useCroppedAvatar() {
  const c = avatarCrop, image = $('avatarCropImage');
  if (!c || !image.complete || !image.naturalWidth) return;
  layoutAvatarCrop();
  const size = c.side/c.scale, canvas = document.createElement('canvas'); canvas.width = canvas.height = 512;
  canvas.getContext('2d').drawImage(image, (c.width-size)/2-c.x/c.scale, (c.height-size)/2-c.y/c.scale, size, size, 0, 0, 512, 512);
  state.identityDraft.image = canvas.toDataURL('image/png');
  avatarCrop = null; popRoute();
}

function openPrimaryPicker(preselect = state.primaryBotId) {primaryChoice = preselect; go('primaryBot');}
function renderPrimaryPicker() {
  $('primaryBotContent').innerHTML = `<div class="primary-intro">${icon('spark')}<h1>你的主助手</h1><p>它会主动联系你，留意挂心的事。<br>随时可以更换，已有对话和任务会保留。</p></div><div class="settings-group primary-options">${state.bots.map(b => `<button class="primary-option" data-primary-choice="${esc(b.id)}" role="radio" aria-checked="${primaryChoice === b.id}">${avatar(b)}<span><strong>${esc(b.name)}</strong><small>${esc(b.title || (b.hidden ? '已隐藏' : b.description) || '个人助手')}</small></span><i class="selection-radio ${primaryChoice === b.id ? 'selected' : ''}">${primaryChoice === b.id ? icon('check') : ''}</i></button>`).join('')}</div><button id="confirmPrimary" class="primary full">确认主助手</button><p id="primaryFeedback" class="field-feedback" role="status"></p>`;
  $('confirmPrimary').onclick = savePrimary;
  loadAvatarImages();
}
async function savePrimary() {
  if (!requirePair()) return;
  $('confirmPrimary').disabled = true;
  try {
    const roster = await api('PUT', '/api/bots/primary', {bot_id:primaryChoice});
    botRevision++; acceptBotRoster(roster); popRoute(); renderBots(); toast(botFor(primaryChoice).name+'现在是你的主助手。');
  } catch (error) {$('primaryFeedback').textContent = error.message; if ($('confirmPrimary')) $('confirmPrimary').disabled = false;}
}

document.addEventListener('click', async event => {
  const el = event.target.closest('[data-avatar-tab],[data-identity-shape],[data-identity-color],[data-avatar-photo],[data-primary-choice]');
  if (!el || el.disabled) return;
  try {
    if (el.dataset.avatarTab) openIdentityAvatar(el.dataset.avatarTab);
    else if (el.dataset.identityShape || el.dataset.identityColor) {
      const d = state.identityDraft; d.image = ''; d.avatar_version = '';
      if (el.dataset.identityShape) d.shape = el.dataset.identityShape;
      if (el.dataset.identityColor) d.color = el.dataset.identityColor;
      openIdentityAvatar('shape');
    } else if (el.dataset.avatarPhoto) await chooseIdentityPhoto(el.dataset.avatarPhoto === 'camera');
    else {primaryChoice = el.dataset.primaryChoice; renderPrimaryPicker();}
  } catch (error) {toast(error.message);}
});
