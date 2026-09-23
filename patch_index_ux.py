# -*- coding: utf-8 -*-
"""
门户体验优化补丁（2026-09-23）

针对用户反馈「必知必会、知识库导航栏等展现形式与功能有不足」的问题：

一、必知必会
  1. 【核心修复】入选规则改用「知识类别 kclass」（原用旧 category 字段 + water_rel≥0.9 过严阈值，
     实测「高分补充」只选出 42 条，而用 kclass 可选出 700+ 条 —— 漏掉了大量政策法规/规划/管理机制）
     新规则：政策法规/标准规范/规划与报告/管理机制 → 按重要性阈值；本地（杭州/浙江）加权放宽；
     权威案例（A 级）纳入。既补全又不泛滥。
  2. 卡片补充「知识类别」「业务域」徽标（原来只有层级/地域，看不出属于哪个业务领域）
  3. 类别下拉改用「知识类别」而非内部层级值（更贴近业务语言）

二、知识库导航栏
  4. 新增「按地域」维度（全国/浙江/杭州/流域/国际）—— 原只有业务域/知识类别/部门/时间，
     而浙江、杭州是本单位重点，却没有地域导航
  5. 导航项在计数旁显示占比百分比（便于判断分布）

用法：python3 patch_index_ux.py
"""
import argparse
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=os.path.join(BASE, "index.html"))
    a = ap.parse_args()
    t = open(a.html, encoding="utf-8").read()
    n = 0

    def rep(old, new, tag):
        nonlocal t, n
        if old not in t:
            raise SystemExit("[FAIL] 锚点未找到：%s" % tag)
        t = t.replace(old, new, 1)
        n += 1
        print("  ✓ %s" % tag)

    # ---------- 1) 必知必会：入选规则改用 kclass ----------
    rep("""/* 入选规则：核心=数据中带 must_know 标记；高分补充=A级政策/标准类且水相关度高、重要性达标 */
function mustMatch(r){
  if(r.must_know) return 'core';
  const cat=r.category||'';
  const rel=Number(r.water_rel)||0, imp=Number(r.importance)||0;
  if(r.quality==='A' && (cat==='policy'||cat==='standard') && rel>=0.9 && imp>=7.0) return 'auto';
  return null;
}""",
        """/* 入选规则（2026-09-23 优化）
   核心必知 = 数据中带 must_know 标记（人工逐条核实并核定层级）
   高分补充 = 按【知识类别】判定（原用旧 category 字段 + water_rel≥0.9 过严阈值，漏掉大量政策/规划）：
     · 政策法规/标准规范/规划与报告/管理机制 → 重要性 ≥8.5
     · 杭州本地 ≥8.0、浙江省级 ≥8.2（本单位重点，放宽）
     · 权威实践案例（A 级）≥8.0
   这样既补全重要内容，又不会让必知必会泛滥成灾。 */
function mustMatch(r){
  if(r.must_know) return 'core';
  const kc=r.kclass||'', imp=Number(r.importance)||0, q=r.quality||'', reg=r.region||'';
  if(kc==='policy'||kc==='standard'||kc==='plan_report'||kc==='management'){
    if(imp>=8.5) return 'auto';
    if(reg==='杭州' && imp>=8.0) return 'auto';
    if(reg==='浙江' && imp>=8.2) return 'auto';
  }
  if(kc==='case_practice' && q==='A' && imp>=8.0) return 'auto';
  return null;
}""",
        "必知必会入选规则改用知识类别")

    # ---------- 2) 类别下拉改用知识类别 ----------
    rep("""    const kinds=[]; (App.kb||[]).forEach(r=>{ if(mustMatch(r) && r.content_type && kinds.indexOf(r.content_type)<0) kinds.push(r.content_type); });""",
        """    // 按【知识类别】聚合选项（比内部层级值更贴近业务语言）
    const kinds=[]; (App.kb||[]).forEach(r=>{ if(mustMatch(r) && r.kclass && kinds.indexOf(r.kclass)<0) kinds.push(r.kclass); });""",
        "必知必会类别下拉改用知识类别")

    rep("""    const sel=$('#mustKind'); if(sel){ kinds.forEach(k=>{ const o=document.createElement('option'); o.value=k; o.textContent=k; sel.appendChild(o); }); }""",
        """    const sel=$('#mustKind'); if(sel){ kinds.forEach(k=>{ const o=document.createElement('option'); o.value=k; o.textContent=(typeof KCLASS_LABELS!=='undefined'&&KCLASS_LABELS[k])||k; sel.appendChild(o); }); }""",
        "类别下拉显示中文名")

    rep("""    if(kind && (r.content_type||'')!==kind) return;""",
        """    if(kind && (r.kclass||'')!==kind) return;""",
        "类别筛选改用 kclass")

    # ---------- 3) 必知必会卡片补充徽标 ----------
    rep("""    +   '<div class="must-meta">'+(r.content_type?'<span class="badge b-cat">'+esc(r.content_type)+'</span>':'')""",
        """    +   '<div class="must-meta">'
    +     (r.kclass?'<span class="badge b-kc">'+esc(KCLASS_LABELS[r.kclass]||r.kclass)+'</span>':'')
    +     ((r.topic&&r.topic.length)?r.topic.slice(0,2).map(x=>'<span class="badge b-topic">'+esc(TOPIC_LABELS[x]||x)+'</span>').join(''):'')
    +     (r.content_type?'<span class="badge b-cat">'+esc(r.content_type)+'</span>':'')""",
        "必知必会卡片补知识类别与业务域徽标")

    # ---------- 4) 知识库导航：新增「按地域」 ----------
    rep("""<button class="on" data-b="topic">按业务域</button><button data-b="category">按知识类别</button><button data-b="department">按部门</button><button data-b="time">按时间</button>""",
        """<button class="on" data-b="topic">按业务域</button><button data-b="category">按知识类别</button><button data-b="region">按地域</button><button data-b="department">按部门</button><button data-b="time">按时间</button>""",
        "导航新增「按地域」按钮")

    rep("""  else if(dim==='topic'){ $('#kbTopic').value=val||''; }""",
        """  else if(dim==='topic'){ $('#kbTopic').value=val||''; }
  else if(dim==='region'){ $('#kbRegion').value=val||''; }""",
        "kbNavPick 支持地域")

    rep("""  } else if(dim==='category'){
    KCLASSES.forEach(o=>{ const n=arr.filter(r=>(r.kclass||'other')===o.id).length; if(!n) return;
      html+='<div class="nav-i'+(curCat===o.id?' on':'')+'" onclick="kbNavPick(\\'category\\',\\''+o.id+'\\')">'+o.zh+' <b>'+n+'</b></div>'; });""",
        """  } else if(dim==='category'){
    const tot=arr.length||1;
    KCLASSES.forEach(o=>{ const n=arr.filter(r=>(r.kclass||'other')===o.id).length; if(!n) return;
      html+='<div class="nav-i'+(curCat===o.id?' on':'')+'" onclick="kbNavPick(\\'category\\',\\''+o.id+'\\')">'+o.zh+' <b>'+n+'</b> <span class="muted">'+Math.round(100*n/tot)+'%</span></div>'; });
  } else if(dim==='region'){
    const tot=arr.length||1, regs=['杭州','浙江','流域','长三角','全国','国际'];
    const cnt={}; arr.forEach(r=>{ const k=r.region||'其他'; cnt[k]=(cnt[k]||0)+1; });
    regs.concat(Object.keys(cnt).filter(k=>regs.indexOf(k)<0)).forEach(k=>{ const n=cnt[k]; if(!n) return;
      html+='<div class="nav-i'+($('#kbRegion').value===k?' on':'')+'" onclick="kbNavPick(\\'region\\',\\''+k+'\\')">'+k+' <b>'+n+'</b> <span class="muted">'+Math.round(100*n/tot)+'%</span></div>'; });""",
        "导航渲染地域维度（含占比）")

    # 导航项占比：业务域维度也加占比
    rep("""    TOPICS.forEach(o=>{ const c=cnt[o.id]; if(!c) return;
      html+='<div class="nav-i'+(curTopic===o.id?' on':'')+'" onclick="kbNavPick(\\'topic\\',\\''+o.id+'\\')">'+o.zh+' <b>'+c+'</b></div>'; });""",
        """    const tot0=arr.length||1;
    TOPICS.forEach(o=>{ const c=cnt[o.id]; if(!c) return;
      html+='<div class="nav-i'+(curTopic===o.id?' on':'')+'" onclick="kbNavPick(\\'topic\\',\\''+o.id+'\\')">'+o.zh+' <b>'+c+'</b> <span class="muted">'+Math.round(100*c/tot0)+'%</span></div>'; });""",
        "业务域导航显示占比")

    # ---------- 5) clearKbNav 清空地域 ----------
    rep("""function clearKbNav(){ App.kbExpanded={}; $('#kbCat').value=''; $('#kbDept').value=''; if($('#kbTopic'))$('#kbTopic').value=''; if($('#kbStatus'))$('#kbStatus').value='';""",
        """function clearKbNav(){ App.kbExpanded={}; $('#kbCat').value=''; $('#kbDept').value=''; if($('#kbTopic'))$('#kbTopic').value=''; if($('#kbStatus'))$('#kbStatus').value=''; if($('#kbRegion'))$('#kbRegion').value='';""",
        "clearKbNav 同时清空地域")

    open(a.html, "w", encoding="utf-8").write(t)
    print("\n[ok] 共 %d 处修改" % n)


if __name__ == "__main__":
    main()
