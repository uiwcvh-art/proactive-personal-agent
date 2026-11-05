'use strict';

let botMarkup = '', hiddenBotMarkup = '';
let botRevision = 0;
const placementBusy = new Set();

function acceptBotUpdate(updated) {
  botRevision++;
  const present = state.bots.some(b => b.id === updated.id);
  state.bots = present ? state.bots.map(b => b.id === updated.id ? updated : b) : [...state.bots, updated];
}

function botHistory(id) {return state.messages.filter(m => (m.bot_id || 'companion') === id);}
function homeBots(query = '') {
  const latest = new Map(state.bots.map(b => [b.id, botHistory(b.id).at(-1)]));
  return state.bots.filter(b => !b.hidden && (!query || `${b.name} ${b.description} ${latest.get(b.id)?.text || ''}`.toLowerCase().includes(query)))
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) ||
      (a.pinned && b.pinned ? (b.pinned_at || 0) - (a.pinned_at || 0) : 0) ||
      (latest.get(b.id)?.created_at || 0) - (latest.get(a.id)?.created_at || 0));
}
function botListRow(b) {
  const history = botHistory(b.id), last = history.at(-1), unread = history.filter(m => state.pending[m.id]).length;
  const working = state.status.execution?.bot_id === b.id;
  return `<button class="bot-row" data-bot="${esc(b.id)}" aria-label="${esc(b.name)}${b.pinned ? '，已置顶' : ''}">${avatar(b)}<span class="bot-content"><span class="bot-heading"><strong>${esc(b.name)}</strong>${state.primaryBotId === b.id ? '<span class="primary-mark">主助手</span>' : ''}${b.pinned ? `<span class="pin-mark" aria-label="已置顶">${icon('pin')}</span>` : ''}${unread ? `<span class="unread-count">${unread}</span>` : ''}<time>${working ? '正在工作' : last ? timeOf(last.created_at) : ''}</time></span><p>${esc(working ? '正在使用工具处理你的任务…' : last ? (last.role === 'user' ? '你：' : '') + last.text.replace(/\s+/g, ' ') : b.description || '发消息，开始一件新事情。')}</p></span></button>`;
}
function renderBots() {
  const query = $('botSearch').value.trim().toLowerCase(), bots = homeBots(query);
  loadAvatarImages();
  const hidden = state.bots.filter(b => b.hidden).length;
  const html = bots.length ? bots.map(botListRow).join('') : `<div class="empty-state">${icon(query ? 'search' : 'chat')}<strong>${query ? '没有找到匹配的助手' : hidden ? '你的助手已隐藏' : '从一位新助手开始'}</strong>${!query && hidden ? '<p>对话和例行任务都还在。<br>随时可以恢复到首页。</p><button class="secondary" data-route="hiddenBots">查看已隐藏助手</button>' : ''}</div>`;
  // Polling must not replace a row while the user is holding it.
  if (html !== botMarkup) {$('botList').innerHTML = html; botMarkup = html;}
  $('showHiddenBots').hidden = !hidden;
  $('showHiddenBots').innerHTML = `${icon('hide')}<span>已隐藏助手</span><span>${hidden}</span>${icon('next')}`;
  if (state.view === 'hiddenBots') renderHiddenBots();
}
function renderHiddenBots() {
  const bots = state.bots.filter(b => b.hidden);
  const html = bots.length ? bots.map(b => `<div class="hidden-bot-row">${botListRow(b)}<button class="restore-bot" data-restore-bot="${esc(b.id)}" aria-label="将${esc(b.name)}恢复到首页" ${placementBusy.has(b.id) ? 'disabled' : ''}>恢复</button></div>`).join('') : `<div class="empty-state">${icon('eye')}<strong>没有隐藏的助手</strong><p>长按首页的助手，或在对话菜单中隐藏它。</p></div>`;
  if (html !== hiddenBotMarkup) {$('hiddenBotList').innerHTML = html; hiddenBotMarkup = html;}
}
async function setBotPlacement(id, changes) {
  if (!requirePair() || placementBusy.has(id)) return;
  placementBusy.add(id); renderHiddenBots();
  try {
    const updated = await api('PATCH', '/api/bots/' + encodeURIComponent(id) + '/placement', changes);
    acceptBotUpdate(updated);
    renderBots(); renderHiddenBots();
    if (changes.hidden && state.view === 'chat' && state.botId === id) {
      saveChatDraft(); state.stack = []; navigationVisuals.length = 0; go('home', {replace: true});
    } else if (state.view === 'profile' && state.botId === id) renderProfile();
    toast(changes.hidden === true ? '已隐藏。对话和例行任务会保留。' : changes.hidden === false ? '已恢复到首页。' : updated.pinned ? '已置顶到首页。' : '已取消置顶。');
  } finally {placementBusy.delete(id); renderHiddenBots();}
}
function botOptions(id = state.botId) {
  const b = state.bots.find(item => item.id === id); if (!b) return;
  const openForBot = view => {state.botId = id; state.botDraft = null; go(view);};
  const items = [];
  if (state.view !== 'chat' || state.botId !== id) items.push({icon: 'chat', title: '打开对话', run: () => openChat(id)});
  items.push(
    {icon: 'spark', title: state.primaryBotId === b.id ? '更换主助手…' : '设为主助手', run: () => openPrimaryPicker(id)},
    {icon: 'pin', title: b.pinned ? '取消置顶' : '置顶到首页', run: () => setBotPlacement(id, {pinned: !b.pinned})},
    {icon: b.hidden ? 'eye' : 'hide', title: b.hidden ? '在首页显示' : '隐藏助手', description: '保留对话、记忆和例行任务', run: () => setBotPlacement(id, {hidden: !b.hidden})},
    {icon: 'settings', title: '助手详情', run: () => openForBot('profile')},
    {icon: 'clock', title: '例行任务', run: () => openForBot('routines')},
    {icon: 'folder', title: '资料库', run: () => openForBot('library')},
    {icon: 'vault', title: '记忆库', run: () => openForBot('vault')}
  );
  menu(b.name, items);
}

// Match the reference's long-press menu, while retaining keyboard/context-menu access.
document.addEventListener('DOMContentLoaded', () => {
  let hold = null, suppressed = null;
  const cancel = () => {if (hold) clearTimeout(hold.timer); hold = null;};
  const show = (row, pointer) => {
    suppressed = {id: row.dataset.bot, until: performance.now() + 900};
    if (pointer) PulseMotion.feedback('hold');
    botOptions(row.dataset.bot);
  };
  $('botList').addEventListener('pointerdown', event => {
    cancel(); const row = event.target.closest('[data-bot]');
    if (!row || !event.isPrimary || event.button !== 0 || PulseMotion.navigating) return;
    hold = {id: event.pointerId, x: event.clientX, y: event.clientY, timer: setTimeout(() => {
      if (row.isConnected) show(row, true); cancel();
    }, 520)};
  }, {passive: true});
  document.addEventListener('pointermove', event => {
    if (hold && (event.pointerId !== hold.id || Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > 10)) cancel();
  }, {passive: true});
  document.addEventListener('pointerup', cancel, {passive: true});
  document.addEventListener('pointercancel', cancel, {passive: true});
  $('botList').addEventListener('scroll', cancel, {passive: true});
  $('botList').addEventListener('contextmenu', event => {
    const row = event.target.closest('[data-bot]'); if (!row) return;
    event.preventDefault(); cancel();
    if (!suppressed || suppressed.id !== row.dataset.bot || performance.now() > suppressed.until) show(row, false);
  });
  document.addEventListener('click', event => {
    const row = event.target.closest('[data-bot]');
    if (row && suppressed?.id === row.dataset.bot && performance.now() < suppressed.until) {
      event.preventDefault(); event.stopImmediatePropagation(); suppressed = null;
    }
  }, true);
});
