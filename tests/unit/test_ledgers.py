"""两本台账的槽位是通用的，叫法由题材包填。

起因：抽取提示词里写死了「资金账」和「所在地/身体状态/关键持有物」——
那是都市文的说法。修仙该记境界与灵石、玄幻记等级、历史记官职与钱粮、
电竞记段位与奖金。机制必须通用，口径必须可插拔。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
ROOT = Path(__file__).resolve().parents[2]
from server.orchestrator import Novelist  # noqa: E402

GENRES = sorted(p.stem for p in (ROOT / "packs" / "genre").glob("*.json"))


def _pack(gid):
    return json.loads((ROOT / "packs" / "genre" / f"{gid}.json").read_text(encoding="utf-8"))


def test_every_genre_declares_both_ledgers():
    """每个题材都要说清自己的资源账与力量账叫什么、记什么。"""
    missing = []
    for gid in GENRES:
        lg = _pack(gid).get("ledgers") or {}
        for slot in ("resource", "power"):
            cfg = lg.get(slot) or {}
            if not cfg.get("label") or not cfg.get("hint"):
                missing.append(f"{gid}.{slot}")
    assert not missing, f"这些题材没声明台账口径：{missing}"


def test_labels_are_genre_specific_not_urban_default():
    """修仙不该记「资金台账」，历史不该记「所在地/持有物」。"""
    xz = _pack("xiuzhen")["ledgers"]
    assert "灵石" in xz["resource"]["label"] or "资源" in xz["resource"]["label"]
    assert "境界" in xz["power"]["label"]
    assert "境界" in xz["power"]["hint"]
    ls = _pack("lishi")["ledgers"]
    assert "官职" in ls["power"]["label"] or "兵权" in ls["power"]["label"]


def test_engine_has_universal_defaults():
    """题材包没声明也必须有台账 —— 槽位是引擎的，不是题材的。"""
    d = Novelist.DEFAULT_LEDGERS
    assert set(d) == {"resource", "power"}
    for slot in d.values():
        assert slot["label"] and slot["hint"]


def test_no_hardcoded_urban_wording_in_extraction():
    """抽取提示词里不许再写死「资金账」这种题材专属字样。"""
    import inspect
    src = inspect.getsource(Novelist._extract_state)
    assert "spec['resource']['label']" in src or 'spec["resource"]["label"]' in src, \
        "资源账标题没走题材口径"
    assert "spec['power']['label']" in src or 'spec["power"]["label"]' in src, \
        "力量账标题没走题材口径"
