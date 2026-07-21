# -*- coding: utf-8 -*-
"""双碳知识库 · 混合检索 RAG 引擎（纯标准库，无需 numpy / 第三方依赖）。
能力：
  - 中文友好分词（CJK 二元组 + 拉丁/数字词）
  - TF-IDF 向量相似度（向量匹配）
  - BM25 关键词检索（关键词/术语匹配）
  - 元数据过滤（部门优先级 / 分类 / 日期 / 质量等级）
  - 倒数排名融合 RRF（向量 + BM25 融合 + 元数据加权）
  - 抽取式问答与主题报告生成（离线，无需 LLM；可外接 LLM 生成）
用法：
  from rag import RAG
  r = RAG("kb/kb.json"); r.build()
  hits = r.search("碳排放双控制度 生态环境", top_k=5, prefer_eco=True)
  print(r.ask("全国碳市场覆盖哪些行业？"))
  print(r.report("杭州市 碳达峰试点", top_k=12))
"""
import os, re, json, math

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KB = os.path.join(BASE, "kb", "kb.json")

# 部门优先级（与 render.py 一致）：生态环境条线优先
PRIORITY = ["生态环境部门", "发展改革委", "经信(工信)", "市场监管局", "其他相关部门"]
PRIORITY_KEYS = [("生态", "环境"), ("发改",), ("经信", "工信"), ("市场监管",), ()]
def dept_priority(dept):
    d = dept or ""
    for i, keys in enumerate(PRIORITY_KEYS):
        if keys and any(k in d for k in keys):
            return i
    return len(PRIORITY) - 1

CJK = re.compile(r"[\u4e00-\u9fff]+")
LAT = re.compile(r"[a-zA-Z0-9]+")
SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")


def tokenize(text):
    """中文二元组 + 拉丁/数字词。"""
    if not text:
        return []
    text = text.lower()
    toks = []
    for w in LAT.findall(text):
        if len(w) >= 1:
            toks.append(w)
    for run in CJK.findall(text):
        n = len(run)
        if n == 1:
            toks.append(run)
        else:
            for i in range(n - 1):
                toks.append(run[i:i + 2])
    return toks


def split_sentences(text):
    text = re.sub(r"\s+", "", text)
    parts = SENT_SPLIT.split(text)
    return [p for p in parts if len(p) >= 4]


def chunk_text(text, max_chars=320, overlap=1):
    """按句子切块，约 max_chars 一块，overlap 为相邻块共享句子数。"""
    sents = split_sentences(text)
    if not sents:
        return [text] if text else []
    chunks, cur, cur_len = [], [], 0
    for s in sents:
        if cur_len + len(s) > max_chars and cur:
            chunks.append("".join(cur))
            if overlap and len(cur) > overlap:
                cur = cur[-overlap:]
                cur_len = sum(len(x) for x in cur)
            else:
                cur, cur_len = [], 0
        cur.append(s)
        cur_len += len(s)
    if cur:
        chunks.append("".join(cur))
    return chunks or [text]


class RAG:
    def __init__(self, kb_path=DEFAULT_KB):
        self.kb_path = kb_path
        self.records = []
        self.chunks = []          # list of dict: {rid, text, tokens, dept, cat, date, quality, source}
        self.df_vec = {}          # token -> doc freq (in chunks)
        self.df_bm = {}
        self.avgdl = 0
        self.N = 0
        self._vec = []            # per chunk tf-idf vector (dict token->weight), L2 normed
        self._built = False

    # ---------- 构建 ----------
    def build(self):
        self.records = json.load(open(self.kb_path, encoding="utf-8"))
        self.chunks = []
        for rid, r in enumerate(self.records):
            body = r.get("content") or r.get("summary") or ""
            if not body:
                continue
            for ci, ctext in enumerate(chunk_text(body)):
                self.chunks.append({
                    "rid": rid,
                    "ci": ci,
                    "text": ctext,
                    "dept": r.get("department", ""),
                    "cat": r.get("category", ""),
                    "date": r.get("date", ""),
                    "quality": r.get("quality", ""),
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "region": r.get("region", ""),
                })
        # 构建 BM25 / 向量统计
        self.N = len(self.chunks)
        self.avgdl = sum(len(c["text"]) for c in self.chunks) / max(self.N, 1)
        for c in self.chunks:
            toks = tokenize(c["text"])
            c["_tok"] = toks
            c["_tf"] = self._term_freq(toks)
            for t in c["_tf"]:
                self.df_bm[t] = self.df_bm.get(t, 0) + 1
        # 向量 df（基于查询时再算 idf，这里先算 df）
        for c in self.chunks:
            for t in c["_tf"]:
                self.df_vec[t] = self.df_vec.get(t, 0) + 1
        # 预计算各块向量
        self._vec = []
        for c in self.chunks:
            vec = {}
            dl = len(c["text"]) or 1
            for t, f in c["_tf"].items():
                idf = math.log((self.N - self.df_vec[t] + 0.5) / (self.df_vec[t] + 0.5)) + 1.0
                tf = 1.0 + math.log(f)
                vec[t] = tf * idf
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self._vec.append({t: w / norm for t, w in vec.items()})
        self._built = True
        return self

    @staticmethod
    def _term_freq(toks):
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        return tf

    # ---------- 检索核 ----------
    def _vector_search(self, qtok, top=200):
        qtf = self._term_freq(qtok)
        qvec = {}
        for t, f in qtf.items():
            df = self.df_vec.get(t, 0)
            if df == 0:
                continue
            idf = math.log((self.N - df + 0.5) / (df + 0.5)) + 1.0
            qvec[t] = (1.0 + math.log(f)) * idf
        qn = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        qvec = {t: w / qn for t, w in qvec.items()}
        scored = []
        for i, vec in enumerate(self._vec):
            dot = 0.0
            # 只遍历查询词命中的维度
            for t, w in qvec.items():
                if t in vec:
                    dot += w * vec[t]
            if dot > 0:
                scored.append((dot, i))
        scored.sort(reverse=True)
        return scored[:top]

    def _bm25_search(self, qtok, top=200, k1=1.5, b=0.75):
        scored = []
        for i, c in enumerate(self.chunks):
            score = 0.0
            dl = len(c["text"]) or 1
            for t in set(qtok):
                df = self.df_bm.get(t, 0)
                if df == 0:
                    continue
                f = c["_tf"].get(t, 0)
                if f == 0:
                    continue
                idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avgdl))
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return scored[:top]

    # ---------- 对外 API ----------
    def search(self, query, top_k=8, filters=None, prefer_eco=False, fusion_k=60):
        """混合检索。filters: {dept, cat, region, quality_min, date_from, date_to}"""
        if not self._built:
            self.build()
        filters = filters or {}
        qtok = tokenize(query)
        vs = self._vector_search(qtok)
        bs = self._bm25_search(qtok)
        # RRF 融合
        rrf = {}
        for rank, (s, i) in enumerate(vs):
            rrf[i] = rrf.get(i, 0) + 1.0 / (fusion_k + rank + 1)
        for rank, (s, i) in enumerate(bs):
            rrf[i] = rrf.get(i, 0) + 1.0 / (fusion_k + rank + 1)
        # 元数据优先加权
        cand = []
        for i, score in rrf.items():
            c = self.chunks[i]
            if not self._pass_filter(c, filters):
                continue
            boost = 1.0
            if prefer_eco and dept_priority(c["dept"]) == 0:
                boost = 1.6
            cand.append((score * boost, i))
        cand.sort(reverse=True)
        out = []
        for score, i in cand[:top_k]:
            c = self.chunks[i]
            r = self.records[c["rid"]]
            out.append({
                "title": c["title"], "url": c["url"], "date": c["date"],
                "department": c["dept"], "category": c["cat"], "quality": c["quality"],
                "region": c["region"], "chunk": c["text"], "score": round(score, 4),
                "rid": c["rid"], "ci": c["ci"],
                "source_summary": r.get("summary", ""),
            })
        return out

    def _pass_filter(self, c, f):
        if f.get("dept") and f["dept"] not in (c["dept"] or ""):
            return False
        if f.get("cat") and f["cat"] != c["cat"]:
            return False
        if f.get("region") and f["region"] not in (c["region"] or ""):
            return False
        if f.get("quality_min"):
            order = {"A": 3, "B": 2, "C": 1}
            if order.get(c["quality"], 0) < order.get(f["quality_min"], 0):
                return False
        if f.get("date_from") and (c["date"] or "") < f["date_from"]:
            return False
        if f.get("date_to") and (c["date"] or "") > f["date_to"]:
            return False
        return True

    # ---------- 抽取式问答 ----------
    def ask(self, question, top_k=6, prefer_eco=True):
        hits = self.search(question, top_k=top_k, prefer_eco=prefer_eco)
        if not hits:
            return {"answer": "未在知识库中检索到相关内容。", "citations": []}
        # 抽取与问题最相关句子
        qset = set(tokenize(question))
        best_sent = []
        for h in hits:
            for s in split_sentences(h["chunk"]):
                overlap = len(qset & set(tokenize(s)))
                if overlap > 0:
                    best_sent.append((overlap, s, h))
        best_sent.sort(key=lambda x: (-x[0], -len(x[1])))
        seen = set()
        picked = []
        for ov, s, h in best_sent:
            key = s[:20]
            if key in seen:
                continue
            seen.add(key)
            picked.append(s)
            if len(picked) >= 5:
                break
        answer = "。".join(picked) + "。" if picked else hits[0]["chunk"]
        cites = [{"title": h["title"], "url": h["url"], "date": h["date"],
                  "department": h["department"]} for h in hits]
        return {"answer": answer, "citations": cites, "raw_hits": hits}

    # ---------- 主题报告 ----------
    def report(self, topic, top_k=12, prefer_eco=True):
        hits = self.search(topic, top_k=top_k, prefer_eco=prefer_eco)
        if not hits:
            return "# 报告\n\n未检索到与「%s」相关的内容。" % topic
        lines = ["# 双碳情报 · 主题检索报告：%s\n" % topic]
        lines.append("> 检索命中 %d 个知识片段（向量匹配 + BM25 关键词 + 元数据融合），按相关度排序。\n" % len(hits))
        # 按部门归类统计
        from collections import Counter
        deptc = Counter(h["department"] for h in hits)
        lines.append("## 一、命中分布（部门）")
        for d, n in deptc.most_common():
            lines.append("- %s：%d 条" % (d or "其他", n))
        lines.append("\n## 二、核心要点（抽取）")
        qset = set(tokenize(topic))
        shown = 0
        for h in hits:
            sents = [s for s in split_sentences(h["chunk"])
                     if len(set(tokenize(s)) & qset) > 0]
            if not sents:
                sents = [h["chunk"][:160]]
            lines.append("\n### %s 〔%s · %s · %s〕" % (
                h["title"], h["department"], h["date"], h["quality"]))
            for s in sents[:3]:
                lines.append("- %s" % s)
            lines.append("  来源：%s" % h["url"])
            shown += 1
            if shown >= 10:
                break
        lines.append("\n## 三、参考文件清单")
        for i, h in enumerate(hits[:12], 1):
            lines.append("%d. [%s](%s) — %s / %s" % (i, h["title"], h["url"], h["department"], h["date"]))
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    r = RAG().build()
    q = sys.argv[1] if len(sys.argv) > 1 else "碳排放双控制度 生态环境"
    mode = sys.argv[2] if len(sys.argv) > 2 else "search"
    if mode == "ask":
        res = r.ask(q)
        print("【回答】", res["answer"])
        print("\n【引用】")
        for c in res["citations"][:6]:
            print(" -", c["title"], c["url"])
    elif mode == "report":
        print(r.report(q))
    else:
        for h in r.search(q, top_k=5):
            print("[%.4f] %s | %s | %s" % (h["score"], h["title"][:30], h["department"], h["date"]))
            print("   ", h["chunk"][:90])
