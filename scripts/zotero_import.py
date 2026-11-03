#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把检索结果导入 Zotero：新建集合 + 建条目 + 上传 PDF（Zotero Web API）。

首次使用需提供 API key（zotero.org/settings/keys 新建，勾选允许写库与文件上传）：
  python zotero_import.py --set-key <API_KEY>
  python zotero_import.py --check

导入：
  python zotero_import.py --records records.json --manifest papers/fulltext_manifest.json --collection "主题 日期"
  python zotero_import.py --records records.json --collection "..." --dry-run

配置保存在 ~/.pubmed-literature/zotero.json（可用环境变量 PUBMED_ZOTERO_CONFIG 改路径）。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.zotero.org"
CONFIG_PATH = os.environ.get(
    "PUBMED_ZOTERO_CONFIG",
    os.path.join(os.path.expanduser("~"), ".pubmed-literature", "zotero.json"),
)


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


def key_info(api_key):
    req = urllib.request.Request(
        API + "/keys/current",
        headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3",
                 "User-Agent": "pubmed-literature-search/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def permission_report(info):
    access = info.get("access") or {}
    user = access.get("user") or {}
    return {
        "userID": info.get("userID"),
        "libraryWrite": bool(user.get("write")),
        "fileWrite": bool(user.get("files")),
    }


def zot_client(cfg):
    try:
        from pyzotero import zotero
    except ImportError:
        raise RuntimeError("缺少 pyzotero，先执行: python -m pip install pyzotero")
    return zotero.Zotero(cfg["library_id"], cfg.get("library_type", "user"), cfg["api_key"])


def _as_json(value):
    """pyzotero 不同版本可能返回 dict 或 JSON 字符串，统一成 Python 对象。"""
    if isinstance(value, (bytes, str)):
        return json.loads(value)
    return value


def _key_of(value):
    """兼容 pyzotero 不同版本的返回：{'key': ...}、{'data': {'key': ...}} 或直接是 key 字符串。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("key") or (value.get("data") or {}).get("key")
    return None


def get_or_create_collection(zot, name):
    found = []
    for c in _as_json(zot.collections(q=name)) or []:
        if (c.get("data", {}).get("name") or "").strip() == name.strip():
            found.append(c)
    if found:
        return found[0]["key"], found[0]["data"]["name"], False
    resp = _as_json(zot.create_collections([{"name": name}]))
    success = resp.get("success") or {}
    if "0" in success:
        key = _key_of(success["0"])
        if key:
            return key, name, True
    raise RuntimeError("新建集合失败: %s" % json.dumps(resp, ensure_ascii=False)[:300])


def build_item(rec, collection_key):
    item = {
        "itemType": "journalArticle",
        "title": rec.get("title") or "",
        "creators": [
            {"creatorType": "author", "firstName": a.get("firstName", ""), "lastName": a.get("lastName", "")}
            for a in rec.get("authors") or []
            if a.get("lastName")
        ],
        "date": rec.get("year") or "",
        "publicationTitle": rec.get("journal") or "",
        "volume": rec.get("volume") or "",
        "issue": rec.get("issue") or "",
        "pages": rec.get("pages") or "",
        "DOI": rec.get("doi") or "",
        "url": rec.get("url") or "",
        "abstractNote": rec.get("abstract") or "",
        "libraryCatalog": "PubMed",
        "language": rec.get("language") or "",
        "collections": [collection_key],
        "tags": [{"tag": t} for t in (rec.get("pubTypes") or []) if t],
        "extra": "PMID: %s%s" % (
            rec.get("pmid", ""),
            ("\nPMCID: " + rec["pmcid"]) if rec.get("pmcid") else "",
        ),
    }
    return item


def find_existing(zot, rec):
    """Zotero 的 q 参数不索引 DOI，改为按标题搜索后再核对 DOI / PMID。"""
    q = (rec.get("title") or "").strip()
    if not q:
        return None
    try:
        hits = _as_json(zot.items(q=q, qmode="titleCreatorYear", limit=10)) or []
    except Exception:
        return None
    for h in hits:
        d = h.get("data") or {}
        if rec.get("doi") and (d.get("DOI") or "").lower() == rec["doi"].lower():
            return h
        if rec.get("pmid") and ("PMID: " + rec["pmid"]) in (d.get("extra") or ""):
            return h
        if (d.get("title") or "").strip().lower() == q.lower():
            return h
    return None


def main():
    ap = argparse.ArgumentParser(description="导入 Zotero（Web API）")
    ap.add_argument("--set-key", metavar="API_KEY", help="保存并校验 Zotero API key")
    ap.add_argument("--set-key-stdin", action="store_true",
                    help="从标准输入读取 API key（避免写入 shell 历史）")
    ap.add_argument("--check", action="store_true", help="检查配置与权限")
    ap.add_argument("--records", help="pubmed_query.py 生成的 records.json")
    ap.add_argument("--manifest", help="fulltext_manifest.json（提供则上传 PDF）")
    ap.add_argument("--collection", help="Zotero 集合名（不存在则新建）")
    ap.add_argument("--limit", type=int, default=0, help="只导入前 N 条")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写 Zotero")
    ap.add_argument("--no-pdf", action="store_true", help="只导元数据，不上传 PDF")
    args = ap.parse_args()

    if args.set_key or args.set_key_stdin:
        key = args.set_key.strip() if args.set_key else sys.stdin.readline().strip()
        if not key:
            print(json.dumps({"ok": False, "error": "没有读到 API key"}, ensure_ascii=False, indent=2))
            sys.exit(2)
        info = key_info(key)
        perms = permission_report(info)
        cfg = {
            "api_key": key,
            "library_id": str(perms["userID"]),
            "library_type": "user",
        }
        save_config(cfg)
        print(json.dumps({"saved": CONFIG_PATH, "permissions": perms},
                         ensure_ascii=False, indent=2))
        if not perms["libraryWrite"]:
            print("警告：该 key 没有写库权限，无法创建条目。请在 zotero.org 重新生成并勾选 Allow write access。",
                  file=sys.stderr)
        if not perms["fileWrite"]:
            print("警告：该 key 没有文件写入权限，PDF 无法上传。请勾选 Allow file writing。",
                  file=sys.stderr)
        return

    cfg = load_config()
    if args.check or not (args.records and args.collection):
        if not cfg.get("api_key"):
            print(json.dumps({"ok": False, "config": CONFIG_PATH,
                              "hint": "还没有配置 API key，先运行 --set-key <API_KEY>"},
                             ensure_ascii=False, indent=2))
            return
        info = key_info(cfg["api_key"])
        perms = permission_report(info)
        print(json.dumps({"ok": True, "config": CONFIG_PATH, "permissions": perms},
                         ensure_ascii=False, indent=2))
        return

    if not args.records or not args.collection:
        ap.error("导入需要 --records 和 --collection")

    if not cfg.get("api_key"):
        print(json.dumps({"ok": False, "config": CONFIG_PATH,
                          "hint": "还没有配置 API key，先运行 --set-key <API_KEY>"},
                         ensure_ascii=False, indent=2))
        sys.exit(2)

    with open(args.records, encoding="utf-8") as f:
        payload = json.load(f)
    records = payload.get("records", payload if isinstance(payload, list) else [])
    if args.limit:
        records = records[: args.limit]

    manifest_map = {}
    if args.manifest and os.path.isfile(args.manifest):
        with open(args.manifest, encoding="utf-8") as f:
            man = json.load(f)
        manifest_map = {m["pmid"]: m for m in man.get("records", [])}

    zot = zot_client(cfg)
    if args.dry_run:
        preview = [{"title": r["title"][:80], "doi": r.get("doi", ""),
                    "pdf": bool(manifest_map.get(r["pmid"], {}).get("pdf"))} for r in records]
        print(json.dumps({"dryRun": True, "collection": args.collection,
                          "items": len(preview), "preview": preview},
                         ensure_ascii=False, indent=2))
        return

    col_key, col_name, created = get_or_create_collection(zot, args.collection)
    if created:
        print("[zotero] 已新建集合: %s (%s)" % (col_name, col_key), file=sys.stderr)

    report = {"collection": col_name, "collectionKey": col_key, "collectionCreated": created,
              "created": [], "existing": [], "failed": [], "attachments": [], "attachmentFailed": []}

    to_create = []
    for rec in records:
        existing = find_existing(zot, rec)
        if existing:
            report["existing"].append({"pmid": rec["pmid"], "key": existing["key"]})
            try:
                zot.addto_collection(col_key, existing)
            except Exception as e:
                report["failed"].append({"pmid": rec["pmid"], "stage": "add_to_collection",
                                         "error": str(e)[:200]})
            continue
        to_create.append(rec)

    if to_create:
        items = [build_item(r, col_key) for r in to_create]
        resp = _as_json(zot.create_items(items))
        success = resp.get("success") or {}
        failed = resp.get("failed") or {}
        created_by_index = {}
        for k, v in success.items():
            idx = int(k)
            item_key = _key_of(v)
            created_by_index[idx] = item_key
            rec = to_create[idx]
            report["created"].append({"pmid": rec["pmid"], "key": item_key, "title": rec["title"]})
        for k, v in failed.items():
            idx = int(k)
            rec = to_create[idx]
            report["failed"].append({"pmid": rec["pmid"], "stage": "create_item", "error": str(v)[:200]})

        if not args.no_pdf:
            for idx, item_key in created_by_index.items():
                rec = to_create[idx]
                pdf = manifest_map.get(rec["pmid"], {}).get("pdf")
                if not item_key or not pdf or not os.path.isfile(pdf):
                    continue
                try:
                    zot.attachment_simple([pdf], parentid=item_key)
                    report["attachments"].append({"pmid": rec["pmid"], "key": item_key, "file": pdf})
                except Exception as e:
                    report["attachmentFailed"].append({"pmid": rec["pmid"], "error": str(e)[:200]})

    out_path = os.path.join(os.path.dirname(os.path.abspath(args.records)), "zotero_import_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        "report": out_path,
        "collection": col_name,
        "created": len(report["created"]),
        "existing": len(report["existing"]),
        "failed": len(report["failed"]),
        "attachments": len(report["attachments"]),
        "attachmentFailed": len(report["attachmentFailed"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
