/* 视图层. 每个 view 返回 HTML 字符串, mount 后绑事件. */
const S = { catalog:null, settings:null, projects:[], cur:null, tab:'overview',
            curChapter:null, pollTimer:null, pulseTimer:null, pulseSeen:null };

/* ─────────────────────────── 工作台 ─────────────────────────── */
const Dashboard = {
  title: () => '工作台',
  actions: () => `<button class="btn btn-primary" id="a-new">＋ 新建项目</button>`,
  async render() {
    if (S.catalogError) {
      // 明确告诉用户出了什么事、怎么办, 而不是给一张白纸
      return `<div class="card"><div class="empty">
        <div class="empty-ico">⚠</div>
        <div class="empty-title">后端连接失败</div>
        <div>${esc(S.catalogError)}</div>
        <div class="card-sub" style="margin-top:8px">
          检查服务是否在跑（scripts/serve.sh），或 .env 里的模型网关配置</div>
        <button class="btn btn-primary" style="margin-top:16px"
          onclick="location.reload()">重试</button></div></div>`;
    }
    S.projects = await API.projects();
    const words = S.projects.reduce((a,p)=>a+(p.words||0),0);
    const chaps = S.projects.reduce((a,p)=>a+(p.done||0),0);
    const c = S.catalog;
    const stats = `<div class="grid grid-3" style="margin-bottom:18px">
      ${stat('项目', S.projects.length, '本')}
      ${stat('已生成章节', chaps, '章')}
      ${stat('累计字数', fmtNum(words), '字')}
      ${stat('可用模型网关', c.gateways.length, '个')}
      ${stat('内容类型', c.types.length, '种')}
      ${stat('题材包', c.genres.length, '个')}
    </div>`;
    if (!S.projects.length) return stats + `<div class="card"><div class="empty">
        <div class="empty-ico">◇</div><div class="empty-title">还没有项目</div>
        <div>新建一个项目，选好内容类型与题材，就能开始自动创作</div>
        <button class="btn btn-primary" id="a-new2" style="margin-top:16px">＋ 新建项目</button>
      </div></div>`;
    return stats + `<div class="grid grid-2">` + S.projects.map(p => {
      const pct = p.target_words ? Math.min(100, p.words/p.target_words*100) : 0;
      const ty = (c.types.find(t=>t.id===p.type_id)||{}).name || p.type_id;
      const ge = (c.genres.find(g=>g.id===p.genre_id)||{}).name || p.genre_id;
      return `<div class="card proj-card" data-slug="${esc(p.slug)}" style="cursor:pointer">
        <div class="card-head"><div class="card-title">${esc(p.title)}</div>
          <div class="card-actions"><span class="badge badge-accent">${esc(ty)}</span></div></div>
        <div class="card-sub">${esc(ge)} · ${esc(p.style_id||'')}</div>
        <div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>
        <div class="card-sub" style="margin-top:7px;font-family:var(--mono)">
          ${p.done||0}/${p.target_chapters} 章 · ${fmtNum(p.words)}/${fmtNum(p.target_words)} 字
          · ${pct.toFixed(1)}%</div></div>`;
    }).join('') + `</div>`;
  },
  mount() {
    $$('.proj-card').forEach(el => el.onclick = () => openProject(el.dataset.slug));
    ['#a-new','#a-new2'].forEach(s => { const b=$(s); if(b) b.onclick = newProjectModal; });
  }
};
const stat = (label, v, unit='') => `<div class="stat"><div class="stat-label">${label}</div>
  <div class="stat-value">${v}</div><div class="stat-sub">${unit}</div></div>`;

/* ─────────────────────────── 新建项目 ─────────────────────────── */
function newProjectModal() {
  const c = S.catalog;
  modal(`<h2>新建项目</h2>
    <div class="modal-sub">内容类型决定层级结构与导出格式；题材包提供专业写作规范</div>
    <div class="field"><label>作品名</label>
      <input class="input" id="f-title" placeholder="例如：我的第一本书"></div>
    <div class="field"><label>内容类型</label><div class="pill-group" id="f-type">
      ${c.types.map((t,i)=>`<div class="pill ${i?'':'active'}" data-v="${t.id}">${esc(t.name)}</div>`).join('')}
    </div><div class="hint" id="f-type-hint"></div></div>
    <div class="row">
      <div class="field"><label>题材包</label><select class="select" id="f-genre">
        ${c.genres.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select></div>
      <div class="field"><label>世界基底 <span class="card-sub">决定哪些红线成立</span></label>
        <select class="select" id="f-mode">
          <option value="auto">跟随题材包（推荐）</option>
          <option value="real">真实历史 —— 用真朝代真人，校史实</option>
          <option value="modern">当代现实 —— 钉死年份，晚于该年的事物即穿帮</option>
          <option value="alt">架空世界 —— 自造国号，出现真朝代名即穿帮</option>
          <option value="invented">纯虚构 —— 无真实时代参照，只校自洽</option>
        </select></div>
      <div class="field"><label>平台文风</label><select class="select" id="f-style">
        <option value="">（不指定）</option>
        ${c.styles.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select></div>
    </div>
    <div class="row">
      <div class="field"><label>目标字数</label>
        <input class="input" id="f-words" type="number" value="100000" step="10000"></div>
      <div class="field"><label>章节数（留空自动算）</label>
        <input class="input" id="f-chapters" type="number" placeholder="自动"></div>
    </div>
    <div class="field"><label>一句话故事</label>
      <textarea class="ta" id="f-premise" style="min-height:60px"></textarea></div>
    <div class="field"><label>背景设定</label><textarea class="ta" id="f-bg"></textarea></div>
    <div class="modal-foot">
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" id="f-ok">创建</button></div>`,
  { onMount(m) {
      const hint = () => { const id = $('.pill.active', $('#f-type')).dataset.v;
        const t = S.catalog.typeDetail[id];
        $('#f-type-hint').textContent = t ? `层级：${t.levels.map(l=>l.name).join(' → ')} ｜ 导出：${(t.exporters||[]).join(' / ')}` : '';
        // 每种内容类型有推荐文风(小说=番茄爽文, 短剧=短剧钩子, 动漫=漫画分镜),
        // 切类型时自动选上, 用户不必猜哪个文风配哪个类型
        const g = (S.catalog.genres||[]).find(x => x.id === $('#f-genre').value);
        const hm = $('#f-mode');
        if (hm && hm.value === 'auto' && g)
          hm.options[0].text = g.historyModeAmbiguous
            ? `跟随题材包（${esc(g.historyMode||'')}）—— 该题材两可，建议手动确认`
            : `跟随题材包（${esc(g.historyMode||'')}）`;
        const ds = t && t.defaultStyle;
        if (ds && $('#f-style').querySelector(`option[value="${ds}"]`))
          $('#f-style').value = ds; };
      const gsel = $('#f-genre'); if (gsel) gsel.onchange = hint;
      $$('.pill', $('#f-type')).forEach(p => p.onclick = () => {
        $$('.pill', $('#f-type')).forEach(x=>x.classList.remove('active'));
        p.classList.add('active'); hint(); });
      hint();
      $('#f-ok').onclick = async () => {
        const title = $('#f-title').value.trim();
        if (!title) return toast('请填写作品名', 'err');
        $('#f-ok').disabled = true;
        try {
          const p = await API.createProject({
            title, type_id: $('.pill.active', $('#f-type')).dataset.v,
            genre_id: $('#f-genre').value, style_id: $('#f-style').value,
            history_mode: $('#f-mode').value,
            target_words: +$('#f-words').value || 100000,
            target_chapters: +$('#f-chapters').value || 0,
            fields: { premise: $('#f-premise').value, background: $('#f-bg').value }
          });
          closeModal(); toast('项目已创建', 'ok'); await openProject(p.slug);
        } catch(e) { toast('创建失败：'+e.message, 'err'); $('#f-ok').disabled = false; }
      };
  }});
}

/* ─────────────────────────── 项目详情 ─────────────────────────── */
const TABS = [['overview','概览'],['setup','设定'],['outline','大纲'],
              ['structure','结构'],['chapters','章节'],['prompts','提示词'],
              ['trace','调用追踪'],['quality','质检'],['memory','记忆'],
              ['teardown','拆书'],['export','导出']];

const ProjectView = {
  title: () => S.cur ? S.cur.meta.title : '项目',
  actions() {
    const j = (S.cur && S.cur.job) || {};
    const running = j.running;
    const waiting = j.waiting;
    return `<span class="badge badge-neutral" id="model-badge">${esc((S.cur&&S.cur.meta.model)||'')}</span>
      ${waiting ? `<span class="badge badge-warn">${esc(j.stage||'待审阅')}</span>
        <button class="btn btn-primary" id="a-resume">✓ 审阅完毕，继续</button>` : ''}
      <button class="btn ${running?'btn-danger':'btn-primary'}" id="a-auto">
        ${running?'■ 停止':'▶ 自动创作'}</button>`;
  },
  async render() {
    const p = S.cur; if (!p) return '';
    const tabs = `<div class="tabs">${TABS.map(([k,n])=>
      `<div class="tab ${S.tab===k?'active':''}" data-tab="${k}">${n}</div>`).join('')}
      <div id="pulse" style="margin-left:auto;display:flex;gap:8px;align-items:center"></div></div>`;
    return tabs + `<div id="tab-body">${TabRender[S.tab](p)}</div>`;
  },
  mount() {
    $$('.tab').forEach(t => t.onclick = () => { S.tab = t.dataset.tab; render();
      if (S.cur) startPulse(); });   // 重渲染会重建 #pulse 节点, 心跳要跟着重挂
    const a = $('#a-auto'); if (a) a.onclick = toggleAuto;
    const rs = $('#a-resume'); if (rs) rs.onclick = async () => {
      const j = S.cur.job || {};
      await API.auto(S.cur.slug, {upto: j.upto || S.cur.meta.target_chapters, mode: 'staged'});
      toast('继续下一阶段', 'ok'); startPoll();
    };
    TabMount[S.tab] && TabMount[S.tab]();
  }
};

/* 可编辑文档卡片: 世界观/角色/总纲/守则/时代卡 通用 */
function editableDoc(docKey, title, text, extra='') {
  return `<div class="card"><div class="card-head"><div class="card-title">${title}</div>
      <div class="card-sub">${(text||'').length} 字 · 可直接编辑</div>
      <div class="card-actions">${extra}
        <button class="btn btn-sm btn-primary doc-save" data-doc="${docKey}">保存</button></div></div>
      <textarea class="ta doc-edit" data-doc="${docKey}"
        style="min-height:260px;max-height:460px;font-size:13.5px;line-height:1.8"
        placeholder="尚未生成，可点上方按钮生成，或直接手写">${esc(text||'')}</textarea></div>`;
}
function bindDocSaves() {
  $$('.doc-save').forEach(b => b.onclick = async () => {
    const k = b.dataset.doc;
    const ta = $(`.doc-edit[data-doc="${k}"]`);
    b.disabled = true;
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(S.cur.slug)}/doc/${k}`,
        {method:'PUT', headers:{'Content-Type':'application/json'},
         body: JSON.stringify({text: ta.value})});
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      toast(`已保存${d.indexed?`，重建索引 ${d.indexed} 条`:''}`, 'ok');
    } catch(e) { toast('保存失败：'+e.message, 'err'); }
    b.disabled = false;
  });
}

const TabRender = {
  overview(p) {
    const pct = p.meta.target_words ? Math.min(100, p.words/p.meta.target_words*100) : 0;
    const j = p.job || {};
    const scores = Object.keys(p.state.summaries||{}).length;
    return `<div class="grid grid-3" style="margin-bottom:16px">
        ${stat('进度', `${(p.state.done||[]).length}/${p.meta.target_chapters}`, '章')}
        ${stat('字数', fmtNum(p.words), `目标 ${fmtNum(p.meta.target_words)}`)}
        ${stat('完成度', pct.toFixed(1)+'%', '')}
        ${stat('记忆条目', Object.entries(p.memory||{}).filter(([k])=>!k.startsWith('fore')).reduce((a,[,v])=>a+v,0), '条')}
        ${stat('未回收伏笔', (p.memory||{}).foreshadow_total - ((p.memory||{}).foreshadow_resolved||0) || 0, '个')}
        ${stat('状态', j.running ? '生成中' : '空闲', esc(j.stage||''))}
      </div>
      <div class="card"><div class="card-head"><div class="card-title">运行日志</div>
        <div class="card-actions"><button class="btn btn-sm btn-ghost" id="a-refresh">刷新</button></div></div>
        <div class="mono-log" id="log">${esc((p.state.log||[]).slice(-20).join('\n'))||'（暂无）'}</div></div>
      <div class="card"><div class="card-head"><div class="card-title">项目管理</div>
        <div class="card-sub">归档只是改名收起来，稿子还在磁盘上；彻底删除不可恢复</div>
        <div class="card-actions">
          <button class="btn btn-sm" id="a-archive">归档</button>
          <button class="btn btn-sm btn-danger" id="a-drop">彻底删除</button></div></div></div>
      <div class="card"><div class="card-head"><div class="card-title">项目看板</div></div>
        <div class="mono-log" style="max-height:320px">${esc(p.board)}</div></div>`;
  },
  setup(p) {
    const f = p.meta.fields || {};
    return `<div class="card"><div class="card-head"><div class="card-title">基础设定</div>
        <div class="card-actions">
          <button class="btn btn-sm btn-primary" id="s-fields">保存设定</button></div></div>
        <div class="field"><label>一句话故事</label>
          <textarea class="ta" id="e-premise" style="min-height:56px">${esc(f.premise||'')}</textarea></div>
        <div class="field"><label>背景设定</label>
          <textarea class="ta" id="e-bg">${esc(f.background||'')}</textarea></div>
        <div class="field"><label>人物关系</label>
          <textarea class="ta" id="e-rel" placeholder="谁和谁什么关系、有什么矛盾；提示词变量 \${relationships}"
            >${esc(f.relationships||'')}</textarea></div>
        <div class="field"><label>知识库（提示词变量 \${kb}）</label>
          <div class="card-sub" style="margin-bottom:6px">写进来的资料每次生成都能被引用；
            系统检索攒下的事实卡会自动追加在后面（当前 ${p.kb_facts||0} 张）</div>
          <textarea class="ta" id="e-kb" style="min-height:140px"
            placeholder="行业知识、专业设定、金融/医学/法律细节、你自己的考据笔记…"
            >${esc(f.kb||'')}</textarea>
          <details style="margin-top:10px" id="kb-facts-box">
            <summary style="cursor:pointer;color:var(--text-3);font-size:13px">
              查看检索攒下的 ${p.kb_facts||0} 张事实卡（模型自己搜来的，可逐条删除）</summary>
            <div style="display:flex;gap:8px;margin-top:8px">
              <input class="input" id="kb-q" placeholder="筛选已有卡片（按主题或正文）">
              <input class="input" id="kb-new" placeholder="检索新主题，例：明代镖局规矩">
              <button class="btn btn-sm btn-primary" id="kb-go">检索入库</button></div>
            <div id="kb-facts" class="scroll-y" style="max-height:360px;margin-top:8px">
              加载中…</div></details></div></div>
      <div class="card"><div class="card-head">
          <div class="card-title">两个旋钮</div>
          <div class="card-sub" id="dl-sub">爽度管回报有多猛，狂野度管敢不敢失控 —— 同时影响剧情与文风</div>
          <div class="card-actions"><button class="btn btn-sm" id="dl-save">保存</button></div>
        </div><div id="dl-body"><div class="card-sub">读取中…</div></div></div>
      ${editableDoc('naming','书名 · 简介 · 标签', p.naming,
        '<button class="btn btn-sm" id="s-naming">重新生成</button>')}
      ${editableDoc('basis','世界基底卡 · 本书红线', p.basis,
        '<button class="btn btn-sm" id="s-basis">重新判定</button>')}
      ${editableDoc('world_bible','世界观圣经', p.world_bible,
        '<button class="btn btn-sm" id="s-wb">重新生成</button>')}
      ${editableDoc('characters','角色档案', p.characters,
        '<button class="btn btn-sm" id="s-ch">重新生成</button>')}
      ${editableDoc('era_card','时代红线卡', p.era_card||'')}
      ${editableDoc('style_guide','写作守则（自审沉淀）', p.style_guide||'')}`;
  },
  outline(p) {
    const co = p.chapter_outlines || {};
    const keys = Object.keys(co).sort((a,b)=>a-b);
    return `<div class="card"><div class="card-head">
        <div class="card-title">剧情概要</div>
        <div class="card-sub" id="rc-sub">读取中…</div>
        <div class="card-actions"><button class="btn btn-sm" id="rc-reload">刷新</button></div>
      </div><div id="rc-body"><div class="card-sub">读取中…</div></div></div>
      ${editableDoc('outline','总纲', p.outline,
        '<button class="btn btn-sm" id="o-gen">重新生成</button>')}
      <div class="card"><div class="card-head"><div class="card-title">分章细纲</div>
        <div class="card-sub">${keys.length} 章 · 每章可独立编辑保存</div>
        <div class="card-actions"><button class="btn btn-sm" id="o-batch">续生成 10 章</button></div></div>
        <div class="scroll-y">${keys.length ? keys.map(k=>
          `<div style="padding:9px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:8px;align-items:center;margin-bottom:5px">
              <span class="badge badge-neutral">第 ${k} 章</span>
              <button class="btn btn-sm co-save" data-n="${k}" style="margin-left:auto">保存</button></div>
            <textarea class="ta co-edit" data-n="${k}"
              style="min-height:100px;font-size:13px">${esc(co[k])}</textarea></div>`).join('')
          : '<div class="empty">尚未生成细纲</div>'}</div></div>`;
  },
  structure(p) {
    return `<div class="card"><div class="card-head">
        <div class="card-title">故事骨架</div>
        <div class="card-sub" id="st-sub">读取中…</div>
        <div class="card-actions">
          <button class="btn btn-sm" id="st-build">生成骨架</button>
          <button class="btn btn-sm" id="st-reload">刷新</button></div>
      </div><div id="st-body"><div class="card-sub">读取中…</div></div></div>`;
  },
  trace(p) {
    return `<div class="card"><div class="card-head">
        <div class="card-title">调用追踪</div>
        <div class="card-sub" id="tr-sub">每一次发给模型的提示词与回复，最近 400 条</div>
        <div class="card-actions"><button class="btn btn-sm" id="tr-reload">刷新</button></div>
      </div><div id="tr-body"><div class="card-sub">读取中…</div></div></div>
      <div class="card" id="tr-detail-card" style="display:none">
        <div class="card-head"><div class="card-title" id="tr-detail-title">调用详情</div>
          <div class="card-actions"><button class="btn btn-sm" id="tr-close">收起</button></div></div>
        <div id="tr-detail"></div></div>`;
  },
  chapters(p) {
    const done = (p.state.done||[]).slice().sort((a,b)=>a-b);
    const co = p.chapter_outlines || {};
    const nameOf = n => { const m = /第\s*\d+\s*章\s*(.+)/.exec(co[n]||''); return m ? m[1].split('\n')[0].slice(0,24) : ''; };
    return `<div class="split">
      <div class="card"><div class="card-head"><div class="card-title">章节</div>
        <div class="card-sub">${done.length} 章</div></div>
        <div class="scroll-y">${done.length ? done.map(n=>
          `<div class="chapter-row ${S.curChapter===n?'active':''}" data-n="${n}">
            <span class="chapter-num">${String(n).padStart(3,'0')}</span>
            <span class="chapter-name">${esc(nameOf(n))||'—'}</span></div>`).join('')
          : '<div class="empty" style="padding:30px 10px"><div>还没有正文</div></div>'}</div></div>
      <div class="card"><div class="card-head"><div class="card-title" id="c-title">正文</div>
        <div class="card-actions" id="c-actions"></div></div>
        <div id="c-think"></div>
        <textarea class="ta prose" id="c-body" style="min-height:460px;border:1px solid var(--border);
          padding:14px" placeholder="从左侧选择章节，或点右上角「自动创作」。选中文字后右键可局部改写。"></textarea>
        <div class="card-sub" style="margin-top:8px">选中文字 → 右键 → 扩写 / 润色 / 去 AI 味 / 加冲突…（菜单来自内容类型包 + 题材包）</div>
        </div></div>`;
  },
  teardown(p) {
    return `<div class="card"><div class="card-head"><div class="card-title">拆书</div>
        <div class="card-sub">把一本现成的小说拆成可复用素材，并直接写进本项目</div>
        <div class="card-actions"><button class="btn btn-sm btn-primary" id="td-go">开始拆解</button></div></div>
        <div class="field"><label>粘贴整本或部分正文（≥500 字）</label>
          <textarea class="ta" id="td-text" style="min-height:150px"
            placeholder="支持「第N章」「Chapter N」「N、标题」等章节标记；识别不出时按 3000 字自动分段"></textarea></div>
        <div class="row" style="align-items:flex-end">
          <div class="field" style="margin:0"><label>抽样章数（章多时避免整本烧钱）</label>
            <input class="input" id="td-n" type="number" value="8"></div>
          <div class="field" style="margin:0"><label>写入本项目</label>
            <select class="select" id="td-apply"><option value="1">是（世界观/角色/爽点/文风入库）</option>
              <option value="0">否（只看结果）</option></select></div></div>
        <div class="mono-log" id="td-out" style="margin-top:14px;max-height:420px">等待拆解…</div></div>`;
  },
  prompts(p) {
    return `<div class="card"><div class="card-head"><div class="card-title">本书提示词</div>
        <div class="card-sub">留空 = 用内置模板；填写后本书生效。支持变量替换</div>
        <div class="card-actions"><button class="btn btn-sm btn-primary" id="pr-save">保存全部</button></div></div>
        <div id="pr-list"><div class="card-sub">加载中…</div></div>
        <div class="card-sub" style="margin-top:12px" id="pr-vars"></div></div>`;
  },
  quality(p) {
    return `<div class="card"><div class="card-head"><div class="card-title">三层质检</div>
        <div class="card-sub">单章合格 ≠ 全书合格：逐章 95 分的稿子，全书体检可能只有 20 分</div>
        <div class="card-actions">
          <button class="btn btn-sm btn-primary" id="q-book">全书体检</button>
          <input class="input" id="q-n" type="number" style="width:78px"
                 value="${(p.state.done||[]).slice(-1)[0]||1}">
          <button class="btn btn-sm" id="q-win">邻章窗口</button></div></div>
        <div id="q-out"><div class="card-sub">点右上角开始体检</div></div></div>
      <div class="card"><div class="card-head"><div class="card-title">自审守则</div>
        <div class="card-sub">每 ${(S.settings&&S.settings.quality&&S.settings.quality.reflect_every)||5} 章自读自批一次，结论注入后续每一章</div>
        <div class="card-actions"><button class="btn btn-sm" id="q-reflect">立即自审</button></div></div>
        <div class="mono-log" id="q-guide" style="max-height:280px">加载中…</div></div>
      <div class="card"><div class="card-head"><div class="card-title">设定治理</div>
        <div class="card-sub">世界观被污染时不必推倒重来</div>
        <div class="card-actions"><button class="btn btn-sm" id="q-repair">设定返修</button></div></div>
        <div id="q-anchor" class="mono-log">加载中…</div></div>`;
  },
  memory(p) {
    const m = p.memory || {};
    return `<div class="card"><div class="card-head"><div class="card-title">动态台账</div>
        <div class="card-sub">每章自动更新，全部进红线约束层 —— 抽错一条会一路错到底，发现不对可直接删</div>
        <div class="card-actions">
          <input class="input" id="lg-q" placeholder="筛选" style="width:150px">
          <button class="btn btn-sm" id="lg-reload">刷新</button></div></div>
        <div class="pill-group" id="lg-kinds" style="margin-bottom:10px"></div>
        <div id="lg-out" class="scroll-y" style="max-height:460px">加载中…</div></div>
      <div class="card"><div class="card-head"><div class="card-title">多记忆索引</div>
        <div class="card-sub">SQLite FTS5 · 世界观 / 角色 / 往期剧情 / 伏笔</div></div>
        <div class="grid grid-3" style="margin-bottom:14px">
          ${stat('世界观', m.world||0,'条')}${stat('角色', m.role||0,'条')}
          ${stat('剧情', m.plot||0,'条')}${stat('伏笔', m.foreshadow_total||0,'个')}
          ${stat('已回收', m.foreshadow_resolved||0,'个')}</div>
        <div class="row" style="align-items:flex-end">
          <div class="field" style="margin:0"><label>检索（写到 300 章也能找回第 30 章埋的线）</label>
            <input class="input" id="m-q" placeholder="例如：武松 玉佩 药铺"></div>
          <button class="btn btn-primary" id="m-go" style="flex:0 0 auto;margin-bottom:0">检索</button></div>
        <div id="m-out" style="margin-top:14px"></div></div>
      <div class="card"><div class="card-head"><div class="card-title">分层记忆预算</div>
        <div class="card-sub">写某一章时，五层各占多少上下文 —— 配比在「全局设置」里可调</div>
        <div class="card-actions">
          <input class="input" id="lc-n" type="number" style="width:82px" value="${(p.state.done||[]).slice(-1)[0]||1}">
          <button class="btn btn-sm" id="lc-go">查看</button></div></div>
        <div id="lc-out"><div class="card-sub">输入章号后点「查看」</div></div></div>`;
  },
  export(p) {
    const t = S.catalog.typeDetail[p.meta.type_id] || {};
    const all = [['txt','纯文本 TXT'],['md','Markdown'],
                 ['plan','创作方案（骨架/支线/阶梯/张力/承诺 + 全部细纲）'],
                 ['outline','仅大纲（总纲+细纲纯文本）'],
                 ['docx','Word DOCX'],['epub','电子书 EPUB'],
                 ['fountain','剧本 Fountain'],['srt','字幕 SRT']];
    return `<div class="card"><div class="card-head"><div class="card-title">导出</div>
      <div class="card-sub">类型「${esc(t.name||'')}」声明的格式：${(t.exporters||[]).join(' / ')}</div></div>
      <div class="grid grid-3">${all.map(([k,n])=>
        `<a class="btn" href="${API.exportUrl(p.slug,k)}" download style="justify-content:center">⬇ ${n}</a>`).join('')}</div>
      <div class="card-sub" style="margin-top:14px">
        一份设定可导出多种形态：小说正文、剧本、字幕、纯大纲。这是「一稿多态」的落点。</div></div>`;
  }
};

const TabMount = {
  overview() {
    const r = $('#a-refresh'); if (r) r.onclick = () => openProject(S.cur.slug);
    const slug = S.cur.slug, title = S.cur.meta.title || slug;
    const drop = async (hard) => {
      // 几十万字误删没有后悔药, 所以彻底删除要把书名打出来
      if (hard) {
        const typed = prompt(`彻底删除《${title}》，${(S.cur.state.done||[]).length} 章将永久消失。\n`
                             + `确认请输入书名：`);
        if (typed !== title) return toast('书名不符，已取消', 'err');
      } else if (!confirm(`归档《${title}》？稿子还在磁盘上，随时可以找回。`)) return;
      const res = await fetch(`/api/projects/${encodeURIComponent(slug)}`,
        {method:'DELETE', headers:{'Content-Type':'application/json'},
         body: JSON.stringify({hard})});
      const j = await res.json();
      if (!res.ok) return toast(j.error || '失败', 'err');
      toast(j.action === 'deleted' ? '已彻底删除' : `已归档为 ${j.slug}`, 'ok');
      S.cur = null; stopPulse(); go('dashboard'); refreshSidebar();
    };
    const ab = $('#a-archive'); if (ab) ab.onclick = () => drop(false);
    const db = $('#a-drop');   if (db) db.onclick = () => drop(true);
  },
  setup() {
    (async () => {
      const slug = encodeURIComponent(S.cur.slug);
      if (!$('#dl-body')) return;
      let D;
      const draw = () => {
        $('#dl-body').innerHTML = (D.spec || []).map(s => {
          const v = D.dials[s.key];
          const band = arr => (arr.find(([lo, hi]) => (v >= lo && v < hi) || (hi >= 100 && v >= lo)) || arr[arr.length-1])[2];
          return `<div style="padding:10px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;align-items:center;gap:10px">
              <b style="flex:0 0 70px">${esc(s.label)}</b>
              <input type="range" min="0" max="100" step="5" value="${v}"
                     data-k="${s.key}" class="dl-range" style="flex:1">
              <span class="badge badge-neutral dl-val" data-k="${s.key}"
                    style="flex:0 0 52px;text-align:center">${v}</span>
            </div>
            <div class="card-sub" style="margin-top:5px">剧情：${esc(band(s.plot))}</div>
            <div class="card-sub">文风：${esc(band(s.style))}</div></div>`;
        }).join('') + `<div class="card-sub" style="margin-top:9px">
            由旋钮推出的硬指标：每阶段主角必须真失手 <b>${D.derived.setback_quota}</b> 次
            ／每 <b>${D.derived.pleasure_interval}</b> 章必须有一次实打实的回报
            ${D.own ? '' : '　<span class="badge badge-neutral">当前用全局默认</span>'}</div>`;
        $$('.dl-range').forEach(el => el.oninput = () => {
          D.dials[el.dataset.k] = +el.value;
          const b = $(`.dl-val[data-k="${el.dataset.k}"]`);
          if (b) b.textContent = el.value;
        });
        $$('.dl-range').forEach(el => el.onchange = draw);
      };
      try {
        D = await API.get(`/api/projects/${slug}/dials`);
        draw();
        $('#dl-save').onclick = async () => {
          const r = await API.put(`/api/projects/${slug}/dials`, D.dials);
          D.derived = r.derived; D.own = true; draw();
          toast('旋钮已保存，下一批生成生效', 'ok');
        };
      } catch (e) { $('#dl-body').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; }
    })();
    bindDocSaves();
    bindMenus();
    $('#s-wb').onclick = () => runStep('world_bible', '.doc-edit[data-doc="world_bible"]', '生成世界观');
    $('#s-ch').onclick = () => runStep('characters', '.doc-edit[data-doc="characters"]', '生成角色档案');
    const box = $('#kb-facts-box');
    if (box) box.ontoggle = async () => {
      if (!box.open || box.dataset.loaded) return;
      box.dataset.loaded = '1';
      const d = await API.get(`/api/projects/${encodeURIComponent(S.cur.slug)}/facts`);
      const draw = () => {
        const q = ($('#kb-q') && $('#kb-q').value.trim()) || '';
        const list = q ? d.cards.filter(c =>
          c.topic.includes(q) || (c.card||'').includes(q)) : d.cards;
        $('#kb-facts').innerHTML = list.length ? list.map(c => `
        <div style="padding:8px 0;border-bottom:1px solid var(--border)">
          <div style="display:flex;gap:8px;align-items:center">
            <span class="badge badge-neutral">${esc(c.topic)}</span>
            <span class="card-sub">${esc(c.at||'')}</span>
            <button class="btn btn-sm kb-del" data-t="${esc(c.topic)}"
              style="margin-left:auto">删除</button></div>
          <div class="card-sub" style="white-space:pre-wrap;margin-top:4px"
            >${esc(c.card)}</div>
          ${(c.sources||[]).map(u=>`<div class="card-sub" style="font-size:11px;
            overflow:hidden;text-overflow:ellipsis">${esc(u)}</div>`).join('')}
        </div>`).join('') : `<div class="empty">${q ? '没有匹配的卡片' : '还没有检索到事实卡'}</div>`;
        $$('.kb-del').forEach(b => b.onclick = async () => {
          await fetch(`/api/projects/${encodeURIComponent(S.cur.slug)}/facts`,
            {method:'DELETE', headers:{'Content-Type':'application/json'},
             body: JSON.stringify({topic: b.dataset.t})});
          d.cards = d.cards.filter(x => x.topic !== b.dataset.t);
          draw(); toast('已删除', 'ok');
        });
      };
      draw();
      $('#kb-q').oninput = draw;
      $('#kb-go').onclick = async () => {
        const topic = $('#kb-new').value.trim();
        if (!topic) return toast('先填要检索的主题', 'err');
        $('#kb-go').disabled = true; toast('检索中…');
        try {
          const r = await fetch(`/api/projects/${encodeURIComponent(S.cur.slug)}/facts`,
            {method:'POST', headers:{'Content-Type':'application/json'},
             body: JSON.stringify({topic})});
          const j = await r.json();
          if (!r.ok) {
            // 「没搜到」和「搜到了但被守门判定答非所问」是两回事, 后者要让用户
            // 看见原始摘录并自己决定留不留
            if (j.reason === 'rejected' && confirm(
                `${j.message}\n实际搜索词：${j.query}\n\n原始摘录：\n`
                + (j.raw_preview||'').slice(0,300) + '\n\n仍然入库？')) {
              const r2 = await fetch(`/api/projects/${encodeURIComponent(S.cur.slug)}/facts`,
                {method:'POST', headers:{'Content-Type':'application/json'},
                 body: JSON.stringify({topic, keep_raw:true, force:true})});
              const j2 = await r2.json();
              if (!r2.ok) throw new Error(j2.message || '入库失败');
              d.cards.unshift({topic:j2.card.topic, card:j2.card.card,
                sources:j2.card.sources||[], at:j2.card.built_at||''});
              $('#kb-new').value=''; draw(); toast('已按原始摘录入库', 'ok');
              $('#kb-go').disabled = false; return;
            }
            throw new Error(j.message || j.error || '检索失败');
          }
          d.cards.unshift({topic: j.card.topic, card: j.card.card,
                           sources: j.card.sources||[], at: j.card.built_at||''});
          $('#kb-new').value = ''; draw(); toast('已入库', 'ok');
        } catch(e) { toast(e.message, 'err'); }
        $('#kb-go').disabled = false;
      };
    };
    const bb = $('#s-basis');
    if (bb) bb.onclick = () => runStep('basis', null, '重新判定世界基底');
    const nb = $('#s-naming');
    if (nb) nb.onclick = () => runStep('naming', null, '重新起名与写简介');
    $('#s-fields').onclick = async () => {
      const meta = S.cur.meta;
      meta.fields = {...(meta.fields||{}), premise: $('#e-premise').value,
        background: $('#e-bg').value, relationships: $('#e-rel').value,
        kb: $('#e-kb').value};
      // 走 doc 通道以外的轻量保存: 直接 PUT prompts（meta 保存借道）
      await fetch(`/api/projects/${encodeURIComponent(S.cur.slug)}/fields`,
        {method:'PUT', headers:{'Content-Type':'application/json'},
         body: JSON.stringify(meta.fields)}).then(r=>{
           if(r.ok) toast('设定已保存','ok'); else toast('保存失败','err');});
    };
  },
  outline() {
    (async () => {
      const slug = encodeURIComponent(S.cur.slug);
      if (!$('#rc-body')) return;
      const load = async () => {
        let d;
        try { d = await API.get(`/api/projects/${slug}/recap`); }
        catch (e) { $('#rc-body').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; return; }
        const rows = d.chapters || [];
        $('#rc-sub').textContent =
          `全书概要写到第 ${d.recap_at||0} 章 ｜ 逐章一句话 ${d.with_one}/${rows.length} 章`;
        const vol = n => (d.volumes||[]).find(v => n >= v.start && n <= v.end);
        let cur = null, body = '';
        rows.forEach(r => {
          const v = vol(r.n);
          const key = v ? v.name : '其余';
          if (key !== cur) {
            cur = key;
            body += `<tr><td colspan="4" style="background:var(--bg-2);font-weight:600;
              padding:7px 6px">${esc(key)}${v?`　<span class="card-sub">第${v.start}-${v.end}章</span>`:''}</td></tr>`;
          }
          body += `<tr><td style="font-family:var(--mono)">${r.n}</td>
            <td>${esc(r.title)}</td>
            <td style="white-space:normal;word-break:break-word">${esc(r.one||'—')}</td>
            <td class="card-sub" style="white-space:normal;word-break:break-word">${esc(r.hook||'')}</td></tr>`;
        });
        $('#rc-body').innerHTML =
          (d.recap ? `<div class="field"><label>到第 ${d.recap_at} 章为止（每批重写，从逐章一句话重新生成）</label>
             <textarea class="ta" readonly style="min-height:200px">${esc(d.recap)}</textarea></div>` : '')
          + (rows.length ? `<div class="scroll-y" style="max-height:520px">
             <table class="tbl" style="table-layout:fixed">
               <colgroup><col style="width:52px"><col style="width:150px">
                 <col style="width:auto"><col style="width:30%"></colgroup>
               <thead><tr><th>#</th><th>章名</th><th>一句话</th><th>章末钩子</th></tr></thead>
               <tbody>${body}</tbody></table></div>`
            : '<div class="empty">还没有细纲</div>');
      };
      $('#rc-reload').onclick = load;
      load();
    })();
    bindDocSaves();
    bindMenus();
    $$('.co-save').forEach(b => b.onclick = async () => {
      const n = b.dataset.n;
      const r = await fetch(`/api/projects/${encodeURIComponent(S.cur.slug)}/chapter_outline/${n}`,
        {method:'PUT', headers:{'Content-Type':'application/json'},
         body: JSON.stringify({text: $(`.co-edit[data-n="${n}"]`).value})});
      toast(r.ok ? `第 ${n} 章细纲已保存` : '保存失败', r.ok ? 'ok' : 'err');
    });
    $('#o-gen').onclick   = () => runStep('outline', '.doc-edit[data-doc="outline"]', '生成总纲');
    $('#o-batch').onclick = () => {
      const done = Object.keys(S.cur.chapter_outlines||{}).length;
      runStep('chapter_outlines', null, '生成细纲', {n: done+1, count: 10});
    };
  },
  structure() {
    const slug = encodeURIComponent(S.cur.slug);
    const load = async () => {
      let d;
      try { d = await API.get(`/api/projects/${slug}/structure`); }
      catch (e) { $('#st-body').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; return; }
      const su = d.search_usage || {};
      $('#st-sub').textContent =
        `细纲 ${d.chapters_planned} 章 · 阶段 ${(d.stages||[]).length} · 支线 ${(d.threads||[]).length}`
        + ` · 张力 ${(d.tensions||[]).length} · 承诺 ${(d.promises||[]).length}`
        + (su.calls ? ` · 检索已用 ${su.calls} 次` : '');
      if (!d.chapters_planned) {
        $('#st-body').innerHTML = '<div class="empty">还没有细纲，先排纲</div>'; return;
      }
      // 每一块都用同一套表格骨架，列宽写死 —— 各块自己排自己的版会左右参差
      const sect = (title, sub, head, rows, empty) => `
        <div style="margin:16px 0 22px">
          <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:7px;
                      padding-bottom:5px;border-bottom:2px solid var(--border)">
            <b style="font-size:14px">${title}</b><span class="card-sub">${sub}</span></div>
          ${rows ? `<table class="tbl" style="table-layout:fixed;width:100%">
              <colgroup>${head.map(h=>`<col style="width:${h[1]}">`).join('')}</colgroup>
              <thead><tr>${head.map(h=>`<th>${h[0]}</th>`).join('')}</tr></thead>
              <tbody>${rows}</tbody></table>`
            : `<div class="card-sub">${empty}</div>`}
        </div>`;
      const wrap = s => `<div style="white-space:normal;word-break:break-word">${s}</div>`;
      const slotLabel = {}; (d.slots||[]).forEach(s => slotLabel[s.key] = s.label);
      const kindLabel = {}; (d.ladder_kinds||[]).forEach(k => kindLabel[k.key] = k.label);
      const mono = s => `<span style="font-family:var(--mono);font-size:12px">${s}</span>`;

      const issues = (d.issues||[]).map(i => `<tr>
          <td><span class="badge badge-warn">${esc(i.kind)}</span></td>
          <td class="card-sub">${esc(i.where||'—')}</td>
          <td>${wrap(esc(i.text))}</td></tr>`).join('');

      const repairs = (d.repairs||[]).map(r => `<tr>
          <td><span class="badge badge-neutral">${esc(r.kind)}</span></td>
          <td>${mono('第 ' + r.chapters.join('、') + ' 章')}</td>
          <td>${wrap(esc(String(r.demand).slice(0,260)))}</td></tr>`).join('');

      const stages = (d.stages||[]).map(s => `<tr>
          <td><b>${esc(s.name)}</b><div>${mono(s.start + '–' + s.end)}</div></td>
          <td>${wrap(esc(s.goal||''))}
            ${(s.steps||[]).length?`<div class="card-sub" style="margin-top:4px">
              ${s.steps.map(esc).join(' → ')}</div>`:''}</td>
          <td>${Object.entries(s.roles||{}).map(([k,v]) => (v&&v.length)
              ? `<div style="font-size:12px;padding:1px 0">
                   <span class="card-sub">${esc(slotLabel[k]||k)}</span> ${v.map(esc).join('、')}</div>`
              : `<div style="font-size:12px;padding:1px 0">
                   <span class="card-sub">${esc(slotLabel[k]||k)}</span>
                   <span class="badge badge-warn">空缺</span></div>`).join('')}</td>
          <td class="card-sub">${wrap(esc(s.exit||'—'))}</td></tr>`).join('');

      const threads = (d.threads||[]).map(x => {
        const end = Math.min(d.upto, x.span[1]);
        const gap = end - (x.last_touched || x.span[0]);
        const bad = gap >= x.cadence;
        return `<tr>
          <td><b>${esc(x.name)}</b>
            <div class="card-sub">${esc(x.kind||'')}</div></td>
          <td>${wrap((x.owner||[]).map(esc).join('、') + (x.org?`<div class="card-sub">${esc(x.org)}</div>`:''))}</td>
          <td>${mono(x.span[0] + '–' + x.span[1])}</td>
          <td>${mono('每 ' + x.cadence + ' 章')}</td>
          <td>${mono(x.last_touched||'—')}</td>
          <td><span class="badge ${bad?'badge-warn':'badge-ok'}">${bad?'断 '+gap+' 章':'正常'}</span></td>
        </tr>`; }).join('');

      const ladders = Object.entries(d.ladders||{}).map(([k,rungs]) => `<tr>
          <td><b>${esc(kindLabel[k]||k)}</b></td>
          <td>${rungs.map(r => {
              const on = d.upto >= r.by;
              return `<span class="badge ${on?'badge-ok':'badge-neutral'}"
                style="margin:2px 3px 2px 0" title="${esc(r.check||'')}">
                ${esc(r.stage)}<span class="card-sub"> 第${r.by}章</span></span>`;
            }).join('')}</td></tr>`).join('');

      const tensions = (d.tensions||[]).map(x => `<tr>
          <td><b>${wrap((x.between||[]).map(esc).join(' ↔ '))}</b></td>
          <td><span class="badge ${x.state==='已了结'?'badge-ok':'badge-neutral'}">${esc(x.state||'压着')}</span></td>
          <td>${wrap(esc(x.about||''))}</td>
          <td>${mono(x.last_touched||'—')}</td></tr>`).join('');

      const promises = (d.promises||[]).map(x => `<tr>
          <td><span class="badge badge-neutral">${esc(x.kind||'')}</span></td>
          <td>${wrap(esc(x.text||''))}</td>
          <td>${mono('第 ' + (x.last_advanced||0) + ' 章')}</td></tr>`).join('');

      const cast = (d.cast||[]).slice(0,30).map(c => `<tr>
          <td><b>${esc(c.name)}</b></td>
          <td>${mono(c.chapters)}</td>
          <td>${mono(c.first + '–' + c.last)}</td>
          <td>${c.gap>=25?`<span class="badge badge-warn">${c.gap}</span>`:mono(c.gap)}</td></tr>`).join('');

      $('#st-body').innerHTML =
        sect('结构问题', `${(d.issues||[]).length} 条`,
             [['类别','110px'],['位置','130px'],['说明','auto']], issues, '没有检出结构问题')
      + sect('待重排', `${(d.repairs||[]).length} 条 · scripts/replan.py 执行`,
             [['类别','130px'],['章节','160px'],['要解决什么','auto']], repairs, '没有待重排的章节')
      + sect('阶段骨架', '目标 → 步骤 → 功能位 → 人',
             [['阶段','130px'],['目标与步骤','auto'],['功能位','260px'],['出口状态','220px']],
             stages, '还没生成阶段骨架')
      + sect('支线', '主线管方向，支线管密度',
             [['支线','150px'],['承载者','170px'],['区间','90px'],['节奏','80px'],
              ['末次','60px'],['状态','100px']], threads, '还没生成支线')
      + sect('三条阶梯', '力量 / 爽点 / 人设，按全书进度演进',
             [['线','100px'],['各级（已到达的高亮）','auto']], ladders, '还没生成阶梯')
      + sect('关系张力', '只能被明写的事件推动，不许靠一方消失来消解',
             [['当事双方','180px'],['状态','90px'],['因何而起','auto'],['末次','60px']],
             tensions, '还没生成张力账')
      + sect('总纲承诺', '久未推进会被判挨饿',
             [['类型','90px'],['承诺','auto'],['末次推进','110px']], promises, '还没生成承诺清单')
      + sect('人物出场', '按细纲「出场角色」统计，前 30 位',
             [['角色','140px'],['出场章数','90px'],['首–末','110px'],['最大空档','90px']],
             cast, '还没有出场数据');
    };
    if ($('#st-body')) {
      $('#st-reload').onclick = load;
      $('#st-build').onclick = async () => {
        if (!confirm('读总纲生成阶段骨架、支线、三条阶梯、张力账与承诺清单。\n'
                     + '要花几次模型调用，已有的不会重建。继续？')) return;
        const b = $('#st-build'); b.disabled = true; b.textContent = '生成中…';
        try {
          const r = await API.post(`/api/projects/${slug}/structure/build`, {});
          toast(r.ok ? '骨架已生成' : ('部分失败：' + JSON.stringify(r.errors)),
                r.ok ? 'ok' : 'err');
          await load();
        } catch (e) { toast('生成失败：' + e.message, 'err'); }
        b.disabled = false; b.textContent = '生成骨架';
      };
      load();
    }
  },
  trace() {
    const slug = encodeURIComponent(S.cur.slug);
    const show = async (seq) => {
      const r = await API.get(`/api/projects/${slug}/trace?seq=${seq}`);
      $('#tr-detail-card').style.display = '';
      $('#tr-detail-title').textContent =
        `#${r.seq}　${r.profile}　${r.model||''}　${r.elapsed}s　`
        + `提示词 ${fmtNum(r.prompt_chars)} 字符 → 输出 ${fmtNum(r.out_chars)} 字符`;
      $('#tr-detail').innerHTML = `
        <div class="field"><label>实际发出去的提示词</label>
          <textarea class="ta" readonly style="min-height:420px;font-size:12px;
            font-family:var(--mono)">${esc(r.prompt||'')}</textarea></div>
        <div class="field"><label>模型回复</label>
          <textarea class="ta" readonly style="min-height:220px;font-size:12px;
            font-family:var(--mono)">${esc(r.output||'')}</textarea></div>`;
      $('#tr-detail-card').scrollIntoView({behavior:'smooth', block:'start'});
    };
    const load = async () => {
      let d;
      try { d = await API.get(`/api/projects/${slug}/trace`); }
      catch (e) { $('#tr-body').innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; return; }
      const cs = d.calls || [];
      $('#tr-sub').textContent = `共 ${d.total||cs.length} 条（保留最近 400 条），点行看全文`;
      if (!cs.length) { $('#tr-body').innerHTML = `<div class="empty">${esc(d.note||'还没有调用记录')}</div>`; return; }
      $('#tr-body').innerHTML = `<table class="tbl" style="table-layout:fixed">
        <colgroup><col style="width:56px"><col style="width:150px"><col style="width:96px">
          <col style="width:72px"><col style="width:110px"><col style="width:auto"></colgroup>
        <thead><tr><th>#</th><th>时间</th><th>档位</th><th>耗时</th><th>提示词/输出</th>
          <th>回复开头</th></tr></thead><tbody>` + cs.map(c => `
        <tr class="tr-row" data-seq="${c.seq}" style="cursor:pointer">
          <td style="font-family:var(--mono)">${c.seq}</td>
          <td class="card-sub">${esc(c.at||'')}</td>
          <td><span class="badge badge-neutral">${esc(c.profile||'')}</span>
            ${c.thinking?'<span class="badge badge-warn">思考</span>':''}</td>
          <td style="font-family:var(--mono)">${c.elapsed}s</td>
          <td style="font-family:var(--mono);font-size:12px">
            ${fmtNum(c.prompt_chars)} → ${fmtNum(c.out_chars)}</td>
          <td class="card-sub" style="white-space:normal;word-break:break-word">
            ${esc(String(c.output||'').slice(0,90))}</td></tr>`).join('')
        + `</tbody></table>`;
      $$('.tr-row').forEach(el => el.onclick = () => show(el.dataset.seq));
    };
    if ($('#tr-body')) {
      $('#tr-reload').onclick = load;
      $('#tr-close').onclick = () => { $('#tr-detail-card').style.display = 'none'; };
      load();
    }
  },
  chapters() {
    $$('.chapter-row').forEach(r => r.onclick = async () => {
      S.curChapter = +r.dataset.n;
      const d = await API.chapter(S.cur.slug, S.curChapter);
      $('#c-title').textContent = `第 ${S.curChapter} 章`;
      $('#c-actions').innerHTML = `<span class="card-sub">${d.audit?.stats?.cn||0} 字</span>
        ${scoreBadge(d.audit?.score)}
        ${d.audit?.rewritten?'<span class="badge badge-warn">已重写</span>':''}
        <button class="btn btn-sm btn-primary" id="c-save">保存</button>`;
      $('#c-save').onclick = saveChapter;
      $('#c-body').value = d.text || '';
      $$('.chapter-row').forEach(x=>x.classList.toggle('active', +x.dataset.n===S.curChapter));
      bindMenus();
    });
    bindMenus();
  },
  teardown() {
    $('#td-go').onclick = async () => {
      const text = $('#td-text').value;
      if (text.length < 500) return toast('文本太短（至少 500 字）', 'err');
      $('#td-out').textContent = '';
      $('#td-go').disabled = true;
      await API.stream('/api/teardown', {
        text, sample: +$('#td-n').value || 8,
        slug: $('#td-apply').value === '1' ? S.cur.slug : null
      }, {
        onText: t => { $('#td-out').textContent += t; $('#td-out').scrollTop = 1e9; },
        onError: e => toast('拆书失败：' + e.message, 'err'),
        onDone: () => { $('#td-go').disabled = false; toast('拆书完成', 'ok'); }
      });
    };
  },
  prompts() {
    const slug = encodeURIComponent(S.cur.slug);
    (async () => {
      const d = await API.get(`/api/projects/${slug}/prompts`);
      $('#pr-vars').innerHTML = '可用变量：' + d.variables.map(v =>
        `<code>${esc(v)}</code>`).join(' ');
      // 不自动预填: 一旦填进去, 这本书就固化了当时的模板, 以后内置模板改进
      // (新增纪律/修 bug)都跟这本书无关了。所以保持「留空=继承」, 但必须让
      // 用户看得见默认模板全文, 并能一键复制进来改 —— 原来只给 160 字截断预览,
      // 等于让人对着空白框猜内置模板长什么样。
      $('#pr-list').innerHTML = Object.entries(d.keys).map(([k, label]) => `
        <div class="field"><label>${esc(label)} <span class="card-sub">(${esc(k)})</span>
          ${d.overrides[k] ? '<span class="badge badge-warn">本书已覆盖</span>'
                           : '<span class="badge badge-neutral">用内置模板</span>'}</label>
          <details style="margin-bottom:6px"><summary style="cursor:pointer;
            color:var(--text-3);font-size:12.5px">内置模板全文（${(d.defaults[k]||'').length} 字）</summary>
            <pre style="white-space:pre-wrap;background:var(--surface-2);padding:10px;
              border-radius:8px;margin:6px 0;font-size:12px;max-height:30vh;overflow:auto"
              >${esc(d.defaults[k]||'（无）')}</pre>
            <button class="btn btn-sm pr-load" data-k="${esc(k)}">载入默认模板到下方编辑</button>
          </details>
          <textarea class="ta pr-edit" data-k="${esc(k)}"
            style="min-height:90px;font-family:var(--mono);font-size:12.5px"
            placeholder="留空 = 跟随内置模板（内置模板日后改进本书自动受益）"
            >${esc(d.overrides[k]||'')}</textarea></div>`).join('');
      $$('.pr-load').forEach(b => b.onclick = () => {
        const t = document.querySelector(`.pr-edit[data-k="${b.dataset.k}"]`);
        if (t) { t.value = d.defaults[b.dataset.k] || ''; t.focus();
                 toast('已载入，改完记得点右上角保存', 'ok'); }
      });
      $('#pr-save').onclick = async () => {
        const body = {};
        $$('.pr-edit').forEach(t => body[t.dataset.k] = t.value);
        const r = await fetch(`/api/projects/${slug}/prompts`,
          {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
        toast(r.ok ? '提示词已保存，本书后续生成生效' : '保存失败', r.ok ? 'ok' : 'err');
      };
    })();
  },
  quality() {
    const slug = encodeURIComponent(S.cur.slug);
    const render = (r, title) => {
      if (r.error) return `<div class="card-sub">${esc(r.error)}</div>`;
      const k = r.score >= 80 ? 'ok' : r.score >= 55 ? 'warn' : 'err';
      return `<div style="margin-bottom:12px"><b>${title}</b>
        <span class="badge badge-${k}" style="margin-left:8px">${r.score} / 100</span>
        ${r.total_words?`<span class="card-sub"> · ${fmtNum(r.total_words)} 字 / ${r.chapters} 章</span>`:''}</div>
        ${(r.issues||[]).length ? `<table class="tbl"><thead><tr><th style="width:70px">级别</th>
          <th style="width:150px">问题</th><th>详情</th></tr></thead><tbody>` +
          r.issues.map(i=>{
            const lv={high:'err',mid:'warn',low:'neutral'}[i.level]||'neutral';
            let d=i.detail; if (typeof d!=='string') d=JSON.stringify(d,null,0);
            return `<tr><td><span class="badge badge-${lv}">${i.level}</span></td>
              <td><b>${esc(i.type)}</b></td>
              <td style="color:var(--text-2);font-size:12.5px">${esc(String(d||'').slice(0,320))}</td></tr>`;
          }).join('') + `</tbody></table>`
          : '<div class="card-sub">未发现问题</div>'}`;
    };
    $('#q-book').onclick = async () => {
      $('#q-out').innerHTML = '<div class="card-sub">体检中…</div>';
      try { $('#q-out').innerHTML = render(await API.get(`/api/projects/${slug}/bookaudit`), '全书体检'); }
      catch(e){ $('#q-out').innerHTML = `<div class="card-sub">${esc(e.message)}</div>`; }
    };
    $('#q-win').onclick = async () => {
      const n = +$('#q-n').value;
      $('#q-out').innerHTML = '<div class="card-sub">体检中…</div>';
      try {
        const r = await API.get(`/api/projects/${slug}/window/${n}`);
        $('#q-out').innerHTML = render(r, `第 ${n} 章 · 邻章窗口 ${JSON.stringify(r.window||[])}`);
      } catch(e){ $('#q-out').innerHTML = `<div class="card-sub">${esc(e.message)}</div>`; }
    };
    $('#q-reflect').onclick = () => runStep('reflect', '#q-guide', '自审');
    $('#q-repair').onclick = () => runStep('repair', null, '设定返修');
    (async () => {
      try {
        const d = await API.project(S.cur.slug);
        $('#q-guide').textContent = d.style_guide || '（还没有自审守则，写满几章后自动生成）';
      } catch { $('#q-guide').textContent = '(读取失败)'; }
      try {
        const a = await API.get(`/api/projects/${slug}/anchor`);
        $('#q-anchor').textContent =
          `历史模式：${a.anchor.mode}\n国号：${a.anchor.dynasty||'（不适用）'}\n` +
          `主场：${a.anchor.main_place||'-'}\n禁用术语：${(a.anchor.forbidden||[]).slice(0,10).join('、')||'无'}\n` +
          `角色花名册（${a.roster.length}）：${a.roster.join('、')}\n\n` +
          `下一章将收到的质检反馈：\n${a.tic_guard||'（暂无）'}`;
      } catch(e){ $('#q-anchor').textContent = '(读取失败) ' + e.message; }
    })();
  },
  memory() {
    const slug = encodeURIComponent(S.cur.slug);
    (async () => {
      let LG = null, cur = 'canon';
      const draw = () => {
        const q = ($('#lg-q').value||'').trim();
        let rows = (LG[cur] || []).slice();
        if (q) rows = rows.filter(r => JSON.stringify(r).includes(q));
        rows.sort((a,b) => (b.chapter||0)-(a.chapter||0));
        $('#lg-out').innerHTML = rows.length ? rows.slice(0,300).map(r => `
          <div style="display:flex;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)">
            <span class="badge badge-neutral" style="flex:0 0 auto">第${r.chapter||'?'}章</span>
            ${r.kind?`<span class="badge badge-neutral" style="flex:0 0 auto">${esc(r.kind)}</span>`:''}
            ${(r.subject || (cur!=='canon'&&cur!=='foreshadow'&&r.key))
              ?`<span style="flex:0 0 auto;font-weight:600">${esc(r.subject||r.key)}</span>`:''}
            <span style="flex:1;font-size:13px;color:var(--text-2)">${esc(r.fact||r.text||'')}
              ${r.done?' <span class="badge badge-ok">已回收</span>':''}</span>
            ${cur==='foreshadow'?'':`<button class="btn btn-sm lg-del"
              data-k="${esc(r.key)}" style="flex:0 0 auto">删除</button>`}
          </div>`).join('') : '<div class="empty">这本台账还是空的</div>';
        $$('.lg-del').forEach(b => b.onclick = async () => {
          if (!confirm('删除这条台账？它正作为红线约束影响后续章节。')) return;
          const r = await fetch(`/api/projects/${slug}/ledgers`,
            {method:'DELETE', headers:{'Content-Type':'application/json'},
             body: JSON.stringify({kind: cur, key: b.dataset.k})});
          if (r.ok) { await load(); toast('已删除', 'ok'); } else toast('删除失败', 'err');
        });
      };
      const load = async () => {
        LG = await API.get(`/api/projects/${slug}/ledgers`);
        $('#lg-kinds').innerHTML = Object.entries(LG.kinds).map(([k, label]) =>
          `<div class="pill ${k===cur?'active':''}" data-k="${k}">${esc(label)}
            <span class="card-sub">${(LG[k]||[]).length}</span></div>`).join('');
        $$('#lg-kinds .pill').forEach(el => el.onclick = () => {
          cur = el.dataset.k;
          $$('#lg-kinds .pill').forEach(x=>x.classList.remove('active'));
          el.classList.add('active'); draw();
        });
        draw();
      };
      if ($('#lg-out')) {
        $('#lg-reload').onclick = load;
        $('#lg-q').oninput = () => LG && draw();
        await load();
      }
    })();
    const go = async () => {
      const q = $('#m-q').value.trim(); if (!q) return;
      $('#m-out').innerHTML = '<div class="card-sub">检索中…</div>';
      const r = await API.memory(S.cur.slug, q);
      $('#m-out').innerHTML = r.hits.length ? `<table class="tbl"><thead><tr>
          <th>类型</th><th>标题</th><th>内容</th></tr></thead><tbody>` +
        r.hits.map(h=>`<tr><td><span class="badge badge-neutral">${esc(h.kind)}</span></td>
          <td>${esc(h.title)}</td><td style="color:var(--text-2)">${esc(h.text.slice(0,150))}…</td></tr>`).join('')
        + `</tbody></table>` : '<div class="card-sub">没有命中</div>';
    };
    $('#m-go').onclick = go;
    $('#m-q').onkeydown = e => { if (e.key==='Enter') go(); };

    $('#lc-go').onclick = async () => {
      const n = +$('#lc-n').value;
      $('#lc-out').innerHTML = '<div class="card-sub">计算中…</div>';
      try {
        const r = await API.get(`/api/projects/${encodeURIComponent(S.cur.slug)}/context/${n}`);
        if (r.error) { $('#lc-out').innerHTML = `<div class="card-sub">${esc(r.error)}</div>`; return; }
        $('#lc-out').innerHTML = `
          <div class="card-sub" style="margin-bottom:10px">
            预算 <b>${fmtNum(r.total_budget)}</b> tok ｜ 实用 <b>${fmtNum(r.used)}</b> tok
            (${r.usage_pct}%) ${r.overflow.length?`｜ <span class="badge badge-warn">溢出：${r.overflow.join('、')}</span>`:''}
          </div>
          <table class="tbl"><thead><tr><th>层</th><th>用量 / 配额</th><th style="width:44%">占比</th><th>状态</th></tr></thead><tbody>
          ${r.layers.map(l=>{
            const w = r.total_budget ? l.tokens/r.total_budget*100 : 0;
            return `<tr><td><b>${esc(l.label)}</b><div class="card-sub" style="font-family:var(--mono)">${esc(l.key)}</div></td>
              <td style="font-family:var(--mono)">${fmtNum(l.tokens)} / ${fmtNum(l.cap)}</td>
              <td><div class="progress" style="margin:0"><div class="progress-bar" style="width:${w}%"></div></div></td>
              <td>${l.truncated?'<span class="badge badge-warn">已裁剪</span>':'<span class="badge badge-ok">完整</span>'}</td></tr>`;
          }).join('')}</tbody></table>`;
      } catch(e) { $('#lc-out').innerHTML = `<div class="card-sub">${esc(e.message)}</div>`; }
    };
  },
  export() {}
};

async function saveChapter() {
  const n = S.curChapter, text = $('#c-body').value;
  if (!n) return;
  try {
    const r = await API.post(`/api/projects/${encodeURIComponent(S.cur.slug)}/chapter/${n}`, {text});
    toast(`已保存，AI 味评分 ${r.audit.score}`, 'ok');
    const b = $('#c-actions .badge'); if (b) b.outerHTML = scoreBadge(r.audit.score);
  } catch(e) { toast('保存失败：'+e.message, 'err'); }
}

/* 右键菜单来源三处合并, 全部配置驱动, 代码里不写死任何一条:
     ① 内容类型包 packs/type/*.json 的 menus
     ② 题材对应的老版菜单 packs/shortcuts/legacy-genre-menus.json (v5.2 的 130 条资产)
     ③ 通用兜底项
   改写结果可「替换选中」直接写回正文 —— 这是老版的核心交互, 必须保留. */
function menuItems() {
  // 三层合并，同名以先出现的为准：
  //   ① 全局设置 config/settings.yaml -> context_menus（用户可随时改，对所有项目生效）
  //   ② 内容类型包 packs/type/*.json 的 menus（小说/剧本/短剧各有专属指令）
  //   ③ 题材包 v5.2 legacy-genre-menus（130 条老资产，按题材匹配）
  const t = S.catalog.typeDetail[S.cur.meta.type_id] || {};
  const lvl = (t.levels||[]).slice(-1)[0] || {};
  const legacy = (S.catalog.shortcuts||{})['legacy-genre-menus'];
  const gname = (S.catalog.genres.find(g=>g.id===S.cur.meta.genre_id)||{}).name;
  const genre = S.catalog.genres.find(g=>g.id===S.cur.meta.genre_id) || {};
  const layers = [
    ['通用',   S.catalog.context_menus || []],
    ['体裁',   (t.menus||{})[lvl.id] || []],
    ['题材',   genre.menus || (legacy && legacy.menus && legacy.menus[gname]) || []],
  ];
  const seen = new Set(), items = [];
  for (const [group, layer] of layers)
    for (const i of layer)
      if (i && i.name && i.prompt && !seen.has(i.name)) {
        seen.add(i.name); items.push({...i, group});
      }
  return items;
}

function bindMenus() {
  const items = menuItems();
  if (!items.length) return;
  // 右键改写不该只在正文可用 —— 大纲、世界观、人物档案、每章细纲都是要反复打磨的
  // 文本, 同样需要「选中一段让模型改」。这些编辑器的保存动作各不相同, 用 data 属性
  // 找回各自的保存按钮。
  const targets = [];
  const cb = $('#c-body');
  if (cb) targets.push({el: cb, save: () => saveChapter()});
  $$('.doc-edit').forEach(el => targets.push({
    el, save: () => { const b = document.querySelector(
      `.doc-save[data-doc="${el.dataset.doc}"]`); if (b) b.click(); }}));
  $$('.co-edit').forEach(el => targets.push({
    el, save: () => { const b = document.querySelector(
      `.co-save[data-n="${el.dataset.n}"]`); if (b) b.click(); }}));
  if (!targets.length) return;
  targets.forEach(({el: body, save}) =>
  bindContextMenu(body, items, async (item, sel, span) => {
    // 只有选中文本前端知道; 其余占位符(禁用词表/世界观/守则)由服务端按 slug 填,
    // 前端手上没有完整数据, 以前是替换成空字符串, 等于把规则悄悄丢了。
    const prompt = item.prompt.replace(/\$\{selected_text\}/g, sel);
    modal(`<h2>${esc(item.name)}</h2>
      <div class="modal-sub">选中原文 ${sel.length} 字 · 只改这一段，正文其余部分不动</div>
      <details style="margin-bottom:10px"><summary style="cursor:pointer;
        color:var(--text-3);font-size:13px">查看提交的原文</summary>
        <div class="prose" style="max-height:20vh;overflow-y:auto;background:var(--surface-2);
          padding:10px;border-radius:8px;margin-top:6px;font-size:13px">${esc(sel)}</div></details>
      <div class="prose" id="rw-out" style="max-height:46vh;overflow-y:auto;
        background:var(--surface);padding:12px;border-radius:8px"></div>
      <div class="modal-foot"><button class="btn" onclick="closeModal()">关闭</button>
        <button class="btn btn-primary" id="rw-apply" disabled>替换选中并保存</button></div>`);
    let out = '';
    API.stream('/api/gen', {prompt, profile:'polishing', slug:S.cur.slug}, {
      onText: t => { out += t; $('#rw-out').textContent = out; },
      onError: e => toast('生成失败：'+e.message, 'err'),
      onDone: () => {
        if (!out.trim()) return;
        const btn = $('#rw-apply'); if (!btn) return;
        btn.disabled = false;
        btn.onclick = async () => {
          const ta = body;
          // 按下标替换。用 ta.value.replace(sel, ...) 会命中文中第一处相同文本,
          // 选的是后文、改的却是前文。
          if (span && ta.value.slice(span.start, span.end) === sel) {
            ta.value = ta.value.slice(0, span.start) + out.trim()
                     + ta.value.slice(span.end);
          } else if (ta.value.includes(sel)) {
            ta.value = ta.value.replace(sel, out.trim());
          } else {
            toast('原文已被改动，未替换（生成结果可自行复制）', 'err');
            return;
          }
          closeModal(); await save();
        };
      }
    });
  }));
}

async function runStep(step, outSel, label, extra={}) {
  toast(label + '…');
  if (outSel) $(outSel).textContent = '';
  await API.stream(`/api/projects/${encodeURIComponent(S.cur.slug)}/step`,
    {step, ...extra}, {
      onText: t => { if (outSel) $(outSel).textContent += t; },
      onError: e => toast('失败：' + e.message, 'err'),
      onDone: async () => { toast(label + ' 完成', 'ok'); await openProject(S.cur.slug); }
    });
}

async function toggleAuto() {
  const running = S.cur.job && S.cur.job.running;
  if (running) { await API.auto(S.cur.slug, {stop:true}); toast('已请求停止'); return; }
  const left = S.cur.meta.target_chapters - (S.cur.state.done||[]).length;
  modal(`<h2>自动创作</h2><div class="modal-sub">
      世界观 → 角色 → 总纲 → 分章细纲 → 逐章正文 → 多遍评审 → 不合格自动重写</div>
    <div class="field"><label>模式</label><div class="pill-group" id="au-mode">
      <div class="pill active" data-v="auto">全自动<br><span class="card-sub">一路写到底，不打断</span></div>
      <div class="pill" data-v="staged">阶段自动<br><span class="card-sub">每阶段停下来，你审阅/编辑后再继续</span></div>
    </div></div>
    <div class="field"><label>本次写到第几章（剩余 ${left} 章）</label>
      <input class="input" id="au-n" type="number" value="${Math.min((S.cur.state.done||[]).length+5, S.cur.meta.target_chapters)}"></div>
    <div class="modal-foot"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" id="au-go">开始</button></div>`, { onMount() {
      $$('#au-mode .pill').forEach(x => x.onclick = () => {
        $$('#au-mode .pill').forEach(y=>y.classList.remove('active')); x.classList.add('active');
      });
      $('#au-go').onclick = async () => {
        await API.auto(S.cur.slug, {upto: +$('#au-n').value,
          mode: $('#au-mode .pill.active').dataset.v});
        closeModal(); toast('已启动后台创作', 'ok'); startPoll();
      };
  }});
}

function startPoll() {
  clearInterval(S.pollTimer);
  S.pollTimer = setInterval(async () => {
    if (!S.cur) return clearInterval(S.pollTimer);
    const j = await API.job(S.cur.slug);
    const log = $('#log'); if (log) log.textContent = (j.log||[]).join('\n');
    if (!j.running) { clearInterval(S.pollTimer); await openProject(S.cur.slug); toast('创作完成', 'ok'); }
  }, 4000);
}

/* 进度心跳 —— 不管是谁在写都要跟上。
   原来只有从界面点「自动创作」才轮询, 而长跑是命令行起的外部进程,
   界面完全不知道, 页面一直停在打开时的快照。进度是磁盘上的事实。*/
function startPulse() {
  clearInterval(S.pulseTimer);
  S.pulseSeen = null;
  const tick = async () => {
    if (!S.cur) return clearInterval(S.pulseTimer);
    let p;
    try { p = await API.get(`/api/projects/${encodeURIComponent(S.cur.slug)}/pulse`); }
    catch (e) { return; }                       // 后端抖一下不该把定时器打死
    const badge = $('#pulse');
    if (badge) badge.innerHTML = p.writing
      ? `<span class="badge badge-ok">写作中</span>
         <span class="card-sub">${p.done} 章 · ${fmtNum(p.words)} 字</span>`
      : `<span class="card-sub">${p.done} 章 · ${fmtNum(p.words)} 字</span>`;
    // 书名改了也要重渲染 —— 只比章数的话，改完书名页面标题一直是旧的
    if (p.title && S.cur.meta && p.title !== S.cur.meta.title) {
      S.pulseSeen = p.done;
      await refreshSidebar();
      await openProject(S.cur.slug);
      return;
    }
    if (S.pulseSeen === null) { S.pulseSeen = p.done; return; }
    if (p.done !== S.pulseSeen) {               // 出新章了才重渲染, 免得白刷
      S.pulseSeen = p.done;
      await refreshSidebar();
      await openProject(S.cur.slug);        // S.tab 是全局的, 刷新后仍停在当前标签
      toast(`已写到第 ${p.done} 章`, 'ok');
    }
  };
  S.pulseTimer = setInterval(tick, 8000);
  tick();
}

/* ─────────────────────────── 全局设置 ─────────────────────────── */
const SettingsView = {
  title: () => '全局设置',
  actions: () => `<button class="btn btn-primary" id="st-save">保存</button>`,
  async render() {
    const s = S.settings = await API.settings();
    const g = s.generation, l = s.limits, q = s.quality, m = s.memory, sd = s.style_defaults;
    const num = (id,label,v,hint='') => `<div class="field"><label>${label}</label>
      <input class="input" id="${id}" type="number" value="${v}" step="any">
      ${hint?`<div class="hint">${hint}</div>`:''}</div>`;
    return `<div class="card"><div class="card-head"><div class="card-title">字数与篇幅</div>
        <div class="card-sub">对所有项目生效，单个项目可覆盖</div></div>
        <div class="row">${num('g-min','单章字数下限',g.chapter_words_min)}
          ${num('g-max','单章字数上限',g.chapter_words_max)}</div>
        <div class="row">${num('l-ch','单本章节上限',l.max_chapters)}
          ${num('l-w','单本总字数上限',l.max_total_words)}</div></div>
      <div class="card"><div class="card-head"><div class="card-title">生成参数</div></div>
        <div class="row">${num('g-batch','每批细纲章数',g.outline_batch)}
          <div class="field"><label>记忆体预算 (token)</label>
          <input class="input" id="g-ctx" value="${esc(String(g.context_budget))}">
          <div class="hint">填 <code>auto</code> 按网关窗口自动推导；或填数字。限定 ${fmtNum(g.min_context_budget)}–${fmtNum(g.max_context_budget)}</div></div></div>
        <div class="row">${num('g-td','正文温度',g.temperature_draft)}
          ${num('g-tp','规划温度',g.temperature_plan)}</div></div>
      <div class="card"><div class="card-head"><div class="card-title">质量闸</div></div>
        <div class="row">${num('q-pass','AI 味合格线 (0-100)',q.audit_pass_score,'低于此分自动重写')}
          ${num('q-rw','每章最多重写次数',q.max_rewrites)}</div></div>
      <div class="card"><div class="card-head"><div class="card-title">记忆索引</div></div>
        <div class="row">${num('m-k','每次召回条数',m.top_k)}
          ${num('m-rec','带入最近章节摘要数',m.recent_chapters)}
          ${num('m-l2','每 N 章压缩一次摘要',m.l2_every)}</div></div>
      <div class="card"><div class="card-head"><div class="card-title">右键菜单</div>
        <div class="card-sub">在正文、大纲、世界观、人物档案、每章细纲里选中文字右键即可用；对所有项目生效；与内容类型包、题材包合并（同名以此处为准）</div>
        <div class="card-actions"><button class="btn btn-sm" id="cm-add">＋ 新增</button></div></div>
        <div id="cm-list"></div>
        <div class="card-sub" style="margin-top:10px;line-height:2">可用变量（除
          <code>\${selected_text}</code> 外均由服务端按当前项目填充）：<br>
          <code>\${selected_text}</code> 选中的文字
          <code>\${title}</code> 书名
          <code>\${background}</code> 时代背景
          <code>\${plot}</code> 核心剧情<br>
          <code>\${premise}</code> 一句话故事
          <code>\${characters}</code> 人物档案
          <code>\${relationships}</code> 人物关系
          <code>\${kb}</code> 知识库<br>
          <code>\${world_bible}</code> 世界观
          <code>\${outline}</code> 总纲
          <code>\${style}</code> 文风名
          <code>\${target_chapters}</code> 目标章数
          <code>\${target_words}</code> 目标字数<br>
          <code>\${era_card}</code> 时代红线卡
          <code>\${cliche_blacklist}</code> 禁用套话表
          <code>\${style_rules}</code> 文风纪律
          <code>\${genre_rules}</code> 题材纪律<br>
          <code>\${anti_ai_rules}</code> 去AI味纪律
          <code>\${chapter_directives}</code> 正文写法要求
          <code>\${character_rules}</code> 人物纪律
          <code>\${common_rules}</code> 通用纪律合集</div></div>
      <div class="card"><div class="card-head"><div class="card-title">统一写作偏好</div>
        <div class="card-sub">会拼进每一次生成的提示词</div></div>
        <div class="row">
          <div class="field"><label>叙事视角</label><input class="input" id="s-nar" value="${esc(sd.narration||'')}"></div>
          <div class="field"><label>时态</label><input class="input" id="s-tense" value="${esc(sd.tense||'')}"></div></div>
        <div class="field"><label>自定义追加要求</label>
          <textarea class="ta" id="s-extra" placeholder="例如：多用短句，少用比喻，对话要有性格差异">${esc(sd.extra||'')}</textarea></div>
        <div class="field"><label>全局禁用词（每行一个）</label>
          <textarea class="ta" id="s-ban">${esc((s.banned_global||[]).join('\n'))}</textarea></div>
        <div class="field"><label>正文写法要求（每行一条，管「怎么写才像网文」）</label>
          <textarea class="ta" id="s-dir" rows="8">${esc((s.chapter_directives||[]).join('\n'))}</textarea></div>
        <div class="field"><label>去 AI 味纪律（每行一条，管「怎么写才不像 AI」）</label>
          <textarea class="ta" id="s-anti" rows="10">${esc((s.anti_ai_rules||[]).join('\n'))}</textarea></div>
        <div class="field"><label>人物纪律（每行一条，管「人得像个人」——不降智 / 有血有肉 / 不死板）
          <span class="card-sub">排纲与写正文两处都会注入</span></label>
          <textarea class="ta" id="s-char" rows="12">${esc((s.character_rules||[]).join('\n'))}</textarea></div></div>`;
  },
  mount() {
    const renderMenus = () => {
      const list = S.settings.context_menus || [];
      $('#cm-list').innerHTML = list.length ? list.map((m,i)=>`
        <div class="card" style="box-shadow:none;padding:12px;margin-top:8px" data-i="${i}">
          <div class="row" style="align-items:flex-end;gap:8px">
            <div class="field" style="margin:0;flex:0 0 150px"><label>菜单名</label>
              <input class="input cm-name" value="${esc(m.name||'')}"></div>
            <button class="btn btn-sm btn-danger cm-del" style="flex:0 0 auto;margin-bottom:0">删除</button>
          </div>
          <div class="field" style="margin:8px 0 0"><label>提示词</label>
            <textarea class="ta cm-prompt" style="min-height:62px">${esc(m.prompt||'')}</textarea></div>
        </div>`).join('') : '<div class="card-sub">暂无，点右上角新增</div>';
      $$('.cm-del').forEach(b => b.onclick = () => {
        S.settings.context_menus.splice(+b.closest('[data-i]').dataset.i, 1);
        renderMenus();
      });
    };
    const collectMenus = () => $$('#cm-list [data-i]').map(el => ({
      name: $('.cm-name', el).value.trim(),
      prompt: $('.cm-prompt', el).value.trim(),
    })).filter(m => m.name && m.prompt);
    S.settings.context_menus = S.settings.context_menus || [];
    renderMenus();
    $('#cm-add').onclick = () => {
      S.settings.context_menus = collectMenus();
      S.settings.context_menus.push({name:'新菜单',
        prompt:'改写下面这段，直接输出：\n' + '$' + '{selected_text}'});
      renderMenus();
    };

    $('#st-save').onclick = async () => {
      const s = JSON.parse(JSON.stringify(S.settings));
      const v = id => +$(id).value;
      Object.assign(s.generation, {chapter_words_min:v('#g-min'), chapter_words_max:v('#g-max'),
        outline_batch:v('#g-batch'),
        context_budget: ($('#g-ctx').value.trim().toLowerCase() === 'auto'
                         ? 'auto' : (+$('#g-ctx').value || 'auto')),
        temperature_draft:v('#g-td'), temperature_plan:v('#g-tp')});
      Object.assign(s.limits, {max_chapters:v('#l-ch'), max_total_words:v('#l-w')});
      Object.assign(s.quality, {audit_pass_score:v('#q-pass'), max_rewrites:v('#q-rw')});
      Object.assign(s.memory, {top_k:v('#m-k'), recent_chapters:v('#m-rec'), l2_every:v('#m-l2')});
      Object.assign(s.style_defaults, {narration:$('#s-nar').value, tense:$('#s-tense').value,
        extra:$('#s-extra').value});
      s.banned_global = $('#s-ban').value.split('\n').map(x=>x.trim()).filter(Boolean);
      s.chapter_directives = $('#s-dir').value.split('\n').map(x=>x.trim()).filter(Boolean);
      s.anti_ai_rules = $('#s-anti').value.split('\n').map(x=>x.trim()).filter(Boolean);
      s.character_rules = $('#s-char').value.split('\n').map(x=>x.trim()).filter(Boolean);
      s.context_menus = collectMenus();
      await API.saveSettings(s);
      S.catalog = await API.catalog();          // 右键菜单立刻生效，不用刷新
      toast(`设置已保存（右键菜单 ${s.context_menus.length} 条），对所有项目生效`, 'ok');
    };
  }
};

/* ─────────────────────────── 插件包 ─────────────────────────── */
const PacksView = {
  title: () => '插件包',
  actions: () => '',
  async render() {
    const c = S.catalog;
    const sec = (title, sub, rows) => `<div class="card"><div class="card-head">
      <div class="card-title">${title}</div><div class="card-sub">${sub}</div></div>${rows}</div>`;
    return sec('内容类型', `${c.types.length} 种 · 决定层级结构与导出格式`,
        `<table class="tbl"><thead><tr><th>类型</th><th>层级链路</th><th>导出</th></tr></thead><tbody>` +
        c.types.map(t=>`<tr><td><b>${esc(t.name)}</b></td>
          <td style="color:var(--text-2)">${t.levels.map(esc).join(' → ')}</td>
          <td>${(t.exporters||[]).join(' / ')}</td></tr>`).join('') + `</tbody></table>`)
      + sec('题材包', `${c.genres.length} 个 · 力量体系 / 节奏表 / 套话黑名单`,
        `<div class="pill-group">${c.genres.map(g=>
          `<div class="pill genre-pill" data-id="${g.id}">${esc(g.name)}</div>`).join('')}</div>
         <div id="g-detail" style="margin-top:14px"></div>`)
      + sec('平台文风', `${c.styles.length} 个`,
        `<div class="pill-group">${c.styles.map(g=>`<div class="pill">${esc(g.name)}</div>`).join('')}</div>`)
      + sec('模型网关', `${c.gateways.length} 个 · 地址与密钥来自 .env，不进仓库`,
        `<table class="tbl"><thead><tr><th>网关</th><th>默认模型</th><th>上下文</th><th>接入自检</th></tr></thead><tbody>` +
        c.gateways.map(g=>`<tr><td>${esc(g.label)}</td><td><code>${esc(g.model||'')}</code></td>
          <td>${fmtNum(g.context_window)}</td>
          <td><button class="btn btn-sm probe-btn" data-gw="${esc(g.id)}">自检</button>
            <span class="probe-out" data-gw="${esc(g.id)}"></span></td></tr>`).join('')
        + `</tbody></table>
        <div class="card-sub" style="margin-top:10px">各厂商「OpenAI 兼容」的字段名并不统一
        （content / reasoning_content / reasoning / result / parts…）。自检会打一发真实请求，
        报告该网关实际用的字段与首字延迟 —— 接新模型出现空白时先跑这个。</div>`);
  },
  mount() {
    $$('.probe-btn').forEach(b => b.onclick = async () => {
      const gw = b.dataset.gw;
      const out = $(`.probe-out[data-gw="${gw}"]`);
      b.disabled = true; out.innerHTML = ' <span class="card-sub">检测中…</span>';
      try {
        const r = await API.post('/api/probe', {gateway: gw});
        out.innerHTML = ` <span class="badge badge-${r.ok?'ok':'err'}">${r.ok?'通':'异常'}</span>
          <span class="card-sub" style="font-family:var(--mono)">
          字段 ${esc((r.fields_seen||[]).join('/'))} · 首字 ${r.first_token_s ?? '-'}s</span>
          <div class="card-sub">${esc(r.diagnosis || r.error || '')}</div>`;
      } catch(e) { out.innerHTML = ` <span class="badge badge-err">失败</span>`; }
      b.disabled = false;
    });
    $$('.genre-pill').forEach(p => p.onclick = async () => {
      $$('.genre-pill').forEach(x=>x.classList.remove('active')); p.classList.add('active');
      const g = await API.get('/api/genre/'+p.dataset.id);
      $('#g-detail').innerHTML = `<div class="kv">
        <dt>核心爽点</dt><dd>${esc((g.corePleasure||[]).slice(0,4).join(' / '))||'—'}</dd>
        <dt>套话黑名单</dt><dd>${esc((g.clicheBlacklist||[]).join('、'))||'—'}</dd>
        <dt>常见坑</dt><dd>${esc((g.pitfalls||[]).slice(0,4).join(' / '))||'—'}</dd>
        <dt>对标</dt><dd>${esc(g.benchmarks||'—')}</dd></div>`;
    });
  }
};
