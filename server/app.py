"""AI 叙事内容生产线 —— Flask 入口.

只有一个页面路由 `/`。老版的 /legacy 已废弃。
所有模型地址与密钥来自 .env，代码与配置里不含任何真实凭据。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from urllib.parse import quote
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from flask import Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.registry import registry, ROOT                       # noqa: E402
from server.settings import load as load_settings, save as save_settings  # noqa: E402
from server import dials as dl
from server import stagecraft as sc                                # noqa: E402
from server.orchestrator import (Project, Novelist, create_project,        # noqa: E402
                                 slugify, call, clean, PROJECTS)
from server.exporters import EXPORTERS, BINARY_EXPORTERS, MIME, EXT                 # noqa: E402
from server.evaluator import audit, book_audit, window_audit
from server.splitter import split_chapters, analyze, apply_to_project
                                # noqa: E402

WEB = ROOT / "web"
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("NOVEL_SECRET") or secrets.token_hex(16)

# 访问密码。留空 = 不启用鉴权（本机自用）。生产务必在 .env 里设置。
AUTH_PASSWORD = (os.environ.get("NOVEL_PASSWORD") or "").strip()
_TOKEN = hashlib.sha256(
    (AUTH_PASSWORD + app.secret_key).encode()).hexdigest()[:32] if AUTH_PASSWORD else ""
OPEN_PATHS = {"/api/health", "/login", "/api/login"}


@app.before_request
def _guard():
    if not AUTH_PASSWORD:
        return None
    if request.path in OPEN_PATHS or request.path.startswith("/css/"):
        return None
    if request.cookies.get("novel_auth") == _TOKEN:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "未登录", "login_required": True}), 401
    return send_from_directory(WEB, "login.html")


@app.post("/api/login")
def login():
    if not AUTH_PASSWORD:
        return jsonify({"ok": True, "auth": False})
    if (request.json or {}).get("password") == AUTH_PASSWORD:
        r = jsonify({"ok": True})
        r.set_cookie("novel_auth", _TOKEN, httponly=True, samesite="Lax",
                     max_age=30 * 86400)
        return r
    return jsonify({"ok": False, "error": "密码错误"}), 401


@app.post("/api/logout")
def logout():
    r = jsonify({"ok": True})
    r.delete_cookie("novel_auth")
    return r

# 后台自动写作任务: slug -> 状态
JOBS: Dict[str, Dict[str, Any]] = {}


def sse(obj: Dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ----------------------------------------------------------------- 页面
@app.route("/")
def index():
    """首页要带上静态资源指纹。

    改了 JS 之后用户看到的还是旧界面(新加的知识库栏死活不显示), 因为浏览器
    缓存住了 /js/views.js。no-cache 头只保证「重新校验」, 遇到激进缓存或
    Service Worker 就不灵。把文件 mtime 拼进 URL, 内容一变 URL 就变,
    浏览器没法拿旧的顶替。
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")

    def stamp(m):
        path = m.group(2)
        f = WEB / path.lstrip("/")
        v = int(f.stat().st_mtime) if f.exists() else 0
        return f'{m.group(1)}="{path}?v={v}"'

    html = re.sub(r'(href|src)="(/(?:css|js)/[^"?]+)"', stamp, html)
    return Response(html, mimetype="text/html",
                    headers={"Cache-Control": "no-store"})


@app.route("/<path:f>")
def assets(f: str):
    p = WEB / f
    if p.is_file():
        return send_from_directory(WEB, f)
    return send_from_directory(WEB, "index.html")   # SPA 回退


# ----------------------------------------------------------------- 元信息
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "gateways": len(registry.gateways),
                    "types": len(registry.types), "genres": len(registry.genres)})


@app.post("/api/probe")
def probe():
    """接入自检: 打一发真实请求, 报告该网关实际用的字段名与耗时.

    接新模型时最常见的坑是"OpenAI 兼容"但字段名不一样, 表现为前端一片空白。
    先跑这个, 一眼看出它把内容放在 content 还是 reasoning / result / parts。
    """
    b = request.json or {}
    gw = b.get("gateway") or next(iter(registry.gateways), None)
    if gw not in registry.gateways:
        return jsonify({"ok": False, "error": f"未知网关 {gw}"}), 400
    provider = registry.provider(gw)
    provider.seen_fields = set()
    model = b.get("model") or registry.gateways[gw].get("default_model")
    t0 = time.time()
    text, reason, chunks, first = [], [], 0, None
    try:
        for d in provider.stream(
                [{"role": "user", "content": b.get("prompt") or "只回复两个字：收到"}],
                model=model, thinking=bool(b.get("thinking")), max_tokens=64):
            chunks += 1
            if d.text:
                if first is None:
                    first = round(time.time() - t0, 2)
                text.append(d.text)
            if d.reasoning:
                reason.append(d.reasoning)
    except Exception as e:
        return jsonify({"ok": False, "gateway": gw, "model": model,
                        "error": str(e)[:400]}), 200

    body = "".join(text)
    rsn = "".join(reason)
    salvaged = ""
    if not body and rsn:
        salvaged = provider.salvage(rsn)
    return jsonify({
        "ok": bool(body or salvaged),
        "gateway": gw, "model": model,
        "fields_seen": sorted(provider.seen_fields) or ["(未命中任何已知字段)"],
        "declared_reasoning_field": provider.reasoning_field,
        "chunks": chunks,
        "first_token_s": first,
        "elapsed_s": round(time.time() - t0, 2),
        "content_chars": len(body),
        "reasoning_chars": len(rsn),
        "salvaged_from_answer_tag": bool(salvaged),
        "sample": (body or salvaged)[:120],
        "diagnosis": (
            "正常：内容走 content" if body and not rsn else
            "正常：内容走 content，另有思考流（创作类建议关思考）" if body and rsn else
            "内容被包在 <answer> 里，已抢救" if salvaged else
            "该网关未返回可用正文 —— 检查 reasoning_field 声明或换模型"),
    })


@app.get("/api/catalog")
def catalog():
    c = registry.catalog()
    c["typeDetail"] = registry.types
    c["shortcuts"] = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in (ROOT / "packs" / "shortcuts").glob("*.json")
    } if (ROOT / "packs" / "shortcuts").exists() else {}
    c["context_menus"] = load_settings().get("context_menus") or []
    return jsonify(c)


@app.get("/api/genre/<gid>")
def genre_detail(gid: str):
    g = registry.genres.get(gid)
    return (jsonify(g), 200) if g else (jsonify({"error": "no such genre"}), 404)


@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    if request.method == "GET":
        return jsonify(load_settings())
    save_settings(request.json or {})
    return jsonify({"ok": True, "settings": load_settings()})


# ----------------------------------------------------------------- 项目
@app.get("/api/projects")
def list_projects():
    out = []
    show_archived = request.args.get("archived") == "1"
    for d in sorted(PROJECTS.iterdir()) if PROJECTS.exists() else []:
        if not (d / "project.json").exists():
            continue
        # 归档的默认不列。归档是「从界面上收起来」，可它们照样有 project.json，
        # 于是照样出现在列表里 —— 实测点「大宋奸商西门庆」点进的是同名的归档本，
        # 追踪页一直显示 0 条，查了半天才发现点错了书。
        if d.name.startswith("_archive_") and not show_archived:
            continue
        p = Project(d.name)
        out.append({"slug": d.name, **p.meta,
                    "done": len(p.state.get("done", [])),
                    "words": p.total_words})
    return jsonify(out)


@app.post("/api/projects")
def new_project():
    b = request.json or {}
    cfg = load_settings()
    avg = (cfg["generation"]["chapter_words_min"] + cfg["generation"]["chapter_words_max"]) // 2
    words = int(b.get("target_words") or 100000)
    chapters = int(b.get("target_chapters") or max(1, round(words / avg)))
    chapters = min(chapters, cfg["limits"]["max_chapters"])
    p = create_project(title=b.get("title", "未命名"), type_id=b.get("type_id", "novel"),
                       genre_id=b.get("genre_id", ""), style_id=b.get("style_id", ""),
                       target_chapters=chapters, target_words=words,
                       fields=b.get("fields") or {},
                       history_mode=b.get("history_mode", "auto"))
    return jsonify({"slug": p.slug, **p.meta})


@app.get("/api/projects/<slug>")
def project_detail(slug: str):
    p = Project(slug)
    if not p.meta:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "slug": slug, "meta": p.meta, "state": p.state, "board": p.board(),
        "words": p.total_words,
        # 检索攒下的事实卡张数 —— 设定页要告诉用户知识库里已经有多少料
        "kb_facts": sum(1 for v in (p._load("facts.json", {}) or {}).values()
                        if isinstance(v, dict) and v.get("card")),
        # 解析好的花名册。characters.md 有两种标题格式, 谁需要角色名谁再解析一遍
        # 就会各踩各的坑（E2E 就在这上面栽过）—— 解析只在引擎里做一次。
        "roster": [c["name"] for c in Novelist(p).roster()],
        "basis": p.read("basis.md"),
        "naming": p.read("naming.md"),
        "basis": p.read("basis.md"),
        "naming": p.read("naming.md"),
        "world_bible": p.read("world_bible.md"),
        "characters": p.read("characters.md"),
        "outline": p.read("outline.md"),
        "style_guide": p.read("style_guide.md"),
        "era_card": p.read("era_card.md"),
        "rules": p._load("rules.json", {}),
        "system_issues": p.read("SYSTEM_ISSUES.md"),
        "volumes": p._load("volumes.json", []),
        "chapter_outlines": p._load("chapter_outlines.json", {}),
        "memory": p.mem.stats(),
        "job": JOBS.get(slug, {}),
    })


@app.get("/api/projects/<slug>/chapter/<int:n>")
def get_chapter(slug: str, n: int):
    p = Project(slug)
    return jsonify({"n": n, "text": p.chapter(n),
                    "audit": p._load(f"audit/{n:03d}.json", {})})


@app.post("/api/projects/<slug>/chapter/<int:n>")
def save_chapter(slug: str, n: int):
    """保存人工/右键改写后的正文, 并重新评分与重建该章记忆索引."""
    p = Project(slug)
    text = (request.json or {}).get("text", "")
    if not text.strip():
        return jsonify({"error": "内容为空"}), 400
    p.write(p.chapter_path(n), text)
    nv = Novelist(p)
    a = audit(text, nv.blacklist(),
              (p.cfg["generation"]["chapter_words_min"] +
               p.cfg["generation"]["chapter_words_max"]) // 2)
    p.write(f"audit/{n:03d}.json", json.dumps(a, ensure_ascii=False, indent=2))
    if p.cfg["memory"].get("index_chapters", True):
        p.mem.add("plot", f"ch{n}", f"第{n}章",
                  p.state.get("summaries", {}).get(str(n), "") + "\n" + text[:1500])
    # 手动保存/导入的章节也要登记, 否则章节列表不显示（实测坑）
    if n not in p.state.get("done", []):
        p.state.setdefault("done", []).append(n)
        p.state["current"] = max(p.state.get("current") or 0, n)
        p.save()
    return jsonify({"ok": True, "audit": a})


@app.post("/api/projects/<slug>/repair")
def repair_api(slug: str):
    """按当前规则批量返修旧章。dry=1 只看候选不动手。"""
    b = request.json or {}
    nv = Novelist(Project(slug))
    return jsonify(nv.repair_violations(limit=int(b.get("limit") or 10),
                                        dry=bool(b.get("dry"))))


@app.post("/api/projects/<slug>/rewrite/<int:n>")
def rewrite_api(slug: str, n: int):
    """章节重写: polish(只改语言) / replace(整章重写) / fork(分叉)。旧稿自动备份。"""
    p = Project(slug)
    b = request.json or {}
    try:
        return jsonify(Novelist(p).rewrite_chapter(
            n, mode=b.get("mode", "polish"), note=b.get("note", "")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.put("/api/projects/<slug>/fields")
def save_fields(slug: str):
    p = Project(slug)
    p.meta["fields"] = {**(p.meta.get("fields") or {}), **(request.json or {})}
    p.save()
    return jsonify({"ok": True})


@app.put("/api/projects/<slug>/doc/<name>")
def save_doc(slug: str, name: str):
    """保存前端编辑的 世界观/角色/总纲/守则/时代卡。"""
    try:
        return jsonify(Novelist(Project(slug)).save_doc(
            name, (request.json or {}).get("text", "")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.put("/api/projects/<slug>/chapter_outline/<int:n>")
def save_chapter_outline(slug: str, n: int):
    """保存某章细纲。"""
    p = Project(slug)
    ol = p._load("chapter_outlines.json", {})
    ol[str(n)] = (request.json or {}).get("text", "")
    p.write("chapter_outlines.json", json.dumps(ol, ensure_ascii=False, indent=2))
    return jsonify({"ok": True, "n": n})


@app.route("/api/projects/<slug>", methods=["DELETE"])
def project_delete(slug: str):
    """删除或归档一个项目。

    默认**归档**（改名加 _archive_ 前缀），不是真删 —— 几十万字的稿子误删
    没有后悔药，而且长跑可能正在写它。真删要显式传 hard=true，且会先确认
    没有进程在写（chapters 目录 15 分钟内动过就拒绝）。
    """
    p = Project(slug)
    if not p.dir.exists():
        return jsonify({"error": "not found"}), 404
    b = request.json or {}
    cdir = p.dir / "chapters"
    if cdir.exists() and (time.time() - cdir.stat().st_mtime) < 900:
        return jsonify({"error": "这本书 15 分钟内还在写，先停掉长跑再删",
                        "hint": "bash scripts/stop_run.sh"}), 409
    if b.get("hard"):
        shutil.rmtree(p.dir)
        return jsonify({"ok": True, "action": "deleted", "slug": slug})
    dst = p.dir.parent / f"_archive_{slug}"
    i = 2
    while dst.exists():
        dst = p.dir.parent / f"_archive_{slug}_{i}"
        i += 1
    p.dir.rename(dst)
    return jsonify({"ok": True, "action": "archived", "slug": dst.name})


@app.route("/api/projects/<slug>/prompts", methods=["GET", "PUT"])
def project_prompts(slug: str):
    """项目级提示词：查看当前生效模板 / 保存覆盖。"""
    p = Project(slug)
    nv = Novelist(p)
    if request.method == "PUT":
        ov = {k: v for k, v in (request.json or {}).items()
              if k in Novelist.PROMPT_KEYS and isinstance(v, str)}
        p.meta["prompt_overrides"] = {k: v for k, v in ov.items() if v.strip()}
        p.save()
        return jsonify({"ok": True, "overrides": list(p.meta["prompt_overrides"])})
    # GET: 每个键给 当前覆盖 + 内置默认说明 + 可用变量
    lvl0 = (nv.type.get("levels") or [{}])[0]
    # 返回引擎真正用的模板全文, 不是「内置：世界观圣经模板」这样的一句话说明 ——
    # 用户要能看见才改得动。
    defaults = {
        "world_bible": nv.builtin_prompt("world_bible"),
        "characters": nv.builtin_prompt("characters"),
        "outline": lvl0.get("prompt", ""),
        "chapter_outline_extra": "（本键是追加指令：内容会拼到细纲生成的约束清单末尾，"
                                 "不替换内置模板）",
        "content_extra": "（本键是追加指令：内容会拼到正文提示词末尾，优先级最高，"
                         "不替换内置模板。正文提示词由编译器按记忆/角色卡/文风包动态"
                         "拼装，无法整体替换）",
    }
    return jsonify({
        "keys": Novelist.PROMPT_KEYS,
        "overrides": p.meta.get("prompt_overrides") or {},
        "defaults": defaults,
        # 与 fill_vars 支持的集合保持一致, 别两处各说各话
        "variables": ["${%s}" % k for k in sorted(VAR_NAMES)],
    })


# 台账槽位是通用的; ledger/power 两本的**叫法**由题材包定
# （都市=资金台账/身份与资源，修仙=灵石与资源/修为境界，历史=钱粮与军资/官职与兵权）
LEDGER_KINDS = {
    "canon":    "不可逆事实",
    "identity": "身份变更",
    "orgs":     "势力架构",
    "terms":    "数字条款",
    "roles":    "角色状态",
    "power":    "实力与地位",
    "ledger":   "资源账",
    "timeline": "时间线",
    "foreshadow": "伏笔",
    # 误读是这套引擎的心脏(一个动作 × N 个误读者 = N 条新支线), 却是唯一
    # 一本前端看不见的台账 —— 抽错一条会一路错到底, 而且没人发现得了。
    "misread":  "活跃误会",
    "methods":  "解法与作废",
}


@app.get("/api/projects/<slug>/pulse")
def project_pulse(slug: str):
    """轻量进度心跳 —— 界面靠它感知「有人在写」。

    原来只有从界面点「自动创作」才会轮询, 而长跑是命令行起的外部进程,
    界面完全不知道, 于是页面一直停在打开时的快照, 得手动刷新。
    进度是磁盘上的事实, 谁写的不重要。
    """
    p = Project(slug)
    st = p.state or {}
    done = st.get("done") or []
    log = st.get("log") or []
    # 判断「还在写」要看章节目录的 mtime, 不能看 state.json ——
    # 字数缓存会写 state.json, 只是打开一下工作台就把完本旧书刷成「写作中」。
    ts = 0.0
    cdir = p.dir / "chapters"
    if cdir.exists():
        ts = cdir.stat().st_mtime
    return jsonify({
        "done": len(done),
        "current": st.get("current", 0),
        # 书名也要回传: 改了书名后已打开的页面标题还是旧的, 心跳只比章数比不出来
        "title": p.meta.get("title", ""),
        "words": p.total_words,
        "updated_at": ts,
        # 窗口要比「写一章的耗时」宽裕: 一章含评审/自愈要 5-8 分钟, 300 秒会在
        # 每章之间反复闪成「已停止」。取 15 分钟。
        "writing": (time.time() - ts) < 900 if ts else False,
        "tail": log[-6:],
    })


@app.route("/api/projects/<slug>/ledgers", methods=["GET", "DELETE"])
def project_ledgers(slug: str):
    """全部动态台账：可看、可删。

    这些台账每章都在变，而且是模型抽出来的 —— 抽错一条（把临时处境记成不可逆
    事实、把配角名字记岔）就会一路错到底，因为它们都进 L5 红线约束层。
    前端原来只显示聚合数字，用户看不见具体条目，也就无从纠正。
    """
    p = Project(slug)
    if request.method == "DELETE":
        b = request.json or {}
        kind, key = b.get("kind"), str(b.get("key", ""))
        if kind == "canon":
            cn = p._load("canon.json", []) or []
            try:
                idx = int(key)
            except ValueError:
                return jsonify({"error": "bad key"}), 400
            if 0 <= idx < len(cn):
                cn.pop(idx)
                p.write("canon.json", json.dumps(cn, ensure_ascii=False, indent=2))
                return jsonify({"ok": True, "left": len(cn)})
            return jsonify({"error": "out of range"}), 404
        if kind in ("misread", "methods"):
            # 列表型台账按下标删。误会记岔了必须能就地拨正, 否则它会一直
            # 当燃料喂进排纲, 越滚越歪。
            lst = p.state.get({"misread": "misreads",
                               "methods": "methods"}[kind]) or []
            try:
                idx = int(key)
            except ValueError:
                return jsonify({"error": "bad key"}), 400
            if 0 <= idx < len(lst):
                lst.pop(idx)
                p.save()
                return jsonify({"ok": True, "left": len(lst)})
            return jsonify({"error": "out of range"}), 404
        if kind in ("identity", "orgs", "roles", "power", "ledger",
                    "terms", "timeline"):
            d = p.state.get(kind) or {}
            if key in d:
                d.pop(key)
                p.save()
                return jsonify({"ok": True, "left": len(d)})
            return jsonify({"error": "no such key"}), 404
        return jsonify({"error": "不支持删除该台账"}), 400

    cn = p._load("canon.json", []) or []
    st = p.state
    kinds = dict(LEDGER_KINDS)
    try:
        spec = Novelist(p).ledger_spec()
        kinds["ledger"] = spec["resource"]["label"]
        kinds["power"] = spec["power"]["label"]
    except Exception:
        pass
    fs = []
    try:
        fs = p.mem.all_foreshadow()
    except Exception:
        pass
    return jsonify({
        "kinds": kinds,
        "canon": [{"key": str(i), "chapter": c.get("chapter"), "kind": c.get("kind"),
                   "subject": c.get("subject"), "fact": c.get("fact")}
                  for i, c in enumerate(cn)][-300:],
        "identity": [{"key": k, "chapter": v.get("at"), "text": v.get("now")}
                     for k, v in (st.get("identity") or {}).items()],
        "orgs": [{"key": k, "chapter": v.get("at"), "text": v.get("state")}
                 for k, v in (st.get("orgs") or {}).items()],
        "terms": [{"key": k, "chapter": v.get("at"),
                   "text": v.get("value") + (f"　⚠ 曾={v['was']}" if v.get("was") else "")}
                  for k, v in (st.get("terms") or {}).items()],
        "roles": [{"key": k, "chapter": v.get("at"), "text": v.get("state")}
                  for k, v in (st.get("roles") or {}).items()],
        "power": [{"key": k, "chapter": v.get("at"), "text": v.get("state")}
                  for k, v in (st.get("power") or {}).items()],
        "ledger": [{"key": k, "chapter": int(k) if k.isdigit() else 0, "text": v}
                   for k, v in (st.get("ledger") or {}).items()],
        "timeline": [{"key": k, "chapter": int(k) if k.isdigit() else 0, "text": v}
                     for k, v in (st.get("timeline") or {}).items()],
        "foreshadow": [{"key": str(f.get("id", i)), "chapter": f.get("chapter"),
                        "text": f.get("text"), "done": bool(f.get("resolved_at"))}
                       for i, f in enumerate(fs)],
        # 误会: 已戳破的标 done, 未戳破的才是活跃燃料
        "misread": [{"key": str(i), "chapter": m.get("at"),
                     "subject": m.get("who"),
                     "text": (f"凭「{m.get('because','')}」→ 认定「{m.get('concludes','')}」"
                              + (f" → 于是「{m.get('acts')}」" if m.get("acts") else "")),
                     "done": bool(m.get("closed_at"))}
                    for i, m in enumerate(st.get("misreads") or [])
                    if isinstance(m, dict)],
        # 解法: 同一路数用满次数就会被下作废令, expired 里的标 done
        "methods": [{"key": str(i), "chapter": m.get("at"),
                     "subject": m.get("method"),
                     "text": m.get("solved") or "",
                     "done": any(Novelist._same_move(str(m.get("method") or ""),
                                                     str(e.get("method") or ""))
                                 for e in (st.get("expired_methods") or []))}
                    for i, m in enumerate(st.get("methods") or [])
                    if isinstance(m, dict)],
    })


@app.get("/api/projects/<slug>/tree")
def project_tree(slug: str):
    """节点树 + 逐条违约。

    树的全部价值在于「程序能查」—— 查出来的东西必须看得见, 否则和以前那套
    「只写日志给人看, 不阻断生成」的告警一样, 没人看就等于没有。
    """
    from server import tree as tr
    p = Project(slug)
    raw = p._load("tree.json", {}) or {}
    nodes = {k: tr.Node.from_dict(v) for k, v in raw.items()
             if isinstance(v, dict) and v.get("id")}
    errs = tr.audit_tree(nodes) if nodes else []
    # 违约按节点归堆, 让前端能把红点打在具体那一块上
    by_node: Dict[str, List[str]] = {}
    for e in errs:
        m = re.match(r"\[([^\]]+)\]\s*(.*)", e)
        by_node.setdefault(m.group(1) if m else "_", []).append(
            m.group(2) if m else e)
    out = []
    for nid in sorted(nodes, key=lambda x: (len(x.split(".")), x)):
        nd = nodes[nid]
        d = nd.to_dict()
        d["depth"] = len(nid.split(".")) - 1
        d["errors"] = by_node.get(nid, [])
        d["brief_entry"] = nd.entry.brief(400)
        d["brief_exit"] = nd.exit.brief(400)
        d["changed"] = sorted(tr.changed_fields(nd))
        out.append(d)
    return jsonify({"nodes": out, "errors": errs, "ok": not errs,
                    "count": len(nodes)})


@app.route("/api/projects/<slug>/dials", methods=["GET", "PUT"])
def project_dials(slug: str):
    """本书的两个旋钮。留空则用全局默认。"""
    p = Project(slug)
    if request.method == "PUT":
        v = dl.normalize(request.json or {})
        p.meta["dials"] = v
        p._meta_touched.add("dials")
        p.save()
        return jsonify({"ok": True, "dials": v, "derived": dl.derived(v)})
    cur = dl.normalize({**(load_settings().get("dials") or {}),
                        **(p.meta.get("dials") or {})})
    return jsonify({"dials": cur, "derived": dl.derived(cur),
                    "spec": dl.DIALS, "own": bool(p.meta.get("dials"))})


@app.get("/api/projects/<slug>/recap")
def project_recap(slug: str):
    """剧情概要（累计）+ 逐章一句话。

    人要看清「这本书讲到哪了」，原来只能翻 346 章细纲。这两样本来就是
    喂给模型的前情，同一份东西给人看一遍，不额外生成。
    """
    p = Project(slug)
    co = p._load("chapter_outlines.json", {}) or {}
    st = p.state
    rows = []
    for k in sorted(co, key=lambda x: int(x)):
        s0 = str(co[k])
        head = (re.search(r"第\d+章\s*(.+)", s0.splitlines()[0]) or [None, ""])[1]
        mo = re.search(r"^\s*一句话\s*[:：]\s*(.+)$", s0, re.M)
        mb = re.search(r"^\s*重场\s*[:：]\s*(.+)$", s0, re.M)
        mh = re.search(r"^\s*章末钩子\s*[:：]\s*(.+)$", s0, re.M)
        rows.append({"n": int(k), "title": head.strip()[:24],
                     "one": (mo.group(1).strip()[:80] if mo else ""),
                     "beat": (mb.group(1).strip()[:8] if mb else ""),
                     "hook": (mh.group(1).strip()[:60] if mh else "")})
    vols = [{"name": v.get("name", ""), "start": v.get("start"), "end": v.get("end")}
            for v in (p._load("volumes.json", []) or [])]
    return jsonify({"recap": st.get("outline_recap") or "",
                    "recap_at": st.get("recap_at") or 0,
                    "chapters": rows, "volumes": vols,
                    "with_one": sum(1 for r in rows if r["one"])})


@app.get("/api/projects/<slug>/trace")
def project_trace(slug: str):
    """最近的模型调用：实际发出去的提示词与回复。

    列表只给元信息（不带正文），详情带 seq 参数取全文 —— 单条提示词可达
    五万字符，列表里全带上会让页面卡死。
    """
    d = Project(slug).dir / "trace"
    if not d.exists():
        return jsonify({"calls": [], "note": "还没有调用记录"})
    seq = request.args.get("seq")
    files = sorted(d.glob("*.json"), reverse=True)
    if seq:
        for f in files:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(rec.get("seq")) == str(seq):
                return jsonify(rec)
        return jsonify({"error": "没有这一条"}), 404
    out = []
    for f in files[:int(request.args.get("limit", 60))]:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rec.pop("prompt", None)
        rec["output"] = str(rec.get("output") or "")[:160]
        out.append(rec)
    return jsonify({"calls": out, "total": len(files)})


@app.route("/api/projects/<slug>/structure")
def project_structure(slug: str):
    """故事骨架总览：阶段 / 支线 / 三阶梯 / 张力 / 承诺 / 待重排 / 检索用量。

    只读已经生成的数据，**不触发任何模型调用** —— 这是个页面刷新就会打的接口，
    让它顺手去建骨架的话，用户点一下标签页就烧掉十几次生成。
    没有的项返回空数组，前端显示「还没生成」。
    """
    p = Project(slug)
    co = p._load("chapter_outlines.json", {}) or {}
    upto = max((int(k) for k in co), default=0)
    stages = p._load("stages.json", []) or []
    threads = p._load("threads.json", []) or []
    ladders = p._load("ladders.json", {}) or {}
    st = p.state
    tensions = st.get("tensions") or []
    promises = st.get("promises") or []

    out = {"upto": upto, "chapters_planned": len(co), "stages": stages,
           "threads": threads, "ladders": ladders, "ladder_kinds": sc.LADDER_KINDS,
           "tensions": tensions, "promises": promises,
           "slots": sc.ROLE_SLOTS, "issues": [], "repairs": [], "cast": []}
    try:                       # 检索用量与有没有细纲无关，早退前就先填上
        out["search_usage"] = registry.searcher().usage()
    except Exception:
        out["search_usage"] = {}
    if not co:
        return jsonify(out)

    try:
        nv = Novelist(p)
        al = nv.name_aliases()
        hero = (nv.alias_pair() or [""])[0]
        app_ = sc.cast_appearances(co, aliases=al)
        if threads:
            sc.thread_last_seen(threads, co, al, protagonist=hero)
        if promises:
            sc.promise_last_seen(promises, co)
        issues = []
        for s in stages:
            vac = sc.vacancies(s)
            if vac:
                issues.append({"kind": "功能位空缺", "where": s["name"],
                               "text": "、".join(vac) + " 无人担当"})
        for nm, where in sc.unregistered(stages, [c["name"] for c in nv.roster()], al):
            issues.append({"kind": "未登记角色", "where": where, "text": nm})
        for x in sc.arc_frozen(stages, aliases=al):
            issues.append({"kind": "弧光停滞", "where": "", "text": x})
        for x in sc.thread_overdue(threads, upto):
            issues.append({"kind": "支线断线", "where": "", "text": x})
        for x in sc.silent_resolution(tensions, app_, upto, aliases=al):
            issues.append({"kind": "张力静默消解", "where": "", "text": x})
        for x in sc.ladder_stalled(ladders, upto):
            issues.append({"kind": "阶梯停滞", "where": "", "text": x})
        for x in sc.starving(promises, upto):
            issues.append({"kind": "承诺挨饿", "where": "", "text": x})
        out["issues"] = issues
        out["repairs"] = nv.outline_repairs(upto) if issues else []
        out["cast"] = sorted(
            ({"name": k, "chapters": len(v), "first": v[0], "last": v[-1],
              "gap": max([b - a for a, b in zip(v, v[1:])] or [0])}
             for k, v in app_.items()), key=lambda x: -x["chapters"])[:40]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"

    return jsonify(out)


@app.post("/api/projects/<slug>/structure/build")
def project_structure_build(slug: str):
    """按需生成故事骨架（阶段／支线／三阶梯／张力／承诺）。

    排纲时会自动建，但**已经排完的老书没有入口** —— 骨架是后加的功能，
    那些书只能干看着五块全是「还没生成」。这里给一个显式的生成按钮。
    要花几次模型调用，所以只在用户明确点击时才跑，绝不挂在页面加载上。
    """
    kinds = (request.json or {}).get("kinds") or ["stages", "threads", "ladders",
                                                 "tensions", "promises"]
    rebuild = bool((request.json or {}).get("rebuild"))
    nv = Novelist(Project(slug))
    got, err = {}, {}
    for k in kinds:
        fn = getattr(nv, k, None)
        if not callable(fn):
            continue
        try:
            v = fn(rebuild=rebuild)
            got[k] = len(v)
        except Exception as e:
            err[k] = f"{type(e).__name__}: {e}"
    return jsonify({"ok": not err, "built": got, "errors": err})


@app.route("/api/projects/<slug>/facts", methods=["GET", "POST", "DELETE"])
def project_facts(slug: str):
    """检索攒下的事实卡：可看、可删。

    这些卡片一路在影响正文(它们是 ${kb} 的后半段, 也是写章节时的召回源),
    却一直没有任何界面。检索是模型自己发的关键词, 难免捞回不相干或过时的
    资料, 用户必须看得见、删得掉。
    """
    p = Project(slug)
    facts = p._load("facts.json", {}) or {}
    if request.method == "POST":
        # 主动检索一个主题入库。检索关键词平时由模型自己发, 但用户比模型更清楚
        # 这本书缺哪块知识(某个行业规矩、某年物价), 得能自己点名要。
        topic = (request.json or {}).get("topic", "").strip()
        if not topic:
            return jsonify({"error": "topic 不能为空"}), 400
        if (request.json or {}).get("force") and topic in facts:
            facts.pop(topic)
            p.write("facts.json", json.dumps(facts, ensure_ascii=False, indent=2))
        nv = Novelist(p)
        q = (request.json or {}).get("query") or \
            nv.retriever.query_for_topic(topic, era=nv.era_brief(60))
        r = nv.retriever.fact_for_verbose(
            {"topic": topic, "query": q},
            keep_raw=bool((request.json or {}).get("keep_raw")))
        return jsonify(r), (200 if r.get("ok") else 404)
    if request.method == "DELETE":
        topic = (request.json or {}).get("topic")
        if topic in facts:
            facts.pop(topic)
            p.write("facts.json", json.dumps(facts, ensure_ascii=False, indent=2))
            return jsonify({"ok": True, "left": sum(1 for v in facts.values()
                                                    if isinstance(v, dict) and v.get("card"))})
        return jsonify({"error": "no such topic"}), 404
    cards = [{"topic": k, "card": v.get("card", ""),
              "sources": (v.get("sources") or [])[:3], "at": v.get("built_at", "")}
             for k, v in facts.items()
             if isinstance(v, dict) and v.get("card")]
    cards.sort(key=lambda x: x["at"], reverse=True)
    return jsonify({"cards": cards, "total_topics": len(facts)})


@app.route("/api/pack/<kind>/<pid>", methods=["GET", "PUT"])
def pack_edit(kind: str, pid: str):
    """插件包查看与编辑（type/genre/style），前端「插件包」页用。"""
    from server.registry import PACKS
    if kind not in ("type", "genre", "style"):
        return jsonify({"error": "kind 须为 type/genre/style"}), 400
    f = PACKS / kind / f"{pid}.json"
    if not f.exists():
        return jsonify({"error": "不存在"}), 404
    if request.method == "PUT":
        try:
            data = request.json
            assert isinstance(data, dict) and data.get("id") == pid
        except Exception:
            return jsonify({"error": "须为合法 JSON 且 id 不变"}), 400
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        registry.reload()
        return jsonify({"ok": True})
    return jsonify(json.loads(f.read_text(encoding="utf-8")))


@app.get("/api/projects/<slug>/memory")
def mem_search(slug: str):
    p = Project(slug)
    q = request.args.get("q", "")
    return jsonify({"hits": p.mem.search(q, k=int(request.args.get("k", 6))),
                    "pending_foreshadow": p.mem.pending_foreshadow(),
                    "stats": p.mem.stats()})


@app.get("/api/projects/<slug>/context/<int:n>")
def context_report(slug: str, n: int):
    """返回写第 n 章时五层记忆的实际占用 —— 用户据此调配比."""
    p = Project(slug)
    cached = p._load(f"audit/{n:03d}.ctx.json", None)
    if cached:
        return jsonify(cached)
    co = p._load("chapter_outlines.json", {}).get(str(n), "")
    if not co:
        return jsonify({"error": f"第 {n} 章还没有细纲"}), 404
    return jsonify(Novelist(p).build_context(n, co)["report"])


@app.post("/api/teardown")
def teardown():
    """拆书: 传入整本文本 -> 结构化素材; 带 slug 时直接落进该项目。"""
    b = request.json or {}
    text = b.get("text", "")
    if len(text) < 500:
        return jsonify({"error": "文本太短"}), 400
    chs = split_chapters(text)

    def stream():
        try:
            yield sse({"t": f"识别到 {len(chs)} 章，开始拆解…\n"})
            box = []
            res = analyze(chs, llm=lambda q: clean(call("judging", q, max_tokens=1200).text),
                          sample=int(b.get("sample") or 8),
                          on_progress=lambda m: box.append(m))
            for m in box:
                yield sse({"t": m})
            yield sse({"t": "\n" + res["summary"]})
            if b.get("slug"):
                p = Project(b["slug"])
                if p.meta:
                    applied = apply_to_project(p, res)
                    yield sse({"t": f"\n\n已写入项目：{json.dumps(applied, ensure_ascii=False)}"})
            yield sse({"done": True})
        except Exception as e:
            traceback.print_exc()
            yield sse({"error": str(e)})

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/usage")
def usage_api():
    from server.orchestrator import USAGE
    return jsonify({**USAGE, "total": USAGE["prompt"] + USAGE["completion"],
                    "budget": load_settings()["limits"].get("daily_call_budget", 0)})


@app.get("/api/projects/<slug>/bookaudit")
def book_audit_api(slug: str):
    """全书体检 —— 单章合格不等于全书合格。"""
    p = Project(slug)
    done = sorted(p.state.get("done", []))
    if not done:
        return jsonify({"error": "还没有章节"}), 404
    nv = Novelist(p)
    chs = {n: p.chapter(n) for n in done}
    anchor = nv.world_anchor()
    names = [c["name"] for c in nv.roster()] or ["主角"]
    return jsonify(book_audit(chs, characters=names,
                              forbidden_terms=anchor.get("forbidden"),
                              forbidden_people=anchor.get("forbidden_people"),
                              protagonist=names[0] if names else ""))


@app.get("/api/projects/<slug>/window/<int:n>")
def window_api(slug: str, n: int):
    """邻章窗口体检 —— 本章和前后几章贴在一起看。"""
    p = Project(slug)
    done = sorted(p.state.get("done", []))
    chs = {i: p.chapter(i) for i in done}
    return jsonify(window_audit(chs, n, span=int(request.args.get("span", 3)),
                                outlines=p._load("chapter_outlines.json", {})))


@app.get("/api/search")
def search_api():
    """直接检索（框架内建的检索 provider，写作时会自动用到，这里只是手动入口）。"""
    sr = registry.searcher(request.args.get("provider"))
    if not sr.available():
        return jsonify({"ok": False, "error": f"检索源 {sr.id} 不可用"}), 200
    q = request.args.get("q", "")
    return jsonify({"ok": True, "provider": sr.id, "query": q,
                    "results": sr.search(q, k=int(request.args.get("k", 6)))})


@app.get("/api/projects/<slug>/anchor")
def anchor_api(slug: str):
    nv = Novelist(Project(slug))
    return jsonify({"anchor": nv.world_anchor(),
                    "roster": [c["name"] for c in nv.roster()],
                    "tic_guard": nv.tic_guard()})


@app.get("/api/projects/<slug>/export")
def export(slug: str):
    p = Project(slug)
    fmt = request.args.get("fmt", "txt")
    if fmt in BINARY_EXPORTERS:
        body = BINARY_EXPORTERS[fmt](p)          # bytes
    elif fmt in EXPORTERS:
        body = EXPORTERS[fmt](p)                 # str
    else:
        return jsonify({"error": f"不支持的格式 {fmt}"}), 400
    name = f"{p.meta.get('title', 'novel')}.{EXT[fmt]}"
    # 中文文件名必须按 RFC 5987 百分号编码, 否则 WSGI 写 header 时 latin-1 编码失败
    mime = MIME[fmt] + ("" if fmt in BINARY_EXPORTERS else "; charset=utf-8")
    return Response(body, mimetype=mime, headers={
        "Content-Disposition": "attachment; filename=\"export.%s\"; filename*=UTF-8''%s"
                               % (EXT[fmt], quote(name, safe=""))})


# ----------------------------------------------------------------- 生成
VAR_NAMES = [
    "title", "premise", "plot", "background", "characters", "relationships",
    "kb", "world_bible", "outline", "era_card", "style", "style_rules",
    "genre_rules", "common_rules", "anti_ai_rules", "chapter_directives",
    "character_rules", "cliche_blacklist", "target_chapters", "target_words",
]


def project_kb(nv: "Novelist", limit: int = 4000) -> str:
    """本书知识库 = 用户自己写的 + 检索攒下的事实卡。

    检索卡一直躺在 facts.json 里(本书 564 个主题、55 张有内容), 只在写章节时
    被召回, 用户既看不见也用不上。它就是这本书的知识库, 应该能被提示词引用。
    """
    parts = []
    own = (nv.p.meta.get("fields", {}) or {}).get("kb", "")
    if own:
        parts.append(own.strip())
    try:
        facts = nv.p._load("facts.json", {}) or {}
        cards = [(k, v.get("card", "")) for k, v in facts.items() if v.get("card")]
        for topic, card in cards[:40]:
            parts.append(f"【{topic}】{card.strip()[:300]}")
    except Exception:
        pass
    return "\n\n".join(parts)[:limit]


def fill_vars(prompt: str, slug: Optional[str]) -> str:
    """把提示词里的 ${...} 占位符按项目填实。

    必须在服务端做 —— 前端只认得它手上那点数据, 实测把 ${cliche_blacklist}
    直接替换成空字符串, 于是「去 AI 味」这条右键指令发出去的是「禁用：」
    后面什么都没有, 旗舰功能形同虚设。
    """
    if not slug or "${" not in prompt:
        return prompt
    try:
        nv = Novelist(Project(slug))
        fields = nv.p.meta.get("fields", {}) or {}
        fill = {
            "cliche_blacklist": "、".join(nv.blacklist()[:60]),
            "title": nv.p.meta.get("title", ""),
            "premise": fields.get("premise", ""),
            "plot": fields.get("premise", ""),          # 同义, 老模板用的是 plot
            "background": fields.get("background", ""),
            # 右键改写的变量同样别截 —— 128k 上下文装得下, 截断只会让模型
            # 拿着半份设定改稿。真正需要限流的是正文与召回, 不是这些小资产。
            "characters": nv.asset("characters.md"),
            "relationships": fields.get("relationships", ""),
            "kb": project_kb(nv),
            "world_bible": nv.asset("world_bible.md"),
            "outline": nv.asset("outline.md"),
            "era_card": nv.asset("era_card.md"),
            "style": nv.style.get("name", "") or nv.p.meta.get("style_id", ""),
            "style_rules": "\n".join(nv.style.get("rules", [])),
            "genre_rules": "\n".join(nv.genre.get("rules", [])),
            "common_rules": "\n".join(
                (nv.common.get("rules", []) if isinstance(nv.common, dict) else [])
                + (nv.cfg.get("chapter_directives") or [])
                + (nv.cfg.get("anti_ai_rules") or [])
                + (nv.cfg.get("character_rules") or [])),
            "anti_ai_rules": "\n".join(nv.cfg.get("anti_ai_rules") or []),
            "chapter_directives": "\n".join(nv.cfg.get("chapter_directives") or []),
            "character_rules": "\n".join(nv.cfg.get("character_rules") or []),
            "target_chapters": str(nv.p.meta.get("target_chapters", "")),
            "target_words": str(nv.p.meta.get("target_words", "")),
        }
        for k, v in fill.items():
            prompt = prompt.replace("${%s}" % k, str(v))
    except Exception as e:
        print(f"[gen] 占位符填充失败({slug}): {e}")
    return prompt


@app.post("/api/gen/preview")
def gen_preview():
    """只做变量填充不生成 —— 让用户(和 E2E)看得见提示词最终长什么样。"""
    b = request.json or {}
    return jsonify({"prompt": fill_vars(b.get("prompt", ""), b.get("slug"))})


@app.post("/api/gen")
def gen():
    """通用单次生成 (右键菜单 / 自由提问). 流式返回 text 与 reasoning 分离."""
    b = request.json or {}
    prompt = b.get("prompt", "")
    profile = b.get("profile", "drafting")

    # 占位符必须在服务端补齐 —— 前端只认得它手上那点数据, 实测把
    # ${cliche_blacklist} 直接替换成空字符串, 于是「去 AI 味」这条右键指令
    # 发出去的是「禁用：」后面什么都没有, 旗舰功能形同虚设。
    prompt = fill_vars(prompt, b.get("slug"))

    def stream() -> Iterator[str]:
        try:
            provider, kw = registry.resolve(profile)
            if b.get("model"):
                kw["model"] = b["model"]
            got = False
            for d in provider.stream([{"role": "user", "content": prompt}], **kw):
                if d.text:
                    got = True
                    yield sse({"t": d.text})
                if d.reasoning:
                    yield sse({"r": d.reasoning})
            if not got:
                yield sse({"t": "（模型未返回正文，已记录）"})
            yield sse({"done": True})
        except Exception as e:
            yield sse({"error": str(e)})

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/projects/<slug>/step")
def step(slug: str):
    """执行流水线单步, SSE 实时吐字."""
    p = Project(slug)
    if not p.meta:
        return jsonify({"error": "not found"}), 404
    b = request.json or {}
    what = b.get("step")
    n = int(b.get("n") or 0)

    def stream() -> Iterator[str]:
        q: list = []
        try:
            nv = Novelist(p)
            emit = lambda t: q.append(t)
            if what == "naming":
                nv.step_naming(emit)
            elif what == "basis":
                emit(nv.basis_card(force=True))
            elif what == "world_bible":
                nv.step_world_bible(emit)
            elif what == "characters":
                nv.step_characters(emit)
            elif what == "outline":
                nv.step_outline(emit)
            elif what == "chapter_outlines":
                # 同上：配置里的 outline_batch 默认是 0（自动），不能直接当章数用
                nv.step_chapter_outlines(n or 1,
                                         int(b.get("count") or 0) or nv.outline_batch(),
                                         emit)
            elif what == "repair":
                nv.step_repair(emit)
            elif what == "volumes":
                nv.step_volumes(emit)
            elif what == "selfcheck":
                nv.step_selfcheck(emit)
            elif what == "reflect":
                nv.step_reflect(emit)
            elif what == "chapter":
                nv.step_chapter(n, emit)
            else:
                yield sse({"error": f"未知步骤 {what}"}); return
            for t in q:
                yield sse({"t": t})
            yield sse({"done": True, "board": p.board()})
        except Exception as e:
            traceback.print_exc()
            yield sse({"error": str(e)})

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _auto_worker(slug: str, upto: int, staged: bool = False):
    """staged=True 时每完成一个阶段就暂停，等用户在前端审阅/编辑后点「继续」。"""
    p = Project(slug)
    nv = Novelist(p)
    job = JOBS[slug]

    def pause(done_stage: str) -> bool:
        if staged:
            job.update({"waiting": True, "stage": f"{done_stage} · 待审阅",
                        "running": False})
            return True
        return False

    try:
        job["mode"] = "staged" if staged else "auto"
        if not p.read("world_bible.md"):
            job["stage"] = "世界观"; nv.step_world_bible()
            if pause("世界观"): return
        if not p.read("characters.md"):
            job["stage"] = "角色档案"; nv.step_characters()
            if pause("角色档案"): return
        if not p.read("outline.md"):
            job["stage"] = "总纲"; nv.step_outline()
            if pause("总纲"): return
        # 原来直接取配置值, 而 outline_batch 默认是 0（含义是「按输出上限自动算」），
        # 于是这里会调 step_chapter_outlines(n, 0) 排 0 章。必须走 nv.outline_batch()。
        # 同时夹住领先上限：细纲不许排到正文太前面（见 Novelist.outline_lead）。
        _lead = nv.outline_lead()
        batch = nv.outline_batch()
        if _lead:
            batch = min(batch, max(1, _lead))
        n = (p.state.get("current") or 0) + 1
        end = min(upto, p.meta["target_chapters"])
        while n <= end and not job.get("stop"):
            job["stage"] = f"第 {n} 章"
            if str(n) not in p._load("chapter_outlines.json", {}):
                nv.step_chapter_outlines(n, batch)
            r = nv.step_chapter(n)
            job.update({"last": r, "done": len(p.state["done"]), "words": p.total_words})
            n += 1
            if staged and (n - 1) % batch == 0 and n <= end:
                job.update({"waiting": True, "running": False,
                            "stage": f"已写到第 {n-1} 章 · 待审阅"})
                return
        job["stage"] = "已停止" if job.get("stop") else "完成"
    except Exception as e:
        job["stage"] = "出错"; job["error"] = str(e)
        traceback.print_exc()
    finally:
        job["running"] = False


@app.post("/api/projects/<slug>/auto")
def auto(slug: str):
    """启动/停止后台自动写作."""
    b = request.json or {}
    if b.get("stop"):
        JOBS.setdefault(slug, {})["stop"] = True
        return jsonify({"ok": True, "stopping": True})
    if JOBS.get(slug, {}).get("running"):
        return jsonify({"ok": False, "msg": "已在运行"}), 409
    upto = int(b.get("upto") or 5)
    staged = (b.get("mode") == "staged")
    JOBS[slug] = {"running": True, "stop": False, "stage": "启动中",
                  "upto": upto, "mode": "staged" if staged else "auto"}
    threading.Thread(target=_auto_worker, args=(slug, upto, staged),
                     daemon=True).start()
    return jsonify({"ok": True, "mode": "staged" if staged else "auto"})


@app.get("/api/projects/<slug>/job")
def job_status(slug: str):
    p = Project(slug)
    j = dict(JOBS.get(slug, {}))
    j.update({"done": len(p.state.get("done", [])), "words": p.total_words,
              "target_words": p.meta.get("target_words", 0),
              "target_chapters": p.meta.get("target_chapters", 0),
              "log": p.state.get("log", [])[-15:]})
    return jsonify(j)


@app.post("/api/audit")
def audit_api():
    b = request.json or {}
    return jsonify(audit(b.get("text", ""), b.get("blacklist"), int(b.get("target_words") or 0)))


def main() -> None:
    port = int(os.environ.get("NOVEL_PORT", 60001))
    print(f"→ http://127.0.0.1:{port}/   网关 {len(registry.gateways)} 个 / "
          f"类型 {len(registry.types)} 种 / 题材 {len(registry.genres)} 个")
    # 代码变了自动重启。长跑 worker 早就有热轮转（每章开工前核对代码戳），
    # Web 服务却没有 —— 改完代码不手动重启，界面就一直显示旧数据/旧页面，
    # 实测新建的项目在侧边栏里死活不出现，查了半天是服务跑的旧代码。
    # reloader 只看 server/ 与 web/，不看 projects/（书稿一直在变，会疯狂重启）。
    # 自动重载遇到语法错误会直接退出, 服务从此不再起来 —— 实测改坏
    # retrieval.py 之后 web 服务静默死掉, 过了一个多小时才被发现。
    # 用一个看门脚本兜住: 进程退出就重启, 语法错误改回来后自动恢复。
    if os.environ.get("NOVEL_SUPERVISED") != "1":
        import subprocess
        env = dict(os.environ, NOVEL_SUPERVISED="1")
        while True:
            code = subprocess.call([sys.executable, __file__], env=env)
            if code == 0:
                break
            print(f"!! 服务退出（码 {code}），3 秒后重启", flush=True)
            time.sleep(3)
        return

    watch = [str(p) for p in (ROOT / "server").rglob("*.py")]
    watch += [str(p) for p in (ROOT / "web").rglob("*.js")]
    watch += [str(p) for p in (ROOT / "web").rglob("*.css")]
    watch += [str(p) for p in (ROOT / "config").glob("*.yaml")]
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False,
            use_reloader=os.environ.get("NOVEL_NO_RELOAD") != "1",
            extra_files=watch)


if __name__ == "__main__":
    main()
