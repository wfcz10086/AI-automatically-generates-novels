#!/usr/bin/env python3
"""正文提示词 A/B：数值指标版 vs 结构指令版。

跑分台第一轮测出：对白 0.11（原作 0.25）、反讽旁白 2（原作 20）、独立反问句 4（原作 17）。
猜想：数值目标（「对白占比 20~28%」「反讽 2~4 处」）在写作时不可执行——模型数不了数。
把每一个数值目标翻译成【结构指令】或【起手句式】，应该能大幅缩小差距。
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, importlib.util, json, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("pb", ROOT / "scripts/prompt_bench.py")
pb = importlib.util.module_from_spec(_s); _s.loader.exec_module(pb)

OUTLINE = """标题：宝没了，往后圣旨怎么发？
视角：司礼监一个捧玺八年的小宦官。他今天最怕的是「玺不用了，咱家算什么」；他想要的是保住这碗手续饭。
承接：三更梆子刚过，值房被叫起来捧玺进乾清宫。
动作：皇帝当众烧掉传国玉玺，下诏「建文以来凡用宝之诏、部议之文、兵符之信一概疑似伪作，停验」。
误读：司礼监老宦凭「武选司那一柜空白诏纸没烧」→ 读成「这是定向清算司礼监，要把诏书出版权夺给武选司」
      → 他连夜抢在武选司之前，要把「重验」的解释权攥回内廷。
后果：内廷与兵部为「谁来重验」开战，皇帝本想冻结所有接口，先冻死的是自己人。
账目：名分 从「靠玉玺发号施令的天子」→ 到「只认御前亲见的天子」。
代价：司礼监几十年的家当一炉火烧光；主角从此失去遥控能力，一切命令必须人到。不可逆。
解说：大明发一道诏走三道关——司礼监批红、尚宝司用宝、驿传房发递。玺一烧，第二关空了，
      头一关废了，可第三关还在，盖着司礼监关防的空白诏纸也还在。
重场：玺落进炭火，铜钮先红后亮然后塌下去；老宦的脸色三变。
章末：老宦低声说「皇上，墨该磨了。停验的诏，总得写下来传示各处，是不是？」——他要写的根本不是皇帝口述的那道。"""


def p_numeric(outline: str) -> str:
    """A 组：现在的写法，数值目标。"""
    return pb.p_draft(outline)


def p_structural(outline: str) -> str:
    """B 组：把每个数值翻译成结构指令 / 起手句式。"""
    return f"""本章细纲：
{outline}

── 调子 ──
{pb.TONE}

下面是同类作品里几段真东西。不要抄内容，听它的嗓子：

【外部视角开场】
{pb.pick('外部视角开场')}

【反讽旁白·把正经话戳破】
{pb.pick('反讽旁白·把正经话戳破', 5)}

【算自己的账】
{pb.pick('算自己的账')}

【制度解说·把规矩讲透】
{pb.pick('制度解说·把规矩讲透', 1)}

── 本章必须包含的结构件（逐条落实，不是建议）──

▍对话
　本章至少三场**成形的对话**，每场至少四个来回（甲说、乙答、甲再说、乙再答）。
　对话要占掉本章一半以上的篇幅。不许用「两人商议了一番」这种概括代替对话。
　每一场对话里，至少有一个人是在**替自己讨价还价**，不是在传达信息。

▍反讽旁白（这是本调子的呼吸，缺了就不是这个味）
　每当你写完一段正经的、庄严的、悲壮的、或者讲道理的内容，
　**下一段必须另起一行**，用下面任一个起手，把刚才那段话戳破：
　　「还别说，……」「说穿了，……」「可问题是，……」「这哪儿是……分明是……」
　　「用脚后跟想也知道，……」「想想都……」「好像很少有人……」
　本章至少要这样戳破**四次**。戳破的时候要用大白话，不要讲道理。

▍角色心里的问句
　每当一个角色**不明白眼前发生了什么**，就**单独起一段**，把他心里那句问话原样写出来。
　例：　「这是怎么回事？」　「他这是要干什么？」　「难道大王真疯了？」　「宝没了咱家算什么？」
　不许把这些问句揉进叙述句里。本章至少要有**五个这样的独立问句段**。

▍别人算错
　本章至少两次，让某个人**推断错**。写法固定成三步，缺一不可：
　　第一步：他看见了什么（具体的，一个动作、一句话、一件东西）
　　第二步：他凭什么这么想——他的处境、他吃过的亏、他此刻正在怕的事
　　第三步：他因此做了什么
　用「他还以为…」「他多半觉得…」「一定是…」「难道是…」「看这意思…」起头。
　**他必须猜错**，而且错得比真相更大更凶险。

▍每个人的账
　配角开口之前，先用一两句让读者知道**这句话对他自己有什么好处或坏处**。
　本章至少有三个人被这样交代过。

▍制度解说
　把细纲里那条规矩讲透，讲成读者能复述的程度（多少道关、谁管哪一关、哪一关废了）。
　**讲完立刻另起一段，用大白话戳破它。**

── 形制 ──
段落短。平均一段两三行（四五十字），最长的一段也不要超过一百五十字。
正文 2600~3200 字，写满，不许提前收尾。
开场用视角人的眼睛，前 300 字里主角可以不出现。
不许替主角辩护——叙述者可以笑他，不许夸他。
章末不许是感想、总结、决心。
不许出现任何英文单词，不许用 markdown 标记（#、**、---、编号列表）。

直接输出正文。"""


def run(model, rnd, out):
    res = []
    for name, fn in [("A_数值指标版", p_numeric), ("B_结构指令版", p_structural)]:
        t = time.time()
        txt = pb.call(model, fn(OUTLINE), 9000, 0.92)
        d = out / f"r{rnd}"; d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(txt, encoding="utf-8")
        m = pb.measure(txt)
        res.append({"组": name, "轮": rnd, "指标": m})
        print(f"  [{model} r{rnd}] {name}: {m['字数']}字 对白{m['对白占比']:.2f} "
              f"问{m['每千字问号']:.1f} 叹{m['每千字叹号']:.1f} 段均{m['段均字数']:.0f} "
              f"独反问{m['独立反问句']} 反讽{m['反讽旁白']} 误读{m['误读推断']} 解说{m['解说体']} "
              f"/{time.time()-t:.0f}s", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()
    out = ROOT / "reports/draft_ab" / f"{a.model}-{time.strftime('%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"A/B → {out}\n", flush=True)
    res = []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for f in cf.as_completed([ex.submit(run, a.model, r, out) for r in range(1, a.rounds + 1)]):
            res += f.result()
    (out / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{'组':16s} {'字数':>6s} {'对白':>6s} {'问/千':>6s} {'叹/千':>6s} {'段均':>6s} "
          f"{'独反问':>6s} {'反讽':>5s} {'误读':>5s} {'解说':>5s}")
    for name in ["A_数值指标版", "B_结构指令版"]:
        rs = [x["指标"] for x in res if x["组"] == name]
        if not rs: continue
        av = lambda k: sum(r[k] for r in rs) / len(rs)
        print(f"{name:16s} {av('字数'):6.0f} {av('对白占比'):6.2f} {av('每千字问号'):6.2f} "
              f"{av('每千字叹号'):6.2f} {av('段均字数'):6.1f} {av('独立反问句'):6.1f} "
              f"{av('反讽旁白'):5.1f} {av('误读推断'):5.1f} {av('解说体'):5.1f}")
    print(f"{'原作单章':16s} {2500:6d} {0.24:6.2f} {5.30:6.2f} {8.90:6.2f} {53.0:6.1f} "
          f"{1.7:6.1f} {2.0:5.1f} {0.6:5.1f} {1.6:5.1f}")


if __name__ == "__main__":
    main()
