"""地区解析：让工具不锁死任何单一城市。

**设计前提**：本工具默认运行在用户自己的 AI 助手（大模型）里，**地区应当来自用户自己输入的需求**
（例如"上海 品牌经理"），本模块只做两件事：
  1. 把用户输入里出现的城市，解析成招聘平台可用的**城市参数**；
  2. 提供该城市的**官方公共就业渠道**（只收录已核实的，绝不编造网址）。

**零编造纪律**：未收录的城市**不猜网址**——而是返回全国通用渠道，并提示用户
「用你自己的 AI 助手检索『〈你的城市〉人社局 招聘公告』」。空白比编造有价值。

用法：
    >>> detect_city("上海 品牌经理")
    '上海'
    >>> city_param("上海")
    'sh'
    >>> [c["name"] for c in official_sources("上海")]
    ['中国公共招聘网（人社部）', ...]
"""
import os
import re

# 样本数据集所在城市：仅当用户**完全没有**指定城市时作为兜底，不代表工具专属于该城市。
FALLBACK_CITY = "广州"

# 城市 → 招聘平台城市参数（猎聘 SEO 落地页路径用的就是这套参数）。
# 说明：这些参数由目标网站定义（例如广州的路径段是 city-gz），
# 它只是**平台参数**，不是本工具的身份标识——本工具对所有城市一视同仁。
CITY_PARAMS = {
    "广州": "gz", "深圳": "sz", "北京": "bj", "上海": "sh", "杭州": "hz",
    "成都": "cd", "武汉": "wh", "南京": "nj", "西安": "xa", "苏州": "su",
    "重庆": "cq", "天津": "tj", "长沙": "cs", "青岛": "qd", "郑州": "zz",
    "合肥": "hf", "厦门": "xm", "佛山": "fs", "东莞": "dg", "珠海": "zh",
    "宁波": "nb", "无锡": "wx", "福州": "fz", "济南": "jn", "大连": "dl",
    "沈阳": "sy", "昆明": "km", "南昌": "nc", "贵阳": "gy", "南宁": "nn",
}

# 省级行政区：省级地名与城市名一样，只表达"在哪找"，不表达"找什么"，
# 因此同样从检索词元里剔除（地区由 city 字段单独承载）。
PROVINCE_NAMES = (
    "广东", "广西", "江苏", "浙江", "山东", "河南", "河北", "四川", "湖北", "湖南",
    "福建", "安徽", "陕西", "辽宁", "吉林", "黑龙江", "云南", "贵州", "江西", "山西",
    "甘肃", "青海", "宁夏", "新疆", "西藏", "内蒙古", "海南", "台湾", "香港", "澳门",
)

# 已核实的**城市专属**官方公共就业渠道。只放确认过真实存在的网址。
CITY_OFFICIAL = {
    "广州": [
        {"name": "广州市人社局·招聘就业", "url": "https://rsj.gz.gov.cn/", "type": "人社"},
        {"name": "广东省人社厅", "url": "https://hrss.gd.gov.cn/", "type": "人社"},
        {"name": "广东政务服务网", "url": "https://www.gdzwfw.gov.cn/", "type": "人社"},
        {"name": "广州本地宝·招聘", "url": "https://gz.bendibao.com/job/", "type": "人社"},
    ],
}

# 全国通用渠道（人社部主办），任何城市都适用。
NATIONAL_OFFICIAL = [
    {"name": "中国公共招聘网（人社部）", "url": "http://job.mohrss.gov.cn/", "type": "人社"},
    {"name": "人社部·12333 公共服务", "url": "https://www.12333.gov.cn/", "type": "人社"},
]


def sample_city():
    """未指定城市时的兜底城市（可用环境变量 JOB_CITY 覆盖，例如 JOB_CITY=上海）。"""
    return (os.environ.get("JOB_CITY") or "").strip() or FALLBACK_CITY


def all_city_names():
    return tuple(CITY_PARAMS.keys())


def all_city_params():
    """所有城市参数（gz/sh/bj…）。用户直接写参数（如"gz 品牌"）时也要能识别为地区词。"""
    return tuple(sorted(set(CITY_PARAMS.values())))


def detect_city(text, default=""):
    """从任意文本里解析城市名。

    优先精确匹配已收录城市；找不到则尝试常见的「XX市」写法。
    都找不到就返回 default——**不猜**。
    """
    t = str(text or "")
    if not t:
        return default
    hits = [c for c in CITY_PARAMS if c in t]
    if hits:
        # 多个城市同时出现时取最长的那个（"佛山" 优先于 "山" 这类子串误判）
        return max(hits, key=len)
    m = re.search(r"([\u4e00-\u9fa5]{2,4})市", t)
    if m:
        return m.group(1)
    return default


def city_param(city):
    """城市 → 平台城市参数。未收录的城市返回兜底城市的参数。

    未收录城市**不会导致搜不到**：用户在 query 里写了城市名，检索本身
    就是按那个城市搜的（本参数只影响"从哪个平台的落地页抓"这一步）。
    """
    c = (city or "").strip()
    if c in CITY_PARAMS:
        return CITY_PARAMS[c]
    return CITY_PARAMS.get(sample_city(), "gz")


def is_known_city(city):
    return (city or "").strip() in CITY_PARAMS


def official_sources(city=""):
    """返回官方公共就业渠道。

    - 已收录的城市：城市专属渠道 + 全国通用渠道；
    - 未收录的城市：仅全国通用渠道（并配合 `city_hint()` 提示用户自行检索本地人社局）。

    **不编造任何未核实的地方网址。**
    """
    c = (city or "").strip()
    out = list(CITY_OFFICIAL.get(c, []))
    out += [dict(x) for x in NATIONAL_OFFICIAL]
    return out


def city_hint(city=""):
    """未收录城市时给用户的一句可执行提示（不是道歉，是下一步）。"""
    c = (city or "").strip()
    if not c or is_known_city(c):
        return ""
    return (f"暂未收录「{c}」的官方渠道网址（不编造）。"
            f"建议用你的 AI 助手检索：〈{c}人社局 招聘公告〉〈{c}公共招聘网〉"
            f"〈{c}国资委 招聘〉，即可拿到本地官方岗位与报名邮箱。")


def describe(city=""):
    """给前端/日志用的一句话地区说明。"""
    c = (city or "").strip() or sample_city()
    return f"检索地区：{c}" + ("" if is_known_city(c) else "（未收录本地官渠，已给全国通用渠道）")
