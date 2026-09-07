/* 无框架 UI 原语. */
const $  = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const esc = s => String(s??'').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtNum = n => (n||0).toLocaleString('zh-CN');

function toast(msg, kind='') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 3200);
}

function modal(html, {onMount} = {}) {
  $('#modal').innerHTML = html;
  $('#modal-mask').classList.add('show');
  onMount && onMount($('#modal'));
}
function closeModal() { $('#modal-mask').classList.remove('show'); }

function scoreBadge(s) {
  if (s === undefined || s === null) return '<span class="badge badge-neutral">–</span>';
  const k = s >= 85 ? 'ok' : s >= 70 ? 'warn' : 'err';
  return `<span class="badge badge-${k}">${s}</span>`;
}

/* 选中文本右键菜单 —— 局部改写能力, 老版的核心交互, 保留并做成配置驱动 */
function bindContextMenu(el, items, onPick) {
  // 视图重渲染时 bindMenus 会被反复调用, 同一个 textarea 绑多次监听 ->
  // 右键一次弹多次菜单、onPick 触发多次(会连发几次生成请求)。打标记去重。
  if (el._ctxBound) return;
  el._ctxBound = true;
  const draw = (m, list, q='') => {
    const show = q ? list.filter(x => x.name.includes(q)) : list;
    let html = '', last = null;
    show.forEach(it => {
      if (it.group && it.group !== last) {
        html += `<div class="ctx-group">${esc(it.group)}</div>`;
        last = it.group;
      }
      html += `<div class="ctx-item" data-n="${esc(it.name)}">${esc(it.name)}</div>`;
    });
    m.querySelector('.ctx-body').innerHTML = html ||
      '<div class="ctx-item" style="color:var(--text-3)">无匹配</div>';
  };
  el.addEventListener('contextmenu', e => {
    const ta = el.tagName === 'TEXTAREA';
    // 记录原始下标: 替换回正文时必须按下标改, 不能拿文本去 replace ——
    // 同一句话在前文出现过, String.replace 会改错地方(改的是第一次出现)。
    let start = 0, end = 0, sel = '';
    if (ta) {
      start = el.selectionStart; end = el.selectionEnd;
      const raw = el.value.slice(start, end);
      const lead = raw.length - raw.trimStart().length;
      sel = raw.trim();
      start += lead; end = start + sel.length;
    } else {
      sel = String(window.getSelection()).trim();
    }
    if (!sel) return;
    e.preventDefault();
    const m = $('#ctx-menu');
    const peek = sel.length > 42 ? sel.slice(0, 20) + '…' + sel.slice(-16) : sel;
    // 选区回显: 菜单一弹出, textarea 就失焦, 浏览器不再绘制选中高亮,
    // 用户看不出选了什么, 会以为整章都要被提交。把选中内容直接写在菜单顶部。
    m.innerHTML = `<div class="ctx-sel" title="${esc(sel.slice(0, 400))}">
                     已选 <b>${sel.length}</b> 字：${esc(peek)}</div>
                   <input class="ctx-search" placeholder="筛选 ${items.length} 条指令…">
                   <div class="ctx-body"></div>`;
    draw(m, items);
    m.style.display = 'block';
    m.style.left = Math.min(e.clientX, innerWidth - 260) + 'px';
    m.style.top  = Math.min(e.clientY, innerHeight - 400) + 'px';
    const inp = m.querySelector('.ctx-search');
    inp.oninput = () => draw(m, items, inp.value.trim());
    // 不再 autofocus 搜索框 —— 抢焦点会让 textarea 的选中高亮消失。
    // 直接敲字即可筛选: 键盘事件转发到搜索框。
    const onKey = ev => {
      if (ev.key === 'Escape') { m.style.display = 'none'; return; }
      if (ev.key.length === 1 || ev.key === 'Backspace') {
        if (document.activeElement !== inp) inp.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    m._cleanup = () => document.removeEventListener('keydown', onKey);
    m.onclick = ev => {
      const n = ev.target.dataset.n;
      if (n) {
        m.style.display = 'none';
        if (m._cleanup) m._cleanup();
        onPick(items.find(x => x.name === n), sel, {start, end});
      }
    };
  });
}
document.addEventListener('click', e => {
  if (!e.target.closest('#ctx-menu')) {
    const m = $('#ctx-menu');
    m.style.display = 'none';
    if (m._cleanup) { m._cleanup(); m._cleanup = null; }
  }
});
$('#modal-mask').addEventListener('click', e => { if (e.target.id === 'modal-mask') closeModal(); });
