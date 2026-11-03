#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 PubMed 记录抓取可合法获取的全文（PDF / 全文 XML）。

优先级：
  1. Europe PMC REST（PMC 开放获取子集）: fullTextXML + fullTextPDF
  2. 无 PMCID 时用 DOI 反查 Europe PMC
  3. OpenAlex 的 best_oa_location / oa_url 中的 PDF 直链
  4. 都没有 -> abstract_only（只用摘要，绝不伪造全文）

用法:
  python fetch_fulltext.py --records records.json --outdir papers
  python fetch_fulltext.py --records records.json --outdir papers --mailto you@example.com

输出:
  <outdir>/<pmid>/paper.pdf
  <outdir>/<pmid>/fulltext.xml
  <outdir>/fulltext_manifest.json
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = "pubmed-literature-search/1.0 (urllib; mailto:%s)"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
OPENALEX = "https://api.openalex.org/works/"
_last = 0.0


def _throttle(seconds=0.5):
    global _last
    delta = time.time() - _last
    if delta < seconds:
        time.sleep(seconds - delta)
    _last = time.time()


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get(url, timeout=90, headers=None, retries=3):
    hdrs = {"User-Agent": UA % os.environ.get("PUBMED_MAILTO", "local-user"), "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", ""), resp.geturl()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as e:  # 包括 DNS 抖动、超时
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err


def _is_pdf(data):
    return data[:4] == b"%PDF"


def _download_pdf(url, dest):
    """下载并校验 PDF 魔数，失败返回 (False, 原因)。"""
    try:
        _throttle()
        data, ctype, final_url = _get(url, headers={"User-Agent": BROWSER_UA,
                                                    "Accept": "application/pdf,*/*"})
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, str(e)[:160]
    if not _is_pdf(data):
        return False, "返回内容不是 PDF（%s）" % ctype
    if len(data) < 2048:
        return False, "PDF 过小（%d 字节）" % len(data)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return True, "%.1f KB · %s" % (len(data) / 1024.0, final_url)


def _download_xml(pmcid, dest):
    try:
        _throttle()
        data, ctype, _ = _get(EPMC + pmcid + "/fullTextXML")
    except Exception as e:
        return False, str(e)[:120]
    if b"<article" not in data[:5000]:
        return False, "无全文 XML（%s）" % ctype
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return True, "%.0f KB" % (len(data) / 1024.0)


def _europepmc_lookup(doi):
    """用 DOI 反查 Europe PMC，返回 (pmcid, isOpenAccess, fullTextUrls)。"""
    q = 'DOI:"%s"' % doi.replace('"', "")
    url = EPMC + "search?" + urllib.parse.urlencode(
        {"query": q, "format": "json", "resultType": "core", "pageSize": 3}
    )
    try:
        _throttle()
        data, _, _ = _get(url, timeout=40)
        js = json.loads(data.decode("utf-8"))
    except Exception:
        return "", False, []
    res = js.get("resultList", {}).get("result", [])
    if not res:
        return "", False, []
    r = res[0]
    urls = []
    for item in (r.get("fullTextUrlList") or {}).get("fullTextUrl", []) or []:
        if item.get("documentStyle", "").lower() == "pdf":
            urls.append((item.get("url"), item.get("availability")))
    return r.get("pmcid", "") or "", bool(r.get("isOpenAccess")) or r.get("inEPMC") == "Y", urls


def _openalex_pdf(doi):
    url = OPENALEX + urllib.parse.quote("https://doi.org/" + doi, safe=":/?&=%")
    try:
        _throttle()
        data, _, _ = _get(url, timeout=40)
        js = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    candidates = []
    best = js.get("best_oa_location") or {}
    if best.get("pdf_url"):
        candidates.append(best["pdf_url"])
    oa = js.get("open_access") or {}
    if oa.get("oa_url"):
        candidates.append(oa["oa_url"])
    for loc in js.get("locations") or []:
        if loc.get("pdf_url"):
            candidates.append(loc["pdf_url"])
    for u in dict.fromkeys(candidates):
        if u:
            return u
    return None


META_PDF = re.compile(
    r"""<meta[^>]+(?:name|property)=["']citation_pdf_url["'][^>]*content=["']([^"']+)""", re.I)
META_PDF_REV = re.compile(
    r"""<meta[^>]+content=["']([^"']+)["'][^>]*(?:name|property)=["']citation_pdf_url["']""", re.I)


def _doi_landing_pdf(doi):
    """访问 DOI 落地页，读 citation_pdf_url 元数据（很多出版社的开放 PDF 都放在这里）。"""
    try:
        _throttle()
        data, ctype, final_url = _get(
            "https://doi.org/" + urllib.parse.quote(doi, safe="/:;()[]@!$&'*+,=%-._~"),
            timeout=45,
            headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,application/pdf"},
        )
    except Exception:
        return None
    if _is_pdf(data):
        return final_url
    text = data.decode("utf-8", "ignore")
    m = META_PDF.search(text) or META_PDF_REV.search(text)
    if m:
        return urllib.parse.urljoin(final_url, m.group(1).replace("&amp;", "&"))
    return None


def _unpaywall_pdf(doi):
    email = os.environ.get("PUBMED_MAILTO", "").strip()
    if not email:
        return None
    try:
        _throttle()
        data, _, _ = _get("https://api.unpaywall.org/v2/%s?email=%s" % (doi, urllib.parse.quote(email)),
                          timeout=40)
        js = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    best = js.get("best_oa_location") or {}
    for key in ("url_for_pdf", "url"):
        if best.get(key):
            return best[key]
    return None


def fetch_one(rec, outdir):
    pmid = rec["pmid"]
    doi = (rec.get("doi") or "").strip()
    pmcid = (rec.get("pmcid") or "").strip()
    rec_dir = os.path.join(outdir, pmid)
    os.makedirs(rec_dir, exist_ok=True)
    pdf_path = os.path.join(rec_dir, "paper.pdf")
    xml_path = os.path.join(rec_dir, "fulltext.xml")
    attempts = []
    has_pdf = False
    has_xml = False

    if not pmcid and doi:
        pmcid, is_oa, urls = _europepmc_lookup(doi)
        if pmcid:
            attempts.append("Europe PMC 反查到 PMCID %s" % pmcid)
    else:
        is_oa, urls = True, []

    if pmcid:
        ok, msg = _download_xml(pmcid, xml_path)
        has_xml = ok
        attempts.append("EuropePMC XML: " + ("成功 " + msg if ok else "失败 " + msg))
        ok, msg = _download_pdf(EPMC + pmcid + "/fullTextPDF", pdf_path)
        has_pdf = ok
        attempts.append("EuropePMC PDF: " + ("成功 " + msg if ok else "失败 " + msg))

    if not has_pdf and urls:
        for u, avail in urls:
            if not u:
                continue
            ok, msg = _download_pdf(u, pdf_path)
            has_pdf = ok
            attempts.append("EuropePMC 全文链接(%s): %s" % (avail, "成功 " + msg if ok else "失败 " + msg))
            if ok:
                break

    if not has_pdf and doi:
        pdf_url = _doi_landing_pdf(doi)
        if pdf_url:
            ok, msg = _download_pdf(pdf_url, pdf_path)
            has_pdf = ok
            attempts.append("DOI 落地页: " + ("成功 " + msg if ok else "失败 " + msg))
        else:
            attempts.append("DOI 落地页: 未找到 citation_pdf_url")

    if not has_pdf and doi:
        oa_url = _openalex_pdf(doi)
        if oa_url:
            ok, msg = _download_pdf(oa_url, pdf_path)
            has_pdf = ok
            attempts.append("OpenAlex: " + ("成功 " + msg if ok else "失败 " + msg))
        else:
            attempts.append("OpenAlex: 未找到开放获取 PDF")

    if not has_pdf and doi:
        oa_url = _unpaywall_pdf(doi)
        if oa_url:
            ok, msg = _download_pdf(oa_url, pdf_path)
            has_pdf = ok
            attempts.append("Unpaywall: " + ("成功 " + msg if ok else "失败 " + msg))

    if has_pdf and has_xml:
        status, source = "pdf+xml", "Europe PMC"
    elif has_pdf:
        status, source = "pdf", "Europe PMC / OpenAlex"
    elif has_xml:
        status, source = "xml", "Europe PMC"
    else:
        status, source = "abstract_only", "无开放全文"

    return {
        "pmid": pmid,
        "doi": doi,
        "pmcid": pmcid,
        "title": rec.get("title", ""),
        "journal": rec.get("journal", ""),
        "year": rec.get("year", ""),
        "status": status,
        "source": source,
        "pdf": pdf_path if has_pdf else "",
        "xml": xml_path if has_xml else "",
        "attempts": attempts,
    }


def main():
    ap = argparse.ArgumentParser(description="抓取 PubMed 记录的开放全文")
    ap.add_argument("--records", required=True, help="pubmed_query.py 生成的 records.json")
    ap.add_argument("--outdir", required=True, help="全文输出目录")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（0 表示全部）")
    args = ap.parse_args()

    with open(args.records, encoding="utf-8") as f:
        payload = json.load(f)
    records = payload.get("records", payload if isinstance(payload, list) else [])
    if args.limit:
        records = records[: args.limit]

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    manifest = []
    for i, rec in enumerate(records, 1):
        item = fetch_one(rec, outdir)
        manifest.append(item)
        tag = {"pdf+xml": "全文", "pdf": "PDF", "xml": "XML", "abstract_only": "仅摘要"}[item["status"]]
        print("[%d/%d] %s  %s  %s" % (i, len(records), rec["pmid"], tag, rec.get("title", "")[:60]),
              file=sys.stderr, flush=True)

    stats = {}
    for m in manifest:
        stats[m["status"]] = stats.get(m["status"], 0) + 1
    out_path = os.path.join(outdir, "fulltext_manifest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"records": manifest, "stats": stats, "outdir": outdir}, f, ensure_ascii=False, indent=2)
    print(json.dumps({"manifest": out_path, "total": len(manifest), "stats": stats,
                      "abstract_only": [m["pmid"] for m in manifest if m["status"] == "abstract_only"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
