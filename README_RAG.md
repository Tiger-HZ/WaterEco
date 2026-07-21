# 双碳领域知识库 · 知识入库与 RAG 检索说明

> 本文件回答三个问题：**知识存哪了 / 怎么查看调用 / RAG 怎么实现（技术链路与工具）**。

## 一、知识存到哪了

所有采集到的双碳知识统一写入一个 JSON 知识库文件：

```
digest/kb/kb.json        # 统一知识库（唯一事实源，只增不删，去重合并）
digest/kb/inbox.json     # 待合并采集箱（脚本采集后先放这里）
digest/kb/backfill_state.json  # 三年历史回填进度
```

每一条记录的字段（已全文抓取的会带 `content` + `content_fetched:true`）：

| 字段 | 含义 |
|---|---|
| title / url / source | 标题 / 原文链接 / 来源 |
| date / region | 发布日期 / 地域（全国·浙江·杭州…） |
| category | 八大分类之一（policy/standard/carbon_market/synergy/industry/tech/literature/intl_region） |
| department | 部门（生态环境 / 发改委 / 经信(工信) / 市场监管 / 其他），决定部门优先级排序 |
| quality | 质量等级 A(高)/B(中)/C(一般) |
| summary | 摘要（始终存在） |
| content | 全文（全文抓取成功后写入，否则回退用 summary） |
| content_fetched | 是否已抓取全文 |
| tags / added_at / _ev | 标签 / 入库日期 / 去重事件指纹 |

**当前规模**：59 条文献、40 条已全文入库、约 200+ 检索分块。

## 二、怎么查看 / 调用

### 1. 浏览器（最常用）
- 站点首页（每日推送）：`https://tiger-hz.github.io/DualCarbon/`
- 全部知识库 / 时间轴：`https://tiger-hz.github.io/DualCarbon/archive.html`
- **RAG 智能检索界面**：`https://tiger-hz.github.io/DualCarbon/rag.html`
  - 支持「检索 / 问答 / 报告」三种模式
  - 可按 部门 / 分类 / 质量 / 日期 过滤；可勾选「优先生态环境条线」
  - 纯前端运行，数据不出浏览器；支持本地 `file://` 打开时手动选择 kb.json

### 2. 命令行（适合自动化 / 批量）
```bash
cd digest
python rag_cli.py search "碳排放双控制度 生态环境" --topk 8 --cat policy
python rag_cli.py ask   "全国碳市场覆盖哪些行业？钢铁水泥铝何时纳入？"
python rag_cli.py report "杭州市 碳达峰试点" --topk 12 > report.md
python rag_cli.py stats
```
依赖：仅 Python 标准库（**无需 numpy / 任何第三方包**）。

### 3. 作为 API / 嵌入其他系统（Python）
```python
from rag import RAG
r = RAG("kb/kb.json").build()
hits = r.search("碳排放双控 生态环境", top_k=5, prefer_eco=True)
print(r.ask("全国碳市场覆盖哪些行业？")["answer"])
```
返回结构化结果（title/url/date/department/category/quality/chunk/score），
可直接喂给大模型做生成式问答，或嵌入业务系统。

## 三、RAG 是怎么实现的（技术链路与工具）

```
[采集] 公众号/政府站/新闻  ──►  seed.py / backfill.py  ──►  kb/inbox.json
        │
        ▼
[清洗去重]  merge.py（norm_url + 事件指纹去重）  ──►  kb/kb.json
        │
        ▼
[全文抓取]  fetch_fulltext.py（urllib 下载 + 正文抽取，纯标准库）──► content / content_fetched
        │
        ▼
[渲染站点]  render.py  ──►  index.html / archive.html / push-*.html  ──►  GitHub Pages
        │
        ▼
[RAG 引擎]  rag.py（核心，纯标准库）
   ├─ 中文分词：CJK 二元组 + 拉丁/数字词（无需 jieba）
   ├─ 切块：按句切分，~320 字/块，带重叠
   ├─ 向量匹配：TF-IDF + 余弦相似度（自实现，无 numpy）
   ├─ 关键词匹配：BM25（自实现）
   ├─ 元数据过滤：部门优先级 / 分类 / 日期 / 质量
   ├─ 融合重排：倒数排名融合 RRF（向量 + BM25）+ 生态环境条线加权
   └─ 应用层：抽取式问答 ask() / 主题报告 report()
        │
        ▼
[应用入口]  rag_cli.py（命令行）  +  rag.html（浏览器，JS 复刻同一套检索）
```

### 关键技术决策
- **零第三方依赖**：沙箱无法安装 numpy/rank_bm25，整套 RAG（TF-IDF、BM25、RRF）全部用 Python / JS 标准能力手写，保证可移植、可长期运行。
- **中文检索**：未引入分词库，采用「字符二元组 + 术语词」方案，对政策术语（如「碳排放双控」「碳市场」）命中稳定。
- **混合检索优于单路**：纯向量在稀有术语上易漏，纯关键词不擅长语义；RRF 融合两者，并用部门优先级做业务加权（生态环境条线优先）。
- **可外接 LLM**：当前问答为「抽取式」（从原文抽相关句 + 引用）。如需生成式回答，把 `search/ask` 的命中片段作为上下文传给任意大模型即可，本仓库不绑定特定模型。

### 已落地 / 可扩展
- ✅ 已落地：采集→入库→全文抓取→静态站点→混合 RAG（CLI + 网页）→每日自动化推送
- 🔧 可扩展（按需）：① 每日自动生成 RAG 简报并随推送发出；② 接入大模型做生成式问答；③ 知识图谱（实体/政策关系）可视；④ 向量库升级为 FAISS/pgvector（数据量大时）

## 四、自动化与持续运行
- 每日 08:00 自动采集 + 渲染 + 推送 GitHub Pages（自动化 `automation-1784299073674`）。
- 每 2 天 03:00 自动补三年历史（自动化 `automation-1784299073874`）。
- 站点托管于 GitHub Pages **固定链接，永不停机**；即使多日不打开，下次访问即最新（由 Actions 在每次推送时部署）。

## 五、安全提醒
GitHub 部署使用的 Personal Access Token 仅具备仓库写入权限，建议用后在
GitHub → Settings → Developer settings → Personal access tokens 中**吊销**，
后续自动化部署如需继续，可改用 Deploy Key 或重新生成。
