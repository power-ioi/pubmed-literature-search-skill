#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PubMed 检索（NCBI E-utilities，无第三方依赖）。

用法:
  # 1) 多套候选检索式对比命中量 + 结果预览 + 可直接打开的 PubMed 链接
  python pubmed_query.py --terms "a" "b" "c" --preview 5

  # 2) 选定检索式，拉取完整元数据（后续下载全文 / 导入 Zotero 用）
  python pubmed_query.py --term "..." --max 20 --out records.json

  # 3) 按 PMID 补取元数据
  python pubmed_query.py --pmids 12345678,23456789 --out records.json

可选: 设置环境变量 NCBI_API_KEY 提高限速上限（3 -> 10 次/秒）。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
UA = "pubmed-literature-search/1.0 (urllib; contact: local user)"
_last_request = 0.0


def _throttle():
    global _last_request
    wait = 0.11 if os.environ.get("NCBI_API_KEY") else 0.36
    delta = time.time() - _last_request
    if delta < wait:
        time.sleep(wait - delta)
    _last_request = time.time()


def eutils(endpoint, params, retries=3):
    params = dict(params)
    key = os.environ.get("NCBI_API_KEY")
    if key:
        params["api_key"] = key
    url = EUTILS + endpoint + "?" + urllib.parse.urlencode(params)
    err = None
    for attempt in range(retries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("NCBI 请求失败: %s (%s)" % (url, err))


def esearch(term, retmax=0, sort=None):
    params = {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json"}
    if sort:
        params["sort"] = sort
    raw = eutils("esearch.fcgi", params)
    data = json.loads(raw.decode("utf-8"))
    es = data.get("esearchresult", {})
    return {
        "count": int(es.get("count", 0)),
        "ids": es.get("idlist", []),
        "translation": es.get("querytranslation") or es.get("translationSet") or [],
        "warnings": es.get("warninglist", {}),
    }


def _txt(el):
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def pub_year(article, mc):
    ad = article.find("ArticleDate")
    if ad is not None and _txt(ad.find("Year")):
        return _txt(ad.find("Year"))
    pub = article.find("Journal/JournalIssue/PubDate")
    if pub is not None:
        if _txt(pub.find("Year")):
            return _txt(pub.find("Year"))
        m = re.search(r"(19|20)\d{2}", _txt(pub.find("MedlineDate")))
        if m:
            return m.group(0)
    for path in ("DateCompleted/Year", "DateRevised/Year"):
        if _txt(mc.find(path)):
            return _txt(mc.find(path))
    return ""


def parse_article(art):
    mc = art.find("MedlineCitation")
    if mc is None:
        return None
    article = mc.find("Article")
    pmid = _txt(mc.find("PMID"))
    title = _txt(article.find("ArticleTitle")) if article is not None else ""

    authors = []
    if article is not None:
        for a in article.findall("AuthorList/Author"):
            last = _txt(a.find("LastName"))
            first = _txt(a.find("ForeName")) or _txt(a.find("Initials"))
            collective = _txt(a.find("CollectiveName"))
            if last:
                authors.append((first.rstrip("."), last))
            elif collective:
                authors.append(("", collective))

    doi = ""
    pmcid = ""
    pubmed_data = art.find("PubmedData/ArticleIdList")
    if pubmed_data is not None:
        for aid in pubmed_data.findall("ArticleId"):
            t = (aid.get("IdType") or "").lower()
            if t == "doi" and not doi:
                doi = _txt(aid)
            elif t == "pmc" and not pmcid:
                pmcid = _txt(aid)
    if article is not None and not doi:
        for el in article.findall("ELocationID"):
            if (el.get("EIdType") or "").lower() == "doi":
                doi = _txt(el)

    abstract_parts = []
    if article is not None:
        for ab in article.findall("Abstract/AbstractText"):
            label = ab.get("Label") or ab.get("NlmCategory") or ""
            text = _txt(ab)
            if text:
                abstract_parts.append(("%s: %s" % (label, text)) if label else text)
    abstract = "\n".join(abstract_parts)

    pub_types = [_txt(x) for x in mc.findall("Article/PublicationTypeList/PublicationType")]
    mesh = [_txt(x) for x in mc.findall("MeshHeadingList/MeshHeading/DescriptorName")]
    keywords = [_txt(x) for x in mc.findall("KeywordList/Keyword")]

    journal = ""
    issn = ""
    if article is not None:
        journal = _txt(article.find("Journal/Title")) or _txt(mc.find("MedlineJournalInfo/MedlineTA"))
        issn = _txt(article.find("Journal/ISSN"))
    volume = _txt(article.find("Journal/JournalIssue/Volume")) if article is not None else ""
    issue = _txt(article.find("Journal/JournalIssue/Issue")) if article is not None else ""
    pages = _txt(article.find("Pagination/MedlinePgn")) if article is not None else ""

    year = pub_year(article, mc) if article is not None else ""
    language = _txt(mc.find("Language"))

    return {
        "pmid": pmid,
        "title": title,
        "authors": [{"firstName": f, "lastName": l} for f, l in authors],
        "authorString": ", ".join((f + " " + l).strip() for f, l in authors),
        "journal": journal,
        "journalAbbrev": _txt(mc.find("MedlineJournalInfo/MedlineTA")),
        "year": year,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "doi": doi,
        "pmcid": pmcid,
        "abstract": abstract,
        "pubTypes": [p for p in pub_types if p],
        "meshTerms": [m for m in mesh if m],
        "keywords": [k for k in keywords if k],
        "language": language,
        "issn": issn,
        "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid,
    }


def efetch(ids):
    if not ids:
        return []
    raw = eutils("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    root = ET.fromstring(raw)
    out = []
    for art in root.findall("PubmedArticle"):
        rec = parse_article(art)
        if rec:
            out.append(rec)
    return out


def pubmed_url(term):
    return "https://pubmed.ncbi.nlm.nih.gov/?term=" + urllib.parse.quote(term, safe="")


def main():
    ap = argparse.ArgumentParser(description="PubMed 检索与元数据抓取")
    ap.add_argument("--terms", nargs="+", help="多套候选检索式，只返回命中量和预览")
    ap.add_argument("--terms-file", help="UTF-8 文本，每行一套候选检索式（避免命令行引号转义问题）")
    ap.add_argument("--term", help="单套检索式，返回完整元数据")
    ap.add_argument("--term-file", help="UTF-8 文本，第一行是最终检索式")
    ap.add_argument("--pmids", help="逗号分隔的 PMID 列表")
    ap.add_argument("--max", type=int, default=20, help="最多取多少条（默认 20，与 skill 上限一致）")
    ap.add_argument("--preview", type=int, default=5, help="--terms 模式下每套显示几条预览")
    ap.add_argument("--sort", default="relevance", help="relevance / pub_date / first_author")
    ap.add_argument("--out", help="把完整记录写入该 JSON 文件")
    args = ap.parse_args()

    if args.terms_file:
        with open(args.terms_file, encoding="utf-8-sig") as f:
            args.terms = [ln.strip() for ln in f if ln.strip()]
    if args.term_file:
        with open(args.term_file, encoding="utf-8-sig") as f:
            args.term = f.readline().strip()

    if args.terms:
        report = []
        for term in args.terms:
            hit = esearch(term, retmax=args.preview, sort=args.sort)
            preview = efetch(hit["ids"])
            report.append({
                "term": term,
                "url": pubmed_url(term),
                "count": hit["count"],
                "queryTranslation": hit["translation"],
                "warnings": hit["warnings"],
                "preview": [
                    {
                        "pmid": r["pmid"],
                        "title": r["title"],
                        "journal": r["journal"],
                        "year": r["year"],
                        "firstAuthor": r["authorString"].split(",")[0] if r["authorString"] else "",
                        "doi": r["doi"],
                    }
                    for r in preview
                ],
            })
        print(json.dumps({"mode": "compare", "candidates": report}, ensure_ascii=False, indent=2))
        return

    if args.pmids:
        ids = [x.strip() for x in args.pmids.split(",") if x.strip()]
        records = efetch(ids)
    elif args.term:
        hit = esearch(args.term, retmax=args.max, sort=args.sort)
        records = efetch(hit["ids"])
        for r in records:
            r["searchTerm"] = args.term
    else:
        ap.error("需要 --terms / --term / --pmids 之一")

    payload = {
        "mode": "records",
        "searchTerm": args.term or "",
        "url": pubmed_url(args.term) if args.term else "",
        "count": len(records),
        "records": records,
    }
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        payload["out"] = os.path.abspath(args.out)
    printable = dict(payload)
    printable["records"] = [
        {k: r[k] for k in ("pmid", "title", "authorString", "journal", "year", "doi", "pmcid")}
        for r in records
    ]
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
