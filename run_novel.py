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


def cmd_outline(a):
    """先把全书细纲排完 —— 边排边写会让前面的章看不见后面的安排。"""
    p = Project(a.title)
    nv = Novelist(p)
    # 种子（铁律/设定/旋钮/目标章数）变了，下游资产全部作废重建。
    # 实测踩过：改完铁律只删了骨架，留下 outline.md 想省一次生成 ——
    # 总纲还是旧设定、阶梯是新的，细纲照着总纲写，改动等于没改。
    stale = nv.stale_assets()
    if stale:
        print(f"[失效] 种子已变，作废下游资产：{'、'.join(stale)}")
        for f in stale:
            (p.dir / f).unlink(missing_ok=True)
        st = p.state
        # 节奏标记也是下游状态, 必须一起清。**漏了它是静默故障**:
        # 实测重开一轮后 swept_at 还留着上一轮的 60, 而新一轮才排到 50,
        # 于是「离上次巡检不到 10 章」一路成立, 50 章一次巡检都没跑 ——
        # 伏笔台账、承诺兑现、张力推进的唯一生产者就这么歇了整整一轮,
        # 日志里看不出任何异常。outline_guide 同理: 那是对上一轮章节的
        # 纠偏, 套到新一轮的章节上是错的。
        for k in ("promises", "tensions", "orgs", "setbacks", "resolution_modes",
                  "pending_sweeps", "summaries", "timeline", "ledger", "power",
                  "identity", "roles", "terms",
                  "swept_at", "selfchecked_at", "recapped_at", "outline_guide"):
            st.pop(k, None)
        st["done"], st["current"] = [], 0
        p.save()
        try:
            p.mem.db.execute("delete from foreshadow")
        except Exception:
            pass
    nv.mark_seed()
    for step, fn in (("世界观", nv.step_world_bible), ("角色档案", nv.step_characters),
                     ("总纲", nv.step_outline)):
        name = {"世界观": "world_bible.md", "角色档案": "characters.md",
                "总纲": "outline.md"}[step]
        if not p.read(name):
            print(f"[前置] {step}")
            fn()
    if not p._load("volumes.json", []):
        print("[前置] 分卷")
        nv.step_volumes()
    total = a.to or int(p.meta.get("target_chapters") or 0)
    # 细纲不许排到正文太前面。一次排完全书是「剧情不连续」的根源：
    # 后一批细纲的输入只能是前一批细纲，永远不可能是正文。见 Novelist.outline_lead()。
    # `--to` 是用户显式指定的范围，尊重它；不指定才按领先上限收着排。
    stop = nv.outline_stop_at()
    if stop and not a.to:
        total = min(total, stop)
        print(f"[滚动排纲] 已写到第 {p.state.get('current') or 0} 章，"
              f"本轮只排到第 {total} 章（领先上限 {nv.outline_lead()} 章）。"
              f"写完再跑一次 outline 会自动接着排。")
    stamp = _code_stamp()
    while True:
        co = p._load("chapter_outlines.json", {}) or {}
        # 太短的当没排 —— 实测有 5 章只排出 55-190 字（多半是那一批被截断了），
        # 留着比缺着更糟：后面的批次会把它当成已排好的内容去接
        miss = [i for i in range(1, total + 1)
                if str(i) not in co or len(str(co.get(str(i), ""))) < 200]
        if not miss:
            break
        start = miss[0]
        batch = nv.outline_batch()
        print(f"  → 第 {start}-{min(start + batch - 1, total)} 章细纲"
              f"（已排 {len(co)}/{total}）", flush=True)
        nv.step_chapter_outlines(start, min(batch, total - start + 1))
        if _code_stamp() != stamp:
            print("!! 代码已更新，退出交由守护以新版本续排")
            return 3
    tgt = a.to or int(p.meta.get("target_chapters") or 0)
    if total < tgt:
        print(f"✓ 第 1-{total} 章细纲已排完（全书 {tgt} 章，滚动排纲）。"
              f"下一步：run 写正文，写完再跑 outline 接着排")
    else:
        print(f"✓ 全书 {total} 章细纲已排完。下一步：review 审阅，再 run 写正文")
    return 0


def cmd_review(a):
    """细纲审阅 —— 动笔前的一道关。现在改一行，比写完二十万字再返工便宜得多。"""
    nv = Novelist(Project(a.title))
    d = nv.step_outline_review()
    if d.get("error"):
        print("审阅失败：", d["error"])
        return 1
    print(f"\n总评：{d.get('verdict','')}\n")
    for i, x in enumerate(d.get("issues", []), 1):
        print(f"{i}. [{x.get('kind')}] {x.get('where')}")
        print(f"   问题：{x.get('what','')[:110]}")
        print(f"   怎么改：{x.get('fix','')[:110]}\n")
    if not d.get("issues"):
        print("未发现结构性问题。")
    print(f"完整报告：projects/{a.title}/outline_review.md")
    return 0


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
    # current 和 chapters/ 目录是两个真相来源，回退状态时很容易只改一个 ——
    # 实测把 done 改回 [1] 却漏了 current，于是从第 3 章接着排纲，
    # 第 2 章整个被跳过，后面几章的细纲全长在一个洞上。
    # 正文文件是唯一硬证据：前面缺哪一章，就从哪一章补起。
    _have = {int(f.stem) for f in (p.dir / "chapters").glob("*.md") if f.stem.isdigit()}
    _gap = next((i for i in range(1, start) if i not in _have), None)
    if _gap:
        print(f"!! 正文缺第 {_gap} 章（current={start-1}），从第 {_gap} 章补起")
        start = _gap
    end = min(start + a.chapters - 1, p.meta["target_chapters"])
    n = start
    while n <= end:
        outlines = p._load("chapter_outlines.json", {})
        if str(n) not in outlines:
            # 领先上限同样适用：写到第 n 章时，最多把细纲排到 n+lead
            lead = nv.outline_lead()
            b = min(batch, max(1, lead)) if lead else batch
            print(f"  → 生成第 {n}-{n+b-1} 章细纲")
            nv.step_chapter_outlines(n, b)
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

    o = sub.add_parser("outline"); o.set_defaults(f=cmd_outline)
    o.add_argument("--title", required=True)
    o.add_argument("--to", type=int, default=0, help="排到第几章（默认全书）")

    v = sub.add_parser("review"); v.set_defaults(f=cmd_review)
    v.add_argument("--title", required=True)

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
    # 返回值就是退出码，必须传出去。丢掉它的后果是静默的：cmd_outline 检测到
    # 代码变更后 return 3 请守护热轮转，可进程退的是 0，守护当成「排完了」就
    # 收工 —— 排纲在第 17/346 章停住，而日志上写着「排纲完成」。
    sys.exit(a.f(a) or 0)
