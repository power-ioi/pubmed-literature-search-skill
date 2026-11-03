# PubMed 检索式构建参考

## 1. 先拆概念（PICO）

| 元素 | 含义 | 检索用途 |
|------|------|----------|
| P | 人群 / 疾病 | `hypertension[MeSH]` OR `hypertensive patients[tiab]` |
| I | 干预 / 暴露 | `probiotics[MeSH]` OR `probiotic*[tiab]` |
| C | 对照 | `placebo[tiab]` OR `usual care[tiab]` |
| O | 结局 | `blood pressure[tiab]` OR `office blood pressure[tiab]` |
| S | 研究类型 | `randomized controlled trial[pt]` / `systematic review[pt]` |

同一概念内用 OR，不同概念之间用 AND。同义词至少覆盖：MeSH 词、英美拼写、单复数、缩写、常用替代词。

## 2. 常用字段标签

| 标签 | 作用 | 示例 |
|------|------|------|
| `[MeSH Terms]` | 规范主题词（含下位词） | `hypertension[MeSH Terms]` |
| `[tiab]` | 标题+摘要 | `gut microbiota[tiab]` |
| `[ti]` | 仅标题 | `probiotic[ti]` |
| `[pt]` | 出版类型 | `randomized controlled trial[pt]` |
| `[dp]` | 出版日期 | `2018:2026[dp]` |
| `[la]` | 语言 | `english[la]` |
| `[tw]` | 全文任意词 | `Akkermansia[tw]` |
| `[au]` | 作者 | `Cani PD[au]` |

日期区间也可写成 `("2018/01/01"[Date - Publication] : "2026/12/31"[Date - Publication])`。

## 3. 研究类型过滤

- RCT：`AND randomized controlled trial[pt]`
- 系统综述 / meta：`AND (systematic review[pt] OR meta-analysis[pt])`
- 观察性：`AND (cohort[tiab] OR observational[tiab] OR prospective[tiab])`
- 排除动物：`NOT (animals[MeSH] NOT humans[MeSH])`
- 排除综述：`NOT review[pt]`

## 4. 三套方案的典型差异

| 方案 | 写法 | 适用 |
|------|------|------|
| 精准 | MeSH + `[pt]` + 年份 | 只要高质量证据，20 篇以内 |
| 均衡 | MeSH + 1–2 个同义词 `[tiab]` | 常规检索，覆盖主要文献 |
| 宽泛 | 全部同义词 OR 展开 | 摸清领域全貌，用于筛查热点 |

## 5. 用 PubMed 自己的翻译结果校验

`pubmed_query.py --terms` 返回的 `queryTranslation` 就是 PubMed 实际执行的检索式，重点看：

- 你的词有没有被映射到 MeSH（`"hypertension"[MeSH Terms]`）；
- 是否因为拼写被拆成奇怪的单复数变体；
- AND/OR 优先级是否被括号固定住。

## 6. 常见坑

- 词组不加引号会被拆词：`gut microbiota` 要写 `"gut microbiota"[tiab]`。
- 缩写歧义（如 `FMT`）要补全称：`(fecal microbiota transplantation[tiab] OR FMT[tiab])`。
- 英美拼写不同：`randomised` / `randomized` 各来一遍。
- 动物实验混入：加 `NOT animals[MeSH]`，但要小心把人类研究也排掉，先看命中量。
- 结果 > 5000 条时先加年份或 `[pt]`，不要直接下载前 20 条（相关性排序未必符合主题）。
