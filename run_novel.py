#!/usr/bin/env python3
"""命令行自动写作器 —— 可断点续跑.

  python3 run_novel.py init  --title "我的第一本书" --genre lishi-jiakong \
                             --style fanqie-shuangwen --words 500000
  python3 run_novel.py run   --title "我的第一本书" --chapters 5
  python3 run_novel.py status --title "我的第一本书"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server.orchestrator import Project, Novelist, create_project, slugify
from server.settings import load as load_settings


def cmd_init(a):
    cfg = load_settings()
    avg = (cfg["generation"]["chapter_words_min"] + cfg["generation"]["chapter_words_max"]) // 2
    # 文风包声明了单章字数就用它 —— 都市爽文 5750/章, 按全局 2600 算章数
    # 会把 50 万字规划成 192 章, 实际写满变 110 万字
    try:
        from server.registry import registry
        cw = (registry.styles.get(a.style) or {}).get("chapterWords")
        if isinstance(cw, (list, tuple)) and len(cw) == 2:
            avg = (int(cw[0]) + int(cw[1])) // 2
    except Exception:
        pass
    chapters = a.chapters or max(1, round(a.words / avg))
    limit = cfg["limits"]["max_chapters"]
    if chapters > limit:
        print(f"! 章节数 {chapters} 超过全局上限 {limit}, 已截断")
        chapters = limit
    p = create_project(
        title=a.title, type_id=a.type, genre_id=a.genre, style_id=a.style,
        target_chapters=chapters, target_words=a.words,
        history_mode=a.history,
        fields={"premise": a.premise or a.title, "background": a.background or "",
                "characters": "", "relationships": "", "kb": "", "style": a.extra or ""},
    )
    print(f"✓ 建项目 {p.dir}")
    print(f"  目标 {chapters} 章 / {a.words:,} 字 (单章 {avg} 字)")
    print(f"  题材={a.genre} 文风={a.style} 模型={p.meta['model']}")


def _code_stamp() -> str:
    """关键代码与配置的版本戳。worker 每章开工前核对, 变了就退出让守护重启。

    本会话踩过 3 次: 改了代码, 跑着的旧进程还在用旧逻辑写稿, 甚至新旧双跑
    并发写同一章。热更新一致性必须由框架保证, 不能靠人记得杀进程。"""
    import hashlib
    h = hashlib.sha1()
    root = Path(__file__).resolve().parent
    for f in sorted((root / "server").rglob("*.py")) +              sorted((root / "config").glob("*.yaml")) +              sorted((root / "packs").rglob("*.json")):
        try:
            st = f.stat()
            h.update(f"{f.name}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


def cmd_run(a):
    p = Project(slugify(a.title))
    if not p.meta:
        sys.exit("项目不存在, 先跑 init")
    nv = Novelist(p)
    t0 = time.time()

    if not p.read("world_bible.md"):
        print("[1/4] 世界观圣经"); nv.step_world_bible()
    if not p.read("characters.md"):
        print("[2/4] 角色档案"); nv.step_characters()
    if not p.read("outline.md"):
        print("[3/4] 总纲"); nv.step_outline()

    print("[4/4] 逐章生成")
    stamp = _code_stamp()
    batch = nv.outline_batch()      # 0 = 按输出上限自动算，见 outline_batch()
    start = (p.state.get("current") or 0) + 1
    end = min(start + a.chapters - 1, p.meta["target_chapters"])
    n = start
    while n <= end:
        outlines = p._load("chapter_outlines.json", {})
        if str(n) not in outlines:
            print(f"  → 生成第 {n}-{n+batch-1} 章细纲")
            nv.step_chapter_outlines(n, batch)
        if _code_stamp() != stamp:
            print("!! 代码或配置已更新，本进程退出交由守护以新版本续跑")
            sys.exit(3)
        r = nv.step_chapter(n)
        print(f"  ✓ 第{r['chapter']}章 {r['chars']}字 得分{r['score']}"
              f"{' [已重写]' if r['rewritten'] else ''} {r['elapsed']:.1f}s")
        n += 1

    done = len(p.state["done"])
    tw = p.total_words
    el = time.time() - t0
    print(f"\n本次 {end-start+1} 章 / {el:.0f}s  累计 {done} 章 {tw:,} 字")
    if done:
        speed = tw / el if el else 0
        left = p.meta["target_words"] - tw
        print(f"速率 {speed:.0f} 字/秒  剩余 {left:,} 字 预计 {left/speed/3600:.1f} 小时" if speed else "")


def cmd_rewrite(a):
    p = Project(slugify(a.title))
    if not p.meta:
        sys.exit("项目不存在")
    r = Novelist(p).rewrite_chapter(a.chapter, mode=a.mode, note=a.note)
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_repair(a):
    p = Project(slugify(a.title))
    if not p.meta:
        sys.exit("项目不存在")
    r = Novelist(p).repair_violations(limit=a.limit, dry=a.dry)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:3000])


def cmd_status(a):
    p = Project(slugify(a.title))
    if not p.meta:
        sys.exit("项目不存在")
    print(p.board())
    try:
        nv = Novelist(p)
        print("\n## 记忆索引\n" + json.dumps(p.mem.stats(), ensure_ascii=False))
    except Exception as e:
        print("memory:", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init"); i.set_defaults(f=cmd_init)
    i.add_argument("--title", required=True)
    i.add_argument("--type", default="novel")
    i.add_argument("--genre", default="lishi-jiakong")
    i.add_argument("--style", default="fanqie-shuangwen")
    i.add_argument("--words", type=int, default=500000)
    i.add_argument("--chapters", type=int, default=0)
    i.add_argument("--premise", default="")
    i.add_argument("--background", default="")
    i.add_argument("--extra", default="")
    # 选项从引擎取, 不写死 —— 加一种基底时这里最容易漏
    from server.orchestrator import Novelist as _Nv
    i.add_argument("--history", default="auto",
                   choices=["auto", "none", *_Nv.BASIS_TYPES],
                   help="real=真实朝代(宋朝就叫宋朝) alt=架空(自造国号) none=与史无关")

    r = sub.add_parser("run"); r.set_defaults(f=cmd_run)
    r.add_argument("--title", required=True)
    r.add_argument("--chapters", type=int, default=3)

    w = sub.add_parser("rewrite"); w.set_defaults(f=cmd_rewrite)
    w.add_argument("--title", required=True)
    w.add_argument("--chapter", type=int, required=True)
    w.add_argument("--mode", default="polish", choices=["polish", "replace", "fork"])
    w.add_argument("--note", default="")

    rp = sub.add_parser("repair"); rp.set_defaults(f=cmd_repair)
    rp.add_argument("--title", required=True)
    rp.add_argument("--limit", type=int, default=10)
    rp.add_argument("--dry", action="store_true", help="只列候选，不动手")

    s = sub.add_parser("status"); s.set_defaults(f=cmd_status)
    s.add_argument("--title", required=True)

    a = ap.parse_args()
    a.f(a)
