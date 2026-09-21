"""40+ 求职者简历分析器：启发式规则引擎 + 可选 DeepSeek LLM 增强。

第一性原理：收入不要断 > 专业对口 > 薪资预期 > 城市就近。
外企/合资优先（年龄歧视低）；快速就业友好岗加权；地点/薪资弹性加权。
"""
import os
import re
import json

import region

# 大厂 HR 2026 最新需求（用于能力对标与"补齐建议"）
HR_DEMANDS = {
    "AI工具应用": "会用 AI 提效（ChatGPT/DeepSeek/Midjourney/自动化工作流），当作基础项而非加分项",
    "数据驱动": "用数据说话、看 ROI、能做 A/B 与复盘",
    "内容闭环": "从策略→内容→投放→转化全链操盘，而非只写稿",
    "跨文化沟通": "英语或小语种可作为工作语言，能对接总部/海外",
    "项目管理": "独立带项目、跨部门协同、控进度与预算",
    "抗压与稳定": "年龄带来的稳定性与抗压是卖点，需在简历显化",
}

# 快速就业友好职能（低门槛、出结果快、不苛求对口）
FAST_TRACK_HINTS = ["培训", "讲师", "内容运营", "新媒体", "社媒", "顾问", "咨询", "行政", "总助", "客户成功", "文化"]


def _to_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _detect_age(text):
    m = re.search(r'(19\d{2})[年./\-]', text)
    if m:
        a = 2026 - int(m.group(1))
        if 18 <= a <= 70:
            return a
    m2 = re.search(r'(\d{2})\s*岁', text)
    if m2:
        a = int(m2.group(1))
        if 18 <= a <= 70:
            return a
    return None


def _detect_city(text, default=""):
    """识别人所在/意向城市——**地区由简历与问卷决定，不预设任何城市**。

    识别不到就返回 default（通常为空），由调用方决定是否用样本城市兜底。
    """
    for c in region.all_city_names():
        if c in text:
            return c
    return default


def _detect_years(text):
    """识别工作年限。

    坑：直接 `(\\d{1,2})\\s*年` 会把出生/任职年份（1980年 → 80）误读成 80 年工龄，
    导致激励文案出现"80 年从业"这种离谱句子。先把 4 位年份整体剔除，再取合理的年数区间。
    """
    t = re.sub(r'(?:19|20)\d{2}\s*年', ' ', text)          # 剔除 1980年 / 2026年 这类年份
    cand = [int(x) for x in re.findall(r'(\d{1,2})\s*年(?!\s*代)', t)]
    cand = [c for c in cand if 2 <= c <= 45]                # 工龄合理区间，排除 1 年以下与明显异常值
    return max(cand) if cand else None


def _detect_industry(text):
    table = {
        '快消/日化': ['快消', '日化', '食品', '饮料', '美妆', '护肤', '个护'],
        '汽车': ['汽车', '整车', '主机厂', '4s'],
        '互联网/电商': ['互联网', '电商', '跨境电商', '直播', '短视频'],
        '制造': ['制造', '五金', '建材', '家电', '电气'],
        '金融/保险': ['金融', '银行', '保险', '证券'],
        '医药/健康': ['医药', '医疗', '健康', '生物', '营养'],
        '培训/教育': ['培训', '教育', '讲师'],
        '文化/文旅/酒店': ['文化', '文旅', '酒店', '民宿'],
        '咨询': ['咨询', '顾问'],
    }
    for ind, kws in table.items():
        if any(k in text for k in kws):
            return ind
    return '未识别'


def _detect_skills(text):
    tbl = {
        '品牌策略': ['品牌策略', 'branding', '品牌规划'],
        '内容营销': ['内容营销', 'content', '新媒体内容'],
        '文案': ['文案', 'copy', '写稿'],
        '短视频/视频': ['短视频', '视频脚本', '抖音'],
        'TikTok/出海': ['tiktok', '出海', '跨境', '海外社媒', 'global'],
        'AI工具': ['ai', 'chatgpt', 'deepseek', 'midjourney', 'agent', '自动化', 'gpt'],
        'PPT/提案': ['ppt', '提案', '演示'],
        '项目管理': ['项目管理', 'pm', '带项目', '项目制'],
        '培训/讲师': ['培训', '讲师', '内训', '导师'],
        '数据分析': ['数据', 'roi', '复盘', 'ab', '转化'],
        '公关/媒介': ['公关', '媒介', 'pr', '媒体'],
    }
    t = text.lower()
    return [k for k, v in tbl.items() if any(w in t for w in v)]


def _encourage(prof):
    """激励文案（面向所有 40+ 求职者，不锚定任何特定个人）。

    原则：只基于用户自己简历里的事实说话，不空喊口号、不承诺结果。
    —— 对 40+ 求职者而言，"被理解"本身就是行动力的一部分。
    """
    out = []
    age = prof.get('age') or 0
    yrs = prof.get('years') or 0
    skills = prof.get('skills') or []

    if yrs >= 12:
        out.append(f"{yrs} 年从业——你至少完整穿越过 3 轮行业周期。这种「见过」的东西写不进简历，但面试里藏不住，它就是你的定价理由。")
    elif yrs:
        out.append(f"{yrs} 年积累是你的底盘：行业可以换，能力不会归零。把经历写成「可交付的解决方案」，筹码立刻不一样。")

    if 40 <= age <= 62:
        out.append("40+ 是筛选函数，不是劣势函数：它筛掉「还能熬夜」，留下「能扛结果」。企业真正的痛点是缺敢为结果负责的人——那正是你的长板。")
    elif age > 62:
        out.append("年龄带来的不是包袱，是别人花钱也买不到的判断密度。把「资历」翻译成「可交付」，你就站在了别人的成本线上方。")

    if 'AI工具' in skills:
        out.append("你已经在用 AI 干活了——这是 40+ 里最稀缺的一类人：既懂业务，又肯换工具。这是可验证的差异化，值得写进简历第一屏。")

    if prof.get('constraints'):
        out.append("有清楚边界的人反而更容易被信任：把底线说在前面，是为了筛掉不合适的机会，不是关上门。")

    out.append("降薪换不来安全感——企业宁可选更年轻更便宜的。真正能换来安全感的是换交易结构：从「被雇为总监」转向「被采购为专家/顾问」，价格由交付定，不由年龄定。")
    out.append("今天只做成一件事也算数：投出 1 份、联系 1 个人、办成 1 项登记。到周末回头看，你已经不在原地。")
    return out[:5]


def analyze_resume(text, q, positions):
    # 样本岗位池所在城市（示例数据）：仅当用户完全没提城市时才用它兜底，
    # 用来给用户一句"这批推荐岗是哪个城市的样本"的如实说明。
    sample_city = positions.get('meta', {}).get('sample_city') or region.sample_city()

    prof = {
        'age': _to_int(q.get('age')) or _detect_age(text),
        # 地区：问卷 > 简历里出现的城市 > 空（不预设用户在哪座城市）
        'city': q.get('city') or _detect_city(text),
        'years': _to_int(q.get('years')) or _detect_years(text),
        'industry': q.get('industry') or _detect_industry(text),
        'skills': _detect_skills(text),
        'constraints': q.get('constraints', ''),
    }
    city_label = prof['city'] or sample_city

    recs = []
    for p in positions.get('harvested', []):
        s = 0
        reasons = []
        hit = 0
        blob = (p.get('role', '') + p.get('duty', '')).lower()
        for sk in prof['skills']:
            if sk.lower() in blob:
                hit += 1
        s += hit * 3
        if hit:
            reasons.append(f"技能匹配 {hit} 项（{', '.join(prof['skills'][:3])}）")

        af = p.get('age_friendly', '')
        if af == '✅':
            s += 5
            reasons.append('年龄友好（经验门槛匹配）')
        elif af == '⚠️':
            s += 2
        elif af == '❌':
            s -= 10

        if p.get('foreign'):
            s += 3
            reasons.append('外企/合资（年龄歧视低）')

        if p.get('fast_track'):
            s += 4
            reasons.append('快速就业友好（低门槛/出结果快）')

        if q.get('location_flex') == '1' and p.get('remote_ok'):
            s += 2
            reasons.append('可远程/外地 base，契合地点弹性')

        if q.get('salary_flex') == '1' and p.get('tier') in ('保底', '过渡'):
            s += 1

        if p.get('need_resources'):
            s -= 3
            reasons.append('⚠️ 需自带客户/人脉资源（投递前请自行评估可行性）')

        recs.append({
            'company': p.get('company'), 'role': p.get('role'), 'city': p.get('city'),
            'salary': p.get('salary'), 'foreign': p.get('foreign'), 'age_friendly': af,
            'fast_track': p.get('fast_track'), 'remote_ok': p.get('remote_ok'),
            'tier': p.get('tier'), 'score': s,
            'reason': '；'.join(reasons) or '综合匹配',
            'source': p.get('source'), 'need_resources': p.get('need_resources'),
        })
    recs.sort(key=lambda x: x['score'], reverse=True)
    recs = recs[:8]

    # 简历体检
    gaps = []
    if prof['age'] and prof['age'] >= 40:
        gaps.append(f"年龄 {prof['age']} 岁：公开招聘平台 HR 初筛常卡年龄，建议主攻「经验门槛≥8年」或「公告未写年龄上限」的岗，并优先走内推/猎头。这是筛选机制的特性，不是能力问题。")
    if 'AI工具' not in prof['skills']:
        gaps.append("简历未显化 AI 工具能力——2026 大厂 HR 普遍将其视为基础项，建议补一句'用 AI 搭内容工作流'。")
    if not prof.get('years') or prof['years'] < 8:
        gaps.append("突出可量化年资（如'20 年品牌内容功力'），对冲年龄偏见。")
    if prof['constraints']:
        gaps.append(f"已记录硬约束：{prof['constraints']}（排序中已折算为规避，不作为扣分项；建议投递前逐条确认岗位是否真的触碰该约束）。")

    strengths = [f"行业: {prof['industry']}", f"技能: {', '.join(prof['skills']) or '待补'}",
                 f"城市: {city_label}" + ("" if prof['city'] else "（未填，暂用样本城市作示例）")]

    strategy = [
        "第一性：收入不要断——优先'能最快入职'的岗，薪资可先放低。",
        "不苛求专业对口：从经历找可迁移职能（文案/培训/沟通/项目管理）投递。",
        "地点灵活：可远程、可外地 base、可出差型岗都纳入考虑。",
        "地区自己定：检索地区完全由你输入的需求决定——想换城市就改一句，例如直接搜「上海 品牌经理」。",
        "外企/合资优先：年龄歧视低、HC 走全球 EVP，社招看能力。",
        "过渡组合：顾问/兼职/项目制 + 政策现金流（失业金/灵活就业补贴）保底。",
    ]

    hr = [f"{k}：{v}" for k, v in HR_DEMANDS.items()]

    actions = [
        "今天：把简历转成'能力清单+可迁移职能'版本，隐去年份过细信息、突出年资区间。",
        "今天：挑 3 个推荐里的外企岗，去官网 careers 页核实在招并投。",
        "本周：办/确认失业登记与就业困难人员认定倒计时（政策现金流）。",
        "本周：联系 2 位老同事做内推（40+ 有效路径第一环）。",
    ]

    out = {
        'profile': prof, 'strengths': strengths, 'gaps': gaps,
        'strategy': strategy, 'recommendations': recs,
        'hr_demands': hr, 'actions': actions,
        'encouragement': _encourage(prof),
        # 如实说明推荐岗的来源：本工具内置的是**样本岗位池**，不是你这座城市的全量在招岗
        'data_note': (f"以上推荐来自内置**样本岗位池**（示例城市：{sample_city}），用于演示排序逻辑。"
                      f"你这座城市的实时岗位请用下方「实时搜索岗位」直接搜——"
                      f"地区完全由你输入决定，例如「{city_label} 品牌经理」。"),
    }
    llm = _llm_enrich(prof, recs)
    if llm:
        out['llm_advice'] = llm
    return out


def _llm_enrich(prof, recs):
    """可选：设 DEEPSEEK_API_KEY 后调用，模拟大厂 HR 需求给补充建议。无 key 或失败则返回 None。"""
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        return None
    try:
        import requests
        url = 'https://api.deepseek.com/v1/chat/completions'
        sys_prompt = ('你是大厂 HR 负责人，基于 2026 招聘最新需求，给 40+ 求职者（第一性：收入不断档）'
                      '做简历与求职建议。语气务实、给可执行动作。')
        user = (f"候选人画像：{json.dumps(prof, ensure_ascii=False)}\n"
                f"推荐岗：{json.dumps(recs[:5], ensure_ascii=False)}\n"
                f"请给：①能力补齐 ②投递话术 ③薪资谈判底线。")
        r = requests.post(url, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                          json={'model': 'deepseek-chat',
                                'messages': [{'role': 'system', 'content': sys_prompt},
                                             {'role': 'user', 'content': user}]}, timeout=30)
        return r.json().get('choices', [{}])[0].get('message', {}).get('content')
    except Exception:
        return None
