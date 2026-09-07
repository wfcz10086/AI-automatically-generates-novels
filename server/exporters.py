"""导出插件. 加一种格式 = 加一个函数 + 注册一行."""
from __future__ import annotations

import html
import io
import re
import zipfile
from typing import Dict, Callable, List, Any, Tuple


def _chapters(project) -> List[tuple]:
    out = []
    for n in sorted(project.state.get("done", [])):
        body = project.chapter(n)
        title = ""
        co = project._load("chapter_outlines.json", {}).get(str(n), "")
        m = re.search(r"第\s*\d+\s*章\s*(.+)", co)
        if m:
            title = m.group(1).strip().splitlines()[0][:30]
        out.append((n, title, body))
    return out


def to_txt(project) -> str:
    parts = [f"《{project.meta.get('title','')}》\n"]
    for n, title, body in _chapters(project):
        parts.append(f"\n第{n}章 {title}\n\n{body}\n")
    return "".join(parts)


def to_md(project) -> str:
    parts = [f"# 《{project.meta.get('title','')}》\n",
             f"> {project.meta.get('genre_id')} · {project.meta.get('style_id')} · "
             f"{project.total_words:,} 字\n"]
    wb = project.read("world_bible.md")
    if wb:
        parts.append(f"\n## 世界观\n\n{wb}\n")
    for n, title, body in _chapters(project):
        parts.append(f"\n## 第{n}章 {title}\n\n{body}\n")
    return "".join(parts)


def to_outline(project) -> str:
    """只导大纲: 总纲 + 全部章节细纲."""
    parts = [f"《{project.meta.get('title','')}》大纲\n\n{project.read('outline.md')}\n\n"
             "———— 分章细纲 ————\n"]
    ol = project._load("chapter_outlines.json", {})
    for k in sorted(ol, key=lambda x: int(x)):
        parts.append(f"\n{ol[k]}\n")
    return "".join(parts)


def to_fountain(project) -> str:
    """Fountain 剧本格式 (行业标准, Final Draft / Highland 可直接打开)."""
    m = project.meta
    parts = [f"Title: {m.get('title','')}\n", "Credit: Written by\n",
             "Author: AI Novel Studio\n\n"]
    for n, title, body in _chapters(project):
        parts.append(f"\n= 第{n}集 {title}\n\n")
        for line in body.splitlines():
            s = line.strip()
            if not s:
                parts.append("\n")
                continue
            # 场头
            if re.match(r"^\s*\d*\s*(内景|外景|INT|EXT)", s):
                parts.append(re.sub(r"^\s*\d+[、.]?\s*", "", s).upper() + "\n\n")
            # 「角色：台词」→ 角色名独立行 + 对白
            elif re.match(r"^[^\s：:]{1,8}[：:]", s):
                who, said = re.split(r"[：:]", s, 1)
                parts.append(f"{who.strip()}\n{said.strip()}\n\n")
            else:
                parts.append(s + "\n\n")
    return "".join(parts)


def to_srt(project) -> str:
    """短剧字幕. 按标点切句, 按字数估时长."""
    idx, t, out = 1, 0.0, []

    def fmt(x: float) -> str:
        h, r = divmod(x, 3600); mnt, s = divmod(r, 60)
        return f"{int(h):02d}:{int(mnt):02d}:{int(s):02d},{int((s%1)*1000):03d}"

    for n, title, body in _chapters(project):
        for raw in re.split(r"(?<=[。！？!?…])", body):
            s = re.sub(r"\s+", " ", raw).strip()
            if len(s) < 2:
                continue
            dur = max(1.2, len(s) * 0.22)
            out.append(f"{idx}\n{fmt(t)} --> {fmt(t+dur)}\n{s}\n")
            idx += 1; t += dur
    return "\n".join(out)


# ---------------------------------------------------------------- 二进制格式
# docx / epub 都是 zip + XML, 手写即可, 不必为此引入依赖。

def _xml_escape(t: str) -> str:
    return html.escape(t, quote=False)


def to_docx(project) -> bytes:
    """最小可用 .docx —— Word / WPS / Pages 都能正常打开。"""
    def para(text: str, style: str = "") -> str:
        pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return (f'<w:p>{pr}<w:r><w:rPr><w:rFonts w:eastAsia="SimSun"/>'
                f'<w:sz w:val="24"/></w:rPr><w:t xml:space="preserve">'
                f'{_xml_escape(text)}</w:t></w:r></w:p>')

    body = [para(f"《{project.meta.get('title','')}》", "Title")]
    for n, title, text in _chapters(project):
        body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        body.append(para(f"第{n}章 {title}", "Heading1"))
        for line in text.split("\n"):
            if line.strip():
                body.append(para(line.strip()))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body)}</w:body></w:document>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def to_epub(project) -> bytes:
    """最小可用 .epub 2.0 —— 微信读书 / Apple Books / Calibre 可读。"""
    title = project.meta.get("title", "novel")
    uid = f"urn:uuid:novel-{abs(hash(title)) & 0xffffffff:08x}"
    chs: List[Tuple[int, str, str]] = _chapters(project)

    def xhtml(head: str, body_html: str) -> str:
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
                f'<head><title>{_xml_escape(head)}</title>'
                '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
                '<style>body{font-family:serif;line-height:1.9;margin:1em}'
                'h1{font-size:1.3em;margin:1.2em 0}p{text-indent:2em;margin:.5em 0}</style>'
                f'</head><body>{body_html}</body></html>')

    files: Dict[str, str] = {}
    for n, ct, text in chs:
        ps = "".join(f"<p>{_xml_escape(l.strip())}</p>"
                     for l in text.split("\n") if l.strip())
        files[f"ch{n:04d}.xhtml"] = xhtml(f"第{n}章 {ct}",
                                          f"<h1>第{n}章 {_xml_escape(ct)}</h1>{ps}")

    manifest = "".join(
        f'<item id="c{n:04d}" href="{f}" media-type="application/xhtml+xml"/>'
        for (n, _, _), f in zip(chs, files))
    spine = "".join(f'<itemref idref="c{n:04d}"/>' for n, _, _ in chs)
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
           'xmlns:opf="http://www.idpf.org/2007/opf">'
           f'<dc:title>{_xml_escape(title)}</dc:title>'
           '<dc:language>zh-CN</dc:language>'
           f'<dc:identifier id="bookid">{uid}</dc:identifier>'
           '<dc:creator>AI Novel Studio</dc:creator></metadata>'
           f'<manifest>{manifest}'
           '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
           f'<spine toc="ncx">{spine}</spine></package>')
    navpoints = "".join(
        f'<navPoint id="n{n:04d}" playOrder="{i+1}"><navLabel><text>'
        f'第{n}章 {_xml_escape(ct)}</text></navLabel>'
        f'<content src="ch{n:04d}.xhtml"/></navPoint>'
        for i, (n, ct, _) in enumerate(chs))
    ncx = ('<?xml version="1.0" encoding="utf-8"?>'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{uid}"/></head>'
           f'<docTitle><text>{_xml_escape(title)}</text></docTitle>'
           f'<navMap>{navpoints}</navMap></ncx>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype 必须是第一个且不压缩
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?>'
                   '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        for f, c in files.items():
            z.writestr(f"OEBPS/{f}", c)
    return buf.getvalue()


BINARY_EXPORTERS: Dict[str, Callable[[Any], bytes]] = {
    "docx": to_docx, "epub": to_epub,
}

EXPORTERS: Dict[str, Callable[[Any], str]] = {
    "txt": to_txt, "md": to_md, "outline": to_outline,
    "fountain": to_fountain, "srt": to_srt,
}
MIME = {"txt": "text/plain", "md": "text/markdown", "outline": "text/plain",
        "fountain": "text/plain", "srt": "application/x-subrip",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "epub": "application/epub+zip"}
EXT = {"txt": "txt", "md": "md", "outline": "txt", "fountain": "fountain", "srt": "srt",
       "docx": "docx", "epub": "epub"}
