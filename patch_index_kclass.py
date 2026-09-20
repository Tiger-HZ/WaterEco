# -*- coding: utf-8 -*-
"""
门户补丁：用「知识类别」(kclass, 16 类) 替换原来的简略「分类」(category, 7 类)

背景（用户要求）：原分类只有 政策法规/标准规范/流域治理与工程/管理机制与实践/技术产品 等 7 类，
覆盖不了常见的知识类别。现有 16 类：政策法规、标准规范、规划与报告、管理机制、案例实践、
技术产品、流域治理与工程、科研文献、数据资源、概念术语、教育科普、重要讲话、机构信息、
人物专家、新闻媒体、其他。

改动位置：
  1. 注入 KCLASSES / KCLASS_LABELS 常量
  2. 工具条「全部分类」下拉 -> 填充知识类别
  3. 过滤条件 r.category===cat -> r.kclass===cat
  4. 卡片徽标：分类徽标 -> 知识类别徽标
  5. 左侧「按分类」导航 -> 按知识类别聚合（标签与取值同步）
用法：python3 patch_index_kclass.py
"""
import argparse
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=os.path.join(BASE, "index.html"))
    ap.add_argument("--kclass", default=os.path.join(BASE, "kclass.json"))
    a = ap.parse_args()

    kc = json.load(open(a.kclass, encoding="utf-8"))
    zh = {c["id"]: c["zh"] for c in kc["classes"]}
    order = kc.get("order") or list(zh.keys())
    items = ",".join('{id:"%s",zh:"%s"}' % (c, zh.get(c, c)) for c in order)

    t = open(a.html, encoding="utf-8").read()
    n = 0

    def rep(old, new, tag, cnt=1):
        nonlocal t, n
        if old not in t:
            raise SystemExit("[FAIL] 锚点未找到：%s" % tag)
        t = t.replace(old, new, cnt)
        n += 1
        print("  ✓ %s" % tag)

    # 1) 常量
    if "const KCLASSES" not in t:
        rep("const CONTENT_CLASSES = [",
            "/* 知识类别（16 类，由 kclass.json 生成；patch_index_kclass.py 同步） */\n"
            "const KCLASSES = [%s];\n"
            "const KCLASS_LABELS = {}; KCLASSES.forEach(x=>KCLASS_LABELS[x.id]=x.zh);\n"
            "const CONTENT_CLASSES = [" % items,
            "注入 KCLASSES 常量")

    # 2) 下拉填充：分类 -> 知识类别
    rep("""  fillSelect($('#kbCat'), CATS, c=>CAT_LABELS[c]); fillSelect($('#graphCat'), CATS, c=>CAT_LABELS[c]);""",
        """  fillSelect($('#kbCat'), KCLASSES.map(o=>o.id), id=>KCLASS_LABELS[id]);
  if($('#graphCat')) fillSelect($('#graphCat'), CATS, c=>CAT_LABELS[c]);""",
        "下拉框改用知识类别")

    # 3) 过滤条件
    rep("""    if(cat) arr=arr.filter(r=>r.category===cat);""",
        """    if(cat) arr=arr.filter(r=>(r.kclass||'')===cat);""",
        "过滤条件改用 kclass")

    # 4) 导航按钮文案
    rep("""<button class="on" data-b="topic">按业务域</button><button data-b="category">按分类</button>""",
        """<button class="on" data-b="topic">按业务域</button><button data-b="category">按知识类别</button>""",
        "导航按钮文案")

    # 5) 导航聚合：category 分支改用 kclass 取值与中文标签
    rep("""  } else if(dim==='category'){
    CATS.forEach(c=>{ const n=arr.filter(r=>(r.category||'other')===c).length; if(!n) return;
      html+='<div class="nav-i'+(curCat===c?' on':'')+'" onclick="kbNavPick(\\'category\\',\\''+c+'\\')">'+(CAT_LABELS[c]||c)+' <b>'+n+'</b></div>'; });""",
        """  } else if(dim==='category'){
    KCLASSES.forEach(o=>{ const n=arr.filter(r=>(r.kclass||'other')===o.id).length; if(!n) return;
      html+='<div class="nav-i'+(curCat===o.id?' on':'')+'" onclick="kbNavPick(\\'category\\',\\''+o.id+'\\')">'+o.zh+' <b>'+n+'</b></div>'; });""",
        "导航聚合改用知识类别")

    # 6) 卡片徽标
    rep("""      ${r.category?`<span class="badge b-cat">${esc(CAT_LABELS[r.category]||r.category)}</span>`:''}""",
        """      ${r.kclass?`<span class="badge b-kc">${esc(KCLASS_LABELS[r.kclass]||r.kclass)}</span>`:''}""",
        "卡片展示知识类别徽标")

    # 7) 样式
    if ".b-kc{" not in t:
        rep(".b-class{background:#eef2f7;color:#33475b;border-color:#d5dde7}",
            ".b-class{background:#eef2f7;color:#33475b;border-color:#d5dde7}\n"
            ".b-kc{background:#eef7f4;color:#0d5c4d;border-color:#c9e6dd;font-weight:600}",
            "新增知识类别徽标样式")

    open(a.html, "w", encoding="utf-8").write(t)
    print("\n[ok] 共 %d 处修改；知识类别 %d 类" % (n, len(order)))


if __name__ == "__main__":
    main()
