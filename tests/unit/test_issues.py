"""状态由程序派生，未知错误默认拦停。

这几条锁住的是这个仓库最贵的一课：**「✓」原来是无条件打的**。
它不表示这一章没出问题，只表示没抛到最外层。
"""
from __future__ import annotations

import pytest

from server import issues as I


def test_没问题才算完成():
    assert I.Ledger().status() == I.STATUS_DONE


def test_未登记的错误一律拦停():
    """整个机制的关键就在这一条。

    未知情况默认继续（我们原来）和未知情况默认拦停（现在），差别只在这里。
    新故障第一次出现就会把这一章标成需人工，逼人看一眼 —— 这个摩擦是故意的。
    """
    L = I.Ledger()
    item = L.record("这个码从来没登记过", "天知道出了什么事")
    assert item["unregistered"] is True
    assert item["severity"] == I.MUST
    assert L.status() == I.STATUS_NEEDS_USER
    assert L.status() != I.STATUS_DONE          # 永远不可能报「完成」


def test_吞掉的代码bug会把这一章判成需人工():
    """实测原型：自检里 `cons` 未定义，被 except 吞成一行

        系统自检失败(不阻塞写作): name 'cons' is not defined

    跟「模型这次没答好」长得一模一样，于是藏了几小时。自检因此看不见不可逆
    事实与红线，判不了「约束失效」那一类。
    """
    L = I.Ledger()
    item = L.record("step_skipped", "自检",
                    NameError("name 'cons' is not defined"))
    assert item["code"] == "step_skipped"       # 调用方报的码
    assert item["severity"] == I.MUST           # 但被抬档了
    assert "cons" in item["detail"]
    assert "trace" in item
    assert L.status() == I.STATUS_NEEDS_USER


@pytest.mark.parametrize("exc", [
    NameError("x"), AttributeError("x"), TypeError("x"), KeyError("x")])
def test_四类代码bug一律抬档(exc):
    L = I.Ledger()
    assert L.record("step_skipped", "", exc)["severity"] == I.MUST


def test_模型没答好只算部分完成():
    """模型这次没答好 ≠ 我们写错了。前者跳过就跳过，后者必须改代码。"""
    L = I.Ledger()
    L.record("step_skipped", "标题选优跳过", ValueError("模型返回空"))
    assert L.status() == I.STATUS_PARTIAL


def test_程序已兜住的不影响状态():
    L = I.Ledger()
    L.record("continuation", "撞上输出上限，已续写")
    L.record("gate_discard", "剔掉了误判的禁用词")
    assert L.status() == I.STATUS_DONE
    assert "已自动兜住 2" in L.brief()


def test_一个字都没写出来算失败不算需人工():
    L = I.Ledger()
    L.record("chapter_write_failed", "正文为空")
    assert L.status(wrote_text=False) == I.STATUS_FAILED
    assert L.status(wrote_text=True) == I.STATUS_NEEDS_USER


def test_未登记的排在最前面():
    """日志只印前几条，未登记的最值得看一眼，不能被一堆已知项挤掉。"""
    L = I.Ledger()
    for i in range(6):
        L.record("continuation", f"第{i}次续写")
    L.record("谁也没见过的码", "！")
    assert "未登记" in L.lines()[0]


def test_每条登记都写清楚了为什么():
    """登记的意思是「我们知道它为什么会发生、知道它要不要紧」。

    只填个 severity 不填 why，等于把未知错误伪装成已知错误 —— 那比不登记更糟。
    """
    for code, spec in I.CATALOG.items():
        assert spec.get("severity") in (I.MUST, I.CONFIRM, I.AUTO), code
        assert spec.get("title"), code
        assert spec.get("why"), f"{code} 没写为什么会发生"
        if spec["severity"] != I.AUTO:
            assert spec.get("next_action"), f"{code} 没写该怎么办"
    assert I.FALLBACK["severity"] == I.MUST, "兜底必须是拦停，这是整条机制的根"


# ───────── 模型不许打分：分数与放行由程序算 ─────────

def test_模型给的分不再是闸门():
    """原来是让模型给 0-100、取平均、拿平均卡门 —— 等于让被考的人自己填分。

    同一篇稿子重评一遍能差十几分，而且它可以「问题照列、分照给高」，
    两者之间没有任何约束。
    """
    from server.critic import parse
    # 模型把每一维都打了 95，却列出两条严重问题
    raw = ('{"scores":{"人物":95,"设定":95},"issues":['
           '{"dim":"设定","severity":"high","what":"甲又活了",'
           ' "evidence":"甲站起身"},'
           '{"dim":"人物","severity":"high","what":"性格突变",'
           ' "evidence":"他忽然温柔起来"}]}')
    d = parse(raw)
    assert d["dim_avg"] == 95           # 模型自己给的分
    assert d["overall"] == 70           # 程序按扣分表算: 100 - 15*2
    assert d["blocking"] is True        # 两条严重 → 拦下
    assert "严重问题 2 条" in "；".join(d["blocking_why"])


def test_没有正文原句为证的问题不扣分():
    """这条逼着模型给证据：空口说的问题不算数。"""
    from server.critic import parse
    raw = ('{"scores":{"a":80},"issues":['
           '{"dim":"a","severity":"high","what":"感觉不太行","evidence":""}]}')
    d = parse(raw)
    assert d["claimed"] == 1 and d["evidenced"] == 0
    assert d["overall"] == 100 and d["blocking"] is False


def test_与已确立事实冲突一票拦下():
    """冲突一旦固化就一路错到底，是最贵的一类。"""
    from server.critic import parse
    raw = ('{"scores":{"a":90},"issues":[],'
           '"contradictions":[{"fact":"甲已死","evidence":"甲开口说道"}]}')
    d = parse(raw)
    assert d["blocking"] is True
    assert d["overall"] == 65           # 100 - 35


def test_评审合并后必须用程序的判定而不是维度平均():
    """「算了但没人用」比「没算」更难发现 —— 字段就在那儿，看起来是全的。

    实测第 8 章：overall=88 正好是 15 个维度的算术平均（1318/15），而
    blocking/penalty/severity_counts 全是 None。因为 step_critique 多遍合并
    之后又写了一句 merged["overall"] = 各维平均，把每一遍 parse() 里
    judge() 算好的结果整个盖掉了。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.step_critique)
    assert "critic_mod.judge(merged)" in src, \
        "多遍合并之后没有再过一次程序判定，judge() 的结果会被平均分盖掉"
    assert 'merged["overall"] = round(sum(vals) / len(vals))' not in src, \
        "overall 又被写回成维度平均了 —— 那就是让被考的人自己填分"


# ───────── 开局落点：把不可判定的语义问题换成可判定的字面问题 ─────────

def test_开局落点必须逐字抄进细纲(tmp_path, monkeypatch):
    """实测落点四「妖姬一吻夺元阳」整条丢了，换成了自创的「瓷片挟持老太监」。

    丢的不是一场戏 —— 夺元阳是他**成为鼎炉的原因**。全书 0 次「元阳」，
    主角却从第 3 章起就被叫鼎炉：果还在，因没了。

    opening_beats() 早就把「一条一章，不许合并也不许跳过」写进提示词了，
    可没有任何程序在查 —— 第九次栽在同一件事上。

    语义覆盖判不了（试过名物匹配：第 1 章明明写全了纽约/FBI/加特林/玉佩，
    命中率却只算出 21%，因为切出来的 2-4 字组大半是「一枚」「一枚古」这类
    碎片）。但**逐字照抄**判得了，跟但是链那条是同一个办法。
    """
    from server.orchestrator import Novelist

    class _P:
        meta = {"fields": {"premise":
                           "【开局落点（前六章骨架，按此写）】\n"
                           "一 纽约曼哈顿，被小弟出卖，FBI 围楼。\n"
                           "二 玉佩崩碎，光华卷走他。落在迷离林。\n"
                           "【基调】要爽。\n"}}

    nv = Novelist.__new__(Novelist)
    nv.p = _P()
    assert nv.beat_for(1).startswith("纽约曼哈顿")
    assert nv.beat_for(2).startswith("玉佩崩碎")
    assert nv.beat_for(9) == ""                      # 超出范围不管

    # 没抄 → 报缺
    assert nv.beat_missed(1, "第1章 主角很强\n一句话：他打赢了")
    # 抄了 → 放行
    good = "第1章 突围\n开局落点：" + nv.beat_for(1) + "\n一句话：…"
    assert nv.beat_missed(1, good) == []
    # 改写了也算没抄 —— 「原样」就是原样
    bad = "第1章\n开局落点：主角在美国某大城市被警方包围\n"
    assert nv.beat_missed(1, bad)
    # 超出落点范围的章节不受这条管
    assert nv.beat_missed(9, "随便写的") == []


def test_开局落点这一栏必须进格式表():
    """只在正文里叮嘱不管用 —— 模型是照着格式表逐栏填的。

    实测：提示词里把要抄的原文都逐条列出来了、还写明「程序会逐字核对」，
    三稿细纲**一个都没写那一行**，三份并列 -135 分，三选一退化成取第一稿。
    因为「开局落点」不在字段契约里，模型填完表上的十栏就收工了。
    """
    from server.prompt_compiler import outline_format_block
    blk = outline_format_block(6, 0, None, "开局落点：（照抄作者原文）")
    lines = blk.split("\n")
    assert lines[0].startswith("第N章")
    assert lines[1].startswith("开局落点："), "额外栏要紧跟标题行，不能垫在最后"
    assert "一句话" in blk                      # 原有的栏一个不少
    # 不传就不该冒出来
    assert "开局落点" not in outline_format_block(6, 0, None)


def test_专名不许被换成同义中文():
    """种子写「FBI 围楼」，两版都写成了「联邦调查局」。

    题材包那条「不许用现代思维嘲笑古人」被模型泛化成「整本书别提现代词」——
    禁的是姿态，不是词；何况这几章发生在主角穿越之前的现实世界。
    """
    from server.orchestrator import Novelist

    class _P:
        meta = {"fields": {"premise":
                           "【开局落点】\n一 纽约曼哈顿，FBI 围楼，CIA 也来了。\n"}}

    nv = Novelist.__new__(Novelist)
    nv.p = _P()
    assert nv.beat_names_missing(1, "联邦调查局破门而入")          # 换掉了 → 报
    assert nv.beat_names_missing(1, "FBI 破门")                    # CIA 还缺
    assert nv.beat_names_missing(1, "FBI 与 CIA 同时破门") == []   # 都在 → 放行
    assert nv.beat_names_missing(9, "随便写") == []                # 无落点的章不管


def test_开局落点那一行由程序钉进去():
    """让模型照抄，它做不到一字不改。

    实测第 1 章抄出来的是「…被小弟出卖，**联邦调查局**围楼」，
    而种子写的是「FBI 围楼」—— 它一边抄一边把专名换成中文同义说法。
    这一行是纯拷贝，就该由程序写：能由程序定死的，不要问模型。
    """
    from server.orchestrator import Novelist

    class _P:
        meta = {"fields": {"premise":
                           "【开局落点】\n一 纽约曼哈顿，FBI 围楼。\n二 玉佩崩碎。\n"}}

    nv = Novelist.__new__(Novelist)
    nv.p = _P()

    # 模型改写了 → 程序换回原文
    got = nv.pin_beat(1, "第1章 突围\n开局落点：纽约，联邦调查局围楼。\n一句话：…")
    assert "FBI" in got and "联邦调查局" not in got
    assert nv.beat_missed(1, got) == []

    # 模型压根没写 → 程序插在标题行后面
    got = nv.pin_beat(1, "第1章 突围\n一句话：…")
    lines = got.split("\n")
    assert lines[1].startswith("开局落点：") and "FBI" in lines[1]

    # 写了两遍 → 只留一行
    got = nv.pin_beat(1, "第1章\n开局落点：甲\n一句话：…\n开局落点：乙")
    assert sum(1 for x in got.split("\n") if x.startswith("开局落点")) == 1

    # 没有落点的章不动它
    body = "第9章 别的事\n一句话：…"
    assert nv.pin_beat(9, body) == body


def test_评审判定该拦就不许报完成():
    """blocking 算出来了却没人用 —— 今天第三次栽在同一件事上。

    实测第 4 章：blocking=True、扣 65 分（3 严重 + 3 中等 + 1 轻微）、
    程序算分 35，而**模型自己给的维度平均是 77**。状态却是「完成」、
    问题清单空的，照样打勾过去 —— 因为 blocking 只用来触发一次重写，
    从没接进问题台账。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.step_chapter)
    assert 'self.iss.record("critique_blocking"' in src, \
        "评审判定该拦却没记进问题台账，这一章会照样报「完成」"
    from server import issues as I
    assert I.CATALOG["critique_blocking"]["severity"] == I.MUST

    L = I.Ledger()
    L.record("critique_blocking", "严重问题 3 条；扣分合计 65")
    assert L.status() == I.STATUS_NEEDS_USER


def test_重写不许把扩写的成果抹掉():
    """实测第 5 章：扩写 2238 → 3163 达标，低分重写一把砍回 2070。

    旧守卫的门槛是 max(目标×0.6, 原稿×0.6) = 1897，2070 高于它，于是
    「分数高」就被采纳了，最后落盘 2070 字、低于下限 2400 —— 扩写白做。
    旧守卫防的是腰斩（实测过 1231 字那种），防不住这种「掉回线下」。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.step_chapter)
    assert "undo_expand" in src, "没有防「重写把字数打回下限以下」的守卫"
    assert 'a["stats"]["cn"] >= floor > _cn2' in src, \
        "判据要写成「原稿达标 且 重写掉到下限以下」"
    assert "not undo_expand" in src, "守卫算出来了但没接进采纳条件"


def test_异常里的主机名不许落盘():
    """网关地址是用户的私有基础设施，不该出现在任何文件里。

    实测一次网关读超时，异常文本被原样记进两个 audit/*.json：
        ConnectionError: HTTPConnectionPool(host='xxx.xxx.com', port=8000)
    projects/ 和 reports/ 都在 .gitignore 里，没有泄漏到版本库；但这类东西
    一旦进了文件，后面每一次归档、打包、贴日志都可能把它带出去。
    在**写入的那一刻**就去掉，比事后到处去搜可靠。
    """
    from server.issues import Ledger, redact

    raw = "HTTPConnectionPool(host='gw.example.com', port=8000): Read timed out"
    assert "example.com" not in redact(raw)
    assert "<host>" in redact(raw)
    assert "Read timed out" in redact(raw)      # 错误类型要留着，不然没法查

    L = Ledger()
    try:
        raise ConnectionError(raw)
    except Exception as e:
        it = L.record("step_skipped", f"评审失败 {raw}", e)
    assert "example.com" not in it["detail"]
    assert "example.com" not in it["trace"]


def test_脱敏不误伤中文和普通文本():
    from server.issues import redact
    for s in ("第3章扩写第1轮 2238 → 3163 字（达标）",
              "NameError: name 'cons' is not defined",
              "评审第10章 26分 问题7 矛盾1"):
        assert redact(s) == s, f"误伤了：{s} → {redact(s)}"


def test_指标打分要有梯度而不是命中与否():
    """原来是「在区间内 +2，不在就 0 分」—— 没有梯度。

    于是「对白占比 0.017」和「0.15」得分完全一样（下限 0.16），三选一挑不出
    更接近区间的那一稿。实测这本书对白占比从第 1 章的 21% 一路塌到第 10 章的
    1.7%，而每一稿都只是「没命中」，打分器对这个塌方一无所知。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.draft_score)
    assert "hit * 2.0 + part" in src, "指标打分还是「命中与否」，没有梯度"
    assert "_metric_blowout" in src, "塌方没有被记下来"
    # 梯度的形状：差半个区间还能拿分，差两倍区间宽就算塌方
    assert "2.0 - min(2.0, off)" in src
    assert "off >= 2.0" in src

    from server import issues as I
    assert I.CATALOG["metric_blowout"]["severity"] == I.CONFIRM
    src2 = inspect.getsource(o.Novelist.step_chapter)
    assert 'self.iss.record("metric_blowout"' in src2, \
        "塌方算出来了却没接进问题台账 —— 又是「算了但没人用」"


def test_返修之后必须复审并按结果决定出队():
    """「修完不复审」—— 我在别人仓库里批评过的毛病，我们自己也有。

    实测第 12 章：[repair] 第12章 -> 70（修到 70 分），而 audit 里还写着
    「需人工」、队列里也没出队。因为 rewrite_chapter 用一份**不含评审、
    不含问题台账**的裸 audit 直接覆盖了原来的，返修之后「这一章现在到底
    怎么样」没有任何人知道：修好了还一直报警，或者没修好却被当成修好了。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.rewrite_chapter)
    assert "self.step_critique(n, new)" in src, "返修之后没有复审"
    assert 'a["ledger"] = self.iss.to_dict()' in src, "返修之后没有重建问题台账"
    assert '"status": self.iss.status()' in src, "返修结果没把状态带回去"

    from pathlib import Path
    sh = (Path(o.__file__).resolve().parent.parent
          / "scripts" / "run_until.sh").read_text(encoding="utf-8")
    # 注意别把 `rest = list(q[2:])` 也判成违规 —— 那是起点, 不是无条件丢弃。
    # 违规的是**直接把 q[2:] 写回文件**。
    assert "write('repair_queue.json', json.dumps(q[2:]" not in sh, \
        "还在无条件丢掉前两条 —— 没修好的也当修好了"
    assert "json.dumps(rest" in sh, "出队结果要按复审状态重算，不是照搬 q[2:]"
    assert "tries" in sh and "不再自动重试" in sh, \
        "要么会无限重修同一章，要么没有次数上限"


def test_台账窗口不许把开篇的根基事实挤掉():
    """原来是 cn[-40:] —— 纯取最近的，台账一长最老的那批最先掉出去。

    而它们恰恰是最容易被后文推翻的：开篇定死的东西（主角怎么来的、什么
    东西毁了、谁死了）会被反复回忆、反复提及；中段那些「某人答应某事」的
    临时约定反而没人会去推翻它。

    实测第 10、16 章连着两次写出「玉佩残片」，而第 1 章白纸黑字写着它
    彻底崩碎消失。当时 canon 才 24 条、窗口还没开始丢东西，但按每章两条的
    速度第 30 章就会越过 40 —— 那时这条就会真的掉出去。
    """
    from server.orchestrator import Novelist
    nv = Novelist.__new__(Novelist)

    cn = [{"chapter": 1, "subject": "甲", "fact": "玉佩彻底崩碎", "kind": "destroy"},
          {"chapter": 2, "subject": "乙", "fact": "某人死了", "kind": "death"}]
    cn += [{"chapter": i, "subject": "丙", "fact": f"临时约定{i}", "kind": "other"}
           for i in range(3, 80)]

    w = nv.canon_window(cn, cap=40)
    assert len(w) == 40
    assert any("玉佩" in c["fact"] for c in w), "开篇的毁灭事实被挤掉了"
    assert any(c["kind"] == "death" for c in w), "死亡事实被挤掉了"
    assert any(c["chapter"] >= 75 for c in w), "最近的事实被挤掉了"
    assert [c["chapter"] for c in w] == sorted(c["chapter"] for c in w), "要按章号排"

    # 没超过上限就原样返回，不做任何取舍
    assert nv.canon_window(cn[:20], cap=40) == cn[:20]

    # 硬事实多到超过上限时，一条都不许丢
    many = [{"chapter": i, "subject": "x", "fact": f"毁了{i}", "kind": "destroy"}
            for i in range(1, 60)]
    w2 = nv.canon_window(many, cap=40)
    assert len(w2) == 59, "毁灭/死亡这类硬事实不许因为超预算被丢"


def test_脱敏不许碰文件名也不许改坏JSON():
    """实测把两个 audit/*.json 改坏过。

    正则 (?:[A-Za-z0-9-]+\\.)+[A-Za-z]{2,24} 会匹配上「\\nserver.py」里的
    「nserver.py」，替换之后成了「\\<host>」—— 非法转义，两个文件直接读不出来。
    而且栈里全是 orchestrator.py / settings.yaml，一律当主机名换掉，栈就看不懂了。
    """
    import json
    from server.issues import redact

    assert "<host>" in redact("HTTPConnectionPool(host='gw.example.com', port=8000)")
    assert "<host>" in redact("POST https://api.foo.co/v1/chat failed")

    for keep in ('File "/opt/x/server/orchestrator.py", line 5859',
                 "config/settings.yaml 读取失败",
                 "chapter_outlines.json 缺字段",
                 "评审第10章 26分 问题7 矛盾1"):
        assert redact(keep) == keep, f"误伤了：{keep} → {redact(keep)}"

    # 脱敏之后仍然是合法 JSON
    raw = json.dumps({"trace": "a\\nserver.py b\nhost=gw.example.com"},
                     ensure_ascii=False)
    out = redact(raw)
    json.loads(out)                      # 读得出来才算数
    assert "example.com" not in out


def test_到期伏笔要有自己的一栏和分数():
    """三档规矩的第三次验证。

    · 第 1 档（只写在提示词里）：日志连着报「本批细纲一条都没碰」
    · 查 trace 确认那段话**确实在** 5 万字的提示词里 —— 模型看见了没照做
    · 第 2 档（进格式表）：开局落点上验证过 —— 插进表之后五章全写了
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.step_chapter_outlines)
    assert "回收伏笔：" in src, "到期伏笔没有自己的栏位，模型填完十栏就收工"
    assert "self.overdue_foreshadows(start)" in src
    # 格式表那一栏和约束里点名的必须是同一份清单，否则模型只会填「无」
    assert src.count("self.overdue_foreshadows(start)") >= 1
    assert "pend = " in src, "未收伏笔清单没定义"

    sc = inspect.getsource(o.Novelist.outline_score)
    assert "回收伏笔" in sc, "有栏没分 —— 模型会全填「无」"
    assert "-20.0" in sc, "本批一条都没收要重罚"


def test_伏笔匹配按前15字而不是全等():
    """模型抄的是前 15 字，不会一字不差抄完整条。"""
    from server.orchestrator import _norm_key
    want = "远处传来清脆的笑声，并非野兽，而是女人，笑声中透着诡异的甜香"

    def hit(v):
        got = _norm_key(v)
        w = _norm_key(want[:15])
        return bool(w and w in got) or bool(got[:10] and got[:10] in _norm_key(want))

    assert hit("远处传来清脆的笑声，并非野兽")          # 抄了前 15 字
    assert not hit("狐媚交出了妖丹")                    # 不相干的不算
    assert _norm_key("远处，传来。清脆") == "远处传来清脆"   # 标点不影响


def test_元叙述词汇类只在叙述里算_台词是戏内的():
    """实测误报：角色让人伪造文书时说「现在，写个草稿」——「草稿」是戏内的。

    词汇类（草稿/大纲/细纲…）先剥引号再扫；章节回指类（第N章中/如前所述）
    在哪儿都是泄漏，台词里出现更荒唐，照报。
    """
    from server.tics import meta_leaks
    assert meta_leaks('他递来纸笔，“现在，写个草稿。字要大。”') == []
    assert meta_leaks('这一段只是草稿，后面还要修订。')
    assert meta_leaks('他说：“如前所述，前文提到过。”')


# ───────── 状态型「事实」不许进不可逆台账 + 书级熔断 ─────────

def test_状态型事实不入台账():
    """实测的死亡螺旋：第 94 章把「双腿神经坏死，严禁站立」当不可逆事实入账，
    之后主角每站起来一次就是冲突扣 35 分，章章被拦；台账越长拦得越多，
    程序分从 41 崩到 21，84/120 章需人工。
    错收一条毒 26 章，漏收一条只少一道闸 —— 代价不对称，宁可拒。
    """
    from server.critic import merge_canon
    poison = [
        {"subject": "甲", "fact": "双腿神经坏死，严禁站立、行走", "kind": "other"},
        {"subject": "甲", "fact": "仅余现银二贯九百文", "kind": "other"},
        {"subject": "甲", "fact": "伤势沉重暂时无法出门", "kind": "other"},
    ]
    for f in poison:
        _, n = merge_canon([], [f], 5)
        assert n == 0, f"状态型事实进账了：{f['fact']}"
    real = [
        {"subject": "王婆", "fact": "死于狮子楼枪下", "kind": "death"},
        {"subject": "文书", "fact": "验尸文书已被当众烧毁", "kind": "destroy"},
        {"subject": "潘金莲", "fact": "背叛西门庆投靠赵德昭", "kind": "betray"},
    ]
    for f in real:
        _, n = merge_canon([], [f], 5)
        assert n == 1, f"真不可逆的被误拒：{f['fact']}"


def test_书级熔断三层都接了():
    """逐章「需人工」只是记一笔+进返修，流水线照冲 —— 实测三振第 94 章就
    报了「事实本身存疑」，之后还继续写了 26 章，全建在坏状态上。
    连片需人工不是 N 个独立问题，是同一个根子在批量产废品。
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    rn = (root / "run_novel.py").read_text(encoding="utf-8")
    assert "bad_run" in rn and "HALT.md" in rn and "sys.exit(4)" in rn, \
        "run_novel 没有熔断"
    ru = (root / "scripts/run_until.sh").read_text(encoding="utf-8")
    assert '"$RC" -eq 4' in ru, "run_until 会把熔断当失败重试 —— 重试只会接着烧"
    gd = (root / "scripts/guard.sh").read_text(encoding="utf-8")
    assert "HALT.md" in gd, "哨兵会把刚熔断的长跑再点着，熔断等于没有"


def test_硬账只对盘点句不对动作量():
    """「开了一发」是动作量不是库存 —— 实测被当成库存报了 120→1 跳变。

    只有「还剩/弹夹里/数了数 N 发」这种盘点句才对账；上下文词按单位扩同义
    （第 10 章「拉开弹夹。十二发。」附近没有「沙漠之鹰」三个字，物名匹配漏掉）。
    """
    from server.accounts import check_prose
    led = {"底牌": {"item": "底牌", "desc": "沙漠之鹰，120发", "value": 119,
                    "unit": "发", "chapter": 5, "log": []}}
    assert check_prose(led, "他拉开弹夹。数了数，还剩十二发。")
    assert check_prose(led, "他拉开弹夹。十二发。")          # 弹夹本身就是盘点语境
    assert check_prose(led, "他抬手开了一发，枪声如雷。") == []
    assert check_prose(led, "弹夹里还剩一百一十九发。") == []
    assert check_prose(led, "院里放了十二发炮仗。") == []


# ───────── 回炉发散：返修的第三档 ─────────

def test_回滚把每类状态都截干净():
    """清不干净等于没回滚 —— 残留的旧事实会立刻把新写的章再拦一遍。"""
    from server import rollback as rb

    canon = [{"chapter": 0, "fact": "种子事实"}, {"chapter": 3, "fact": "a"},
             {"chapter": 8, "fact": "坏段里的"}]
    assert [c["chapter"] for c in rb.prune_canon(canon, 4)] == [0, 3]

    st = rb.prune_state({"done": [1, 2, 3, 8, 9], "current": 9,
                         "summaries": {"2": "x", "8": "y", "roles": "z"}}, 4)
    assert st["done"] == [1, 2, 3] and st["current"] == 3
    assert "8" not in st["summaries"] and "roles" in st["summaries"]

    co = rb.prune_outlines({"3": "留", "4": "删", "12": "删"}, 4)
    assert list(co) == ["3"]

    led = {"底牌": {"value": 12, "unit": "发", "chapter": 10,
                    "log": [{"chapter": 0, "value": 120},
                            {"chapter": 3, "value": 119},
                            {"chapter": 10, "value": 12}]}}
    out = rb.replay_accounts(led, 4)
    assert out["底牌"]["value"] == 119, "硬账要按流水回放到坏段之前"
    assert out["底牌"]["chapter"] == 3

    q = rb.prune_queue([{"ch": 2}, {"ch": 8}], 4)
    assert [x["ch"] for x in q] == [2]


def test_自愈三级递进都在():
    """polish → replace → reroll(回炉发散) → MANUAL。缺一级就退化成傻等。"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    heal = (root / "scripts/heal_halt.py").read_text(encoding="utf-8")
    assert "rollback_to" in heal and "MAX_REROLL_PER_SEGMENT" in heal
    assert "MANUAL" in heal, "没有终点档, 会无限回炉烧钱"
    # 止损必须看**方向**不是次数: 实测一夜两轮回炉 n0 7→14、章数 7→27,
    # 是磨着前进; 按次数一刀切会把正常前进误判成乒乓
    assert "ping_pong" in heal and "hw" in heal, \
        "止损退回按次数计了 —— 会把有进度的回炉误判成乒乓"
    # 止损唯一判据是高水位单调 —— 回到同一坏点但盖得更高不算乒乓
    assert "hw <= prev_hw" in heal, \
        "止损又按回滚点判了 —— 同坑盖高会被误判(05:31 教训)"
    gd = (root / "scripts/guard.sh").read_text(encoding="utf-8")
    assert "heal_halt.py" in gd, "守护没接自愈, 熔断还是纯等人"
    assert "grep -q MANUAL" in gd, "MANUAL 档没被尊重, 会绕过人工"


def test_台账与硬账不许时间倒挂():
    """返修第 21 章时，「第 27 章确立：烟雾弹已消耗最后两枚」被拿来拦
    第 21 章正常使用烟雾弹 —— 未来的事实穿越回来当判据，中段章的返修
    永远过不了，MANUAL 就是这么被逼出来的。
    """
    import inspect
    from server import orchestrator as o
    assert hasattr(o.Novelist, "canon_upto") and hasattr(o.Novelist, "accounts_at")
    crit_src = inspect.getsource(o.Novelist.step_critique)
    assert "canon_upto(n)" in crit_src, "评审还在拿全书台账判老章"
    ctx_src = inspect.getsource(o.Novelist.build_context)
    assert "canon_upto(n)" in ctx_src and "accounts_at(n)" in ctx_src
    ch_src = inspect.getsource(o.Novelist.step_chapter)
    assert "accounts_at(n)" in ch_src, "硬账对数还在拿终局值对中段章"
    assert "_frontier" in crit_src, "返修老章会把消耗重复入账"

    # 纯函数验证
    nv = o.Novelist.__new__(o.Novelist)
    nv.canon = lambda: [{"chapter": 0, "fact": "种子"},
                        {"chapter": 21, "fact": "用了烟雾弹"},
                        {"chapter": 27, "fact": "烟雾弹耗尽"}]
    got = [c["chapter"] for c in o.Novelist.canon_upto(nv, 21)]
    assert got == [0, 21], f"第21章视角不该看见第27章: {got}"


def test_红线词回流_点名才学_语域不学():
    """评审点名「枪管」「膛线」→ 学进黑名单，下一章三选一就地淘汰。
    评审只说「现代语域/战略术语」没点名 → 学 0 个（语域问题归修补与纪律，
    乱进黑名单会误杀正常词——「蔡德茂」事故的教训）。人物名恒不学。
    """
    from server.orchestrator import Novelist
    nv = Novelist.__new__(Novelist)
    nv.roster = lambda: [{"name": "武松"}]
    nv._log = lambda m: None
    store = {}
    class _P:
        def _load(self, f, d): return store.get(f, d)
        def write(self, f, t):
            import json; store[f] = json.loads(t)
    nv.p = _P()

    crit = {"issues": [
        {"what": "严重违反时代红线，NPC使用现代枪械术语「枪管」「膛线」",
         "evidence": "他摸着枪管说膛线磨平了"},
        {"what": "旁白使用现代战略术语，违反认知红线",
         "evidence": "基于一种冷酷的战略判断"},          # 没点名 → 不学
        {"what": "时代红线：出现「武松」？", "evidence": "武松说"},  # 人物名 → 不学
    ]}
    text = "他摸着枪管说膛线磨平了。武松皱眉。"
    n = Novelist.learn_redline_terms(nv, 5, text, crit)
    got = store["rules.json"]["forbidden_terms"]
    assert n == 2 and "枪管" in got and "膛线" in got
    assert "武松" not in got and "战略判断" not in got
