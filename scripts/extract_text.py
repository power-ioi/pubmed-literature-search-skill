#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把下载到的全文转成纯文本，供逐篇速览使用。

优先级: fulltext.xml (JATS) -> paper.pdf (pymupdf) -> 摘要（records.json）
绝不把摘要冒充全文，输出文件头会写清来源。

用法:
  python extract_text.py --records records.json --manifest papers/fulltext_manifest.json --outdir texts
  python extract_text.py --records records.json --papers papers --outdir texts --max-chars 24000
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def jats_sections(xml_path):
    """解析 JATS XML，返回 (标题, [(章节标题, 文本), ...])。"""
    root = ET.parse(xml_path).getroot()
    title = clean("".join(root.find(".//article-title").itertext())) \
        if root.find(".//article-title") is not None else ""

    sections = []
    abstract = root.find(".//abstract")
    if abstract is not None:
        paras = [clean("".join(p.itertext())) for p in abstract.iter()
                 if p.tag.split("}")[-1] == "p"]
        sections.append(("Abstract", "\n".join([p for p in paras if p])))

    body = root.find(".//body")
    if body is not None:
        def handle_sec(sec):
            head = clean("".join(sec.find("./title").itertext())) \
                if sec.find("./title") is not None else ""
            paras = []
            for sub in sec:
                stag = sub.tag.split("}")[-1]
                if stag == "p":
                    t = clean("".join(sub.itertext()))
                    if t:
                        paras.append(t)
                elif stag in ("fig", "table-wrap"):
                    cap = sub.find(".//caption")
                    if cap is not None:
                        paras.append("[图表] " + clean("".join(cap.itertext())))
            if paras:
                sections.append((head, "\n".join(paras)))
            for nested in sec.findall("./sec"):
                handle_sec(nested)

        def walk(node):
            for child in node:
                tag = child.tag.split("}")[-1]
                if tag == "sec":
                    handle_sec(child)
                elif tag == "p":
                    t = clean("".join(child.itertext()))
                    if t:
                        sections.append(("", t))
        walk(body)
    return title, sections


PRIORITY = ("abstract", "conclusion", "result", "discussion", "method",
            "introduction", "background", "reference")


def _section_rank(heading):
    h = (heading or "").lower()
    for i, key in enumerate(PRIORITY):
        if key in h:
            return i
    return len(PRIORITY)


def jats_to_text(xml_path, max_chars):
    title, sections = jats_sections(xml_path)
    lines = ["# " + title] if title else []
    used = len(lines[0]) if lines else 0
    ordered = sorted(sections, key=lambda s: _section_rank(s[0]))
    truncated = False
    emitted = []
    for head, body in ordered:
        block = ("\n## " + head if head else "") + "\n" + body
        if used + len(block) > max_chars:
            remain = max_chars - used
            if remain > 800:
                emitted.append(block[:remain])
                truncated = True
            break
        emitted.append(block)
        used += len(block)
    # 保持阅读顺序：摘要 -> 结论 -> 结果 -> 讨论 -> 方法 -> 引言
    return "\n".join(lines + emitted), truncated


def pdf_to_text(pdf_path):
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            raise RuntimeError("缺少 PDF 解析库，先执行: python -m pip install pymupdf")
    doc = fitz.open(pdf_path)
    parts = []
    try:
        for i, page in enumerate(doc):
            parts.append(page.get_text("text"))
            if i > 400:
                break
    finally:
        doc.close()
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="全文转纯文本")
    ap.add_argument("--records", required=True)
    ap.add_argument("--manifest", help="fulltext_manifest.json（推荐，含每条状态）")
    ap.add_argument("--papers", help="全文目录（未提供 manifest 时按 <papers>/<pmid>/ 推断）")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--max-chars", type=int, default=24000)
    args = ap.parse_args()

    with open(args.records, encoding="utf-8") as f:
        payload = json.load(f)
    records = payload.get("records", payload if isinstance(payload, list) else [])

    manifest_map = {}
    if args.manifest:
        with open(args.manifest, encoding="utf-8") as f:
            man = json.load(f)
        manifest_map = {m["pmid"]: m for m in man.get("records", [])}

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    summary = []

    for rec in records:
        pmid = rec["pmid"]
        m = manifest_map.get(pmid, {})
        base = None
        if args.papers:
            base = os.path.abspath(args.papers)
        elif args.manifest:
            base = os.path.dirname(os.path.abspath(args.manifest))

        xml_path = m.get("xml") or (os.path.join(base, pmid, "fulltext.xml") if base else "")
        pdf_path = m.get("pdf") or (os.path.join(base, pmid, "paper.pdf") if base else "")

        text = ""
        source = ""
        truncated = False
        if xml_path and os.path.isfile(xml_path):
            try:
                text, truncated = jats_to_text(xml_path, args.max_chars)
                source = "fulltext_xml"
            except Exception:
                text = ""
                source = ""
        if not text and pdf_path and os.path.isfile(pdf_path):
            try:
                raw = pdf_to_text(pdf_path)
                if len(raw) > args.max_chars:
                    head = int(args.max_chars * 0.55)
                    text = raw[:head] + "\n...[中间省略]...\n" + raw[-(args.max_chars - head):]
                    truncated = True
                else:
                    text = raw
                source = "pdf"
            except Exception:
                text = ""
                source = ""
        if not text:
            text = rec.get("abstract", "")
            source = "abstract_only"

        if len(text) > args.max_chars:
            text = text[: args.max_chars]
            truncated = True

        header = "\n".join([
            "PMID: " + pmid,
            "Title: " + (rec.get("title") or ""),
            "Journal: %s (%s)" % (rec.get("journal") or "", rec.get("year") or ""),
            "DOI: " + (rec.get("doi") or "-"),
            "TextSource: " + source + (" (已截断)" if truncated else ""),
            "",
        ])
        body = header + text.strip() + "\n"
        out_path = os.path.join(outdir, pmid + ".txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(body)
        summary.append({"pmid": pmid, "source": source, "chars": len(text),
                        "truncated": truncated, "path": out_path,
                        "title": rec.get("title", "")})

    stats = {}
    for s in summary:
        stats[s["source"]] = stats.get(s["source"], 0) + 1
    print(json.dumps({"outdir": outdir, "stats": stats, "files": summary},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
