# -*- coding: utf-8 -*-
"""水生态环境知识库 · 静态站点渲染脚本（纯标准库，从 kb/kb.json 渲染）。
核心视图：
 - index.html       ：一体化门户 SPA（手写，运行时读取 kb/kb.json）—— 本脚本不覆盖它
 - feed.html        ：每日推送首页（最新一期 + 历史推送导航 + 可回看任意一天）—— 无 JS 环境/深链回退
 - push-YYYY-MM-DD.html ：单日推送（该日新增/入库的知识，前后日导航、筛选）
 - archive.html     ：全部知识库（按发布日期时间轴 + 时段/分类/区域/质量筛选）
增量机制：kb/kb.json 只增不删（merge.py 去重），render 每次由最新 added_at 生成推送。
用法：python render.py
"""
import json, os, datetime, re

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")
CONFIG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
FEEDBACK = CONFIG.get("feedback_url", "")
OWNER = CONFIG.get("owner_email", "")
PNAME = CONFIG.get("project_name", "水生态环境知识库")

# 七大分类（对齐杭州市生态环境局水生态环境处职责）
CATS = [
    ("policy", "政策法规与顶层设计", "#0e7490"),
    ("standard", "标准与技术规范", "#0f766e"),
    ("basin_eng", "流域治理与工程实践", "#1d4ed8"),
    ("management", "管理实践与制度创新", "#7c3aed"),
    ("tech", "技术、产品与监测装备", "#0891b2"),
    ("literature", "研究文献与调查评估", "#db2777"),
    ("intl_region", "国际与区域动态", "#475569"),
]
CATNAME = {c[0]: c[1] for c in CATS}
CATCOLOR = {c[0]: c[2] for c in CATS}
CAT_ORDER = {c[0]: i for i, c in enumerate(CATS)}
QUAL_NAME = {"A": "高", "B": "中", "C": "一般"}
HOT_REGIONS = {"浙江", "杭州", "长三角"}

# 部门优先级维度：生态环境条线优先，水利/住建/发改/农业农村为重点
PRIORITY = [
    ("生态环境", "#0e7490", ("生态", "环境")),
    ("水利", "#1d4ed8", ("水利",)),
    ("住建", "#b45309", ("住建",)),
    ("发展改革", "#0f766e", ("发改",)),
    ("农业农村", "#7c3aed", ("农业", "农村")),
    ("其他", "#64748b", ()),
]


def scope_of(dept):
    d = dept or ""
    for name, color, keys in PRIORITY:
        if any(k in d for k in keys):
            return name, color
    return PRIORITY[-1][0], PRIORITY[-1][1]


def priority_rank(dept):
    d = dept or ""
    for idx, (name, color, keys) in enumerate(PRIORITY):
        if any(k in d for k in keys):
            return idx
    return len(PRIORITY) - 1


def load_kb():
    if os.path.exists(KB):
        try:
            return json.load(open(KB, encoding="utf-8"))
        except Exception:
            return []
    return []


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def card(item):
    q = item.get("quality", "C")
    cat = item.get("category", "")
    title = esc(item.get("title", ""))
    url = esc(item.get("url", "#"))
    source = esc(item.get("source", ""))
    date = esc(item.get("date", ""))
    dept = esc(item.get("department", ""))
    region = esc(item.get("region", ""))
    summary = esc(item.get("summary", ""))
    tags = "".join('<span class="tag">%s</span>' % esc(t) for t in item.get("tags", []))
    scope_name, scope_color = scope_of(item.get("department", ""))
    full = item.get("content_fetched")
    fullbadge = '<span class="full yes">全文入库</span>' if full else '<span class="full no">摘要</span>'
    vis = item.get("visibility")
    visbadge = '<span class="vis">%s</span>' % esc(vis) if vis else ""
    met = ""
    m = item.get("metrics") or {}
    if m.get("read"):
        rd = m["read"]
        rd = ("%.1f万" % (rd / 10000)) if rd >= 10000 else str(rd)
        met = '<span class="metric">👁 %s阅读</span>' % rd
    hot = " hot" if region in HOT_REGIONS else ""
    text = (item.get("title", "") + " " + item.get("summary", "") + " " + source + " " +
            region + " " + " ".join(item.get("tags", [])) + " " + " ".join(item.get("kg_terms", []))).lower()
    return ('''<article class="card" data-cat="%s" data-region="%s" data-qual="%s" '''
            'data-ctype="%s" data-date="%s" data-text="%s">'
            '<div class="card-top"><span class="badge" style="background:%s">%s</span>'
            '<span class="q q-%s">质量 %s</span></div>'
            '<h3 class="card-title"><a href="%s" target="_blank" rel="noopener">%s</a></h3>'
            '<div class="meta">%s · %s · <span class="dept">%s</span> · <span class="region%s">%s</span> · %s%s%s</div>'
            '<p class="summary">%s</p><div class="tags">%s</div></article>') % (
        cat, region, q, esc(item.get("content_type", "")), date, esc(text), CATCOLOR.get(cat, "#475569"),
        CATNAME.get(cat, cat), q, QUAL_NAME.get(q, q), url, title, source, date, dept, hot, region,
        fullbadge, visbadge, met, summary, tags)


CSS = """
:root{--bg:#eef3f6;--card:#fff;--ink:#16242c;--sub:#5c6f78;--line:#d8e3e9;--teal:#0e7490;--teal-d:#0c5e76;--blue:#1d4ed8;--chip:#e6f1f5;--shadow:0 1px 3px rgba(13,60,80,.10)}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.62}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px 70px}
header.top{background:linear-gradient(125deg,#0c5e76,#0e7490 60%,#0891b2);color:#fff;padding:24px 20px 18px}
header.top .inner{max-width:1180px;margin:0 auto}
header.top h1{margin:0;font-size:22px;font-weight:800;letter-spacing:.5px}
header.top p{margin:6px 0 0;opacity:.92;font-size:13px}
header.top .up{margin-top:10px;font-size:12px;opacity:.85}
.statbar{max-width:1180px;margin:14px auto;padding:0 20px;display:flex;gap:9px;flex-wrap:wrap}
.chip{background:#fff;border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:13px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.chip b{color:var(--teal-d)}
.bar{position:sticky;top:0;z-index:20;background:rgba(238,243,246,.96);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:12px 0;margin-bottom:14px}
.bar .inner{max-width:1180px;margin:0 auto;padding:0 20px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input#search{flex:1;min-width:200px;padding:9px 12px;border:1px solid var(--line);border-radius:9px;font-size:14px}
select{padding:9px 10px;border:1px solid var(--line);border-radius:9px;font-size:13.5px;background:#fff}
.pills{display:flex;gap:6px;flex-wrap:wrap}
.pill{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 12px;font-size:12.5px;cursor:pointer;color:#334}
.pill.active{background:var(--teal);color:#fff;border-color:var(--teal)}
.winbtns{display:flex;gap:6px;flex-wrap:wrap}
.winbtn{border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 10px;font-size:12.5px;cursor:pointer;color:#334}
.winbtn.active{background:#0e7490;color:#fff;border-color:#0e7490}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
section.block{margin:22px 0 8px}
section.block h2{color:var(--teal-d);font-size:17px;margin:0 0 10px;padding-bottom:6px;border-bottom:2px solid #cfe7ef}
section.block h2 .cnt{color:var(--sub);font-size:13px;font-weight:400;margin-left:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px;box-shadow:0 1px 3px rgba(0,0,0,.05);transition:transform .12s,box-shadow .12s;display:flex;flex-direction:column}
.card:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(13,60,80,.14)}
.card-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;gap:6px;flex-wrap:wrap}
.badge{color:#fff;border-radius:7px;padding:3px 10px;font-size:12px;font-weight:700}
.q{font-size:11.5px;padding:2px 8px;border-radius:6px;font-weight:700}
.q-A{background:#dcfce7;color:#166534}.q-B{background:#fef9c3;color:#854d0e}.q-C{background:#e5e7eb;color:#4b5563}
.vis{font-size:10.5px;padding:1px 7px;border-radius:5px;font-weight:600;background:#eef4f8;color:#3a5a6a}
.full{font-size:10.5px;padding:1px 7px;border-radius:5px;font-weight:600}
.full.yes{background:#e6f6ee;color:#0c5e76}.full.no{background:#f1f5f4;color:#8a978f}
.metric{font-size:10.5px;padding:1px 7px;border-radius:5px;font-weight:600;background:#fff1e6;color:#9a3412}
.card-title{margin:0 0 6px;font-size:15.5px;line-height:1.45}
.card-title a{color:#0c2b35}
.meta{font-size:12px;color:var(--sub);margin-bottom:7px}
.dept{background:#e6f1f5;color:#0c5e76;border-radius:5px;padding:1px 7px}
.region{background:#eef2ff;color:#1d4ed8;border-radius:5px;padding:1px 7px}
.region.hot{background:#fef3c7;color:#92400e;font-weight:700}
.summary{font-size:13.5px;color:#37424a;margin:0 0 9px;flex:1}
.tags{display:flex;gap:5px;flex-wrap:wrap}
.tag{background:#eef4f6;color:#3c5963;border-radius:5px;padding:1px 7px;font-size:11.5px}
.fab{position:fixed;right:22px;bottom:22px;z-index:50;background:var(--teal);color:#fff;border:none;border-radius:999px;padding:13px 18px;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 6px 18px rgba(13,60,80,.3)}
.count{font-size:13px;color:var(--sub);margin:0 20px 8px;max-width:1180px}
.pushnav{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 4px}
.pday{border:1px solid var(--line);background:#fff;border-radius:8px;padding:7px 12px;font-size:13px;cursor:pointer;color:#334;text-decoration:none}
.pday:hover{background:#eaf6fa;border-color:var(--teal)}
.pday.cur{background:var(--teal);color:#fff;border-color:var(--teal)}
.phead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:18px 0 2px}
.phead h2{margin:0;color:var(--teal-d);font-size:18px}
.phead .sub{color:var(--sub);font-size:13px}
.pager{display:flex;gap:10px;margin:10px 0 4px}
.pager a{border:1px solid var(--line);background:#fff;border-radius:8px;padding:7px 13px;font-size:13px;color:#0c5e76;text-decoration:none}
.pager a:hover{background:#eaf6fa}
.pager .dis{color:#b9c2be;border:1px dashed var(--line);background:#fafbfa}
.note{background:#eaf6fa;border:1px solid #cfe7ef;color:#0c5e76;border-radius:9px;padding:9px 13px;font-size:13px;margin:10px 0}
.empty{text-align:center;color:var(--sub);padding:40px;font-size:14px}
footer{margin-top:40px;border-top:1px solid var(--line);padding-top:16px;color:var(--sub);font-size:12.5px;text-align:center}
footer a{color:var(--teal)}
"""

# 与门户一致的时段窗口
WINDOWS = [
    ("all", "全部"), ("7", "近7天"), ("30", "近1月"), ("90", "近3月"),
    ("180", "近半年"), ("2026", "2026年"), ("2025", "2025年"),
    ("2024", "2024年"), ("2023", "2023年"), ("3y", "近三年"), ("older", "2023年前"),
]

JS = """
<script>
function daysBetween(d){var t=new Date(),y=new Date(d);return (t-y)/86400000;}
function inWindow(d,win){
  if(win==='all')return true;
  var dt=new Date(d);if(isNaN(dt))return false;
  if(win==='7')return daysBetween(d)<=7;
  if(win==='30')return daysBetween(d)<=30;
  if(win==='90')return daysBetween(d)<=90;
  if(win==='180')return daysBetween(d)<=180;
  if(win==='3y')return daysBetween(d)<=365*3;
  if(win==='older')return dt.getFullYear()<2023;
  return dt.getFullYear()===parseInt(win);
}
function applyFilter(){
  var q=(document.getElementById('search').value||'').toLowerCase().trim();
  var region=document.getElementById('fregion').value;
  var qual=document.getElementById('fqual').value;
  var cat=document.getElementById('fcat').value;
  var ctype=document.getElementById('fctype').value;
  var win=document.getElementById('fwin')?document.getElementById('fwin').value:'all';
  var cards=document.querySelectorAll('.card');var count=0;
  cards.forEach(function(c){
    var ok=true;
    if(q && c.dataset.text.indexOf(q)===-1) ok=false;
    if(cat && c.dataset.cat!==cat) ok=false;
    if(region && c.dataset.region!==region) ok=false;
    if(ctype && c.dataset.ctype!==ctype) ok=false;
    if(qual && c.dataset.qual!==qual) ok=false;
    if(win && win!=='all' && !inWindow(c.dataset.date,win)) ok=false;
    c.style.display=ok?'':'none'; if(ok)count++;
  });
  document.querySelectorAll('section.block').forEach(function(s){
    var any=Array.prototype.some.call(s.querySelectorAll('.card'),function(c){return c.style.display!=='none';});
    s.style.display=any?'':'none';
  });
  var ce=document.getElementById('count'); if(ce) ce.textContent='当前显示 '+count+' 条';
}
function bind(){
  document.getElementById('search').addEventListener('input',applyFilter);
  ['fregion','fqual','fctype'].forEach(function(id){var e=document.getElementById(id);if(e)e.addEventListener('change',applyFilter);});
  var w=document.getElementById('fwin'); if(w) w.addEventListener('change',applyFilter);
  document.querySelectorAll('.pill').forEach(function(p){
    p.addEventListener('click',function(){
      document.querySelectorAll('.pill').forEach(function(x){x.classList.remove('active');});
      p.classList.add('active');
      document.getElementById('fcat').value=p.dataset.cat; applyFilter();
    });
  });
  document.querySelectorAll('.winbtn').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.winbtn').forEach(function(x){x.classList.remove('active');});
      b.classList.add('active');
      document.getElementById('fwin').value=b.dataset.win; applyFilter();
    });
  });applyFilter();
}
document.addEventListener('DOMContentLoaded',bind);
</script>
"""


def dept_region_ctype_options(items):
    depts = sorted(set(i.get("department", "") for i in items if i.get("department")))
    regions = sorted(set(i.get("region", "") for i in items if i.get("region")))
    ctypes = sorted(set(i.get("content_type", "") for i in items if i.get("content_type")))
    return depts, regions, ctypes


def build_filters(with_window=True, with_cat=True):
    dept_opts = "".join('<option value="%s">%s</option>' % (esc(d), esc(d)) for d in DEPTS)
    region_opts = "".join('<option value="%s">%s</option>' % (esc(r), esc(r)) for r in REGIONS)
    ctype_opts = "".join('<option value="%s">%s</option>' % (esc(t), esc(t)) for t in CTYPES)
    qual_opts = "".join('<option value="%s">质量 %s</option>' % (k, v) for k, v in QUAL_NAME.items())
    cat_pills = '<button class="pill active" data-cat="">全部分类</button>' + "".join(
        '<button class="pill" data-cat="%s">%s</button>' % (c[0], c[1]) for c in CATS)
    win_btns = "".join('<button class="winbtn" data-win="%s">%s</button>' % (w[0], w[1]) for w in WINDOWS)
    inner = '<input id="search" placeholder="搜索标题 / 摘要 / 标签 / 来源 / 部门 / 实体…">'
    if with_cat:
        inner += '<div class="pills">%s</div>' % cat_pills
    if with_window:
        inner += '<div class="winbtns">%s</div>' % win_btns
    inner += ('<select id="fregion"><option value="">全部区域</option>%s</select>'
              '<select id="fctype"><option value="">全部类型</option>%s</select>'
              '<select id="fqual"><option value="">全部质量</option>%s</select>'
              '<input type="hidden" id="fcat" value="">') % (region_opts, ctype_opts, qual_opts)
    return inner


def day_disp(d):
    return "未知日期" if d == "unknown" else d


def push_nav(days, current):
    parts = []
    for d in days:
        n = len(PUSHES[d])
        cls = "pday cur" if d == current else "pday"
        parts.append('<a class="%s" href="push-%s.html">%s <b>%d</b></a>'
                     % (cls, esc(d), esc(day_disp(d)), n))
    return '<div class="pushnav">%s</div>' % "".join(parts)


def pager(days, day):
    idx = days.index(day)
    newer = days[idx - 1] if idx - 1 >= 0 else None
    older = days[idx + 1] if idx + 1 < len(days) else None
    left = ('<a href="push-%s.html">← 更早：%s</a>' % (esc(older), esc(day_disp(older)))) if older else '<span class="dis">← 最早</span>'
    right = ('<a href="push-%s.html">更新：%s →</a>' % (esc(newer), esc(day_disp(newer)))) if newer else '<span class="dis">最新 →</span>'
    return '<div class="pager">%s %s</div>' % (left, right)


def build_index(kb, days):
    latest_day = days[0]
    latest = PUSHES[latest_day]
    ychips = " ".join('<span class="chip">%s年 <b>%d</b></span>' % (y, n)
                     for y, n in sorted(year_stats(kb).items()))
    body = (
        '<div class="statbar">'
        '<span class="chip">知识总量 <b>%d</b> 条</span>'
        '<span class="chip">全文入库 <b>%d</b> 条</span>'
        '<span class="chip">推送期数 <b>%d</b> 期</span>'
        '<span class="chip">分类 <b>%d</b> 类</span></div>' % (
            len(kb), sum(1 for i in kb if i.get("content_fetched")), len(days), len(CATS))
        + (('<div class="statbar">%s</div>' % ychips) if ychips else '')
        + '<div class="bar"><div class="inner">%s'
          '<a href="archive.html" style="font-size:13px">全部知识库 / 时间轴 →</a>'
          '<a href="rag.html" style="margin-left:auto;font-size:13px;font-weight:700;color:#0e7490">🧠 RAG 智能检索（向量+关键词+元数据）→</a>'
          '</div></div>' % build_filters(with_window=False, with_cat=True)
        + '<div class="count" id="count"></div>'
        + '<div class="phead"><h2>最新推送</h2><span class="sub">%s · 当日新增 %d 条</span></div>'
          % (esc(latest_day), len(latest))
        + '<div class="grid">%s</div>' % "".join(card(i) for i in latest)
        + '<div class="note">📅 回看历史推送：点击下方任意日期，即可查看当日新增/入库的知识（今天也能看昨天的推送）。</div>'
        + '<div class="phead"><h2>历史推送</h2><span class="sub">共 %d 期</span></div>' % len(days)
        + push_nav(days, latest_day)
    )
    return shell("每日推送", body)


def build_push_day(day, items, days):
    depts, regions, ctypes = dept_region_ctype_options(items)
    is_year = len(day) == 4
    label = ("%s 年度" % day) if is_year else day
    sub_text = "该年度收录的知识" if is_year else "该日新增 / 入库的知识"
    sections = ""
    for cid, cname, _ in CATS:
        sub = sorted([i for i in items if i.get("category") == cid],
                     key=lambda x: x.get("date", ""), reverse=True)
        sub.sort(key=lambda x: priority_rank(x.get("department", "")))
        if not sub:
            continue
        sections += ('<section class="block"><h2>%s<span class="cnt">%d 条</span></h2>'
                     '<div class="grid">%s</div></section>') % (cname, len(sub), "".join(card(i) for i in sub))
    body = (
        '<div class="statbar"><span class="chip">本%s <b>%d</b> 条</span>'
        '<span class="chip">分类 <b>%d</b> 类</span></div>' % ("年度收录" if is_year else "日推送", len(items), len(CATS))
        + '<div class="bar"><div class="inner">%s'
          '<a href="index.html" style="margin-left:auto;font-size:13px">← 返回每日推送</a>'
          '</div></div>' % build_filters(with_window=False, with_cat=True)
        + '<div class="count" id="count"></div>'
        + '<div class="phead"><h2>推送 · %s</h2><span class="sub">%s</span></div>'
          % (esc(label), esc(sub_text))
        + pager(days, day)
        + (sections or '<div class="empty">暂无内容</div>')
        + '<div class="note">本页为 <b>%s</b> 的推送内容。回看其它日期/年度请使用上方翻页或'
          '<a href="index.html">首页历史推送</a>。</div>' % esc(label)
    )
    return shell("推送 · " + label, body)


def build_archive(kb):
    items = sorted(kb, key=lambda x: x.get("date", ""), reverse=True)
    depts, regions, ctypes = dept_region_ctype_options(items)
    by_date = {}
    for i in items:
        by_date.setdefault(i.get("date") or i.get("added_at") or "未知日期", []).append(i)
    dates = sorted(by_date.keys(), reverse=True)
    sections = ""
    for dt in dates:
        sub = sorted(by_date[dt], key=lambda x: (CAT_ORDER.get(x.get("category"), 99), priority_rank(x.get("department", ""))))
        sections += ('<section class="block"><h2>%s<span class="cnt">%d 条</span></h2>'
                     '<div class="grid">%s</div></section>') % (dt, len(sub), "".join(card(i) for i in sub))
    body = (
        '<div class="statbar"><span class="chip">历史累计 <b>%d</b> 条</span>'
        '<span class="chip">覆盖 <b>%d</b> 个发布日期</span></div>' % (len(items), len(dates))
        + '<div class="bar"><div class="inner">%s'
          '<a href="index.html" style="margin-left:auto;font-size:13px">← 返回每日推送</a>'
          '</div></div>' % build_filters(with_window=True, with_cat=True)
        + '<div class="count" id="count"></div>'
        + sections
    )
    return shell("全部知识库 · 时间轴", body)


def shell(subtitle, body):
    up = ("水生态环境知识中台 ｜ 生成于 %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>%s</title><style>%s</style></head><body>' % (PNAME, CSS)
        + '<header class="top"><div class="inner"><h1>%s</h1><p>%s · %s</p>'
          '<div class="up">%s</div></div></header>' % (PNAME, CONFIG.get("description", ""), subtitle, up)
        + '<div class="wrap">' + body + '</div>'
        + '<button class="fab" onclick="location.href=\'%s\'">💬 留言反馈</button>' % FEEDBACK
        + '<footer>水生态环境知识库 · 自动采集更新（内容入库，支持 <a href="rag.html" style="color:#fff;text-decoration:underline">RAG 智能检索</a> / 知识图谱）｜ 团队反馈：'
          '<a href="%s" target="_blank" rel="noopener">填写问卷</a> ｜ 负责人邮箱 '
          '<a href="mailto:%s">%s</a><br>%s</footer>' % (
            FEEDBACK, OWNER, OWNER, CONFIG.get("update_note", ""))
        + JS + '</body></html>'
    )
    return html


DEPTS, REGIONS, CTYPES = [], [], []
PUSHES = {}


def year_stats(items):
    ys = {}
    for i in items:
        y = (i.get("date") or "")[:4]
        if y.isdigit():
            ys[y] = ys.get(y, 0) + 1
    return ys


def main():
    global DEPTS, REGIONS, CTYPES, PUSHES
    kb = load_kb()
    if not kb:
        print("no kb")
        return
    DEPTS, REGIONS, CTYPES = dept_region_ctype_options(kb)
    RECENT_DAYS = 180
    cutoff = (datetime.date.today() - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    raw_days = {}
    for i in kb:
        raw = i.get("added_at") or i.get("date") or ""
        key = raw if re.match(r"^\d{4}-\d{2}-\d{2}$", raw or "") else "unknown"
        raw_days.setdefault(key, []).append(i)
    PUSHES = {}
    for day, items in raw_days.items():
        if day == "unknown" or day >= cutoff:
            PUSHES[day] = items
        else:
            PUSHES.setdefault(day[:4], []).extend(items)
    days = sorted(PUSHES.keys(), reverse=True)
    open(os.path.join(BASE, "feed.html"), "w", encoding="utf-8").write(build_index(kb, days))
    for day in days:
        open(os.path.join(BASE, "push-%s.html" % day), "w", encoding="utf-8").write(
            build_push_day(day, PUSHES[day], days))
    open(os.path.join(BASE, "archive.html"), "w", encoding="utf-8").write(build_archive(kb))
    print("rendered: feed.html (fallback), %d push pages (recent day + year), archive.html (kb total=%d) | index.html=SPA门户(未覆盖)" % (len(days), len(kb)))


if __name__ == "__main__":
    main()
