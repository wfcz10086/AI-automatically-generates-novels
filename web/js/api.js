/* 后端 API 客户端. 所有网络访问集中在这里, 视图层不直接 fetch. */
const API = {
  async get(u)      { const r = await fetch(u); if(!r.ok) throw new Error(await r.text()); return r.json(); },
  async post(u, b)  { const r = await fetch(u, {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify(b||{})}); if(!r.ok) throw new Error(await r.text()); return r.json(); },

  catalog()             { return API.get('/api/catalog'); },
  health()              { return API.get('/api/health'); },
  settings()            { return API.get('/api/settings'); },
  saveSettings(s)       { return API.post('/api/settings', s); },
  projects()            { return API.get('/api/projects'); },
  createProject(b)      { return API.post('/api/projects', b); },
  project(slug)         { return API.get(`/api/projects/${encodeURIComponent(slug)}`); },
  chapter(slug, n)      { return API.get(`/api/projects/${encodeURIComponent(slug)}/chapter/${n}`); },
  job(slug)             { return API.get(`/api/projects/${encodeURIComponent(slug)}/job`); },
  auto(slug, b)         { return API.post(`/api/projects/${encodeURIComponent(slug)}/auto`, b); },
  memory(slug, q)       { return API.get(`/api/projects/${encodeURIComponent(slug)}/memory?q=${encodeURIComponent(q)}`); },
  exportUrl(slug, fmt)  { return `/api/projects/${encodeURIComponent(slug)}/export?fmt=${fmt}`; },

  /* SSE 流式. onText 收正文, onReason 收思考 —— 两者必须分开渲染,
     否则模型的内部独白会污染正文 (老版就是这么出空白/串味的). */
  async stream(url, body, {onText, onReason, onDone, onError} = {}) {
    const resp = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                                   body: JSON.stringify(body||{})});
    if (!resp.ok) { onError && onError(new Error(`HTTP ${resp.status}`)); return; }
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    for(;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      const lines = buf.split('\n\n'); buf = lines.pop();
      for (const chunk of lines) {
        if (!chunk.startsWith('data: ')) continue;
        let d; try { d = JSON.parse(chunk.slice(6)); } catch { continue; }
        if (d.t)     onText   && onText(d.t);
        if (d.r)     onReason && onReason(d.r);
        if (d.error) onError  && onError(new Error(d.error));
        if (d.done)  onDone   && onDone(d);
      }
    }
    onDone && onDone({});
  }
};
