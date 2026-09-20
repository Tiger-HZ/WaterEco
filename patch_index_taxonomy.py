# -*- coding: utf-8 -*-
"""
门户专业化升级补丁：
  1. 新增「业务域」维度（23 个受控值，来自 taxonomy.json）—— 筛选 + 左侧导航 + 卡片徽标
  2. 新增「时效性」维度（有效/已修订/已废止/待核）—— 筛选 + 卡片徽标
  3. 内嵌 TOPIC_LABELS 常量（由 taxonomy.json 生成，改词表后重跑本脚本即可同步）
所有替换均带断言，命中失败会明确报错，不会静默漏改。
用法：python3 patch_index_taxonomy.py [--html index.html] [--taxo taxonomy.json]
"""
import argparse
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=os.path.join(BASE, "index.html"))
    ap.add_argument("--taxo", default=os.path.join(BASE, "taxonomy.json"))
    a = ap.parse_args()

    tax = json.load(open(a.taxo, encoding="utf-8"))
    topics = [(t["id"], t["zh"]) for t in tax["topic"]["values"]]
    statuses = tax["status"]["values"]
    js_topics = ",".join('{id:"%s",zh:"%s"}' % (i, z) for i, z in topics)
    js_status = ",".join('"%s"' % s for s in statuses)

    t = open(a.html, encoding="utf-8").read()
    n = 0

    def rep(old, new, tag, count=1):
        nonlocal t, n
        if old not in t:
            raise SystemExit("[FAIL] 未找到锚点：%s" % tag)
        t = t.replace(old, new, count)
        n += 1
        print("  ✓ %s" % tag)

    # ---------- 1) 常量：业务域 + 时效 ----------
    if "const TOPICS =" not in t:
        rep("const CAT_LABELS = {",
            "/* 业务域受控词表（由 taxonomy.json 生成，勿手改；改词表后重跑 patch_index_taxonomy.py） */\n"
            "const TOPICS = [%s];\n"
            "const TOPIC_LABELS = {}; TOPICS.forEach(x=>TOPIC_LABELS[x.id]=x.zh);\n"
            "const STATUSES = [%s];\n"
            "const CAT_LABELS = {" % (js_topics, js_status),
            "注入 TOPICS / STATUSES 常量")

    # ---------- 2) 工具条：两个新筛选项 ----------
    if 'id="kbTopic"' not in t:
        rep('<select id="kbQuality"><option value="">全部质量</option><option value="A">A 高</option><option value="B">B 中</option><option value="C">C 低</option></select>',
            '<select id="kbTopic" title="按水生态环境业务域筛选"><option value="">全部业务域</option></select>\n'
            '      <select id="kbQuality"><option value="">全部质量</option><option value="A">A 高</option><option value="B">B 中</option><option value="C">C 低</option></select>\n'
            '      <select id="kbStatus" title="按文件时效性筛选"><option value="">全部时效</option></select>',
            "工具条新增业务域/时效筛选")

    # ---------- 3) 过滤逻辑 ----------
    rep("""        quality=$('#kbQuality').value, only_full=$('#kbFull').checked;""",
        """        quality=$('#kbQuality').value, only_full=$('#kbFull').checked,
        topic=$('#kbTopic')?$('#kbTopic').value:'', status=$('#kbStatus')?$('#kbStatus').value:'';""",
        "loadKB 读取业务域/时效")

    rep("""    if(quality) arr=arr.filter(r=>r.quality===quality);
    if(only_full) arr=arr.filter(r=>r.content_fetched);""",
        """    if(quality) arr=arr.filter(r=>r.quality===quality);
    if(topic) arr=arr.filter(r=>(r.topic||[]).includes(topic));
    if(status) arr=arr.filter(r=>r.status===status);
    if(only_full) arr=arr.filter(r=>r.content_fetched);""",
        "loadKB 业务域/时效过滤")

    # ---------- 4) 左侧导航：新增"按业务域" ----------
    rep('<span class="seg" id="kbBrowse"><button class="on" data-b="category">按分类</button><button data-b="department">按部门</button><button data-b="time">按时间</button></span>',
        '<span class="seg" id="kbBrowse"><button class="on" data-b="topic">按业务域</button><button data-b="category">按分类</button><button data-b="department">按部门</button><button data-b="time">按时间</button></span>',
        "导航切换按钮新增按业务域")

    rep("""  else if(dim==='department'){ $('#kbDept').value=val||''; }""",
        """  else if(dim==='department'){ $('#kbDept').value=val||''; }
  else if(dim==='topic'){ $('#kbTopic').value=val||''; }""",
        "kbNavPick 支持业务域")

    rep("""function clearKbNav(){ App.kbExpanded={}; $('#kbCat').value=''; $('#kbDept').value='';""",
        """function clearKbNav(){ App.kbExpanded={}; $('#kbCat').value=''; $('#kbDept').value=''; if($('#kbTopic'))$('#kbTopic').value=''; if($('#kbStatus'))$('#kbStatus').value='';""",
        "clearKbNav 清空业务域/时效")

    rep("""  const curCat=$('#kbCat').value, curDept=$('#kbDept').value, curRange=App.kbRange;""",
        """  const curCat=$('#kbCat').value, curDept=$('#kbDept').value, curRange=App.kbRange;
  const curTopic=$('#kbTopic')?$('#kbTopic').value:'', curStatus=$('#kbStatus')?$('#kbStatus').value:'';""",
        "renderKbNav 读取当前业务域")

    rep("""  html+='<div class="nav-i'+(!curCat&&!curDept&&!curRange?' on':'')+'" onclick="clearKbNav()">全部</div>';
  if(dim==='category'){""",
        """  html+='<div class="nav-i'+(!curCat&&!curDept&&!curRange&&!curTopic&&!curStatus?' on':'')+'" onclick="clearKbNav()">全部</div>';
  if(dim==='topic'){
    const cnt={}; arr.forEach(r=>(r.topic||[]).forEach(x=>cnt[x]=(cnt[x]||0)+1));
    TOPICS.forEach(o=>{ const c=cnt[o.id]; if(!c) return;
      html+='<div class="nav-i'+(curTopic===o.id?' on':'')+'" onclick="kbNavPick(\\'topic\\',\\''+o.id+'\\')">'+o.zh+' <b>'+c+'</b></div>'; });
  } else if(dim==='category'){""",
        "renderKbNav 渲染业务域导航")

    # ---------- 5) 卡片徽标 ----------
    rep("""  const realDate=(r.date||'').slice(0,10);""",
        """  const topb = (r.topic&&r.topic.length)? r.topic.slice(0,2).map(x=>`<span class="badge b-topic" title="业务域">${esc(TOPIC_LABELS[x]||x)}</span>`).join('') : '';
  const stCls = r.status==='已废止'?'b-dead':(r.status==='已修订'?'b-rev':'b-on');
  const stb = r.status? `<span class="badge ${stCls}" title="文件时效性">${esc(r.status)}</span>` : '';
  const wbb = (r.waterbody&&r.waterbody.length)? r.waterbody.slice(0,2).map(x=>`<span class="badge b-wb" title="重点水体/流域">${esc(x)}</span>`).join('') : '';
  const realDate=(r.date||'').slice(0,10);""",
        "recCard 计算业务域/时效/水体徽标")

    rep("""      ${r.category?`<span class="badge b-cat">${esc(CAT_LABELS[r.category]||r.category)}</span>`:''}""",
        """      ${r.category?`<span class="badge b-cat">${esc(CAT_LABELS[r.category]||r.category)}</span>`:''}
      ${topb}
      ${wbb}
      ${stb}""",
        "recCard 展示业务域/水体/时效徽标")

    # ---------- 6) 启动时填充选项 ----------
    rep("""  fillSelect($('#kbRegion'), REGIONS);""",
        """  fillSelect($('#kbRegion'), REGIONS);
  if($('#kbTopic')) fillSelect($('#kbTopic'), TOPICS.map(o=>o.id), id=>TOPIC_LABELS[id]);
  if($('#kbStatus')) fillSelect($('#kbStatus'), STATUSES);""",
        "启动填充业务域/时效选项")

    # ---------- 7) 徽标样式 ----------
    if ".b-topic{" not in t:
        rep(".must-note{font-size:11.5px;color:var(--amber);margin-top:4px}",
            ".must-note{font-size:11.5px;color:var(--amber);margin-top:4px}\n"
            ".b-topic{background:#e8f0fe;color:#1a4b8c;border-color:#c3d6f5}\n"
            ".b-wb{background:#e6f5f2;color:#0f6b5c;border-color:#bfe5dd}\n"
            ".b-on{background:#e9f7ee;color:#146b34;border-color:#c2e6ce}\n"
            ".b-rev{background:#fff4e0;color:#8a5a00;border-color:#f0dcb0}\n"
            ".b-dead{background:#f1f2f4;color:#6a7280;border-color:#dcdfe4;text-decoration:line-through}",
            "新增徽标样式")

    open(a.html, "w", encoding="utf-8").write(t)

    # 记录业务域/时效受控值到站点（供前端与外部读取）
    print("\n[ok] 共完成 %d 处修改" % n)
    print("[info] 业务域 %d 个｜时效 %d 个" % (len(topics), len(statuses)))


if __name__ == "__main__":
    main()
