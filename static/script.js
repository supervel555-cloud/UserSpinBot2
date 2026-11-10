/* ЮЗЫ — только отображение и HTTP-запросы.
 * Никаких клиентских балансов, генерации реальных дропов или начислений.
 * Имена, цены, стоимость круток, инвентарь, достижения и рынок — на Flask.
 */
'use strict';
const tg = window.Telegram?.WebApp;
if (tg) { try { tg.ready(); tg.expand(); } catch (e) { console.warn(e); } }
function getTelegramInitData() {
    // Основной способ — через Telegram SDK
    if (tg?.initData) {
        return tg.initData;
    }

    // Запасной способ — из параметров запуска Mini App
    const params = new URLSearchParams(
        window.location.hash.slice(1)
    );

    return params.get("tgWebAppData") || "";
}

console.log("Telegram diagnostics:", {
    sdk: Boolean(tg),
    initData: Boolean(getTelegramInitData()),
    platform: tg?.platform || "unknown"
});

const $ = id => document.getElementById(id);
const money = n => Number(n || 0).toLocaleString('ru-RU') + ' $';
const harmlessTag = v => String(v || '').replace(/[^a-z0-9]/g, '').slice(0, 32);
const escapeText = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const viewStoreKey = 'yuzy_view_preferences';
let config, state, quoteNow, mode = 'common', length = 0, x5 = false;
let busy = false, skipAnimation = false, quoteVersion = 0;
// КД на пропуск анимации; обычные крутки не блокируем.
const SKIP_COOLDOWN_MS = 10000;
let skipCooldownUntil = 0, skipUsedThisSpin = false, spinInProgress = false;
let lastResults = [], lastBatchTotal = 0, activeRoll = null, pendingRequest = null;
let marketItems = [];
let previewTimer = null, notificationTimer = null;

try {
  const prefs = JSON.parse(localStorage.getItem(viewStoreKey) || '{}');
  if (typeof prefs.mode === 'string') mode = prefs.mode;
  if (Number.isInteger(prefs.length)) length = prefs.length;
  x5 = !!prefs.x5;
} catch (_) { /* preferences only */ }
function saveView() { localStorage.setItem(viewStoreKey, JSON.stringify({mode, length, x5})); }
function randomId() {
  return window.crypto?.randomUUID?.() || 'req_' + Date.now() + '_' + Math.random().toString(36).slice(2);
}

async function api(path, method = 'GET', payload = undefined) {
  const headers = {'Accept':'application/json'};
  if (payload !== undefined) headers['Content-Type'] = 'application/json';
  const initData = getTelegramInitData();

  if (initData) {
      headers["X-Telegram-Init-Data"] = initData;
  }
  const response = await fetch(path, {
    method, headers, credentials: 'same-origin',
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'Сервер вернул ' + response.status);
  return result;
}
async function post(path, payload = {}) { return api(path, 'POST', payload); }

function notify(title, detail = '', type = '') {
  const box = $('notif-container');
  const item = document.createElement('div');
  item.className = 'notif show' + (type ? ' ' + type : '');
  const icon = document.createElement('span'); icon.className = 'notif-icon'; icon.textContent = type === 'achievement' ? '🏆' : '★';
  const text = document.createElement('div'); text.className = 'notif-text'; text.textContent = title;
  if (detail) { const sub = document.createElement('span'); sub.className = 'small'; sub.textContent = detail; text.appendChild(sub); }
  item.append(icon, text); box.appendChild(item);
  setTimeout(() => { item.classList.remove('show'); setTimeout(() => item.remove(), 400); }, 3400);
}
function errorNotice(err) { console.error(err); notify('Ошибка', err.message || String(err)); }
function openModal(title, html) { $('modal-title').textContent = title; $('modal-content').innerHTML = html; $('modal-overlay').classList.add('show'); }
function closeModal() { $('modal-overlay').classList.remove('show'); }
function closeFullInventory() { $('inv-full').classList.remove('show'); }
function closeMarket() { $('market-full').classList.remove('show'); }
function closeProfile() { $('profile-full').classList.remove('show'); }
function setNav(screen) {
  document.querySelectorAll('.bottom-nav .nav-btn').forEach(b => b.classList.toggle('active', b.dataset.screen === screen));
}
function currentPlayer() { return state?.player || {balance: 0, spins:0, auto_level:0, level:{level:1,percent:0}}; }
function isInstalled(item) { return item.id === currentPlayer().installed_item_id; }
function rarityStyle(rare) { return (typeof rare === 'object' ? rare : config?.rarities?.[rare]) || {name:'Обычный', color:'#9ca3af',pips:1}; }

async function reloadState() {
  state = await api('/api/state');
  // После обновления сервера pending могут быть автоматически проданы.
  if (activeRoll) {
    const matching = state.pending.find(r => r.id === activeRoll.id);
    if (matching) activeRoll = matching;
    else activeRoll = {...activeRoll, status: activeRoll.status === 'pending' ? 'settled' : activeRoll.status};
  } else if (state.pending.length) {
    activeRoll = state.pending[0];
  }
  updateBalance();
  updateProfileIfOpen();
  return state;
}
function updateBalance() {
  if (!state) return;
  $('balance').textContent = money(currentPlayer().balance);
  const lev = currentPlayer().level;
  $('level-text').textContent = 'Ур. ' + lev.level;
  $('level-bar').style.width = lev.percent + '%';
  $('spin-btn5').classList.toggle('toggled', x5);
  $('spin-btn5').disabled = busy;
  const btn = $('main-action-btn');
  if (busy && spinInProgress) {
    const remaining = Math.max(0, Math.ceil((skipCooldownUntil - Date.now()) / 1000));
    btn.disabled = skipUsedThisSpin || remaining > 0;
    btn.classList.add('skip-mode');
    btn.textContent = skipUsedThisSpin ? 'Пропущено' : remaining > 0 ? `КД: ${remaining} сек.` : 'Пропустить анимацию';
  } else if (busy) {
    btn.disabled = true;
    btn.classList.add('skip-mode');
    btn.textContent = 'Обработка...';
  } else {
    btn.classList.remove('skip-mode');
    const total = quoteNow?.total_cost;
    btn.innerHTML = 'Крутить' + (x5 ? ' ×5' : '') + ' <span>' + (total === undefined ? '...' : money(total)) + '</span>';
    btn.disabled = total === undefined || currentPlayer().balance < total;
  }
}
function showErrorBanner(message) {
  const old = $('server-error'); if (old) old.remove();
  const div = document.createElement('div');
  div.className = 'server-error'; div.id = 'server-error'; div.textContent = message;
  document.querySelector('.app').prepend(div);
  $('main-action-btn').disabled = true;
}

async function refreshQuote() {
  if (!config) return;
  const v = ++quoteVersion;
  quoteNow = undefined;
  updateBalance();
  try {
    const q = await api('/api/quote?mode=' + encodeURIComponent(mode) + '&length=' + length + '&count=' + (x5 ? 5 : 1));
    if (v === quoteVersion) { quoteNow = q; updateBalance(); }
  } catch (err) { if (v === quoteVersion) errorNotice(err); }
}
function buildModeMenu() {
  $('mode-menu').innerHTML = config.modes.map(m => `<button data-mode="${escapeText(m.id)}" class="${m.id===mode?'current':''}">
    <span><span style="color:${rarityStyle(m.name).color};font-weight:700">${escapeText(m.name)}</span>
    <span class="mode-desc">${escapeText(m.desc)}</span></span><span class="mode-cost">${money(m.cost)}</span></button>`).join('');
  $('mode-menu').querySelectorAll('button').forEach(b => b.onclick = () => {
    mode = b.dataset.mode; saveView(); buildModeMenu(); updateModeButton();
    $('mode-dropdown').classList.remove('open'); refreshQuote();
  });
}
function updateModeButton() {
  const selected = config.modes.find(m => m.id === mode) || config.modes[0];
  $('mode-btn').textContent = selected.name + ' ▾';
}
function buildLengthMenu() {
  $('length-menu').innerHTML = config.lengths.map(l => `<button data-len="${l.len}" class="${l.len===length?'current':''}">
    <span class="len-label">${escapeText(l.label)}</span><span class="len-mult">${escapeText(l.multText)}</span></button>`).join('');
  $('length-menu').querySelectorAll('button').forEach(b => b.onclick = () => {
    length = Number(b.dataset.len); saveView(); buildLengthMenu(); updateLengthButton();
    $('length-dropdown').classList.remove('open'); refreshQuote();
  });
}
function updateLengthButton() {
  $('length-btn').textContent = (config.lengths.find(l => l.len === length) || config.lengths[0]).label + ' ▾';
}

function stopPreview() { if (previewTimer) clearInterval(previewTimer); previewTimer = null; }
function startPreview() {
  stopPreview();
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  const field = $('username');
  previewTimer = setInterval(() => {
    // Только визуальная анимация: это НЕ генерация игрового выигрыша.
    const n = length || 3 + Math.floor(Math.random() * 6);
    let fake = ''; for (let i = 0; i < n; i++) fake += chars[Math.floor(Math.random() * chars.length)];
    field.textContent = '@' + fake;
  }, 85);
}
function showRoll(roll) {
  if (!roll) return;
  const tag = harmlessTag(roll.tag);
  const rare = rarityStyle(roll.rarity);
  $('username').textContent = '@' + tag;
  $('username').classList.remove('show-result'); void $('username').offsetWidth;
  $('username').classList.add('show-result');
  $('rarity-name').textContent = rare.name;
  $('rarity-name').style.color = rare.color;
  $('rarity-price').textContent = money(roll.price);
  $('rarity-price').style.color = rare.color;
  $('rarity-pips').textContent = '▬'.repeat(rare.pips) + '▭'.repeat(8-rare.pips);
  $('rarity-pips').style.color = rare.color;
  let kind = /^[a-z]+$/.test(tag) ? 'Только буквы' : /^[0-9]+$/.test(tag) ? 'Только цифры' : 'Буквы и цифры';
  $('subtitle').textContent = kind + ' · ' + tag.length + ' символов';
  const bonuses = roll.bonuses || [];
  $('bonuses').innerHTML = bonuses.length ? bonuses.map(s=>'<div class="bonus">'+escapeText(s)+'</div>').join('') : '<div>Бонусов нет</div>';
  const status = roll.status;
  $('price-note').textContent = status === 'sold' ? 'Продано за ' + money(roll.price) :
       status === 'collected' ? 'Сохранено в инвентарь' :
       status === 'pending' ? 'Выбери: продать или забрать в инвентарь' : 'Результат уже обработан';
  const available = status === 'pending';
  $('btn-collect').disabled = !available || state.inventory.length >= config.max_inventory;
  $('btn-sell').disabled = !available;
  $('btn-collect').classList.toggle('collected', status === 'collected');
  $('btn-collect').textContent = status === 'collected' ? 'В инвентаре' : 'В инвентарь';
  $('btn-sell').textContent = 'Продать · ' + money(roll.price);
}
function playSpecial(roll) {
  const name = roll.rarity.name;
  if (!['Мифический','Секретный','Эксклюзивный'].includes(name)) return;
  const id = name === 'Мифический' ? 'mythic-overlay' : name === 'Секретный' ? 'secret-overlay' : 'exclusive-overlay';
  const overlay = $(id), color = rarityStyle(roll.rarity).color;
  const text = document.createElement('div');
  text.style.cssText = `position:relative;text-align:center;background:#080808;border:2px solid ${color};padding:25px;border-radius:18px;box-shadow:0 0 60px ${color};color:${color};font-weight:800;`;
  const heading = document.createElement('div'); heading.textContent = name.toUpperCase();
  const tag = document.createElement('div'); tag.style.cssText = 'font-size:26px;margin:20px 0'; tag.textContent = '@' + harmlessTag(roll.tag);
  const price = document.createElement('div'); price.textContent = money(roll.price);
  const note = document.createElement('div'); note.style.cssText = 'font-size:12px;margin-top:25px;color:#999'; note.textContent = 'Нажми, чтобы продолжить';
  text.append(heading, tag, price, note); overlay.replaceChildren(text);
  overlay.classList.add('show');
  const timer = setTimeout(() => { overlay.classList.remove('show'); overlay.replaceChildren(); }, 4500);
  overlay.onclick = () => { clearTimeout(timer); overlay.classList.remove('show'); overlay.replaceChildren(); };
}

async function spin() {
  if (busy) {
    if (spinInProgress && !skipUsedThisSpin && Date.now() >= skipCooldownUntil) {
      skipUsedThisSpin = true;
      skipAnimation = true;
      skipCooldownUntil = Date.now() + SKIP_COOLDOWN_MS;
      updateBalance();
    }
    return;
  }
  if (!quoteNow) return;
  if (currentPlayer().balance < quoteNow.total_cost) { notify('Недостаточно средств', 'Нужно ' + money(quoteNow.total_cost)); return; }
  const params = {mode, length, count: x5 ? 5 : 1};
  const req = pendingRequest && JSON.stringify(pendingRequest.params) === JSON.stringify(params) ? pendingRequest.id : randomId();
  pendingRequest = {id:req,params};
  busy = true; skipAnimation = false; spinInProgress = true; skipUsedThisSpin = false;
  $('bonuses').textContent = '🎰 Крутим...';
  $('price-note').textContent = 'Сервер генерирует результат';
  $('btn-collect').disabled = true; $('btn-sell').disabled = true;
  updateBalance();
  startPreview();
  const started = Date.now();
  try {
    const response = await post('/api/spin', {...params,request_id:req});
    if (!skipAnimation) await sleep(Math.max(0, 1200-(Date.now()-started)));
    stopPreview();
    pendingRequest = null;
    lastResults = response.results;
    lastBatchTotal = response.total_price;
    activeRoll = [...lastResults].sort((a,b)=>b.price-a.price)[0];
    await reloadState();
    // У результата сохраняем подтверждённый сервером статус.
    const inPending = state.pending.find(r=>r.id===activeRoll.id);
    if (inPending) activeRoll = inPending;
    showRoll(activeRoll);
    if (lastResults.length === 5) showX5(lastResults);
    if (lastResults.length === 1) playSpecial(activeRoll);
    notify('Крутка готова', lastResults.length === 5 ? 'Получено 5 результатов' : '@'+activeRoll.tag);
  } catch (err) {
    stopPreview();
    errorNotice(err);
    try { await reloadState(); if (activeRoll) showRoll(activeRoll); } catch (_) { /* offline */ }
  } finally {
    busy = false; spinInProgress = false; updateBalance(); refreshQuote();
  }
}
async function rollAction(id, action) {
  if (busy) return;
  busy = true; updateBalance();
  try {
    await post('/api/rolls/' + encodeURIComponent(id) + '/' + action);
    await reloadState();
    const result = lastResults.find(r=>r.id===id);
    if (result) result.status = action === 'sell' ? 'sold' : 'collected';
    if (activeRoll?.id === id) {
      activeRoll = {...activeRoll, status: action === 'sell' ? 'sold' : 'collected'};
      showRoll(activeRoll);
    }
    if ($('x5-overlay').classList.contains('show')) showX5(lastResults);
    if ($('inv-full').classList.contains('show')) renderInventory();
    notify(action==='sell'?'Юз продан':'Юз добавлен в коллекцию');
  } catch (err) { errorNotice(err); }
  finally { busy=false; updateBalance(); }
}
function showX5(results) {
  $('x5-total').textContent = money(lastBatchTotal);
  $('x5-cards').innerHTML = results.map((r,i) => {
    const rare=rarityStyle(r.rarity), pending=r.status==='pending';
    return `<div class="x5-row" style="opacity:1;transform:none;animation-delay:${i*100}ms">
      <span class="x5-num">${i+1}</span><span class="x5-dot" style="background:${rare.color}"></span>
      <div class="x5-info"><div class="x5-tag" style="color:${rare.color}">@${escapeText(r.tag)}</div>
      <div class="x5-rar">${escapeText(rare.name)} · ${r.status==='sold'?'Продано':r.status==='collected'?'В инвентаре':'Ожидает решения'}</div>
      ${pending ? `<div class="x5-ops"><button data-roll="${r.id}" data-action="collect">Забрать</button><button data-roll="${r.id}" data-action="sell">Продать</button></div>` : ''}</div>
      <div class="x5-money">${money(r.price)}</div></div>`;
  }).join('');
  $('x5-overlay').classList.add('show');
  $('x5-cards').querySelectorAll('button[data-roll]').forEach(b => b.onclick=()=>rollAction(b.dataset.roll,b.dataset.action));
}
function closeX5() { $('x5-overlay').classList.remove('show'); }

async function renderInventory() {
  if (!state) return;
  const list = $('inv-full-list'), inv = state.inventory, pending = state.pending;
  $('inv-full-stats').innerHTML = `Всего: <b>${inv.length}/${config.max_inventory}</b> · Стоимость: <b>${money(state.inventory_total)}</b> · Ожидают: <b>${pending.length}</b>`;
  const pendingHtml = pending.length ? `<div class="section-label" style="margin-bottom:8px">Неразобранные крутки</div>` +
    pending.map(r=>`<div class="pending-card"><b>@${escapeText(r.tag)}</b> · ${escapeText(r.rarity.name)} · ${money(r.price)}<br>
      <button data-pending="${r.id}" data-action="collect">В инвентарь</button><button data-pending="${r.id}" data-action="sell">Продать</button></div>`).join('') : '';
  const invHtml = inv.length ? [...inv].sort((a,b)=>b.price-a.price).map(item=>{
    const c=item.rarity.color, installed=isInstalled(item);
    return `<div class="inv-card ${installed?'installed':''}"><div class="inv-content">
      <div class="inv-tag">@${escapeText(item.tag)} ${installed?'<span class="inv-badge">УСТАНОВЛЕН</span>':''}</div>
      <div class="inv-row-bottom"><span class="inv-rarity" style="color:${c}">${escapeText(item.rarity.name)}</span>
      <span class="inv-price">${money(item.price)}</span></div></div>
      <button class="inv-sell" data-item="${item.id}" ${installed?'disabled':''}>${installed?'Снять в профиле':'Продать'}</button></div>`;
  }).join('') : '<div class="inv-full-empty">Инвентарь пока пуст</div>';
  list.innerHTML = pendingHtml+invHtml;
  list.querySelectorAll('button[data-pending]').forEach(b=>b.onclick=()=>rollAction(b.dataset.pending,b.dataset.action));
  list.querySelectorAll('button[data-item]').forEach(b=>b.onclick=()=>sellChoice(b.dataset.item));
}
function openInventory() { closeModal(); closeMarket(); closeProfile(); renderInventory(); $('inv-full').classList.add('show'); setNav('inv'); }
function getItem(id) { return state.inventory.find(i=>i.id===id) || state.own_listings.find(i=>i.id===id); }
function sellChoice(id) {
  const item=getItem(id); if (!item) return;
  const html = `<div class="info-block"><div class="tag-row">@${escapeText(item.tag)}</div><div class="rar-row">${escapeText(item.rarity.name)}</div>
   <div class="price-row">Стоимость: ${money(item.price)}</div></div>
   <button class="option" id="sell-item-btn">Продать в игре за ${money(item.price)}</button>
   <button class="option" id="list-item-btn">Выставить на рынок</button>`;
  openModal('Продать или выставить',html);
  $('sell-item-btn').onclick=()=>mutate('/api/inventory/'+id+'/sell',{},'Предмет продан');
  $('list-item-btn').onclick=()=>{
    openModal('Рыночная цена',`<div class="info-block">@${escapeText(item.tag)} · ${money(item.price)}</div>
      <input type="number" class="price-input" id="asking-price" min="1" max="999999999" value="${item.suggested_asking_price}">
      <button class="option accent" id="confirm-list">Выставить</button>`);
    $('confirm-list').onclick=()=>{
      const price = Number($('asking-price').value);
      if (!Number.isSafeInteger(price) || price<1 || price>999999999) {notify('Некорректная цена');return;}
      mutate('/api/market/list',{item_id:id,price},'Лот выставлен');
    };
  };
}
async function mutate(url, data={}, success='Готово') {
  try { const result=await post(url,data); await reloadState(); closeModal();
    if ($('inv-full').classList.contains('show')) renderInventory();
    if ($('market-full').classList.contains('show')) await renderMarket();
    notify(success); return result;
  } catch (err) {errorNotice(err); return null;}
}
async function renderMarket() {
  const response=await api('/api/market');
  marketItems=response.listings;
  $('market-full-stats').textContent = 'Лотов: '+marketItems.length+' · Покупки за игровую валюту ($)';
  const list=$('market-full-list');
  if (!marketItems.length) {list.innerHTML='<div class="market-empty"><div class="title">Рынок пуст</div><div class="hint">Выставьте юз из инвентаря</div></div>';return;}
  list.innerHTML=marketItems.map(item=>{
    const c=item.rarity.color;
    return `<div class="market-lot"><div class="market-lot-header"><div class="market-lot-tag">@${escapeText(item.tag)}</div>
    <div class="market-lot-badge">${item.mine?'ВАШ ЛОТ':'ПРОДАЁТСЯ'}</div></div>
    <div class="market-lot-rarity" style="color:${c}">${escapeText(item.rarity.name)}</div>
    <div class="market-lot-prices"><div class="market-lot-price-block base"><div class="label">Стоимость</div><div class="value">${money(item.price)}</div></div>
    <span class="market-lot-arrow">→</span><div class="market-lot-price-block ask"><div class="label">Цена лота</div>
    <div class="value">${money(item.asking_price)}</div></div></div>
    <div class="market-lot-actions"><button data-market="${item.id}" data-action="${item.mine?'cancel':'buy'}">${item.mine?'Снять с продажи':'Купить за $'}</button></div></div>`;
  }).join('');
  list.querySelectorAll('button[data-market]').forEach(b=>b.onclick=async()=>{
    const id=b.dataset.market, action=b.dataset.action;
    if (!confirm(action==='buy'?'Купить предмет за игровую валюту?':'Снять свой лот с продажи?')) return;
    await mutate('/api/market/'+id+'/'+action,{}, action==='buy'?'Предмет куплен':'Лот снят');
  });
}
async function openMarket() {
  closeModal();closeFullInventory();closeProfile();setNav('market');
  $('market-full').classList.add('show');
  try {await renderMarket();}catch(err){errorNotice(err);}
}

function refreshTelegramProfile() {
  // Важно: берём сведения из проверенного ответа Flask, не из initDataUnsafe.
  const info = currentPlayer().telegram || {};
  const name = [info.first_name, info.last_name].filter(Boolean).join(' ').trim() || 'Игрок';
  const handle = info.username ? '@' + info.username : name;
  const label = $('profile-telegram-handle');
  $('profile-name').textContent = handle;
  label.textContent = info.username ? name + ' · Telegram' : (info.id ? 'Аккаунт Telegram' : 'Локальная демонстрация');
  const avatar = $('profile-avatar');
  avatar.replaceChildren();
  avatar.textContent = (info.first_name || info.username || 'И').trim().charAt(0).toLocaleUpperCase('ru-RU');
  if (info.photo_url) {
    try {
      const url = new URL(info.photo_url);
      if (url.protocol === 'https:') {
        const img = document.createElement('img');
        img.alt = 'Аватар Telegram';
        img.loading = 'lazy';
        img.referrerPolicy = 'no-referrer';
        img.src = url.href;
        img.onerror = () => img.remove();
        avatar.appendChild(img);
      }
    } catch (_) { /* Если фото недоступно, оставляем букву. */ }
  }
}

function refreshProfile() {
  const p=currentPlayer(), inv=state.inventory;
  refreshTelegramProfile();
  $('profile-level-sub').textContent = 'Уровень '+p.level.level;
  $('p-balance').textContent = money(p.balance);
  $('p-spins').textContent = String(p.spins);
  $('p-items').textContent = inv.length+'/'+config.max_inventory;
  $('p-ach').textContent = state.achievements.filter(a=>a.claimed).length+'/'+state.achievements.length;
  const installed=inv.find(i=>i.id===p.installed_item_id);
  const container=$('installed-container');
  if (installed) {
    container.innerHTML=`<div class="installed-box"><div class="info"><div class="tag">@${escapeText(installed.tag)}</div>
      <div class="rar">${escapeText(installed.rarity.name)}</div><div class="prc">${money(installed.price)}</div></div>
      <button class="option" id="unset-tag-btn">Снять</button></div>`;
    $('unset-tag-btn').onclick=()=>mutate('/api/profile/uninstall',{},'Юз снят');
  } else container.innerHTML='<div class="installed-empty">Юз не установлен</div>';
  renderAchievements();
}
function updateProfileIfOpen() {if($('profile-full').classList.contains('show'))refreshProfile();}
function renderAchievements() {
  const list=state.achievements;
  const done=list.filter(a=>a.claimed).length, ready=list.filter(a=>a.ready).length;
  $('ach-summary-count').textContent=done+' / '+list.length+(ready?' · '+ready+' готово':'');
  $('ach-list').innerHTML=list.map(a=>`<div class="ach-card ${a.claimed?'claimed':a.ready?'ready':''}">
    <div class="ach-card-head"><div class="ach-card-name">${escapeText(a.name)}</div>
    <div class="ach-card-reward">+${money(a.reward)}</div></div><div class="ach-card-desc">${escapeText(a.desc)}</div>
    <div class="ach-card-progress"><div class="ach-card-bar"><div class="ach-card-bar-fill" style="width:${a.percent}%"></div></div>
    <div class="ach-card-counter">${a.progress} / ${a.target}</div></div>
    <button class="ach-claim-btn" data-ach="${a.id}" ${a.ready?'':'disabled'}>${a.claimed?'Получено':a.ready?'Получить награду':'В процессе'}</button></div>`).join('');
  $('ach-list').querySelectorAll('button[data-ach]').forEach(b=>b.onclick=async()=>{
    const result=await mutate('/api/achievements/'+b.dataset.ach+'/claim',{},'Награда за достижение получена');
    if(result) refreshProfile();
  });
}
function openProfile() {
  closeModal();closeFullInventory();closeMarket();
  $('profile-picker-view').classList.add('hidden'); $('profile-main-view').classList.remove('hidden');
  $('profile-header-title').textContent='Профиль';
  refreshProfile(); $('profile-full').classList.add('show');
}
function showProfilePicker() {
  if (!state.inventory.length) {notify('В инвентаре нет предметов');return;}
  $('profile-main-view').classList.add('hidden');$('profile-picker-view').classList.remove('hidden');
  $('profile-header-title').textContent='Поставить юз';
  $('profile-pick-list').innerHTML=[...state.inventory].sort((a,b)=>b.price-a.price).map(item=>`
    <div class="pick-item" data-id="${item.id}"><div class="info"><div class="tag" style="color:${item.rarity.color}">@${escapeText(item.tag)}</div>
    <div class="rar">${escapeText(item.rarity.name)}</div></div><div class="prc">${money(item.price)}</div></div>`).join('');
  $('profile-pick-list').querySelectorAll('.pick-item').forEach(el=>el.onclick=async()=>{
    const result=await mutate('/api/profile/install',{item_id:el.dataset.id},'Юз установлен');
    if(result){ $('profile-picker-view').classList.add('hidden');$('profile-main-view').classList.remove('hidden');refreshProfile();}
  });
}
function openShop() {
  const p=currentPlayer();
  openModal('Магазин',`<div class="info-block">Ваш баланс: ${money(p.balance)}</div>
    <button class="option" id="sell-all-btn">Продать весь инвентарь (кроме установленного)</button>
    <div class="section-label">Telegram Stars</div><div class="info-block">Покупки за ⭐ пока не подключены. Они появятся только после серверной проверки платежей.</div>`);
  $('sell-all-btn').onclick=()=>mutate('/api/inventory/sell-all',{},'Инвентарь продан');
}
function openSettings() {
  const current=currentPlayer().auto_level;
  const rows=config.auto_levels.map(l=>`<button class="option ${l.level===current?'picked':''}" data-auto="${l.level}">
    ${l.level===current?'●':'○'} <b>${escapeText(l.name)}</b> <span style="font-size:11px;color:#94a3b8">${escapeText(l.desc)}</span></button>`).join('');
  openModal('Настройки',`<div class="section-label">Авто-инвентарь</div>
    <p style="color:#94a3b8;font-size:12px;line-height:1.5;margin-bottom:12px">Выкл: можно решить вручную. При следующих крутках неразобранные юзы продаются автоматически. Остальные уровни сразу сохраняют подходящие юзы, остальные продают.</p>
    ${rows}<div class="section-label">Аккаунт</div>
    <button class="option danger" id="reset-btn">Сбросить прогресс на сервере</button>`);
  $('modal-content').querySelectorAll('[data-auto]').forEach(b=>b.onclick=async()=>{
    const result=await mutate('/api/settings',{auto_level:Number(b.dataset.auto)},'Настройки сохранены');
    if(result)openSettings();
  });
  $('reset-btn').onclick=async()=>{
    if (!confirm('Сбросить ВЕСЬ прогресс и коллекцию на сервере? Это необратимо.')) return;
    const ok=await mutate('/api/reset',{confirm:'RESET'},'Прогресс сброшен');
    if(ok){activeRoll=null;lastResults=[];$('username').textContent='@нажми крутить';
      $('rarity-price').textContent='0 $';$('bonuses').textContent='Нажми «Крутить», чтобы начать';
      $('btn-collect').disabled=true;$('btn-sell').disabled=true;refreshQuote();}
  };
}
async function cheat(action) {
  try { await post('/api/dev/cheat',{action,count:x5?5:1}); await reloadState();
    notify('Тестовая команда применена'); }catch(err){errorNotice(err);}
}

function wireUI() {
  $('main-action-btn').onclick=()=>spin();
  $('spin-btn5').onclick=()=>{if(busy)return;x5=!x5;saveView();refreshQuote();updateBalance();};
  $('btn-collect').onclick=()=>activeRoll&&rollAction(activeRoll.id,'collect');
  $('btn-sell').onclick=()=>activeRoll&&rollAction(activeRoll.id,'sell');
  $('x5-close').onclick=closeX5;
  $('modal-close').onclick=closeModal;
  $('modal-overlay').onclick=e=>{if(e.target.id==='modal-overlay')closeModal();};
  $('btn-menu').onclick=openSettings; $('btn-settings').onclick=openSettings;
  $('btn-burger').onclick=openProfile;
  $('btn-close').onclick=()=>{if(tg?.close)tg.close();else notify('Прогресс сохраняется на сервере');};
  $('profile-full-close').onclick=()=>{closeProfile();setNav('play');};
  $('inv-full-close').onclick=()=>{closeFullInventory();setNav('play');};
  $('market-full-close').onclick=()=>{closeMarket();setNav('play');};
  $('btn-open-shop').onclick=openShop;
  $('btn-set-username').onclick=showProfilePicker;
  $('btn-pick-cancel').onclick=()=>{$('profile-picker-view').classList.add('hidden');$('profile-main-view').classList.remove('hidden');$('profile-header-title').textContent='Профиль';};
  $('mode-dropdown').onclick=e=>{if(!e.target.closest('#mode-menu')) $('mode-dropdown').classList.toggle('open');};
  $('length-dropdown').onclick=e=>{if(!e.target.closest('#length-menu')) $('length-dropdown').classList.toggle('open');};
  document.addEventListener('click',e=>{
    if(!e.target.closest('#mode-dropdown'))$('mode-dropdown').classList.remove('open');
    if(!e.target.closest('#length-dropdown'))$('length-dropdown').classList.remove('open');
  });
  document.querySelectorAll('.bottom-nav .nav-btn').forEach(btn=>btn.onclick=()=>{
    if(btn.dataset.screen==='play') {closeMarket();closeFullInventory();closeProfile();setNav('play');}
    else if(btn.dataset.screen==='inv')openInventory();
    else if(btn.dataset.screen==='market')openMarket();
  });
  $('tab-yuzy').onclick=()=>{closeMarket();closeFullInventory();closeProfile();setNav('play');};
  $('cheat-toggle').onclick=()=>{$('cheat-panel').classList.toggle('show');};
  $('cheat-money').onclick=()=>cheat('money');
  $('cheat-luck').onclick=()=>cheat('luck');
  $('cheat-mythic').onclick=()=>cheat('mythic');
  $('cheat-secret').onclick=()=>cheat('secret');
  $('cheat-exclusive').onclick=()=>cheat('exclusive');
}

async function main() {
  wireUI();
  try {
    config=await api('/api/config');
    if (!config.modes.some(m=>m.id===mode))mode='common';
    if (!config.lengths.some(l=>l.len===length))length=0;
    await reloadState();
    buildModeMenu();buildLengthMenu();updateModeButton();updateLengthButton();
    if(config.demo){$('cheat-toggle').style.display='inline-block';$('cheat-panel').style.display='';}
    if(state.pending.length){activeRoll=state.pending[0];showRoll(activeRoll);notify('Есть неразобранные крутки','Можно продать или забрать результат');}
    await refreshQuote();
  } catch(err) {
    console.error(err);
    showErrorBanner('Не удалось подключиться к игровому серверу: '+err.message+' Для локального запуска используйте LOCAL_DEMO=1; на сервере открывайте приложение внутри Telegram.');
  }
}
main();

// Обратный отсчёт КД, пока идёт серверная крутка.
setInterval(() => { if (spinInProgress) updateBalance(); }, 500);
