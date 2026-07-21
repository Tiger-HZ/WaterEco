# -*- coding: utf-8 -*-
"""为 kb.json 每条记录抽取「内容实体」kg_terms（数组 of 字符串），
用于丰富知识图谱与可视化（纯规则词典匹配，无后端 / 无第三方依赖）。
实体来源：受控词表 + 水生态环境术语词典 + 地区/部门专名 + 已有 tags。
update.py 会在 enrich_importance 之后调用本脚本，确保每日更新都带 kg_terms。
"""
import os, json, sys

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, "kb", "kb.json")

# 水生态环境领域术语词典（规范写法；匹配时对全文小写化后子串匹配）
LEXICON = [
    # 政策 / 制度
    "水生态环境", "水环境", "地表水", "水生态", "水资源", "水安全", "水功能区", "三水统筹",
    "碧水保卫战", "水污染防治", "水十条", "五水共治", "河湖长制", "河长制", "湖长制",
    "生态补偿", "横向生态补偿", "新安江模式", "流域横向补偿", "断面考核", "生态流量",
    "排污许可", "入河排污口", "入海排污口", "查测溯治", "排污口排查", "排污口整治",
    "饮用水水源", "饮用水源地", "水源地保护", "千岛湖", "供水安全", "备用水源",
    "再生水", "污水资源化", "节水", "国家节水行动", "水资源刚性约束",
    "美丽河湖", "幸福河湖", "美丽中国", "生态文明", "绿水青山",
    # 流域 / 河流 / 湖库
    "流域", "重点流域", "跨界河流", "跨界水体", "流域治理", "流域规划", "长江保护法",
    "黄河保护法", "钱塘江", "苕溪", "运河", "京杭大运河", "太湖", "西湖", "白洋淀",
    "滇池", "巢湖", "茅洲河", "清河", "莱茵河", "多瑙河", "水乡客厅",
    # 治理 / 工程
    "黑臭水体", "城市黑臭水体", "长治久清", "雨污分流", "污水管网", "提质增效",
    "城镇污水处理", "污水处理厂", "准IV类", "提标改造", "厂网河一体化", "合流制溢流",
    "河湖水系连通", "生态缓冲带", "生态廊道", "生态护岸", "人工湿地", "生态浮岛",
    "底泥清淤", "清淤", "水生态修复", "水生态调查", "水生态监测", "水生生物多样性",
    "水华", "蓝藻", "富营养化", "氮磷", "农业面源", "面源污染", "生态沟渠",
    "海绵城市", "亲水", "滨水空间", "数字治水", "智慧水务", "数字孪生", "水质预警",
    # 技术 / 监测
    "水质监测", "在线监测", "污染溯源", "水质自动站", "遥感", "水动力模型", "水模型",
    "厌氧氨氧化", "MBR", "人工湿地", "生物完整性指数", "B-IBI", "承载力", "水环境承载力",
    "水生态考核", "水生态健康", "水生动植物", "底栖动物",
    # 标准 / 规范
    "地表水环境质量标准", "GB3838", "城镇污水处理厂污染物排放标准", "GB18918",
    "排放标准", "水污染物排放标准", "入河排污口监督管理技术指南", "HJ1308",
    "饮用水水源保护区划分技术规范", "HJ338", "技术规范", "地方标准",
    # 时间 / 目标
    "十四五", "十五五", "2025", "2030", "2035",
    # 部门 / 机构（专名）
    "生态环境部", "水利部", "住房和城乡建设部", "住建部", "国家发展改革委", "发展改革委",
    "农业农村部", "财政部", "自然资源部", "国家林草局", "国家市场监管总局", "市场监管总局",
    "浙江省生态环境厅", "杭州市生态环境局", "长三角一体化", "环境监测总站", "中国水科院",
    "中科院水生所", "清华大学环境学院",
    # 地区
    "浙江", "杭州", "长三角", "京津冀", "长江经济带", "黄河流域", "珠三角", "成渝",
    "广东", "江苏", "上海", "北京", "深圳", "成都", "武汉", "南京", "苏州", "宁波",
    "温州", "嘉兴", "湖州", "绍兴", "金华", "舟山", "台州", "丽水", "淳安",
]

_LEX = sorted(set(LEXICON), key=lambda s: -len(s))

MAX_TERMS = 24
CATS = {"policy", "standard", "basin_eng", "management", "tech", "literature", "intl_region"}


def extract_terms(rec):
    tags = rec.get("tags") or []
    title = (rec.get("title") or "")
    summary = (rec.get("summary") or "")
    source = (rec.get("source") or "")
    content = (rec.get("content") or rec.get("content_fetched") or "")
    if isinstance(content, str) and len(content) > 8000:
        content = content[:8000]
    text = (title + "。" + summary + "。" + source + "。" + str(content) + "。" + " ".join(tags)).lower()
    found = []
    seen = set()
    for t in tags:
        if t and t not in seen:
            seen.add(t); found.append(t)
    cand = []
    for term in _LEX:
        c = text.count(term.lower())
        if c > 0 and term not in seen:
            cand.append((term, c))
    cand.sort(key=lambda x: -x[1])
    for term, _c in cand:
        if term not in seen:
            seen.add(term); found.append(term)
    return found[:MAX_TERMS]


def norm_cat(c):
    if c in CATS:
        return c
    return "policy"


def main():
    data = json.load(open(KB, encoding="utf-8"))
    n_with = 0
    for r in data:
        r["category"] = norm_cat(r.get("category"))
        r["kg_terms"] = extract_terms(r)
        if r["kg_terms"]:
            n_with += 1
    json.dump(data, open(KB, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("enriched kg_terms for %d/%d records (avg %.1f terms, cat normalized)" % (
        n_with, len(data), sum(len(r["kg_terms"]) for r in data) / max(1, len(data))))


if __name__ == "__main__":
    main()
