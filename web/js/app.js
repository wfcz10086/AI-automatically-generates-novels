/* 路由与启动. */
const VIEWS = { dashboard: Dashboard, settings: SettingsView, packs: PacksView, project: ProjectView };
let curView = 'dashboard';

async function render() {
  const v = VIEWS[curView];
  $('#page-title').textContent = v.title();
  $('#top-actions').innerHTML = v.actions ? v.actions() : '';
  $('#view').innerHTML = '<div class="card-sub">加载中…</div>';
  try {
    $('#view').innerHTML = await v.render();
    v.mount && v.mount();
    if (curView === 'dashboard') { const b=$('#a-new'); if(b) b.onclick = newProjectModal; }
  } catch (e) {
    $('#view').innerHTML = `<div class="card"><div class="empty">
      <div class="empty-ico">⚠</div><div class="empty-title">加载失败</div>
      <div style="font-family:var(--mono);font-size:12px">${esc(e.message)}</div></div></div>`;
  }
}

function go(view) {
  curView = view;
  if (view !== 'project') { S.cur = null; stopPulse(); }
  $$('.nav-item[data-view]').forEach(n => n.classList.toggle('active', n.dataset.view === view));
  $$('.proj-item').forEach(n => n.classList.toggle('active',
    S.cur && n.dataset.slug === S.cur.slug));
  render();
}

async function openProject(slug) {
  S.cur = await API.project(slug);
  S.cur.slug = slug;
  curView = 'project';
  $$('.nav-item[data-view]').forEach(n => n.classList.remove('active'));
  await refreshSidebar();
  await render();
  if (S.cur.job && S.cur.job.running) startPoll();
  startPulse();          // 命令行长跑也要能被界面看见
}

function stopPulse() { clearInterval(S.pulseTimer); S.pulseSeen = null; }

async function refreshSidebar() {
  const list = await API.projects();
  // 同名项目（如归档旧稿）用 slug 区分，否则侧栏两条一模一样根本分不清
  const dup = new Set();
  const byTitle = {};
  list.forEach(p => { byTitle[p.title] = (byTitle[p.title]||0)+1; });
  Object.entries(byTitle).forEach(([t,c]) => { if (c > 1) dup.add(t); });
  $('#proj-list').innerHTML = list.length ? list.map(p => {
    const archived = /^_v\d+_|^_archive/.test(p.slug);
    const tag = archived ? '<span class="badge badge-neutral" style="font-size:10px;margin-left:5px">归档</span>'
              : (dup.has(p.title) ? `<span class="card-sub" style="font-size:11px"> · ${esc(p.slug)}</span>` : '');
    return `<div class="proj-item ${S.cur && S.cur.slug === p.slug ? 'active' : ''}" data-slug="${esc(p.slug)}"
       style="${archived ? 'opacity:.62' : ''}">
      <div class="proj-title">${esc(p.title)}${tag}</div>
      <div class="proj-meta">${p.done || 0}/${p.target_chapters} 章 · ${fmtNum(p.words)} 字</div>
    </div>`; }).join('') : '<div class="card-sub" style="padding:8px 10px">暂无项目</div>';
  $$('.proj-item').forEach(el => el.onclick = () => openProject(el.dataset.slug));
}

function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('theme', t);
}

(async function boot() {
  applyTheme(localStorage.getItem('theme') || 'light');
  $('#btn-theme').onclick = () =>
    applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  $('#btn-new').onclick = newProjectModal;
  $$('.nav-item[data-view]').forEach(n => n.onclick = () => go(n.dataset.view));
  document.addEventListener('keydown', e => {          // 退出登录: Ctrl+Shift+Q
    if (e.ctrlKey && e.shiftKey && e.key === 'Q')
      fetch('/api/logout', {method:'POST'}).then(() => location.reload());
  });

  // 目录取不到时给一个安全的空壳: 各视图都在直接读 S.catalog.types/gateways,
  // 后端抖一下就会抛 TypeError, 结果是整页空白 —— 用户只看见一个 toast,
  // 完全不知道发生了什么, 也没有重试入口。
  S.catalog = {types: [], genres: [], styles: [], gateways: [],
               profiles: [], search: [], typeDetail: {}};
  try {
    S.catalog = await API.catalog();
    S.catalogOk = true;
    $('#pack-count').textContent =
      S.catalog.types.length + S.catalog.genres.length + S.catalog.styles.length;
    const h = await API.health();
    if (!h.gateways) toast('没有可用模型网关，请配置 .env', 'err');
  } catch (e) {
    if (String(e.message).includes('login_required') || String(e.message).includes('未登录')) {
      location.href = '/login.html'; return;
    }
    toast('后端连接失败：' + e.message, 'err');
    S.catalogError = e.message;
  }
  await refreshSidebar();
  await render();
})();
