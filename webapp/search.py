"""实时搜索引擎（v3）：**零配置优先**。

设计原则（2026-09-21 按用户要求收敛）：
    帮助人是第一位的，使用阻碍要尽量小。**不要求用户申请任何 API key。**

因此执行搜索的主体是**运行本 skill 的 Agent 自己**——它已经具备联网搜索能力，
把搜到的结果落盘即可，本模块负责读取、归一化、去重。

后端优先级（`auto`，也是默认）：
  1. agent   —— 读 Agent 落盘的实时结果（`positions_live.json` 或 POST /search/import）。
                **零配置**，Agent 自带大模型与联网搜索，无需用户做任何事。
  2. direct  —— 直连已知源（人社官网目录 + 外企目录 + 猎聘 SEO 页抓取）。**零配置**，
                curl/requests 可达即用，已实测可用。
  3. llm / search_api —— **仅进阶用户可选**：想指定自己的模型或搜索 API 时才配。
                不配完全不影响使用，UI 上也不再暴露为主路径。

所有后端输出统一归一化为 positions.json 同构字段，并打 live:true 标记。
"""
import os
import re
import json
import datetime
import requests

import region
from abc import ABC, abstractmethod

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# 人社部门官方招聘信息源：**按用户指定的城市动态解析**（见 region.py）。
# 不再把任何单一城市写死在这里——未收录的城市会拿到全国通用渠道 + 一句自助检索提示。


# ---------------------------------------------------------------------------
# 猎聘 SEO 落地页路由：**按 query 关键词选 slug**
# ---------------------------------------------------------------------------
# 2026-09-21 修复：原实现用固定 6 个 slug，query 传进来从不使用——
# 所以搜「品牌」和搜「会计」返回完全一样的结果。改为关键词路由。
# 所有 slug 均来自 SKILL.md 已核实 slug 库（真实可访问，见 skill 第一节）。
SLUG_ROUTES = (
    (("品牌", "品牌营销", "品牌策划", "品牌总监", "品牌规划", "品牌管理", "品牌文策"),
     ("zppinpaiguihuazongjian", "zpchppzjj52n", "zpppchyxzj5f2h", "zpppwco4i3",
      "zpshichangpinpai", "zpppnrzg6u6u")),
    (("内容", "内容营销", "内容运营", "新媒体", "文案", "短视频", "内容创作", "内容制作"),
     ("zpnrzzgjjl10z1e", "zpdsnryxzge1y6", "zpnryxwav8d5", "zpnrczzjc6u1",
      "zpppnrzg6u6u", "zpdspnrchjl3t9i")),
    (("出海", "海外", "跨境", "国际", "tiktok", "外贸", "出海品牌"),
     ("zphwppchzjp55l", "zpgjppfwgw1d9v", "zpmjswzj10p8b", "zphwswgwp9v1",
      "zpgjhwgg9e1a", "zphwmjyyzg8z3d")),
    (("咨询", "顾问", "战略", "方案", "诊断"),
     ("zpbwzy", "zpppwco4i3", "zpgjppfwgw1d9v")),
    (("培训", "讲师", "导师", "内训", "教练"),
     ("zpaqypxjs3z7r", "zpyxpxds1c5r", "zpfzzbfrpxjsv3h4", "zpshangwuliyijiangshi")),
    (("总监", "负责人", "资深", "高级经理", "主管"),
     ("zppinpaiguihuazongjian", "zpchppzjj52n", "zpppyyfzj", "zpbjppzjn2l9",
      "zpdsnryxzge1y6")),
    (("市场", "营销", "公关", "媒介", "传播", "推广"),
     ("zpyxcbjl7a3v", "zpshichangpinpai", "zpshichangpinpaituiguangzongjian",
      "zpmjswzj10p8b", "zpbjppzjn2l9")),
    (("电商", "直播", "用户运营", "社群"),
     ("zpzbnryyzje25l", "zpdsqptnryy1y8c", "zpvipzhyxa2p5", "zpzsdswach2x8b",
      "zpshipinneirongyunying")),
)

# 关键词没命中任何路由时的兜底（覆盖面较广的高产页）
DEFAULT_SLUGS = ("zpppwco4i3", "zpnrzzgjjl10z1e", "zpgjppjl",
                 "zpppwhycbbbcr7q3", "zphwppchzjp55l", "zpvipzhyxa2p5")

# 单次请求最多抓几个落地页（控制延迟；每页去重后约 37 条候选）
MAX_SLUGS_PER_QUERY = 6


def _route_slugs(query, limit=MAX_SLUGS_PER_QUERY):
    """按 query 关键词挑 slug；命中多个关键词时并集、去重、保序。

    无命中时回落 DEFAULT_SLUGS（保证仍有结果，不空手而归）。
    """
    q = (query or "").lower()
    picked = []
    for keywords, slugs in SLUG_ROUTES:
        if any(k.lower() in q for k in keywords):
            for s in slugs:
                if s not in picked:
                    picked.append(s)
    for s in DEFAULT_SLUGS:
        if len(picked) >= limit:
            break
        if s not in picked:
            picked.append(s)
    return picked[:limit]


def _load_positions(path):
    """从 positions.json 读 mnc_directory / announcements / harvested（单一数据源）。

    2026-09-21 修复：原实现只读 mnc_directory 与 announcements，**完全没有读 harvested**，
    导致库里 14 条真实品牌岗（立白品牌副总监、健合 Swisse 高级品牌经理、usmile 品牌内容经理…）
    永远不出现在结果里。这是"搜品牌搜不到品牌岗"的直接原因之一。
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return (d.get("mnc_directory", []) or [],
                d.get("announcements", []) or [],
                d.get("harvested", []) or [])
    except Exception:
        return [], [], []


# ---------------------------------------------------------------------------
# 来源标注 → 可点开的链接
# ---------------------------------------------------------------------------
_LIEPIN_JOB_RE = re.compile(r"^liepin\s+job/(\d+)$")
_LIEPIN_SLUG_RE = re.compile(r"^liepin\s+([A-Za-z0-9_]+)$")


def _link_from_source(src, city=""):
    """把数据里的来源标注转成可点开的链接（不编造，只做格式推导）。

    'liepin zpppwco4i3'      → 该城市的猎聘 SEO 落地页（该岗被发现/可检索的页面）
    'liepin job/1985334545'  → 猎聘岗位详情页
    已是 http(s) 的原样返回；其它返回空串。

    城市取自用户输入（未指定时用 region.sample_city() 兜底）。
    """
    s = (src or "").strip()
    if s.startswith("http"):
        return s
    m = _LIEPIN_JOB_RE.match(s)
    if m:
        return f"https://www.liepin.com/job/{m.group(1)}.shtml"
    m = _LIEPIN_SLUG_RE.match(s)
    if m:
        return f"https://www.liepin.com/city-{region.city_param(city)}/{m.group(1)}/"
    return ""


# ---------------------------------------------------------------------------
# 相关度引擎：让 query 真正参与检索与排序
# ---------------------------------------------------------------------------
# 这些词只表达"我在找工作"，不含岗位信息，参与打分只会引入噪声。
# 城市名同样剔除：地区已由 city 字段单独承载（任意城市都适用，不针对某一城）。
_QUERY_STOPWORDS = {
    "招聘", "岗位", "职位", "工作", "求职", "找工作", "相关", "方面", "方向", "想找", "找",
    "的", "和", "或", "与", "及", "个", "一个", "一位", "份", "做", "在", "有", "要",
    "job", "jobs", "招聘信息", "全职", "兼职",
}
_CITY_WORDS = set(region.all_city_names()) | set(region.PROVINCE_NAMES)
_CITY_PARAMS = {p.lower() for p in region.all_city_params()}

# 字段权重：role 命中权重最高——用户搜"品牌"，首先要的是职位名里带"品牌"的岗。
_FIELD_WEIGHTS = (
    ("role", 4.0),
    ("company", 2.0),
    ("company_type", 2.0),
    ("duty", 1.2),
    ("city", 1.0),
    ("exp", 0.6),
    ("edu", 0.6),
)


def _tokens(query):
    """把 query 切成检索词；中文长词补 2-gram。

    为什么补 2-gram：中文没有词边界，用户输入"品牌相关工作"整串去匹配
    "品牌内容经理"会匹配失败。拆成 2-gram（品牌/牌相/相关/关工/工作）后，
    "品牌" 即可命中，兼顾召回与精度。
    """
    if not query:
        return []
    raw = re.split(r"[\s,，、/|;；:：()（）\[\]【】+]+", str(query).strip())
    toks = []
    for t in raw:
        t = t.strip()
        if not t or t.lower() in _QUERY_STOPWORDS or t in _QUERY_STOPWORDS:
            continue
        if _is_city_word(t):
            continue
        toks.append(t)
        has_cjk = re.search(r"[\u4e00-\u9fff]", t)
        if len(t) >= 3 and has_cjk:
            # 整串（如"品牌营销"）已加入，再补 2-gram 提升召回。
            # 2-gram 同样要过停用词表，否则"品牌相关"会重新生成"相关"这种噪声词。
            for i in range(len(t) - 1):
                g = t[i:i + 2]
                if (re.search(r"[\u4e00-\u9fff]", g) and g not in _QUERY_STOPWORDS
                        and not _is_city_word(g)):
                    toks.append(g)
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _is_city_word(tok):
    """判断一个词元是否是「地区词」（城市名 / 省份 / 城市参数 / XX市）。

    地区已由 city 字段单独计分，再参与词元打分只会让"上海 品牌"这类输入
    被地区稀释相关度。任意城市一视同仁，不特指某一城。
    """
    t = (tok or "").strip()
    if not t:
        return False
    if t in _CITY_WORDS or t.lower() in _CITY_PARAMS:
        return True
    return len(t) >= 2 and t.endswith("市")


def _relevance(p, toks):
    """按字段权重累加命中分数。0 分 = 与 query 无关。"""
    if not toks:
        return 0.0
    score = 0.0
    for field, w in _FIELD_WEIGHTS:
        v = str(p.get(field) or "")
        if not v:
            continue
        for t in toks:
            if t in v:
                score += w
    return score


def _rank_key(p, toks=None):
    """排序键（越大越靠前）：相关度 → 有邮箱 → 年龄友好 → 外企 → 快速就业 → 有直链。

    有邮箱排在高位，是因为用户明确反馈"很多信息没有邮箱地址"——在有邮箱的渠道
    （人社/国企公告）产出岗位时，让它们优先浮上来。
    """
    return (
        _relevance(p, toks or []),
        1 if (p.get("email") or "").strip() else 0,
        1 if p.get("age_friendly") == "✅" else (0.5 if p.get("age_friendly") == "⚠️" else 0),
        1 if p.get("foreign") else 0,
        1 if p.get("fast_track") else 0,
        1 if (p.get("link") or "").strip() else 0,
    )


def _apply_query(results, query):
    """把 query 真正作用到结果上：过滤零相关 + 按相关度排序。

    返回 (结果列表, 命中的检索词, 是否启用了过滤)。
    兜底纪律：**过滤后为空时退回全集**（按邮箱/年龄友好排序），
    绝不因为关键词没匹配上就给用户一个空列表——那是最糟的体验。
    """
    toks = _tokens(query)
    if not toks:
        return sorted(results, key=lambda p: _rank_key(p, [])), [], False
    scored = [(p, _relevance(p, toks)) for p in results]
    hit = [(p, s) for p, s in scored if s > 0]
    if hit:
        hit.sort(key=lambda ps: _rank_key(ps[0], toks), reverse=True)
        return [p for p, _ in hit], toks, True
    return sorted(results, key=lambda p: _rank_key(p, [])), toks, False


def _norm(p):
    """归一化到统一字段。缺字段补默认值，**不编造**。

    2026-09-21 按用户要求扩表：列表要能直接看到
    公司名 / 公司属性 / 地区 / 职位名称 / 直链网址 / 投简历邮箱。
    """
    p = dict(p or {})
    p.setdefault("live", True)
    p.setdefault("source_type", "其他")
    for k in ["foreign", "fast_track", "remote_ok", "need_resources"]:
        p.setdefault(k, False)
    # 展示六件套
    p.setdefault("company", "待核实")       # 公司名字
    p.setdefault("company_type", "")        # 公司属性（行业·阶段·规模 / 外企·合资·国企）
    p.setdefault("city", "")                # 地区
    p.setdefault("role", "待核实")          # 职位名称
    p.setdefault("link", "")                # 直链网址（岗位详情页）；无则回落 source
    p.setdefault("email", "")               # 投简历的邮箱；猎聘等平台渠道不公开，留空
    p.setdefault("apply_method", "")        # 报名/投递方式（邮箱/系统/现场）——邮箱为空时给用户实际出路
    p.setdefault("status", "")              # 在招 / 即将开始报名 / 已截止 / 待核实
    # 辅助字段
    p.setdefault("salary", "待核实")
    p.setdefault("exp", "")
    p.setdefault("edu", "")
    p.setdefault("recruiter", "")
    p.setdefault("recruiter_title", "")
    p.setdefault("age_friendly", "")
    p.setdefault("tier", "")
    p.setdefault("duty", "")
    p.setdefault("source", "")
    if not p["link"]:
        p["link"] = p["source"]
    # 投递方式兜底：邮箱为空时，至少要告诉用户"这个渠道怎么投"。
    # 一条真实岗位若只给一个空格，用户拿不到任何行动指引 —— 那是无效信息。
    if not p.get("apply_method"):
        p["apply_method"] = {
            "主流平台": "平台站内投递（该渠道不公开邮箱）",
            "外企": "官网 careers 系统投递（无公开邮箱）",
            "人社": "见公告页的报名方式（邮箱/系统/现场）",
        }.get(p.get("source_type"), "见来源链接投递")
    return p


def _company_type_from_tags(tags):
    """把猎聘的 company-tags-box 三个 span 组合成「公司属性」。

    页面注释明确：compIndustry || compStage || compScale（行业 / 上市阶段 / 规模）。
    例：'互联网,广告/公关/会展 · 已上市 · 2000-5000人'
    """
    tags = [t.strip() for t in (tags or []) if t and t.strip()]
    return " · ".join(tags)


# ---------------------------------------------------------------------------
# 后端基类
# ---------------------------------------------------------------------------
class SearchBackend(ABC):
    name = "base"

    @abstractmethod
    def search(self, query, sources, max_results=10):
        """返回归一化岗位 dict 列表。"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 0) Agent 后端（**首选**）：由"运行本 skill 的那个 Agent"把搜索结果落盘，这里读入
# ---------------------------------------------------------------------------
class AgentBackend(SearchBackend):
    """零配置路径：Agent 用它自带的联网搜索能力搜完，把结果写成 JSON，本后端只负责读。

    为什么这样设计：用户已经在和 Agent 对话，Agent 的模型自带搜索——**不需要用户再去
    申请第二个 API key**。少一个配置项，就少一个放弃使用的理由。

    数据来源（按优先级）：
      1. `positions_live.json`（Agent 直接写文件，最省事）
      2. `POST /search/import`（Agent 通过 HTTP 推入，写入同一文件）
    """
    name = "agent"

    def __init__(self, path=None):
        self.path = path

    def has_data(self):
        if not self.path or not os.path.exists(self.path):
            return False
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return bool(d.get("results") if isinstance(d, dict) else d)
        except Exception:
            return False

    def search(self, query, sources, max_results=10):
        if not self.has_data():
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("results", [])
        out, seen = [], set()
        for p in data or []:
            if not isinstance(p, dict):
                continue
            p = _norm(p)
            # 源过滤：仅在条目确有 source_type 且不在勾选范围时跳过（宽进严出，避免误杀）
            if sources and p.get("source_type") not in sources and p.get("source_type") != "其他":
                continue
            k = (p.get("role"), p.get("company"), p.get("salary"))
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
        # Agent 落盘的结果同样要受 query 约束与排序，否则"搜什么都是同一批"
        out, _, _ = _apply_query(out, query)
        return out[:max_results]


# ---------------------------------------------------------------------------
# 1) LLM 后端（进阶可选）：用户指定自己的 OpenAI 兼容模型来执行搜索
# ---------------------------------------------------------------------------
class LLMSearchBackend(SearchBackend):
    name = "llm"

    def __init__(self):
        self.api_key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")
        self.web_search = os.environ.get("LLM_WEB_SEARCH", "0") == "1"

    def _build_prompt(self, query, sources):
        src_map = {
            "人社": "人社部门官方网站发布的招聘/公益性岗位（**按求职需求里出现的城市**去找当地人社局、公共招聘网、国资委、政务服务网；"
                    "全国通用入口如中国公共招聘网 job.mohrss.gov.cn）",
            "外企": "外资企业/合资企业的官方招聘页（careers 页）",
            "主流平台": "猎聘/BOSS/智联/51job 等主流招聘平台的实时在招岗位",
        }
        src_text = "；".join(src_map.get(s, s) for s in sources) or "全部来源"
        return (
            "你具备实时联网搜索能力。请现在联网搜索以下求职需求对应的【当前真实在招】岗位，"
            f"来源范围：{src_text}。\n求职需求：{query}\n\n"
            "只返回 JSON 数组，不要任何解释文字。每个元素字段（按此顺序，缺一不可）：\n"
            "company(公司名字), company_type(公司属性，如'外企(美资)'/'合资'/'国企'/'上市公司'/"
            "'互联网·已上市·2000-5000人'), city(地区), role(职位名称), "
            "link(岗位直链网址，必须是该岗位详情页而非列表页), "
            "email(投简历的邮箱；**只有真实公开的才填，没有就留空字符串，严禁编造或猜测**), "
            "apply_method(报名/投递方式：如'邮箱投递 xxx'/'线上报名系统 xxx'/'现场报名 xxx'；未知留空), "
            "status(在招/即将开始报名/已截止/待核实), "
            "salary(薪资，未知写'待核实'), exp(经验要求), source(你找到它的来源页链接), "
            "source_type(人社/外企/主流平台/其他), foreign(是否外企布尔), "
            "fast_track(是否快速就业友好布尔), remote_ok(是否可远程/外地布尔)。\n"
            "纪律：每条必须带来源链接；**禁止编造公司、岗位、薪资与邮箱**；不确定的写'待核实'或留空。"
            "若某岗位投递只能走官网/平台站内/现场报名、无公开邮箱，email 留空并**在 apply_method 里写清实际报名方式**"
            "——**空着比编造好**，但空着的同时要告诉用户怎么投。"
        )

    def search(self, query, sources, max_results=10):
        if not self.api_key:
            return []
        sys_p = "你是招聘信息实时检索助手，必须基于联网搜索结果返回真实数据，禁止编造。"
        user_p = self._build_prompt(query, sources)
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
            "temperature": 0.1,
        }
        if self.web_search:
            payload["tools"] = [{"type": "web_search"}]
            payload["tool_choice"] = "auto"
        try:
            r = requests.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload, timeout=60,
            )
            content = r.json()["choices"][0]["message"]["content"]
            return self._parse_json(content)[:max_results]
        except Exception as e:
            return [{"role": "error", "source": "llm", "duty": f"LLM 搜索失败：{e}"}]

    @staticmethod
    def _parse_json(text):
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
            return [_norm(x) for x in arr if isinstance(x, dict)]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# 2) 通用搜索 API 后端
# ---------------------------------------------------------------------------
class SearchApiBackend(SearchBackend):
    name = "search_api"

    def __init__(self):
        self.key = os.environ.get("SEARCH_API_KEY")
        self.type = os.environ.get("SEARCH_API_TYPE", "serper")  # serper/brave/tavily

    def search(self, query, sources, max_results=10):
        if not self.key:
            return []
        # 地区来自用户输入本身；用户没写城市时才补兜底城市，不把任何城市写死。
        city = region.detect_city(query, default=region.sample_city())
        q = query if region.detect_city(query) else f"{query} 招聘 {city}"
        try:
            if self.type == "serper":
                r = requests.post("https://google.serper.dev/search",
                                  headers={"X-API-KEY": self.key, "Content-Type": "application/json"},
                                  json={"q": q, "gl": "cn", "hl": "zh-cn"}, timeout=30)
                raw = [{"title": i.get("title"), "snippet": i.get("snippet", ""), "link": i.get("link")}
                       for i in r.json().get("organic", [])]
            elif self.type == "brave":
                r = requests.get("https://api.search.brave.com/res/v1/web/search",
                                 headers={"X-Subscription-Token": self.key}, params={"q": q}, timeout=30)
                raw = [{"title": i.get("title"), "snippet": i.get("description", ""), "link": i.get("url")}
                       for i in r.json().get("web", {}).get("results", [])]
            else:  # tavily
                r = requests.post("https://api.tavily.com/search",
                                  json={"api_key": self.key, "query": q, "max_results": max_results}, timeout=30)
                raw = [{"title": i.get("title"), "snippet": i.get("content", ""), "link": i.get("url")}
                       for i in r.json().get("results", [])]
            return [self._to_posting(x, city) for x in raw][:max_results]
        except Exception as e:
            return [{"role": "error", "source": "search_api", "duty": f"搜索 API 失败：{e}"}]

    @staticmethod
    def _to_posting(x, city=""):
        text = (x.get("title", "") + " " + x.get("snippet", ""))
        m = re.search(r"\d{1,2}\s*-\s*\d{1,2}\s*k|\d+\s*-\s*\d+\s*元|\d+k", text, re.I)
        salary = m.group(0) if m else "待核实"
        foreign = bool(re.search(r"外企|外资|合资|跨国|global|mnc", text, re.I))
        link = x.get("link", "")
        st = "主流平台" if ("liepin" in link or "zhaopin" in link or "zhipin" in link or "51job" in link) else "其他"
        # 摘要里若出现公司属性词，提取出来（不猜、只在命中时填）
        ct = ""
        mt = re.search(r"(外企|外资|合资|国企|央企|上市公司|已上市|未融资|B轮|C轮|D轮|天使轮)", text)
        if mt:
            ct = mt.group(1)
        return _norm({
            "company": "待核实", "company_type": ct, "role": x.get("title", ""),
            # 地区：先看摘要里有没有写城市，没有再用本次检索的城市，不写死。
            "city": region.detect_city(text) or city or region.sample_city(),
            "salary": salary, "link": link, "email": "",
            "age_friendly": "", "duty": (x.get("snippet", "") or "")[:200],
            "source": link, "source_type": st, "foreign": foreign,
        })


# ---------------------------------------------------------------------------
# 3) 直连已知源后端（无需任何 key）
# ---------------------------------------------------------------------------
class DirectBackend(SearchBackend):
    name = "direct"

    def __init__(self, mnc_path=None):
        if mnc_path:
            self.mnc, self.announcements, self.harvested = _load_positions(mnc_path)
        else:
            self.mnc, self.announcements, self.harvested = [], [], []

    def search(self, query, sources, max_results=10):
        """返回**岗位**列表（已按 query 过滤 + 排序）。

        2026-09-21 修复：此处原先会把 _official()（4 条）与 _mnc()（15 条）这类
        「渠道入口」一并塞进结果，它们的 role 是"官方招聘/公益性岗位（请进链接查看在招岗）"
        ——**根本不是岗位**。19 条入口 + max_results=20，等于真实岗位一条都进不来。
        现在入口改由 channels() 单独返回，不再占用岗位名额。
        """
        sources = sources or []
        city = region.detect_city(query, default=region.sample_city())
        pool = []
        if "人社" in sources or not sources:
            pool += self._announcements()
        if "主流平台" in sources or not sources:
            pool += self._harvested(city)
            pool += self._liepin(query, city)
        # 「外企」渠道的具体岗位须官网核实，不在此编造；其入口见 channels()
        results, toks, filtered = _apply_query(pool, query)
        return results[:max_results]

    def channels(self, sources=None, city=""):
        """「渠道入口」——不是岗位，而是可进站搜索的官方/官网入口。

        单独返回，避免与真实岗位混排，也避免用户误以为这是岗位。
        地区由用户输入决定：未收录的城市只给全国通用渠道，并附一句自助检索提示。
        """
        sources = sources or []
        out = []
        if "人社" in sources or not sources:
            out += self._official(city)
        if "外企" in sources or not sources:
            out += self._mnc(city)
        return out

    def _harvested(self, city=""):
        """已核实真实岗（positions.json 的 harvested 段）——**此前从未被读取**。"""
        out = []
        for h in self.harvested:
            link = h.get("link") or _link_from_source(h.get("source"), city)
            out.append(_norm({
                "company": h.get("company", ""),
                "company_type": h.get("company_type", ""),
                "role": h.get("role", ""),
                "city": h.get("city", ""),
                "link": link,
                "email": h.get("email", ""),          # 平台岗不公开邮箱，如实留空
                "apply_method": h.get("apply_method", "") or "猎聘站内投递（点直链→在线沟通/投递）",
                "status": h.get("status", "待核实"),
                "salary": h.get("salary", "待核实"),
                "exp": h.get("exp", ""), "edu": h.get("edu", ""),
                "age_friendly": h.get("age_friendly", ""),
                "duty": h.get("duty", ""),
                "source": link, "source_type": h.get("source_type", "主流平台"),
                "foreign": bool(h.get("foreign")),
                "fast_track": bool(h.get("fast_track")),
                "remote_ok": bool(h.get("remote_ok")),
                "need_resources": bool(h.get("need_resources")),
                "tier": h.get("tier", ""),
            }))
        return out

    def _announcements(self):
        """已核实的官方公告岗（含投递邮箱、截止日期、年龄要求）。"""
        out = []
        for a in self.announcements:
            out.append(_norm({
                "company": a.get("company", ""),
                "company_type": a.get("company_type", ""),
                "role": a.get("role", ""),
                "city": a.get("city", ""),
                "link": a.get("link", ""),
                "email": a.get("email", ""),          # 真实投递邮箱
                "apply_method": a.get("apply_method", ""),
                "status": a.get("status", ""),
                "salary": a.get("salary", "待核实"),
                "exp": a.get("exp", ""), "edu": a.get("edu", ""),
                "age_friendly": a.get("age_friendly", ""),
                "duty": (a.get("note", "") + ("　截止：" + a["deadline"] if a.get("deadline") else "")),
                "source": a.get("source", ""),
                "source_type": a.get("source_type", "人社"),
            }))
        return out

    def _official(self, city=""):
        """官方公共就业渠道入口（按城市动态取，未收录城市给全国通用渠道）。"""
        return [_norm({
            "company": s["name"],
            "company_type": "人社部门（官方公共就业渠道）",
            # 措辞明确标为「入口」而非岗位——避免用户误把它当岗位投递
            "role": "招聘入口（不是岗位，点进去查在招岗）",
            "city": (city or region.sample_city()),
            "salary": "待核实", "age_friendly": "",
            "duty": "人社部门官方招聘渠道，含公益性岗位/就业困难人员岗位。"
                    "事业单位与公益岗公告通常附「报名邮箱」或报名系统入口。"
                    + (region.city_hint(city) or ""),
            "link": s["url"], "email": "",
            "apply_method": "进官网/公告页查在招岗及报名方式",
            "source": s["url"], "source_type": "人社",
        }) for s in region.official_sources(city)]

    def _mnc(self, city=""):
        out = []
        label = (city or region.sample_city())
        for m in self.mnc:
            out.append(_norm({
                "company": m["name"],
                "company_type": m.get("type", "外企/合资"),      # 公司属性
                "role": "官网 careers 入口（不是岗位，点进去查在招岗）",
                # local=True 表示该外企在**样本城市**有办公室/工厂（样本数据，非全量）
                "city": label if m.get("local") else "外地",
                "salary": "待核实", "age_friendly": "",
                "duty": m.get("note", ""),
                "link": m["careers_url"],
                "email": m.get("email", ""),                    # 一般不公开，留空
                "apply_method": "官网 careers 系统投递（无公开邮箱）",
                "source": m["careers_url"], "source_type": "外企", "foreign": True,
            }))
        return out

    def _liepin(self, query, city=""):
        """按 query 关键词路由到对应 slug 抓取（不再写死 6 个 slug）。

        城市取自用户输入（未指定则兜底），所以换了城市照样能用——
        slug 本身是按职能划分的，不绑定城市。
        """
        cc = region.city_param(city)
        out, seen = [], set()
        for slug in _route_slugs(query):
            url = f"https://www.liepin.com/city-{cc}/{slug}/"
            try:
                r = requests.get(url, headers=HEADERS, timeout=20)
                for it in self._parse_liepin(r.text, url, default_city=city):
                    k = it.get("link") or (it["role"], it["company"], it["salary"])
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append(it)
            except Exception:
                pass
        return out

    @staticmethod
    def _parse_liepin(html, base, limit=40, default_city=""):
        """从猎聘 SEO 落地页解析在招岗位（2026-09-21 已实测 DOM）。

        单块结构（已核实）：
            <a href="https://www.liepin.com/a/<id>.shtml">
              job-title-box > [title="职位名"] ellipsis-1
                          > job-dq-box > 【 ellipsis-1(城市) 】
              span.job-salary
              job-labels-box > span.labels-tag (第0个=经验, 第1个=学历)
            </a>
            span.company-name
            div.company-tags-box > span ×3  (行业 / 上市阶段 / 规模 → 公司属性)
            div.recruiter-name / div.recruiter-title

        三个已实测的坑：
          1) 禁用"前瞻定界"正则切块（会丢最后一个岗位块）。
             切分点优先用 `job-card-pc-container`：实测 39/39 段**同时含直链与公司名**
             （比 `job-list-item` 更稳，后者会因重叠片段把 `<a href>` 切到上一段）。
          2) 必须按 (role, company, salary) 去重（class 重叠会产生重复片段）。
          3) **猎聘全页零邮箱**（实测正则扫全页命中 0 条）：平台走站内沟通，不公开投递邮箱。
             因此 email 一律留空，由前端如实标注"该渠道不公开邮箱，请经直链投递"。
             —— 严禁为了填满字段而编造邮箱。
        """
        parts = html.split("job-card-pc-container")[1:]
        if not parts:
            parts = html.split("job-list-item")[1:]
        res, seen = [], set()
        for it in parts:
            # 职位名称：优先 job-title-box 内的 title 属性（最稳），否则首个 ellipsis-1
            tb = re.search(r'job-title-box(.*?)job-dq-box', it, re.S)
            role = re.search(r'title="([^"]+)"', tb.group(1)) if tb else None
            if not role:
                role = re.search(r'class="ellipsis-1"[^>]*>([^<]+)<', it)
            city = None
            dq = re.search(r'job-dq-box(.*?)</div>', it, re.S)
            if dq:
                for m in re.finditer(r'<span[^>]*>([^<]+)</span>', dq.group(1)):
                    t = m.group(1).strip()
                    if t and t not in ("【", "】"):
                        city = t
                        break
            salary = re.search(r'job-salary[^>]*>([^<]+)<', it)
            link = re.search(r'href="(https://www\.liepin\.com/(?:a|job)/[^"]+)"', it)
            company = re.search(r'company-name[^>]*>([^<]+)<', it)
            tags = []
            ctb = re.search(r'company-tags-box[^>]*>(.*?)</div>', it, re.S)
            if ctb:
                tags = [m.group(1).strip() for m in re.finditer(r'<span[^>]*>([^<]+)</span>', ctb.group(1))]
            exp = edu = ""
            lb = re.search(r'job-labels-box(.*?)</div>', it, re.S)
            if lb:
                labs = [m.group(1).strip() for m in re.finditer(r'labels-tag[^>]*>([^<]+)<', lb.group(1))]
                exp = labs[0] if len(labs) > 0 else ""
                edu = labs[1] if len(labs) > 1 else ""
            recruiter = re.search(r'recruiter-name[^>]*>([^<]+)<', it)
            rtitle = re.search(r'recruiter-title[^>]*>([^<]+)<', it)

            if not (role or salary or company):
                continue
            r = role.group(1).strip() if role else "待核实"
            c = company.group(1).strip() if company else "待核实"
            s = salary.group(1).strip() if salary else "待核实"
            lk = link.group(1) if link else ""
            rec = recruiter.group(1).strip() if recruiter else ""
            # 投递方式：猎聘不公开邮箱，但有真实可行的路径——站内直投，且多可直连招聘人。
            # 写清楚比留空有用：用户要的是"知道怎么投"，不是"看到一个空格"。
            apply_method = "猎聘站内投递（点直链→在线沟通/投递）"
            if rec:
                apply_method = f"猎聘站内直投（可直接联系招聘人 {rec}）"
            # 去重键：优先用直链（同一直链=同一岗位，最精确）；
            # 无直链时回落到 (role, company, salary) 三元组。
            k = lk or (r, c, s)
            if k in seen:
                continue
            seen.add(k)
            res.append(_norm({
                "company": c,
                "company_type": _company_type_from_tags(tags) or "企业（属性未公开）",
                "role": r,
                "city": city or default_city or region.sample_city(),
                "link": link.group(1) if link else base,       # 直链网址
                "email": "",                                    # 猎聘不公开邮箱，如实留空
                "apply_method": apply_method,                   # 但必须告诉用户怎么投
                "status": "在招",
                "salary": s, "exp": exp, "edu": edu,
                "recruiter": rec,
                "recruiter_title": rtitle.group(1).strip() if rtitle else "",
                "age_friendly": "", "duty": "猎聘实时在招",
                "source": base, "source_type": "主流平台",
            }))
            if len(res) >= limit:
                break
        return res


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------
class SearchEngine:
    def __init__(self, mnc_path=None, live_path=None):
        self.backends = {
            "agent": AgentBackend(live_path),
            "direct": DirectBackend(mnc_path),
            "llm": LLMSearchBackend(),
            "search_api": SearchApiBackend(),
        }

    def search(self, query, sources=None, backend=None, max_results=10):
        sources = sources or []
        backend = backend or os.environ.get("SEARCH_BACKEND", "auto")
        if backend == "auto":
            # 零配置优先：Agent 落盘结果 → 直连 → 仅当用户主动配了 key 才用进阶后端
            if self.backends["agent"].has_data():
                backend = "agent"
            elif self.backends["llm"].api_key:
                backend = "llm"
            elif self.backends["search_api"].key:
                backend = "search_api"
            else:
                backend = "direct"
        backend = backend if backend in self.backends else "direct"
        return self.backends[backend].search(query, sources, max_results)

    def channels(self, sources=None, city=""):
        """返回「渠道入口」（人社官网 / 外企 careers 目录）。

        这些**不是岗位**，单独给前端分区展示——避免它们混进岗位列表、
        挤占名额，也避免用户把它们当成岗位去投。

        `city` 由用户输入推导：未收录的城市只给全国通用渠道 + 自助检索提示。
        """
        return self.backends["direct"].channels(sources, city)

    def ingest(self, results):
        """把 Agent 搜到的结果写入 positions_live.json（去重合并），供 agent 后端读取。

        返回 (新增条数, 合并后总条数)。
        """
        path = self.backends["agent"].path
        if not path:
            return 0, 0
        existing = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                existing = d.get("results", []) if isinstance(d, dict) else d
            except Exception:
                existing = []
        seen = {(x.get("role"), x.get("company"), x.get("salary"))
                for x in existing if isinstance(x, dict)}
        added = 0
        for p in results or []:
            if not isinstance(p, dict):
                continue
            p = _norm(p)
            k = (p.get("role"), p.get("company"), p.get("salary"))
            if k in seen:
                continue
            seen.add(k)
            existing.append(p)
            added += 1
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"updated": datetime.date.today().isoformat(),
                           "results": existing}, f, ensure_ascii=False, indent=1)
        except Exception:
            return 0, len(existing)
        return added, len(existing)
