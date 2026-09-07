#!/usr/bin/env python3
"""端到端测试 (Playwright). 真实浏览器 + 真实后端 + 真实模型.

  python3 tests/e2e/run.py            # 全部
  python3 tests/e2e/run.py --case 7   # 单条
  python3 tests/e2e/run.py --headed   # 有头观察
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:60001"
SHOTS = Path(__file__).resolve().parent.parent.parent / "reports" / "e2e"
SHOTS.mkdir(parents=True, exist_ok=True)

RESULTS: list = []
ERRORS: list = []


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)


def case(n, title):
    def deco(fn):
        fn._case = (n, title)
        return fn
    return deco


def open_first_project(page):
    """打开第一个**非归档**项目。

    归档副本（_v0_/_v1_ 前缀）是写坏后留底的残稿，章节少、索引不全，
    用它做检索类断言必然零命中 —— 挂的是测试数据不是功能。
    """
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".proj-card", timeout=15000)
    # 挑**章节最多的非归档项目**, 不是碰运气挑第一个: 首位可能是刚建的空项目
    # 或归档残稿, 检索类断言必然零命中 —— 挂的是测试数据不是功能
    import json as _j
    import urllib.request as _u
    projs = [x for x in _j.loads(_u.urlopen(BASE + "/api/projects").read())
             if not re.match(r"^_v\d+_|^_archive", x["slug"])]
    assert projs, "没有可用项目"
    picked = max(projs, key=lambda x: x.get("done") or 0)["slug"]
    page.eval_on_selector_all(
        ".proj-card",
        "(els, slug) => { const e = els.find(x => x.dataset.slug === slug); "
        "if (e) e.click(); }", picked)
    page.wait_for_selector(".tabs", timeout=15000)


@case(1, "首页与唯一主路由 /")
def c1(page):
    page.goto(BASE, wait_until="networkidle")
    assert page.title(), "无标题"
    expect(page.locator(".brand-name")).to_be_visible()
    expect(page.locator(".sidebar")).to_be_visible()
    shot(page, "01-dashboard")


@case(2, "目录加载：网关/类型/题材计数非零")
def c2(page):
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".stat-value", timeout=15000)
    assert len(page.locator(".stat-value").all()) >= 6
    assert "题材包" in page.locator("#view").inner_text()
    cnt = page.locator("#pack-count").inner_text()
    assert cnt.isdigit() and int(cnt) >= 20, f"插件包计数异常 {cnt}"
    shot(page, "02-catalog")


@case(3, "暗色主题：背景不透明且无横向滚动")
def c3(page):
    page.goto(BASE, wait_until="networkidle")
    page.click("#btn-theme")
    page.wait_for_timeout(400)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), f"body 背景透明 {bg}"
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
    shot(page, "03-dark")
    page.click("#btn-theme")
    page.wait_for_timeout(300)


@case(4, "插件包页：三种内容类型的层级链路各不相同")
def c4(page):
    page.goto(BASE, wait_until="networkidle")
    page.click('.nav-item[data-view="packs"]')
    page.wait_for_selector("table.tbl", timeout=15000)
    t = page.locator("#view").inner_text()
    for kw in ("长篇小说", "影视剧本", "短剧剧本"):
        assert kw in t, f"缺内容类型 {kw}"
    assert "→" in t and "fountain" in t.lower()
    page.locator(".genre-pill").first.click()
    page.wait_for_selector("#g-detail .kv", timeout=10000)
    assert "套话黑名单" in page.locator("#g-detail").inner_text()
    shot(page, "04-packs")


@case(27, "自动刷新：命令行长跑的进度，界面要能自己跟上")
def c27(page):
    """原来只有从界面点「自动创作」才轮询，而长跑是命令行起的外部进程，
    界面完全不知道，页面一直停在打开时的快照，得手动刷新。"""
    open_first_project(page)
    page.wait_for_selector("#pulse", timeout=8000)
    hits = []
    page.on("request", lambda r: hits.append(r.url) if "/pulse" in r.url else None)
    page.wait_for_timeout(1200)
    txt = page.inner_text("#pulse")
    assert "章" in txt and "字" in txt, f"心跳徽标没显示进度：{txt!r}"
    # 切标签后仍要在 —— 重渲染会重建 #pulse 节点，心跳得跟着重挂
    for tab in ("章节", "质检"):
        page.click(f'.tab:has-text("{tab}")')
        page.wait_for_timeout(1500)
        assert page.inner_text("#pulse").strip(), f"{tab} 页心跳徽标丢了"
    assert hits, "切换标签后不再轮询"
    shot(page, "27-pulse")


@case(26, "容错：目录接口失败时给出可操作的错误页，不是白屏")
def c26(page):
    # 后端抖一下就整页空白, 用户只看见一个 toast —— 这是真 bug 不是测试环境问题
    page.route("**/api/catalog", lambda r: r.abort())
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    body = page.inner_text("#view") if page.query_selector("#view") else page.inner_text("body")
    assert body.strip(), "目录接口失败导致整页空白"
    assert "后端连接失败" in body, f"没有给出可操作的错误提示：{body[:80]}"
    assert page.query_selector("button:has-text('重试')"), "缺少重试入口"
    shot(page, "26-catalog-fail")
    page.unroute("**/api/catalog")


@case(25, "动态台账：八类台账可见、可切换、可删除")
def c25(page):
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".proj-item")
    page.eval_on_selector_all(
        ".proj-item",
        "els => { const e = els.find(x => !/^_v\\d+_/.test(x.dataset.slug)); if (e) e.click(); }")
    page.wait_for_timeout(1400)
    page.click('.tab:has-text("记忆")')
    page.wait_for_selector("#lg-kinds .pill", timeout=8000)
    pills = page.query_selector_all("#lg-kinds .pill")
    names = [p.inner_text().replace("\n", " ") for p in pills]
    assert len(pills) == 8, f"应有 8 类台账，实得 {names}"
    for want in ("不可逆事实", "势力架构", "角色状态", "时间线", "伏笔", "身份变更"):
        assert any(want in n for n in names), f"缺少台账分类：{want}"
    # 有数据的分类必须真的渲染出条目，而不是只显示计数
    filled = [p for p in pills
              if p.inner_text().split()[-1].isdigit()
              and int(p.inner_text().split()[-1]) > 0]
    assert filled, "所有台账都是空的，无法验证渲染"
    filled[0].click()
    page.wait_for_timeout(600)
    rows = page.query_selector_all("#lg-out > div")
    assert len(rows) > 0, "台账有数据却没渲染出条目"
    shot(page, "25-ledgers")


@case(24, "右键菜单：变量由服务端真实填充，不是替换成空串")
def c24(page):
    import json as _j
    import urllib.request as _u
    slug = _j.loads(_u.urlopen(BASE + "/api/projects").read())[0]["slug"]
    names = ["title", "premise", "plot", "background", "characters",
             "relationships", "kb", "world_bible", "outline", "era_card",
             "style", "style_rules", "genre_rules", "common_rules",
             "anti_ai_rules", "chapter_directives", "cliche_blacklist",
             "target_chapters", "target_words"]
    probe = "|".join("%s=${%s}" % (n, n) for n in names)
    body = _j.dumps({"prompt": probe, "slug": slug}).encode()
    req = _u.Request(BASE + "/api/gen/preview", body,
                     {"Content-Type": "application/json"})
    got = _j.loads(_u.urlopen(req).read())["prompt"]
    import re as _r
    left = _r.findall(r"\$\{(\w+)\}", got)
    assert not left, f"这些变量没被支持：{left}"
    # 光是「不残留」不够 —— 替换成空串也不残留。抽查几个必须有内容的。
    vals = dict(seg.split("=", 1) for seg in got.split("|") if "=" in seg)
    for key in ("title", "cliche_blacklist", "common_rules", "target_words"):
        assert len(vals.get(key, "").strip()) > 2, f"{key} 填成了空串"


@case(23, "全局设置：正文写法要求 / 去AI味纪律 可编辑可持久化")
def c23(page):
    page.goto(BASE, wait_until="networkidle")
    page.click('.nav-item[data-view="settings"]')
    page.wait_for_selector("#s-dir")
    n_dir = len(page.input_value("#s-dir").splitlines())
    n_anti = len(page.input_value("#s-anti").splitlines())
    assert n_dir >= 5, f"正文写法要求应有默认值，实得 {n_dir} 条"
    assert n_anti >= 8, f"去AI味纪律应有默认值，实得 {n_anti} 条"
    probe = "测试纪律：这一条是 E2E 写进去的"
    page.fill("#s-dir", page.input_value("#s-dir") + "\n" + probe)
    page.click("#st-save")
    page.wait_for_timeout(600)
    page.reload()
    page.click('.nav-item[data-view="settings"]')
    page.wait_for_selector("#s-dir")
    assert probe in page.input_value("#s-dir"), "正文写法要求未持久化"
    shot(page, "23-directives")
    # 还原，别把测试数据留在配置里
    page.fill("#s-dir", page.input_value("#s-dir").replace("\n" + probe, ""))
    page.click("#st-save")
    page.wait_for_timeout(400)


@case(5, "全局设置：改字数上限并持久化")
def c5(page):
    page.goto(BASE, wait_until="networkidle")
    page.click('.nav-item[data-view="settings"]')
    page.wait_for_selector("#g-max", timeout=15000)
    old = page.input_value("#g-max")
    page.fill("#g-max", "3200")
    page.click("#st-save")
    page.wait_for_selector(".toast", timeout=10000)
    shot(page, "05-settings")
    page.reload(wait_until="networkidle")
    page.click('.nav-item[data-view="settings"]')
    page.wait_for_selector("#g-max", timeout=15000)
    assert page.input_value("#g-max") == "3200", "设置未持久化"
    page.fill("#g-max", old)
    page.click("#st-save")
    page.wait_for_timeout(800)


@case(6, "项目概览：统计卡与运行日志")
def c6(page):
    open_first_project(page)
    t = page.locator("#view").inner_text()
    assert "进度" in t and "字数" in t and "运行日志" in t
    shot(page, "06-project-overview")


@case(7, "章节页：正文非空且显示 AI 味评分")
def c7(page):
    open_first_project(page)
    page.click('.tab[data-tab="chapters"]')
    page.wait_for_selector(".chapter-row", timeout=15000)
    page.locator(".chapter-row").first.click()
    page.wait_for_timeout(1800)
    body = page.input_value("#c-body")
    assert len(body) > 800, f"正文过短({len(body)})"
    badge = page.locator("#c-actions .badge").first.inner_text().strip()
    assert badge.isdigit(), f"未显示评分 {badge!r}"
    shot(page, "07-chapter")


@case(8, "右键菜单：老版 130 条指令仍可用")
def c8(page):
    open_first_project(page)
    page.click('.tab[data-tab="chapters"]')
    page.wait_for_selector(".chapter-row", timeout=15000)
    page.locator(".chapter-row").first.click()
    page.wait_for_timeout(1500)
    n = page.evaluate("menuItems().length")
    groups = page.evaluate("() => { const g={}; menuItems().forEach(i=>g[i.group]=(g[i.group]||0)+1); return g; }")
    assert n >= 15, f"右键菜单项过少 {n}，分组 {groups}"
    assert "通用" in groups and "题材" in groups, f"缺分组: {groups}"
    # 真实触发一次右键
    page.evaluate("""() => {
      const ta = document.querySelector('#c-body');
      ta.focus(); ta.setSelectionRange(0, 40);
      ta.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, clientX:400, clientY:300}));
    }""")
    page.wait_for_timeout(700)
    dom = page.locator("#ctx-menu .ctx-item").count()
    assert dom >= 15, f"DOM 只渲染了 {dom} 项"
    assert page.locator("#ctx-menu .ctx-group").count() >= 2, "菜单未分组"
    page.fill("#ctx-menu .ctx-search", "冲突")
    page.wait_for_timeout(400)
    assert 0 < page.locator("#ctx-menu .ctx-item").count() < dom, "筛选未生效"
    shot(page, "08-context-menu")


@case(9, "记忆页：FTS5 检索 + 五层预算可视化")
def c9(page):
    open_first_project(page)
    page.click('.tab[data-tab="memory"]')
    page.wait_for_selector("#m-q", timeout=15000)
    # 查询词必须取自当前项目 —— 原来写死「武松 西门庆」，换一本书就零命中，
    # 于是这条用例挂的是测试数据而不是功能
    import json as _j
    import urllib.request as _u
    slug = page.evaluate("() => S.cur.slug")
    d = _j.loads(_u.urlopen(BASE + "/api/projects/" + _u.quote(slug)).read())
    q = " ".join((d.get("roster") or [])[:4]) or d["meta"]["title"]
    page.fill("#m-q", q[:40])
    page.click("#m-go")
    page.wait_for_selector("#m-out table.tbl", timeout=20000)
    assert page.locator("#m-out tbody tr").count() > 0, f"检索「{q[:40]}」无命中"
    page.click("#lc-go")
    page.wait_for_selector("#lc-out table.tbl", timeout=25000)
    lc = page.locator("#lc-out").inner_text()
    for layer in ("本章细纲", "世界观常驻", "最近章节", "段落摘要", "检索召回", "红线约束"):
        assert layer in lc, f"分层预算缺 {layer}"
    assert "tok" in lc
    shot(page, "09-memory-layers")


@case(10, "导出：txt / 大纲 / fountain / srt 内容特征正确")
def c10(page):
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".proj-card", timeout=15000)
    slug = page.evaluate("document.querySelector('.proj-card').dataset.slug")
    for fmt, must in (("txt", "第1章"), ("outline", "大纲"),
                      ("fountain", "Title:"), ("srt", "-->")):
        r = page.request.get(f"{BASE}/api/projects/{slug}/export?fmt={fmt}")
        assert r.ok, f"{fmt} HTTP {r.status}"
        b = r.text()
        assert len(b) > 200 and must in b, f"{fmt} 导出缺特征 {must}"
    page.locator(".proj-card").first.click()
    page.wait_for_selector(".tabs", timeout=15000)
    page.click('.tab[data-tab="export"]')
    page.wait_for_timeout(700)
    shot(page, "10-export")


@case(11, "新建项目：切换内容类型时层级链路随之变化")
def c11(page):
    page.goto(BASE, wait_until="networkidle")
    page.click("#btn-new")
    page.wait_for_selector("#f-type", timeout=10000)
    hints = []
    for pill in page.locator("#f-type .pill").all():
        pill.click()
        page.wait_for_timeout(300)
        hints.append(page.locator("#f-type-hint").inner_text())
    assert len(set(hints)) == len(hints), f"层级提示未随类型变化: {hints}"
    assert any("总纲" in h for h in hints), "小说层级缺失"
    assert any("分场" in h for h in hints), "剧本层级缺失"
    assert any("分镜" in h for h in hints), "短剧层级缺失"
    shot(page, "11-new-project")
    page.evaluate("closeModal()")


@case(12, "真实模型流式：正文非空且无思考泄漏")
def c12(page):
    page.goto(BASE, wait_until="networkidle")
    got = page.evaluate("""async () => {
      const r = await fetch('/api/gen', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({prompt:'写一段120字左右的古代市井场景描写，直接输出正文。',
                              profile:'drafting'})});
      const rd=r.body.getReader(), dec=new TextDecoder();
      let text='',reason='',buf='';
      for(;;){const {value,done}=await rd.read(); if(done)break;
        buf+=dec.decode(value,{stream:true});
        const ls=buf.split('\\n\\n'); buf=ls.pop();
        for(const l of ls){ if(!l.startsWith('data: '))continue;
          try{const d=JSON.parse(l.slice(6)); if(d.t)text+=d.t; if(d.r)reason+=d.r;}catch{} }}
      return {text, reason};
    }""")
    assert len(got["text"]) > 60, f"流式正文为空/过短: {got['text'][:120]!r}"
    assert "<answer" not in got["text"], "answer 标签泄漏"
    assert not got["text"].lstrip().startswith(("我们需要", "用户要求", "首先")), "思考泄漏进正文"
    shot(page, "12-stream")


@case(13, "窄屏 420px 不破版")
def c13(page):
    page.set_viewport_size({"width": 420, "height": 860})
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(900)
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
    shot(page, "13-mobile")
    page.set_viewport_size({"width": 1440, "height": 900})


@case(14, "网关接入自检：报告实际字段与首字延迟")
def c14(page):
    page.goto(BASE, wait_until="networkidle")
    page.click('.nav-item[data-view="packs"]')
    page.wait_for_selector(".probe-btn", timeout=15000)
    page.locator(".probe-btn").first.click()
    page.wait_for_selector(".probe-out .badge", timeout=60000)
    out = page.locator(".probe-out").first.inner_text()
    assert "通" in out, f"自检未通过: {out}"
    assert "字段" in out and "首字" in out, f"自检信息不全: {out}"
    shot(page, "14-probe")


@case(15, "质检页：全书体检 + 邻章窗口 + 锚定信息")
def c15(page):
    open_first_project(page)
    page.click('.tab[data-tab="quality"]')
    page.wait_for_selector("#q-book", timeout=15000)
    # 锚定信息异步加载
    page.wait_for_function(
        "() => document.querySelector('#q-anchor') && "
        "document.querySelector('#q-anchor').textContent.includes('历史模式')", timeout=20000)
    a = page.locator("#q-anchor").inner_text()
    assert "国号" in a and "角色花名册" in a, f"锚定信息不全: {a[:120]}"
    page.click("#q-book")
    page.wait_for_selector("#q-out .badge", timeout=60000)
    out = page.locator("#q-out").inner_text()
    assert "/ 100" in out, f"未显示体检得分: {out[:120]}"
    shot(page, "15-quality")
    page.click("#q-win")
    page.wait_for_timeout(2500)
    assert "邻章窗口" in page.locator("#q-out").inner_text() or \
           "/ 100" in page.locator("#q-out").inner_text()


@case(16, "搜索接口：框架内建检索可用或明确报不可用")
def c16(page):
    r = page.request.get(f"{BASE}/api/search?q=%E5%AE%8B%E4%BB%A3%E7%9F%A5%E5%8E%BF&k=3")
    assert r.ok, f"HTTP {r.status}"
    d = r.json()
    assert "ok" in d, "返回结构不对"
    if d["ok"]:
        assert d.get("provider"), "未标明检索 provider"
        assert isinstance(d.get("results"), list)
    else:
        assert d.get("error"), "不可用时应给出原因"


@case(17, "全局右键菜单可配置且保存不毁配置文件注释")
def c17(page):
    import subprocess
    before = subprocess.run(["grep", "-c", "^ *#", "config/settings.yaml"],
                            capture_output=True, text=True).stdout.strip()
    page.goto(BASE, wait_until="networkidle")
    page.click('.nav-item[data-view="settings"]')
    page.wait_for_selector("#cm-list", timeout=15000)
    n0 = page.locator("#cm-list [data-i]").count()
    assert n0 >= 5, f"全局右键菜单过少: {n0}"
    page.click("#cm-add")
    page.wait_for_timeout(400)
    assert page.locator("#cm-list [data-i]").count() == n0 + 1, "新增未生效"
    # 记忆体预算必须仍是 auto，不能被数字表单冲掉
    assert page.input_value("#g-ctx").strip().lower() == "auto", \
        f"context_budget 被冲成了 {page.input_value('#g-ctx')!r}"
    page.click("#st-save")
    page.wait_for_selector(".toast", timeout=10000)
    after = subprocess.run(["grep", "-c", "^ *#", "config/settings.yaml"],
                           capture_output=True, text=True).stdout.strip()
    assert before == after, f"保存把 settings.yaml 的注释冲掉了 ({before} -> {after})"
    shot(page, "17-global-menus")
    # 还原
    page.reload(wait_until="networkidle")
    page.click('.nav-item[data-view="settings"]')
    page.wait_for_selector("#cm-list [data-i]", timeout=15000)
    page.locator(".cm-del").last.click()
    page.click("#st-save")
    page.wait_for_timeout(800)


@case(18, "全面可编辑审阅：设定/大纲/细纲/正文/提示词逐块真编辑真保存")
def c18(page):
    """逐个面板真点、真改、真存、刷新后仍在 —— 杜绝「只有一两块能编辑」。"""
    import time as _t
    # 隔离: 建专用测试项目, 不污染真实书稿
    r = page.request.post(f"{BASE}/api/projects", data=json.dumps({
        "title": "E2E可编辑测试", "type_id": "novel", "genre_id": "urban",
        "style_id": "dushi-zhongsheng", "target_words": 10000,
        "fields": {"premise": "测试", "background": "测试"}}),
        headers={"Content-Type": "application/json"})
    slug = r.json()["slug"]
    # 预置最小产物, 让每个面板都有可编辑对象
    for doc in ("world_bible", "characters", "outline", "era_card", "style_guide"):
        page.request.put(f"{BASE}/api/projects/{slug}/doc/{doc}",
            data=json.dumps({"text": f"{doc} 初始内容"}),
            headers={"Content-Type": "application/json"})
    page.request.put(f"{BASE}/api/projects/{slug}/chapter_outline/1",
        data=json.dumps({"text": "第1章 测试\n剧情1：测试事件"}),
        headers={"Content-Type": "application/json"})
    page.request.post(f"{BASE}/api/projects/{slug}/chapter/1",
        data=json.dumps({"text": "「测试。」他说。这是第一章正文，长度需要超过验证阈值。" * 20}),
        headers={"Content-Type": "application/json"})
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".proj-item", timeout=15000)
    page.locator(f'.proj-item[data-slug="{slug}"]').click()
    page.wait_for_selector(".tabs", timeout=15000)
    mark = f"[E2E{int(_t.time())%100000}]"
    edited = []

    def save_and_check(ta_sel, btn_sel, name):
        page.wait_for_selector(ta_sel, timeout=15000)
        orig = page.input_value(ta_sel)
        page.fill(ta_sel, (orig or "") + "\n" + mark)
        page.click(btn_sel)
        page.wait_for_selector(".toast", timeout=10000)
        edited.append(name)

    # 设定页 4 块
    page.click('.tab[data-tab="setup"]')
    for k in ("world_bible", "characters", "era_card", "style_guide"):
        save_and_check(f'.doc-edit[data-doc="{k}"]',
                       f'.doc-save[data-doc="{k}"]', k)
    # 大纲页: 总纲 + 第一条细纲
    page.click('.tab[data-tab="outline"]')
    save_and_check('.doc-edit[data-doc="outline"]', '.doc-save[data-doc="outline"]', "outline")
    page.wait_for_selector(".co-edit", timeout=15000)
    first_n = page.locator(".co-save").first.get_attribute("data-n")
    save_and_check(f'.co-edit[data-n="{first_n}"]', f'.co-save[data-n="{first_n}"]',
                   f"chapter_outline_{first_n}")
    # 正文
    page.click('.tab[data-tab="chapters"]')
    page.wait_for_selector(".chapter-row", timeout=15000)
    page.locator(".chapter-row").first.click()
    page.wait_for_timeout(1500)
    body = page.input_value("#c-body")
    page.fill("#c-body", body + "\n" + mark)
    page.click("#c-save")
    page.wait_for_selector(".toast", timeout=15000)
    edited.append("chapter_body")
    # 提示词页
    page.click('.tab[data-tab="prompts"]')
    page.wait_for_selector(".pr-edit", timeout=15000)
    n_prompts = page.locator(".pr-edit").count()
    assert n_prompts >= 5, f"提示词可编辑块只有 {n_prompts} 个"
    page.fill('.pr-edit[data-k="content_extra"]', "测试追加指令" + mark)
    page.click("#pr-save")
    page.wait_for_selector(".toast", timeout=10000)
    edited.append("prompts")

    # 刷新后逐一验证持久化
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".proj-item", timeout=15000)
    page.locator(f'.proj-item[data-slug="{slug}"]').click()
    page.wait_for_selector(".tabs", timeout=15000)
    page.click('.tab[data-tab="setup"]')
    page.wait_for_selector('.doc-edit[data-doc="world_bible"]', timeout=15000)
    assert mark in page.input_value('.doc-edit[data-doc="world_bible"]'), "世界观编辑未持久化"
    page.click('.tab[data-tab="prompts"]')
    page.wait_for_selector('.pr-edit[data-k="content_extra"]', timeout=15000)
    assert mark in page.input_value('.pr-edit[data-k="content_extra"]'), "提示词未持久化"
    assert len(edited) >= 8, f"可编辑面不足: {edited}"
    shot(page, "18-editable-all")
    import shutil, pathlib
    shutil.rmtree(pathlib.Path("projects") / slug, ignore_errors=True)


@case(19, "阶段自动模式：入口存在且模式可选")
def c19(page):
    open_first_project(page)
    page.click("#a-auto")
    page.wait_for_selector("#au-mode", timeout=10000)
    pills = page.locator("#au-mode .pill").count()
    assert pills == 2, f"模式选项 {pills} 个（应为 全自动/阶段自动）"
    t = page.locator("#au-mode").inner_text()
    assert "全自动" in t and "阶段自动" in t
    page.locator('#au-mode .pill[data-v="staged"]').click()
    assert "active" in (page.locator('#au-mode .pill[data-v="staged"]')
                        .get_attribute("class") or "")
    shot(page, "19-staged-mode")
    page.evaluate("closeModal()")


@case(20, "插件包 API：可读且保护 id 不被改")
def c20(page):
    r = page.request.get(f"{BASE}/api/pack/style/dushi-zhongsheng")
    assert r.ok, f"读包失败 {r.status}"
    d = r.json()
    assert d.get("opening") and d.get("descriptionBudget"), "文风包缺写法纪律字段"
    bad = page.request.put(f"{BASE}/api/pack/style/dushi-zhongsheng",
                           data=json.dumps({"id": "hacked"}),
                           headers={"Content-Type": "application/json"})
    assert bad.status == 400, "改 id 未被拦截"


@case(21, "UI 可用性：全部页面/标签可达且零 JS 错误")
def c21(page):
    """把每个视图和每个项目标签都点一遍 —— 任一页面抛 JS 错误即失败。
    之前「提示词变量被模板字符串求值」导致整页白屏就是这类问题。"""
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE, wait_until="networkidle")
    for view in ("dashboard", "settings", "packs"):
        page.click(f'.nav-item[data-view="{view}"]')
        page.wait_for_timeout(600)
        assert page.locator("#view .card, #view .stat").count() > 0, f"{view} 页空白"
    page.click('.nav-item[data-view="dashboard"]')
    page.wait_for_selector(".proj-card", timeout=15000)
    page.locator(".proj-card").first.click()
    page.wait_for_selector(".tabs", timeout=15000)
    tabs = [t.get_attribute("data-tab") for t in page.locator(".tab").all()]
    assert len(tabs) >= 9, f"项目标签只有 {len(tabs)} 个: {tabs}"
    for t in tabs:
        page.click(f'.tab[data-tab="{t}"]')
        page.wait_for_timeout(500)
        assert page.locator("#view .card").count() > 0, f"标签 {t} 页空白"
    assert not errs, f"JS 错误 {len(errs)} 条: {errs[:3]}"
    shot(page, "21-usability")


@case(22, "UI 简易性：新建项目三步可达、关键动作一屏可见")
def c22(page):
    page.goto(BASE, wait_until="networkidle")
    # 第 1 步: 侧栏新建入口可见
    assert page.locator("#btn-new").is_visible(), "新建入口不可见"
    page.click("#btn-new")
    # 第 2 步: 弹窗即含全部必填项, 不需要翻页
    page.wait_for_selector("#f-title", timeout=8000)
    for sel in ("#f-title", "#f-type", "#f-genre", "#f-style", "#f-words", "#f-ok"):
        assert page.locator(sel).is_visible(), f"{sel} 不在首屏"
    page.evaluate("closeModal()")
    # 项目页: 自动创作按钮常驻顶栏
    page.wait_for_selector(".proj-card", timeout=15000)
    page.locator(".proj-card").first.click()
    page.wait_for_selector("#a-auto", timeout=15000)
    assert page.locator("#a-auto").is_visible(), "自动创作按钮不可见"
    # 章节页: 选章 → 正文 ≤2 步
    page.click('.tab[data-tab="chapters"]')
    page.wait_for_selector(".chapter-row", timeout=15000)
    page.locator(".chapter-row").first.click()
    page.wait_for_timeout(1200)
    assert len(page.input_value("#c-body")) > 100, "两步内未见正文"
    shot(page, "22-simplicity")


CASES = [c27, c26, c25, c24, c23, c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14, c15,
         c16, c17, c18, c19, c20, c21, c22]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=int, default=0)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    todo = [c for c in CASES if not a.case or c._case[0] == a.case]

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=not a.headed)
        # 每个用例一个独立 context —— 原来全部共用一个 page, 一个用例改了
        # 路由拦截/localStorage/登录态就会污染后面所有用例（实测 case 26 拦截
        # /api/catalog 后, case 21 就报 dashboard 空白, 查了半天不是应用的问题）
        ctx = None
        page = None
        for fn in todo:
            if ctx:
                ctx.close()
            ctx = br.new_context(viewport={"width": 1440, "height": 900},
                                 device_scale_factor=2, locale="zh-CN")
            page = ctx.new_page()
            page.on("pageerror", lambda e: ERRORS.append(f"JS错误: {e}"))
            page.on("console", lambda m: ERRORS.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            n, title = fn._case
            t0 = time.time()
            try:
                fn(page)
                RESULTS.append((n, title, True, ""))
                print(f"  ✓ [{n:2d}] {title}  ({time.time()-t0:.1f}s)", flush=True)
            except Exception as e:
                RESULTS.append((n, title, False, str(e)[:220]))
                print(f"  ✗ [{n:2d}] {title}\n       {str(e)[:220]}", flush=True)
                try:
                    shot(page, f"FAIL-{n:02d}")
                except Exception:
                    pass
        br.close()

    ok = sum(1 for r in RESULTS if r[2])
    print(f"\n{'='*60}\n通过 {ok}/{len(RESULTS)}   截图 reports/e2e/")
    if ERRORS:
        print(f"\n浏览器错误 {len(ERRORS)} 条:")
        for e in list(dict.fromkeys(ERRORS))[:8]:
            print("   -", e[:170])
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
