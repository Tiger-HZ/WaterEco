# -*- coding: utf-8 -*-
"""
门户补丁：内容类型筛选 + 低相关文献过滤开关

背景（用户要求）：
  · 政策/规范/标准/报告/案例要「确保不遗漏」—— 需要在界面上可单独查看这些类别；
  · 学术文献按期刊分级准入，低相关/无关学科刊需可被**一键隐藏**，
    让有限的注意力集中在高质量内容上（但不删除，保留可查）。

改动：
  1. 内嵌 CONTENT_CLASSES 常量（与 content_policy.json 的 classes 对齐）
  2. 工具条新增「内容类型」下拉 + 「隐藏不相关文献」勾选（默认勾选）
  3. loadKB 增加两类过滤
  4. recCard 增加内容类型徽标（政策/报告/案例/学术…）
  5. 「知识库」统计栏显示当前过滤状态
用法：python3 patch_index_class.py
"""
import argparse
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=os.path.join(BASE, "index.html"))
    ap.add_argument("--policy", default=os.path.join(BASE, "content_policy.json"))
    a = ap.parse_args()

    pol = json.load(open(a.policy, encoding="utf-8"))
    ZH = {
        "policy_standard": "政策法规标准", "report_bulletin": "报告与规划",
        "case_practice": "实践案例", "expert_org": "专家机构",
        "tech_method": "技术方法", "academic": "学术文献", "news": "动态资讯",
    }
    order = pol.get("display_priority", {}).get("order", list(ZH.keys()))
    items = ",".join('{id:"%s",zh:"%s"}' % (c, ZH.get(c, c)) for c in order)

    t = open(a.html, encoding="utf-8").read()
    n = 0

    def rep(old, new, tag):
        nonlocal t, n
        if old not in t:
            raise SystemExit("[FAIL] 锚点未找到：%s" % tag)
        t = t.replace(old, new, 1)
        n += 1
        print("  ✓ %s" % tag)

    # 1) 常量
    if "const CONTENT_CLASSES" not in t:
        rep("const STATUSES = [",
            "/* 内容类型（与 content_policy.json 对齐；由 patch_index_class.py 生成） */\n"
            "const CONTENT_CLASSES = [%s];\n"
            "const CLASS_LABELS = {}; CONTENT_CLASSES.forEach(x=>CLASS_LABELS[x.id]=x.zh);\n"
            "const STATUSES = [" % items,
            "注入 CONTENT_CLASSES 常量")

    # 2) 工具条
    if 'id="kbClass"' not in t:
        rep('<label class="chk"><input type="checkbox" id="kbFull"> 仅全文</label>',
            '<select id="kbClass" title="按内容类型筛选"><option value="">全部内容类型</option></select>\n'
            '      <label class="chk"><input type="checkbox" id="kbFull"> 仅全文</label>\n'
            '      <label class="chk" title="隐藏未达期刊门槛或主题不相关的文献（不影响库中存在，仅不显示）">'
            '<input type="checkbox" id="kbNoNoise" checked> 只看高相关</label>',
            "工具条新增内容类型与高相关开关")

    # 3) 过滤
    rep("""        topic=$('#kbTopic')?$('#kbTopic').value:'', status=$('#kbStatus')?$('#kbStatus').value:'';""",
        """        topic=$('#kbTopic')?$('#kbTopic').value:'', status=$('#kbStatus')?$('#kbStatus').value:'',
        cls=$('#kbClass')?$('#kbClass').value:'', noNoise=$('#kbNoNoise')?$('#kbNoNoise').checked:false;""",
        "loadKB 读取内容类型与高相关开关")

    rep("""    if(topic) arr=arr.filter(r=>(r.topic||[]).includes(topic));
    if(status) arr=arr.filter(r=>r.status===status);""",
        """    if(topic) arr=arr.filter(r=>(r.topic||[]).includes(topic));
    if(status) arr=arr.filter(r=>r.status===status);
    if(cls) arr=arr.filter(r=>(r.content_class||'')===cls);
    if(noNoise) arr=arr.filter(r=>!(r.gate_status==='reject'));""",
        "loadKB 内容类型与高相关过滤")

    # 4) 卡片徽标
    rep("""  const topb = (r.topic&&r.topic.length)?""",
        """  const clsb = r.content_class ? ((r.gate_status==='reject')
      ? '<span class="badge b-rej" title="未达准入门槛（可隐藏）">'+esc(CLASS_LABELS[r.content_class]||r.content_class)+'</span>'
      : '<span class="badge b-class" title="内容类型">'+esc(CLASS_LABELS[r.content_class]||r.content_class)+'</span>') : '';
  const topb = (r.topic&&r.topic.length)?""",
        "recCard 计算内容类型徽标")

    rep("""      ${r.category?`<span class="badge b-cat">${esc(CAT_LABELS[r.category]||r.category)}</span>`:''}""",
        """      ${clsb}
      ${r.category?`<span class="badge b-cat">${esc(CAT_LABELS[r.category]||r.category)}</span>`:''}""",
        "recCard 展示内容类型徽标")

    # 5) 启动填充
    rep("""  if($('#kbStatus')) fillSelect($('#kbStatus'), STATUSES);""",
        """  if($('#kbStatus')) fillSelect($('#kbStatus'), STATUSES);
  if($('#kbClass')) fillSelect($('#kbClass'), CONTENT_CLASSES.map(o=>o.id), id=>CLASS_LABELS[id]);""",
        "启动填充内容类型选项")

    # 6) 样式
    if ".b-class{" not in t:
        rep(".b-topic{background:#e8f0fe;color:#1a4b8c;border-color:#c3d6f5}",
            ".b-topic{background:#e8f0fe;color:#1a4b8c;border-color:#c3d6f5}\n"
            ".b-class{background:#eef2f7;color:#33475b;border-color:#d5dde7}\n"
            ".b-rej{background:#f6f7f9;color:#9aa3af;border-color:#e3e6ea}",
            "新增内容类型徽标样式")

    open(a.html, "w", encoding="utf-8").write(t)
    print("\n[ok] 共 %d 处修改；内容类型 %d 类" % (n, len(order)))


if __name__ == "__main__":
    main()
