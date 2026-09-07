#!/usr/bin/env python3
"""按体检结论修复已写章节。

三类问题三种修法，都走框架，不手改文本：
  · 格式类（markdown 标题、引号风格）—— 确定性，normalize_body() 直接改
  · 语义类（英文残留、数字矛盾）—— 换成什么是判断题，交给模型定点重写
  · 叙事类（角色断线、标志动作过密）—— 只影响后续章节，由约束层管，不回改旧稿

    python3 scripts/repair.py --title 书名 [--dry]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.evaluator import audit                       # noqa: E402
from server.orchestrator import Novelist, Project, call, clean   # noqa: E402

_EN_OK = {"cpu", "dna", "gdp", "app", "kpi", "ceo", "cto"}


def english_hits(t: str):
    return [w for w in re.findall(r"[A-Za-z]{3,}", t) if w.lower() not in _EN_OK]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--terms", action="store_true", help="额外做数字条款一致性修复")
    a = ap.parse_args()

    nv = Novelist(Project(a.title))
    done = sorted(nv.p.state.get("done", []))
    if not done:
        print("没有已写章节")
        return 1
    print(f"《{a.title}》{len(done)} 章，引号主流风格：{nv.quote_style() or '未定'}\n")

    n_fmt = n_sem = 0
    for n in done:
        raw = nv.p.chapter(n)
        if not raw:
            continue

        # ① 格式类：确定性修复
        fixed = nv.normalize_body(raw)
        if fixed != raw:
            n_fmt += 1
            what = []
            if re.search(r"^#{1,6}\s+\S", raw, re.M):
                what.append("markdown标题")
            if raw.count(chr(0x300C)) != fixed.count(chr(0x300C)):
                what.append("引号风格")
            print(f"第{n:3d}章 格式修复：{'、'.join(what)}")
            if not a.dry:
                nv.p.write(nv.p.chapter_path(n), fixed)
            raw = fixed

        # ② 语义类：英文残留交给模型定点改
        en = english_hits(raw)
        if not en:
            continue
        n_sem += 1
        print(f"第{n:3d}章 英文残留：{en}")
        if a.dry:
            continue
        p = (f"下面这章正文里混进了英文单词：{'、'.join(dict.fromkeys(en))}。\n"
             f"请把它们换成符合本书语境的中文（这是一部北宋背景的小说，"
             f"人物对话里不该出现英文），**其余一字不改**，"
             f"不要重写、不要调整段落、不要改标点。\n"
             f"直接输出修改后的完整正文，无前言。\n\n{raw}")
        t2 = clean(call("polishing", p, max_tokens=8192).text)
        t2 = nv.normalize_body(t2)
        if not t2 or english_hits(t2):
            print(f"        改后仍有英文，跳过")
            continue
        # 长度守卫：这一步只该换几个词，字数不该有大变化
        c1 = len(re.findall(r"[一-鿿]", raw))
        c2 = len(re.findall(r"[一-鿿]", t2))
        if abs(c2 - c1) > c1 * 0.15:
            print(f"        改后字数异常（{c1}→{c2}），跳过")
            continue
        nv.p.write(nv.p.chapter_path(n), t2)
        r = audit(t2, extra_blacklist=nv.hard_blacklist(),
                  target_words=nv.target_words(),
                  check_modern=nv.anachronism_check())
        r["target_words"] = nv.target_words()
        nv.p.write(f"audit/{n:03d}.json", json.dumps(r, ensure_ascii=False, indent=2))
        print(f"        ✓ 已改（{c1}→{c2} 字，得分 {r['score']}）")

    print(f"\n格式修复 {n_fmt} 章，语义修复 {n_sem} 章")
    if a.terms:
        return fix_terms(nv, done, a.dry)
    return 0


def fix_terms(nv, done, dry: bool) -> int:
    """数字条款一致性修复。

    竞标底价从第 10 章的八百贯，到第 14 章变一千、第 15 章变五百 —— 这类
    「同一笔买卖的规则参数前后打架」既不是格式问题（没有唯一正确形态），
    也不是单章问题（要跨章看才发现）。所以：让模型通读所有涉及该数字的章节，
    自己判定哪个是权威值、哪些章要改，再逐章定点重写。
    """
    import re as _re
    # 先把带数字条款的段落摘出来, 不用全文喂
    # 两路扫：① 带关键词的（底价/报价/期限…）② 裸数额（八百贯、六百五十贯）。
    # 只认关键词会漏掉「八百贯，卖了你那身好皮也凑不出」这种句子 —— 而冲突
    # 恰恰藏在这类裸数字里。
    KW = _re.compile(r"[^。！？\n]{0,40}"
                     r"(?:底价|标底|起标|报价|递价|喊价|标的|期限|分成|利息|月息|"
                     r"违约|定金|赎金|悬赏|折价|买扑)[^。！？\n]{0,40}")
    AMT = _re.compile(r"[^。！？\n]{0,30}"
                      r"[一二三四五六七八九十百千万零两\d]{2,8}(?:贯|两|石|匹|亩)"
                      r"[^。！？\n]{0,30}")
    ctx, seen = [], set()
    for n in done:
        t = nv.p.chapter(n)
        for pat, cap in ((KW, 6), (AMT, 8)):
            for h in pat.findall(t)[:cap]:
                h = h.strip()
                key = (n, h[:24])
                if len(h) > 6 and key not in seen:
                    seen.add(key)
                    ctx.append(f"第{n}章：{h}")
    if not ctx:
        print("没有可比对的数字条款")
        return 0
    print(f"\n扫出 {len(ctx)} 条带数字的表述，交给模型比对…")

    p = ("下面是一部小说里所有涉及**规则性数字**的句子（竞标底价、报价、期限、"
         "分成、利率等），按章号排列。请找出**同一件事在不同章节数字对不上**的地方。\n\n"
         + "\n".join(ctx[:200]) +
         "\n\n只输出 JSON（不要代码围栏）：\n"
         '{"conflicts":[{"item":"码头竞标底价","canonical":"八百贯",'
         '"why":"第10章反复出现三次且驱动了后续借钱情节，应以此为准",'
         '"fix":[{"ch":14,"from":"标底一千贯","to":"标底八百贯"}]}]}\n'
         "要求：canonical 选**最早确立且被后续情节依赖**的那个值；"
         "fix 里逐条列出要改的章号与原文片段。没有冲突就输出 {\"conflicts\":[]}。")
    try:
        r = call("judging", p, max_tokens=2000)
        m = _re.search(r"\{.*\}", clean(r.text), _re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        print(f"比对失败：{e}")
        return 1
    conf = data.get("conflicts") or []
    if not conf:
        print("未发现数字冲突")
        return 0

    for c in conf:
        print(f"\n【{c.get('item')}】权威值：{c.get('canonical')}")
        print(f"  理由：{c.get('why','')[:80]}")
        for f in c.get("fix", []):
            print(f"  第{f.get('ch')}章：{f.get('from')} → {f.get('to')}")
    if dry:
        return 0

    # 逐章重写：只改数字与因之失真的表述，其余不动
    todo = {}
    for c in conf:
        for f in c.get("fix", []):
            todo.setdefault(int(f["ch"]), []).append(
                f"{c['item']}应为「{c['canonical']}」：{f.get('from')} → {f.get('to')}")
    for n, items in sorted(todo.items()):
        raw = nv.p.chapter(n)
        if not raw:
            continue
        prompt = (f"下面这一章里的数字与全书其他章节对不上，请改正：\n"
                  + "\n".join(f"- {x}" for x in items) +
                  "\n\n要求：\n"
                  "- 只改这些数字，以及**因为数字变了而说不通的那几句**"
                  "（比如「低于底价却没当场废标」这类）\n"
                  "- 其余一字不改：不重写、不调段落、不改标点、不加情节\n"
                  "- 改完自己核一遍：这一章里所有金额之间要算得通\n"
                  f"直接输出修改后的完整正文，无前言。\n\n{raw}")
        t2 = nv.normalize_body(clean(call("polishing", prompt, max_tokens=8192).text))
        c1 = len(_re.findall(r"[一-鿿]", raw))
        c2 = len(_re.findall(r"[一-鿿]", t2))
        if not t2 or abs(c2 - c1) > c1 * 0.2:
            print(f"  第{n}章改后字数异常（{c1}→{c2}），跳过")
            continue
        nv.p.write(nv.p.chapter_path(n), t2)
        print(f"  ✓ 第{n}章已改（{c1}→{c2} 字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
