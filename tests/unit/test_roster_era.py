"""角色花名册解析 + 时代锚点保护 —— 两个曾让 76 章带病生产的缺陷。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.orchestrator import Novelist, Project  # noqa: E402


class _P(Project):
    def __init__(self, tmp, chars, fields):
        self.dir = Path(tmp)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "characters.md").write_text(chars, encoding="utf-8")
        self.meta = {"title": "测试书", "history_mode": "real", "fields": fields,
                     "type_id": "novel", "genre_id": "lishi", "style_id": "qidian-lishi",
                     "target_chapters": 10, "target_words": 30000}
        self.state = {"done": []}
        from server.settings import load
        self.cfg = load()

    def read(self, name):
        f = self.dir / name
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def write(self, name, text):
        (self.dir / name).write_text(text, encoding="utf-8")

    def _load(self, name, default=None):
        f = self.dir / name
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default


CHARS = """### 1. 陈九四：抄写小吏/财政操盘手
**身份**：应天府户部老仓未入流抄写吏，长期在抄家名单边缘游走求生。
**性格三词**：谨慎、贪婪、务实

### 2. 朱元璋：大明开国皇帝
**身份**：本朝天子，猜忌深重，清洗从未停止过一天。
**性格三词**：多疑、酷烈、务实

### 8. 锦衣卫都指挥使：毛骧
**身份**：洪武朝锦衣卫首任都指挥使，胡惟庸案的具体执行者之一。
**性格三词**：冷硬、忠犬、狠辣
"""


def test_roster_parses_name_first_headers(tmp_path):
    """「### 1. 陈九四：抄写小吏」格式必须切得开。

    只认「### N. 姓名：X」时整个文件会被当成一张卡，花名册只剩 1 人，
    命名注册表随之失效（新配角可以和主角重名）、配角卡不再注入。
    """
    n = Novelist(_P(tmp_path / "a", CHARS, {}))
    names = [c["name"] for c in n.roster()]
    assert len(names) == 3, f"应解析出 3 个角色，实得 {names}"
    assert "陈九四" in names and "朱元璋" in names


def test_roster_handles_title_colon_name(tmp_path):
    """「### 8. 锦衣卫都指挥使：毛骧」是职务在前，取的名字必须是毛骧。"""
    n = Novelist(_P(tmp_path / "b", CHARS, {}))
    names = [c["name"] for c in n.roster()]
    assert "毛骧" in names, f"职务:姓名 格式取名错误，实得 {names}"
    assert "锦衣卫都指挥使" not in names


def test_era_words_never_banned(tmp_path):
    """本书自己的年号不能进禁用表。

    real 模式下守门模型会把「洪武二十三年」判成「真实朝代名 → 穿帮词」，
    落盘后全书每次提到自己的年代都算违规（实测拖掉全书体检 27 分）。
    """
    fields = {"background": "洪武二十三年起的真实大明", "premise": "洪武末年的杀局"}
    p = _P(tmp_path / "c", CHARS, fields)
    p.write("rules.json", json.dumps(
        {"forbidden_terms": ["洪武二十三年", "赛博朋克"], "tics": []},
        ensure_ascii=False))
    n = Novelist(p)
    assert "洪武二十三" in "".join(n.era_words())
    forb = n.learned_rules()["forbidden_terms"]
    assert "洪武二十三年" not in forb, "本书年号被误禁"
    assert "赛博朋克" in forb, "真穿帮词不该被连坐清掉"


def test_era_hint_is_never_truncated():
    """时代背景不能被截断。

    era_hint() 原来返回 background[:300]，而本书的时间轴从第 300 字才开始
    ——「政和五年（1115）」整段被截掉，年份抽不到，时代红线卡随之缺位，
    全书没有物价、俸禄、币制的锚。需要短文本的地方必须显式调 era_brief()。
    """
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist.era_hint)
    assert "[:300]" not in src and "[:200]" not in src, "era_hint 仍在截断"
    assert "def era_brief" in inspect.getsource(Novelist), "缺少显式的短版方法"
    # 全库不得再出现对设定字段的隐式截断
    root = Path(__file__).resolve().parents[2]
    import re as _re
    bad = []
    for f in list((root / "server").glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _re.search(r'(?:background|premise)"?\s*,?\s*""\)\s*\)?\[:\d+\]', line):
                bad.append(f"{f.name}:{i}")
    assert not bad, f"这些地方直接截断了设定字段：{bad}"


def test_era_extraction_handles_paren_years_and_reign_names():
    """「政和五年（1115）」两种写法都要认，且不许贪婪匹配出半截句子。"""
    from server.orchestrator import Novelist
    txt = ("政和五年（1115）起笔，宣和二年（1120）海上之盟。"
           "天下始知能打的不是朝廷。洪武二十三年另有一说。")
    cn = Novelist._ERA_CN.findall(txt)
    ad = Novelist._ERA_AD.findall(txt)
    assert "政和五年" in cn and "宣和二年" in cn and "洪武二十三年" in cn
    assert "1115" in ad and "1120" in ad
    assert not any("能打的" in w for w in cn), "贪婪匹配抓出了半截句子"


def test_book_assets_are_not_silently_truncated():
    """本书资产不许在生成链路里被硬截断。

    实测：分卷生成只喂了总纲前 1600 字，而终局、爽点节奏表、伏笔总账全在
    第 2600 字之后 —— 排卷时连「登基」两个字都没看见，全靠猜。细纲更狠，
    只看前 1200 字。128k 的上下文装得下三五千字的总纲，截断纯属旧习惯。
    """
    import re as _re
    root = Path(__file__).resolve().parents[2]
    bad = []
    pat = _re.compile(r'read\(\s*"(outline|world_bible|characters|era_card|basis|'
                      r'style_guide)\.md"\s*\)\s*\[:\d+\]')
    for f in list((root / "server").glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                bad.append(f"{f.name}:{i} {line.strip()[:60]}")
    assert not bad, "这些地方直接截断了本书资产：\n  " + "\n  ".join(bad)


def test_long_text_is_condensed_not_truncated():
    """超长文本要压缩，不是截断。

    截断等于把后半段扔掉：实测结构化抽取只读章节前 4000 字，8683 字的章
    后 4683 字里的伏笔、角色状态、资金变动全部丢失；FTS5 索引只存前 1500 字，
    「写到 300 章也能找回第 30 章埋的线」对后半章根本不成立。
    """
    from server.orchestrator import Novelist
    body = "开场第一句。\n\n" + "中间的段落。\n\n" * 300 + "最后的钩子句。"
    out = Novelist.condense(body, 2000)
    assert len(out) <= 2100
    assert out.startswith("开场第一句。"), "丢了开头"
    assert out.rstrip().endswith("最后的钩子句。"), "丢了结尾 —— 钩子就在最后一句"
    assert "中段节选" in out, "没告诉模型这是节选"
    assert Novelist.condense("短文本", 2000) == "短文本", "不超长时不该动"


def test_chapter_body_is_fully_indexed():
    """整章都要进 FTS5，索引不到的内容等于不存在。"""
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist.step_chapter)
    assert 'text[:1500]' not in src, "章节正文仍被截断后入索引"
    assert 'f"第{n}章", one + "\\n" + text)' in src


def test_extraction_reads_whole_chapter():
    """结构化抽取要覆盖整章，否则后半章的伏笔与状态变化全丢。"""
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist._extract_state)
    assert "text[:4000]" not in src, "抽取仍只读前 4000 字"
    assert "condense(text" in src
