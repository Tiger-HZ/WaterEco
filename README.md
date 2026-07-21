# 水生态环境知识库（WaterEco）

> 面向水生态环境领域**研究人员（专家）与管理人员（领导）**的知识中台。
> 自动采集 · 分类整理 · 元数据标注 · 知识图谱 · 向量化 · RAG 检索 · 每日推送。
> 固定访问入口：**https://tiger-hz.github.io/WaterEco/**

参照 [双碳知识库](https://tiger-hz.github.io/DualCarbon/) 架构，按杭州市生态环境局水生态环境处职责改造。

---

## 一、功能

1. **自动采集与跟踪**：政策法规标准、研究文献、领导讲话、技术产品、实践案例（工程/技术/管理）、专家团队、科研院所与企业。
2. **团队共享知识库**：统一沉淀，按 7 大类结构化分类与元数据标注。
3. **智能处理**：向量化（TF-IDF）、知识图谱实体抽取（`kg_terms`）、元数据三位一体。
4. **RAG 检索**：向量 + 关键词（BM25）+ 元数据融合（RRF），支持抽取式问答与主题报告。
5. **每日推送**：网页链接形式推送，按分类 + 时段（近7天→更早）组织，可回看历史日期。

## 二、分类（7 类，对齐水生态环境处职责）

| key | 名称 |
| --- | --- |
| `policy` | 政策法规与顶层设计 |
| `standard` | 标准与技术规范 |
| `basin_eng` | 流域治理与工程实践 |
| `management` | 管理实践与制度创新 |
| `tech` | 技术、产品与监测装备 |
| `literature` | 研究文献与调查评估 |
| `intl_region` | 国际与区域动态 |

质量等级：`A 重点` / `B 中等` / `C 一般`。区域优先级：浙江 / 杭州最高，其次长三角与重点流域，国外仅收录高度相关且可借鉴内容。

## 三、目录结构

```
WaterEco/
├── index.html            # 一体化门户 SPA（运行时读取 kb/kb.json）
├── feed.html             # 每日推送首页（无 JS 环境/深链回退）
├── push-YYYY-MM-DD.html  # 单日推送（可回看任意一天）
├── push-YYYY.html        # 年度聚合推送
├── archive.html          # 全部知识库（时间轴 + 筛选）
├── rag.html / rag.py     # 混合检索 RAG（纯前端引擎 + Python 引擎）
├── config.json           # 项目配置（名称/固定链接/微信过滤阈值等）
├── kb/
│   ├── kb.json           # 单一数据源（知识库本体，只增不删）
│   └── seed_items.py     # 种子数据生成器
├── crawl_gov.py          # 政府政策库采集（含水相关度过滤）
├── crawl_weixin.py       # 微信公众号采集（含阅读量/关注数过滤）
├── crawl_academic.py     # 开源学术库（OpenAlex）采集
├── merge.py              # 去重合并 inbox → kb
├── enrich_importance.py  # 质量 / 水相关度 / 重要性
├── enrich_kg.py          # 知识图谱实体抽取
├── render.py             # 静态站点渲染
├── update.py             # 一键更新入口（merge→enrich→render）
├── gh_deploy.py          # GitHub API 兜底部署
├── .github/workflows/pages.yml  # GitHub Pages 自动部署
└── docs/知识库建设方案.md
```

## 四、本地运行

```bash
# 1) 生成/重置种子数据（覆盖 kb/kb.json）
python3 kb/seed_items.py

# 2) 每日更新（合并 + 富集 + 渲染）
python3 update.py

# 3) 启动本地预览
python3 -m http.server 8080
#   打开 http://localhost:8080/        （门户 SPA，可点击"知识图谱""RAG 检索"）
#   打开 http://localhost:8080/feed.html （无 JS 回退首页）
```

采集（需联网）：

```bash
python3 crawl_gov.py          # 政府政策库（默认水生态关键词）
python3 crawl_weixin.py       # 微信公众号（按 config.json weixin_filter 阈值过滤）
python3 crawl_academic.py     # 学术文献（OpenAlex）
python3 update.py             # 合并 + 富集 + 渲染
```

## 五、微信内容质量过滤

`config.json → weixin_filter` 集中配置阈值：

- 公众号关注人数 ≥ `min_follower`（默认 5000）**或** 阅读量 ≥ `min_read`（默认 2000）；
- 且水相关度 ≥ 0.6 才入库；
- 阅读量 ≥ `high_value_read`（默认 10000）自动提级；
- 政务/权威号（`crawl_weixin.py` 中 `AUTH_ACCOUNTS`）视为官方权威，放宽阈值。

> 说明：搜狗微信搜索不直接返回阅读量/关注数；精确数值需对原文页抓取或接入第三方接口。脚本已内置阈值逻辑，待 `metrics` 字段就位后自动生效。

## 六、部署到 GitHub Pages（固定链接）

1. 将本仓库推送到 `Tiger-HZ/WaterEco`（或你的仓库）。
2. 仓库 **Settings → Pages → Source = GitHub Actions**。
3. 推送 `main` 分支，`.github/workflows/pages.yml` 自动发布到：
   **https://tiger-hz.github.io/WaterEco/**
4. 团队直接收藏该链接，每天点开即看最新；支持回看历史日期版本。

## 七、每日信息量

目标每日约 **30 条**（结合实际可多可少）。质量门槛：门户可按质量/区域/分类筛选，确保不漏高质量、不堆低质量。

---

*由团队知识库协同助手生成并初始化。知识库是持续运营的体系，欢迎补充文档、标注负责人与更新时间。*
