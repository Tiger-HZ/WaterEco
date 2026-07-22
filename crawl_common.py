# -*- coding: utf-8 -*-
"""采集公共工具：限流请求、回溯游标、分类、inbox 安全追加。
供 crawl_crossref / crawl_europepmc / crawl_academic 复用，保证：
 - 礼貌池 + 429 指数退避，避免被学术 API 限流；
 - 回溯游标（kb/cursor.json）让每次运行抓取比上一次“更旧”的批次，单调增长、零重复；
 - 多爬虫可安全向同一 inbox 追加，不会互相覆盖。
"""
import os, json, time, ssl, urllib.request, urllib.parse, re, datetime, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "kb", "inbox.json")
CURSOR = os.path.join(BASE, "kb", "cursor.json")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {
    "User-Agent": "Mozilla/5.0 (WaterEcoBot; +https://github.com/Tiger-HZ/WaterEco)",
    "Accept": "application/json",
}

# ——— 限流 GET（礼貌池 + 429 退避）———
_rl_state = [0.0]


def _ratelimit(min_gap=0.3):
    while True:
        now = time.time()
        left = _rl_state[0] + min_gap - now
        if left <= 0:
            _rl_state[0] = now
            return
        time.sleep(min(left, 0.5))


def safe_get(url, mail=None, min_gap=0.3, timeout=30, max_retries=8):
    if mail and "mailto=" not in url and ("crossref.org" in url or "openalex.org" in url):
        url = url + ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(mail)
    backoff = 4
    for _ in range(max_retries):
        _ratelimit(min_gap)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                if r.status == 429:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 90)
                    continue
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(backoff)
                backoff = min(backoff * 2, 90)
                continue
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


# ——— 回溯游标 ———
def load_cursor():
    if os.path.exists(CURSOR):
        try:
            return json.load(open(CURSOR, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cursor(cur):
    os.makedirs(os.path.dirname(CURSOR), exist_ok=True)
    json.dump(cur, open(CURSOR, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ——— 分类（水生态 7 类）———
def classify(title, text):
    t = ((title or "") + " " + (text or "")).lower()
    if re.search(r"standard|规范|guideline|指南|method|gb/|gb |methodolog", t):
        return "standard"
    if re.search(r"technology|technolog|技术|material|monitor|监测|equipment|装备|wetland|湿地|生态修复|sensor|模型|model|remediation|修复", t):
        return "tech"
    if re.search(r"management|governance|管理|policy|政策|补偿|institution|regulat|法律|law|permit|排污许可", t):
        return "management"
    if re.search(r"basin|river|lake|流域|湖|河|watershed|stream|riverine", t):
        return "basin_eng"
    if re.search(r"international|跨国|transboundary|eu |europe|美国|united states|\bwfd\b|framework directive|european", t):
        return "intl_region"
    return "literature"


def looks_china(title, text):
    return bool(re.search(r"china|中国|中华|中文", (title or "") + " " + (text or ""), re.I))


# ——— 分类（政策感知版，用于政府政策库 / 微信）———
def classify_cat(title, summary, is_lit=False):
    """水生态 7 类（政策感知）：意见/批复/通知/规划/方案/办法/条例 等行政公文归政策或管理，
    仅当标题确为“标准/规范”（含 GB/HJ/DB/ISO 代号或“排放标准/技术规范/技术指南”）才归 standard。
    避免把“提到标准的意见/批复”误判为 standard。"""
    if is_lit:
        return "literature"
    title = title or ""
    hay = title + " " + (summary or "")
    # 1) 行政公文类型优先判为政策/管理（意见/批复/通知/函/决定/规划/纲要/方案→政策；
    #    办法/规定/令/公告/答记者问/细则→管理），确保绝不进入“标准规范”类。
    if re.search(r"(意见|批复|通知|函|决定|规划|纲要|行动方案|实施方案|行动计划|工作方案)", title):
        return "policy"
    if re.search(r"(办法|规定|令|公告|答记者问|细则|条例)", title):
        return "management"
    # 2) 标准/规范（含代号或明确类型）→ 标准规范
    if re.search(r"(GB|HJ|DB\d{2}|ISO|TB)\s*[/T]?\s*\d|排放标准|技术规范|技术指南|"
                 r"行业标准|国家标准|地方标准|团体标准", hay):
        return "standard"
    if re.search(r"标准|规范", title):
        return "standard"
    # 3) 管理/制度类
    if re.search(r"生态补偿|横向补偿|流域协作|跨省|河长制|排污许可|断面考核|再生水|"
                 r"数字治水|水源保护|管理办法|条例|规定|制度", hay):
        return "management"
    # 4) 流域治理/工程
    if re.search(r"黑臭|流域治理|美丽河湖|水源地|水生态修复|河湖|治理工程|流域", hay):
        return "basin_eng"
    # 5) 技术/监测
    if re.search(r"技术|监测|装备|材料|智慧水务|修复|湿地|模型", hay):
        return "tech"
    # 6) 国际
    if re.search(r"国际|全球|欧盟|美国|莱茵|跨国|transboundary", hay, re.I):
        return "intl_region"
    # 7) 研究
    if re.search(r"研究|论文|调查|评估|承载力", hay):
        return "literature"
    return "policy"


# ——— 分类（学术收紧版，用于 OpenAlex/Crossref/EuropePMC）———
def classify_academic(title, text):
    """学术文献分类（收紧）：默认 literature；仅当明确讨论标准（含代号）才归 standard，
    其余按主题归 basin_eng/management/tech/intl_region，避免把“提到 standard 的研究论文”误判为标准。"""
    t = ((title or "") + " " + (text or "")).lower()
    if re.search(r"\b(gb|hj|db\d{2}|iso|tb)\b", (title or "").lower()) or \
       re.search(r"排放标准|技术规范|技术指南|国家标准|行业标准|地方标准", t):
        return "standard"
    if re.search(r"basin|river|lake|流域|湖|河|watershed|riverine", t):
        return "basin_eng"
    if re.search(r"management|governance|管理|policy|政策|补偿|institution|regulat|法律|law|permit|排污许可", t):
        return "management"
    if re.search(r"technology|technolog|技术|material|monitor|监测|equipment|装备|wetland|湿地|"
                 r"生态修复|remediation|修复|sensor|模型|model", t):
        return "tech"
    if re.search(r"international|跨国|transboundary|eu |europe|美国|united states|\bwfd\b|framework directive|european", t):
        return "intl_region"
    return "literature"


# ——— inbox 安全追加 ———
def append_inbox(records):
    inbox = json.load(open(INBOX, encoding="utf-8")) if os.path.exists(INBOX) else []
    inbox.extend(records)
    os.makedirs(os.path.dirname(INBOX), exist_ok=True)
    json.dump(inbox, open(INBOX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(inbox)


def today():
    return datetime.date.today().isoformat()


# ——— 内容 ID（用于媒体/全文文件命名，爬虫与 extract_fulltext 必须共用，保证一致）———
def make_cid(r):
    """基于 url(优先) 或 title|date 的 sha1 前 16 位，作为 kb/full/<cid>.txt /
    kb/img/<cid>_N.* / kb/pdf/<cid>.pdf 的稳定文件名。爬虫在抓媒体时即按此命名，
    extract_fulltext 复用同一函数得到相同 cid，确保门户能正确定位媒体/全文文件。"""
    key = (r.get("url") or "").strip().lower()
    if not key:
        key = (r.get("title") or "") + "|" + (r.get("date") or "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
