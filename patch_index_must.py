# -*- coding: utf-8 -*-
"""
必知必会重构补丁（2026-10-03，按用户要求）

用户反馈两个问题：
  ①【排版 bug】同一个分层标题（如「一、法律（水生态环境条线根本依据）」）重复出现很多次
     —— 根因：分组时比较的是**原始 must_rank 数值**（101→102 就触发一次分新组），
        应按**分层标签**比较（101 与 150 同属「一、法律」）
  ②【内容组织】必知必会应分两类：
     · 基础必知 = 领域基本功（法律 / 行政法规 / 国标 / 行标 / 法规 / 规章），
       可能是多年前发布的，但最基础、最入门、最重要（如生态环境法典、GB 3838 地表水环境质量标准）
     · 新知速递 = 近期（近 3 年）重要的各级政策、标准、规划、报告

改动：
  1. 修复分组 bug（按 mustRankLabel 判断）
  2. 新增 mustCategory(r) 判定「基础 / 新知」
  3. mustRender 改为两个区块：基础必知（分层展示）+ 新知速递（按时间倒序）
  4. 统计栏同步显示两类数量

用法：python3 patch_index_must.py
"""
import argparse
import os

BASE = os.path.dirname(os.path.abspath(__file__))

NEW_MUST = '''function mustRankLabel(n){ n=Number(n)||999; for(const [a,b,l] of MUST_RANK_LABEL){ if(n>=a&&n<=b) return l; } return '十二、其他'; }
/* 必知必会两类（2026-10-03 按用户要求）：
   基础必知 = 基础性文件类型（法律/行政法规/标准/法规/规章）—— 不分年份，
              它们是领域基本功，如《生态环境法典》《地表水环境质量标准》(GB 3838)
   新知速递 = 其他类型（政策文件/规划/报告/管理机制等）中近 3 年内发布的 */
const MUST_BASE_LEVELS = ['法律','行政法规','部门规章','规范性文件','国家标准','行业标准',
                          '地方性法规','省政府规章','地方政府规章','地方标准','法规标准','政府规章'];
function mustCategory(r){
  const lv = r.content_type || '';
  if (MUST_BASE_LEVELS.indexOf(lv) >= 0) return 'base';
  const y = String(effDate(r)||'').slice(0,4);
  const nowY = new Date().getFullYear();
  if (y && (nowY - Number(y) <= 3)) return 'newly';
  return 'other';
}
/* 入选规则（2026-09-23 优化）
   核心必知 = 数据中带 must_know 标记（人工逐条核实并核定层级）
   高分补充 = 按【知识类别】判定：政策法规/标准规范/规划与报告/管理机制 → 重要性 ≥8.5；
              杭州本地 ≥8.0、浙江省级 ≥8.2（本单位重点放宽）；权威实践案例（A级）≥8.0 */
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
}'''

NEW_RENDER = '''function mustRender(){
  const host=$('#mustGrid'); if(!host) return;
  const q=App.mustQ, reg=App.mustRegion, kind=App.mustKind, tier=App.mustTier;
  const core=[], auto=[];
  (App.kb||[]).forEach(r=>{
    const t=mustMatch(r); if(!t) return;
    if(q && (((r.title||'')+' '+(r.summary||'')).toLowerCase().indexOf(q)<0)) return;
    if(reg && r.region!==reg) return;
    if(kind && (r.kclass||'')!==kind) return;
    (t==='core'?core:auto).push(r);
  });
  core.sort((a,b)=> (Number(a.must_rank)||999)-(Number(b.must_rank)||999)
        || (Number(b.importance)||0)-(Number(a.importance)||0)
        || effDate(b).localeCompare(effDate(a)));
  auto.sort((a,b)=> (Number(b.importance)||0)-(Number(a.importance)||0) || effDate(b).localeCompare(effDate(a)));

  // —— 合并后按「基础必知 / 新知速递」两类拆分 ——
  const all = core.concat(auto);
  const base = all.filter(r=>mustCategory(r)==='base');
  const newly= all.filter(r=>mustCategory(r)==='newly');
  const other= all.filter(r=>mustCategory(r)==='other');
  // 基础必知按 must_rank 分层排序（法律→行政法规→标准→法规…）
  base.sort((a,b)=> (Number(a.must_rank)||9999)-(Number(b.must_rank)||9999)
        || (Number(b.importance)||0)-(Number(a.importance)||0));
  newly.sort((a,b)=> effDate(b).localeCompare(effDate(a)));

  $('#mustStat').innerHTML='<span class="chip">基础必知 <b>'+base.length+'</b> 条</span>'
    +'<span class="chip">新知速递 <b>'+newly.length+'</b> 条</span>'
    + (other.length? '<span class="chip">其他 <b>'+other.length+'</b> 条</span>':'')
    +'<span class="chip">数据来源：政府/部委官网 · 人大法规库 · 标准平台，逐条核实</span>';

  let html='', rank=0;
  const showCore = (tier==='all'||tier==='core'), showAuto=(tier==='all'||tier==='auto');

  // ===== 第一类：基础必知（按文件层级分层，同层合并到一个标题下）=====
  if(base.length){
    html+='<div class="must-sec">📚 基础必知 <span class="muted">领域基本功：法律、行政法规、国家标准、行业标准、地方性法规与规章 —— 不分发布年份，都是入门与依据之本</span></div>';
    let curLbl=null, buf=[];
    const flush=()=>{ if(!buf.length) return;
      html+='<div class="must-group">'+esc(curLbl)+' <span class="n">'+buf.length+' 条</span></div>';
      html+='<div class="must-list">'+buf.map(r=>mustCard(r, rank++)).join('')+'</div>';
      buf=[]; };
    base.forEach(r=>{
      const lbl = mustRankLabel(Number(r.must_rank)||999);   // ← 按【分层标签】比较，修复重复分组
      if(curLbl===null) curLbl=lbl;
      if(lbl!==curLbl){ flush(); curLbl=lbl; }
      buf.push(r);
    });
    flush();
  }

  // ===== 第二类：新知速递（近 3 年重要文件，按时间倒序）=====
  if(newly.length){
    html+='<div class="must-sec">🆕 新知速递 <span class="muted">近 3 年重要的各级政策、标准、规划与报告（按发布时间倒序）</span></div>';
    html+='<div class="must-list">'+newly.map(r=>mustCard(r, rank++)).join('')+'</div>';
  }
  if(other.length){
    html+='<div class="must-sec">📎 其他相关 <span class="muted">时间较早且非基础性文件</span></div>';
    html+='<div class="must-list">'+other.map(r=>mustCard(r, rank++)).join('')+'</div>';
  }
  host.innerHTML = html || '<div class="empty">暂无匹配条目</div>';
}'''


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

    # 1) 替换 mustRankLabel + mustMatch（旧块）
    old_head = """function mustRankLabel(n){ n=Number(n)||999; for(const [a,b,l] of MUST_RANK_LABEL){ if(n>=a&&n<=b) return l; } return '十二、其他'; }"""
    # 定位旧的 mustRankLabel..mustMatch 整块：从 mustRankLabel 行到 mustMatch 的结尾 '}' 前
    i = t.find(old_head)
    if i < 0:
        raise SystemExit('[FAIL] 未找到 mustRankLabel')
    j = t.find('async function loadMust()', i)
    if j < 0:
        raise SystemExit('[FAIL] 未找到 loadMust')
    t = t[:i] + NEW_MUST + "\n" + t[j:]
    n += 1
    print("  ✓ 替换 mustRankLabel/mustMatch（新增 mustCategory 两类判定）")

    # 2) 替换 mustRender
    k = t.find('function mustRender(){')
    if k < 0:
        raise SystemExit('[FAIL] 未找到 mustRender')
    m = t.find('\n}\n', k)          # mustRender 的结尾
    if m < 0:
        raise SystemExit('[FAIL] 未找到 mustRender 结尾')
    t = t[:k] + NEW_RENDER + t[m + 2:]
    n += 1
    print("  ✓ 重写 mustRender（修复分组 bug + 基础/新知两类分区）")

    # 3) 新增样式
    if '.must-sec{' not in t:
        rep(".must-group{",
            ".must-sec{font-size:15px;font-weight:700;color:var(--ink);margin:22px 0 10px;padding-left:10px;border-left:4px solid var(--teal)}\n"
            ".must-sec .muted{font-weight:400;font-size:12.5px;margin-left:6px}\n"
            ".must-group{",
            "新增 must-sec 分区标题样式")

    open(a.html, "w", encoding="utf-8").write(t)
    print("\n[ok] 必知必会共 %d 处修改" % n)


if __name__ == "__main__":
    main()
