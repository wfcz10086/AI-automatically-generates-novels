"""多记忆索引 (SQLite FTS5).

长篇的真问题: 写到第 300 章时, "第 30 章埋的那个玉佩伏笔" 既不在最近摘要里,
也不在 L2 段摘要的细节里 —— 只能靠检索找回来.

索引四类记忆, 统一打分召回:
    world   世界观条目
    role    角色档案
    plot    章节摘要 / 正文片段
    fore    伏笔 (埋于第 N 章, 是否已回收)
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS mem USING fts5(
    kind, ref, title, body, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS foreshadow(
    id INTEGER PRIMARY KEY, planted INTEGER, text TEXT,
    resolved INTEGER DEFAULT 0, resolved_at INTEGER
);
"""

# FTS5 unicode61 不切中文词, 用双字 bigram 兜底做中文召回
def _bigrams(text: str) -> str:
    zh = re.findall(r"[一-鿿]+", text)
    grams = []
    for seg in zh:
        grams += [seg[i:i + 2] for i in range(len(seg) - 1)]
    other = re.findall(r"[A-Za-z0-9]+", text)
    return " ".join(grams + other)


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- 写入 ----------
    def add(self, kind: str, ref: str, title: str, body: str) -> None:
        # 同时删 bigram 行与 raw 行 —— 只删前者会让旧 raw 残留, 更新后仍搜到旧内容
        self.db.execute("DELETE FROM mem WHERE kind IN (?, ?) AND ref=?",
                        (kind, kind + "_raw", ref))
        self.db.execute("INSERT INTO mem(kind,ref,title,body) VALUES(?,?,?,?)",
                        (kind, ref, title, _bigrams(title + " " + body)))
        self.db.execute("INSERT INTO mem(kind,ref,title,body) VALUES(?,?,?,?)",
                        (kind + "_raw", ref, title, body))
        self.db.commit()

    def index_document(self, kind: str, ref_prefix: str, text: str, chunk: int = 500) -> int:
        """按小节切块入库."""
        # 兼容三种小标题写法: # 标题 / **标题** / 一、标题; 都没有就定长切块
        blocks = [b.strip() for b in re.split(
            r"\n(?=#{1,4}\s|\*\*[^*\n]{2,20}\*\*|[一二三四五六七八九十]+[、.]|\d+[、.])",
            text) if b.strip()]
        blocks = [b for b in blocks if len(b) > 30]
        if len(blocks) < 2:
            blocks = [text[i:i + chunk] for i in range(0, len(text), chunk)]
        n = 0
        for i, b in enumerate(blocks):
            head = b.splitlines()[0] if b.splitlines() else ""
            head = re.sub(r"[#*`>\-]|^\s*\d+[、.]\s*", "", head).strip(" :：")[:40]
            self.add(kind, f"{ref_prefix}#{i}", head, b[:2000])
            n += 1
        return n

    def add_foreshadow(self, chapter: int, text: str) -> None:
        self.db.execute("INSERT INTO foreshadow(planted,text) VALUES(?,?)", (chapter, text))
        self.add("fore", f"ch{chapter}-{text[:12]}", f"第{chapter}章伏笔", text)
        self.db.commit()

    def resolve_foreshadow(self, fid: int, chapter: int) -> None:
        self.db.execute("UPDATE foreshadow SET resolved=1, resolved_at=? WHERE id=?", (chapter, fid))
        self.db.commit()

    # ---------- 检索 ----------
    def search(self, query: str, k: int = 6, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        q = " OR ".join(set(_bigrams(query).split()[:40]))
        if not q.strip():
            return []                      # 空查询直接返回, FTS MATCH '' 会抛错
        sql = "SELECT kind, ref, title, bm25(mem) AS s FROM mem WHERE mem MATCH ?"
        args: List[Any] = [q]
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += kinds
        sql += " ORDER BY s LIMIT ?"
        args.append(k * 2)
        rows = self.db.execute(sql, args).fetchall()
        out, seen = [], set()
        for kind, ref, title, s in rows:
            key = (kind.replace("_raw", ""), ref)
            if key in seen:
                continue
            seen.add(key)
            raw = self.db.execute(
                "SELECT body FROM mem WHERE kind=? AND ref=?",
                (kind.replace("_raw", "") + "_raw", ref)).fetchone()
            out.append({"kind": key[0], "ref": ref, "title": title,
                        "text": (raw[0] if raw else "")[:1200], "score": s})
            if len(out) >= k:
                break
        return out

    def all_foreshadow(self) -> List[Dict[str, Any]]:
        """全部伏笔（含已回收）—— 前端台账要能看见回收情况，不只是待办。"""
        rows = self.db.execute(
            "SELECT id, planted, text, resolved, resolved_at FROM foreshadow "
            "ORDER BY planted DESC").fetchall()
        return [{"id": r[0], "chapter": r[1], "text": r[2],
                 "resolved_at": r[4] if r[3] else None} for r in rows]

    def pending_foreshadow(self) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, planted, text FROM foreshadow WHERE resolved=0 ORDER BY planted").fetchall()
        return [{"id": r[0], "planted": r[1], "text": r[2]} for r in rows]

    def stats(self) -> Dict[str, int]:
        out = {}
        for (kind, c) in self.db.execute(
                "SELECT kind, COUNT(*) FROM mem WHERE kind NOT LIKE '%_raw' GROUP BY kind"):
            out[kind] = c
        fr = self.db.execute("SELECT COUNT(*), SUM(resolved) FROM foreshadow").fetchone()
        out["foreshadow_total"] = fr[0] or 0
        out["foreshadow_resolved"] = fr[1] or 0
        return out
