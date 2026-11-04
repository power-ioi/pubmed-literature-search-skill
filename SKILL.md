---
name: pubmed-literature-search
description: PubMed 文献检索全流程，中文主题转英文检索式、给检索链接、下载开放获取全文并导入 Zotero 新集合、逐篇速览。当用户要求检索文献、查 PubMed、批量下载论文或整批导入 Zotero 时使用。单篇精读笔记用 literature-to-obsidian。
---

# PubMed 文献检索工作流

四步交付：检索方案 → 链接确认 → 下载并导入 Zotero → 逐篇速览。每步都要停下来等用户确认，不要一口气跑完。

总原则：只用合法开放来源，不编造全文和数据，拿不到全文就如实标注。

## 路径约定

| 组件 | 路径 |
|------|------|
| 脚本目录 | `C:\Users\86186\.trae-cn\skills\pubmed-literature-search\scripts\` |
| Python | `python` |
| 工作目录 | 当前工作区的 `pubmed-<主题简称>-<YYYYMMDD>\` |
| Zotero 配置 | `~\.pubmed-literature\zotero.json`（API key 存在这里，只存不回显） |
| 依赖 | `pyzotero`（写 Zotero）、`pymupdf`（读 PDF）；缺失时 `python -m pip install pyzotero pymupdf` |

## 第 0 步 环境自检

```powershell
python "C:\Users\86186\.trae-cn\skills\pubmed-literature-search\scripts\zotero_import.py" --check
```

- 返回 `ok:false` 且没有 key：让用户到 `https://www.zotero.org/settings/keys` 新建 key，勾选 **Allow write access** 和 **Allow file writing**，然后让用户在**自己的终端**里执行（不要把 key 写进命令行或文件，key 会进 shell 历史）：
  ```powershell
  $key = Read-Host "粘贴 Zotero API key，然后回车"
  $key | python "...\zotero_import.py" --set-key-stdin
  ```
  key 只写进配置文件，不在回复里复述。
- `libraryWrite` 或 `fileWrite` 为 false：先告知用户权限不足（能建条目但传不了 PDF），让用户重新生成 key。
- 自检失败就先解决环境，不要带病往下跑。

## 第 1 步 中文主题转英文检索方案（必须让用户选）

1. 从中文主题里抽出 PICO 或核心概念（人群 / 干预 / 对照 / 结局 / 研究类型），参考 `references/search-strategy.md`。
2. 生成 **3 套**方案：精准（MeSH + 研究类型过滤）、均衡、宽泛（同义词 OR 展开）。
3. 先跑命中量和样例，不要空口报数字：
   ```powershell
   python "...\pubmed_query.py" --terms-file "queries.txt" --preview 5
   ```
   `queries.txt` 每行一套检索式。**必须用文件传检索式**：PowerShell 会把双引号吃掉，`--terms "..."` 容易把 `"small interfering RNA"[tiab]` 这类短语裁坏，导致命中数虚高或日期过滤丢失。
5. 最终检索式同样写进 `final.txt`，用 `--term-file "final.txt"` 调用。
6. 用 AskUserQuestion 让用户选（每套标出命中数 + 结果预览），并说明用户可以自己改词或直接给英文检索式。
7. 命中数为 0 或过大（> 5000）时先调整再用：加年份、加 `[pt]` 研究类型、收窄人群词。
8. 用户选定后，把最终检索式和链接复述一遍，确认无误再进下一步。

## 第 2 步 给检索链接，等用户浏览确认

- 输出脚本返回的 `url`（就是 PubMed 结果页），加前 5 条预览（标题 / 期刊 / 年份）。
- 明确请用户打开链接核对，并说明可以要求：换检索词、加过滤（年份 / 综述 / RCT / 人类研究）、缩减到 20 篇以内。
- **用户确认前不要下载、不要建 Zotero 集合。**

## 第 3 步 下载全文并导入 Zotero

```powershell
python "...\pubmed_query.py" --term "<最终检索式>" --max 20 --out "<工作目录>\records.json"
python "...\fetch_fulltext.py" --records "<工作目录>\records.json" --outdir "<工作目录>\papers"
python "...\zotero_import.py" --records "<工作目录>\records.json" --manifest "<工作目录>\papers\fulltext_manifest.json" --collection "<集合名>"
```

- 集合名默认 `<主题简称> <YYYY-MM-DD>`；先问用户是否沿用，同名集合存在时脚本复用，不会重复建。
- 单次上限 20 篇（用户明确要更多时按 20 一批，每批确认后再跑）。
- 下载完成后报告全文覆盖率：`pdf+xml` / `pdf` / `xml` 算有全文，`abstract_only` 算缺全文，列出缺全文的 PMID + 标题。
- 导入后检查 `zotero_import_report.json`：新建 / 已存在 / 失败 / 附件成功数。失败要如实说原因。
- 缺全文的条目照样入库（元数据 + 摘要），在报告里标「需手动获取全文」，绝不说成已下载。
- 不要让用户手动导入；只有脚本确实失败时才退回到「导出 RIS + 手动导入」并说明原因。

## 第 4 步 逐篇速览

```powershell
python "...\extract_text.py" --records "<工作目录>\records.json" --manifest "<工作目录>\papers\fulltext_manifest.json" --outdir "<工作目录>\texts"
```

- 逐篇读 `texts\<pmid>.txt`（文件头 `TextSource` 标明来源：`fulltext_xml` / `pdf` / `abstract_only`）。
- 每篇输出 2–4 行，固定两栏：
  - **做了什么**：研究设计、样本量、干预/暴露、对照、随访。
  - **达到什么效果**：关键结果 + 具体数字（效应量、差值、P 值、发生率），不要只写「有效」「改善」。
  - 可选第三行：局限 / 与主题的关系。
- `abstract_only` 的篇目写「仅摘要，结论待全文核实」，并降低措辞强度。
- 数字必须能在文中找到；找不到就写「文中未报告」，不要推算或编造。
- `summary.md` 与 Obsidian 笔记用同一套结构，**全程不要用 Markdown 表格**（用户明确不喜欢表格样式）。Obsidian 写入 `D:\电脑云盘\文档\obsidian\AI\Trae\<主题简称>-文献速览-<YYYYMMDD>.md`，结构如下：

  ```markdown
  # <主题> — 文献速览

  - 检索日期：YYYY-MM-DD
  - 检索库：PubMed
  - 检索式：`...`
  - 命中：N 篇；入库：相关度前 M 篇
  - 链接：<PubMed 链接>
  - Zotero 集合：`<集合名>`

  ## 文献概览

  - 文献1 · PMID 34394960 · 2021 Nat Rev Mater · 综述
  - 文献2 · PMID ... · ...

  ## 文献1 <英文原题>
  ### 文献做了什么
  内容
  ### 达到了什么效果
  内容

  ## 文献2 <英文原题>
  ...

  ## 整体结论

  1. ...

  ## 缺全文清单

  - PMID 36316378 · <标题> — 付费墙
  ```

  序号从 1 递增；`abstract_only` 的篇目在「达到了什么效果」末尾标注「（仅摘要，未核验全文）」，并在缺全文清单里注明原因。写完在回复里给出该笔记的路径。

## 第 5 步 交付报告

向用户报告：

1. Zotero 集合名、条目数、PDF 附件数。
2. 全文覆盖率（X/Y）与缺全文清单。
3. 速览文档路径（`summary.md`）、Obsidian 笔记路径，以及最值得先读的 2–3 篇。
4. PubMed 检索链接。
5. 提示：Zotero 客户端同步后即可看到新集合，PDF 会自动挂到对应条目下。

## 硬规则

- 单次 ≤ 20 篇；超出必须分批并逐批确认。
- 只下载开放获取来源（Europe PMC / PMC OA / 出版社开放 PDF / OpenAlex / Unpaywall）；不碰付费墙、不碰 Sci-Hub 类站点，遇到 403 不反复硬闯。
- 不编造全文、数据、影响因子；未核验的写「暂未核验」。
- API key 只落盘 `~\.pubmed-literature\zotero.json`，不在回复、日志、报告里回显。
- Zotero 只走 Web API 写入，绝不直接改 `zotero.sqlite`。
- 脚本报错时如实说明并给替代方案（手动下载 PDF 放回 Zotero），不要假装成功。

## 常见问题

| 现象 | 处理 |
|------|------|
| 多条 `abstract_only` | 该文不在 PMC 开放子集，OpenAlex/Unpaywall 也没有合法 PDF；提示用户在机构网络下手动下载，条目已入库 |
| PDF 下载 403 | 出版社反爬，标记缺全文，不重试硬闯 |
| 上传附件失败 | key 缺 `Allow file writing`，让用户重新生成 key 后重跑 `--set-key` |
| 集合里出现重复条目 | 脚本按 DOI/PMID 去重；若仍重复，让用户在 Zotero 里合并 |
| 检索结果太多 | 回第 1 步加年份、`[pt]` 限定或收窄人群词，再确认 |
