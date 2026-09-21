"""search.py 回归测试：验证零配置路径（agent/direct）、query 相关度、解析、调度均不报错。

运行：cd webapp && python tests/smoke_search.py
预期：全部通过 / 0 失败，退出码 0。（真网络项允许返回 0 条，不做硬断言）

本文件在 2026-09-21 新增了「query 真正参与检索」的成组断言——
此前存在严重缺陷：`_liepin(query)` 收了 query 却从不使用（写死 6 个 slug），
且 positions.json 的 harvested 段从未被读取，导致**搜什么结果都一样、且全是伪岗**。
"""
import os
import re
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import search as S  # noqa: E402

MNC = os.path.join(ROOT, "positions.json")
ok, fail = 0, 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {name} {extra}")
    else:
        fail += 1
        print(f"  ❌ {name} {extra}")


eng = S.SearchEngine(mnc_path=MNC)

print("== 1. query 必须真正参与检索（防回归核心）==")
r_brand = eng.search("品牌", sources=[], backend="direct", max_results=20)
r_acct = eng.search("会计", sources=[], backend="direct", max_results=20)
check("品牌 query 有结果", len(r_brand) > 0, f"({len(r_brand)} 条)")
n_brand_in_role = sum(1 for r in r_brand if "品牌" in (r.get("role") or ""))
check("品牌 query 返回的岗位名含『品牌』≥3 条", n_brand_in_role >= 3,
      f"({n_brand_in_role}/{len(r_brand)} 条 role 含『品牌』)")
check("品牌 / 会计 结果集必须不同（旧版完全相同＝缺陷）",
      [r.get("link") for r in r_brand] != [r.get("link") for r in r_acct])
check("会计 query 不得返回品牌岗",
      all("品牌" not in (r.get("role") or "") for r in r_acct), f"({len(r_acct)} 条)")
# 兜底纪律：关键词完全无命中时退回全集，绝不返回空列表
r_none = eng.search("zzzzz无此岗", sources=["人社"], backend="direct", max_results=20)
check("无命中时兜底返回全集（不甩空列表）", len(r_none) > 0, f"({len(r_none)} 条)")

print("== 2. _tokens / _relevance / _route_slugs 纯函数 ==")
check("中文长词补 2-gram（'品牌营销'→含'品牌'）", "品牌" in S._tokens("品牌营销"))
check("检索词命中整串", "品牌营销" in S._tokens("品牌营销"))
check("停用词被剔除", "广州" not in S._tokens("广州 品牌") and "相关" not in S._tokens("品牌相关"))
check("role 命中权重 > duty 命中权重",
      S._relevance({"role": "品牌经理"}, ["品牌"]) > S._relevance({"duty": "品牌相关"}, ["品牌"]))
check("零命中得 0 分", S._relevance({"role": "会计"}, ["品牌"]) == 0.0)
check("slug 路由：'品牌' 命中品牌线", "zpppwco4i3" in S._route_slugs("品牌"))
check("slug 路由：'出海' 命中出海线", "zphwppchzjp55l" in S._route_slugs("出海"))
check("slug 路由：无命中时回落默认且非空", len(S._route_slugs("xyzzy")) > 0)
check("slug 路由有上限（控延迟）", len(S._route_slugs("品牌 内容 出海 培训 市场")) <= S.MAX_SLUGS_PER_QUERY)

print("== 3. 渠道入口与岗位结果必须分离（旧的伪岗挤占槽位）==")
chans = eng.channels([])
check("channels() 返回入口", len(chans) > 0, f"({len(chans)} 个)")
check("channels 含外企 careers 目录", any(c["source_type"] == "外企" for c in chans))
check("channels 含人社官网目录", any(c["source_type"] == "人社" for c in chans))
check("岗位结果里不再混入『请进链接查看』伪岗",
      not any("请进" in (r.get("role") or "") for r in r_brand))
check("岗位结果里不再混入外企 careers 占位岗",
      not any("入口" in (r.get("role") or "") for r in r_brand))

print("== 4. positions.json 的 harvested 必须被读取（旧版从未读取）==")
check("harvested 真实岗出现在结果中",
      any(r.get("company") in ("立白科技集团", "中大咨询集团", "健合(中国) Swisse") for r in r_brand))
check("harvested 的 link 由 source 标注推导出来",
      all((r.get("link") or "").startswith("https://www.liepin.com/")
          for r in r_brand if r.get("source_type") == "主流平台"),
      f"({sum(1 for r in r_brand if r.get('source_type') == '主流平台')} 条平台岗)")

print("== 5. 展示字段覆盖（公司名/公司属性/地区/职位/直链/投递方式）==")
cov = {f: sum(1 for r in r_brand if (r.get(f) or "").strip()) for f in
       ["company", "company_type", "role", "city", "link"]}
for f, label in [("company", "公司名字"), ("company_type", "公司属性"),
                 ("role", "职位名称"), ("city", "地区"), ("link", "直链网址")]:
    check(f"{label}全部有值", cov[f] == len(r_brand), f"({cov[f]}/{len(r_brand)})")

print("== 6. 邮箱与报名方式：有邮箱给邮箱，无邮箱必须给真实报名方式 ==")
r_hr = eng.search("行政辅助 宣传", sources=["人社"], backend="direct", max_results=40)
check("人社渠道能按关键词命中公告岗", len(r_hr) >= 3, f"({len(r_hr)} 条)")
check("人社渠道存在真实投递邮箱",
      any((r.get("email") or "").strip() for r in r_hr),
      f"({sum(1 for r in r_hr if (r.get('email') or '').strip())} 条带邮箱)")
mails = [r["email"] for r in r_hr if (r.get("email") or "").strip()]
check("邮箱格式合法", all(re.match(r"^[\w.+-]+@[\w-]+\.[\w.]+$", m) for m in mails), str(mails[:3]))
check("无邮箱的公告岗必须给出报名方式（不得留空）",
      all((r.get("email") or "").strip() or (r.get("apply_method") or "").strip() for r in r_hr))
check("平台渠道邮箱如实留空（不编造）",
      all(not (r.get("email") or "").strip()
          for r in r_brand if r.get("source_type") == "主流平台"))
check("平台渠道岗位必须给出投递方式（不得只留一个空格）",
      all((r.get("apply_method") or "").strip() for r in r_brand))
check("招聘人可直连时投递方式应带上招聘人",
      all("招聘人" in (r.get("apply_method") or "") or not (r.get("recruiter") or "")
          for r in r_brand))

print("== 7. AgentBackend：零配置首选路径（读落盘结果）==")
tmp = tempfile.mkdtemp()
live = os.path.join(tmp, "positions_live.json")
fresh = S.SearchEngine(mnc_path=MNC, live_path=live)
check("无落盘文件时 has_data=False", fresh.backends["agent"].has_data() is False)
check("无落盘文件时返回空", fresh.backends["agent"].search("x", [], 10) == [])
check("ingest 写入 2 条", fresh.ingest([
    {"company": "宝洁", "role": "品牌经理", "city": "广州", "salary": "25-35k",
     "source": "https://x/1", "source_type": "外企", "foreign": True},
    {"company": "某国企", "role": "文宣岗", "city": "广州", "salary": "8-10k",
     "source": "https://x/2", "source_type": "人社"},
]) == (2, 2))
check("has_data=True", fresh.backends["agent"].has_data() is True)
check("auto 优先走 agent", fresh.backends["agent"].search("品牌", [], 10)[0]["company"] == "宝洁")
check("按源过滤生效", len(fresh.backends["agent"].search("x", ["人社"], 10)) == 1)
check("重复 ingest 不重复计数",
      fresh.ingest([{"company": "宝洁", "role": "品牌经理", "city": "广州", "salary": "25-35k"}]) == (0, 2))
check("落盘文件已生成", os.path.exists(live))
check("Agent 落盘结果同样受 query 约束（搜'文宣'不应返回'品牌经理'）",
      len(fresh.backends["agent"].search("文宣", [], 10)) == 1)

print("== 8. _parse_liepin（mock HTML，对齐真实 DOM）==")
mock = """
<div class="job-card-pc-container">
 <a href="https://www.liepin.com/a/11111111.shtml" data-jobId="11111111">
  <div class="job-title-box"><div title="品牌营销总监" class="ellipsis-1">品牌营销总监</div>
    <div class="job-dq-box"><span class="dq-bracket">【</span><span class="ellipsis-1">天河区</span><span class="dq-bracket">】</span></div>
  </div><span class="job-salary">40-50k</span>
  <div class="job-labels-box"><span class="labels-tag">10年以上</span><span class="labels-tag">本科</span></div>
 </a>
 <div class="job-detail-company-box">
   <span class="company-name ellipsis-1">某某科技</span>
   <div class="company-tags-box ellipsis-1"><span>互联网,广告/公关/会展</span><span>已上市</span><span>2000-5000人</span></div>
 </div>
 <div class="recruiter-name ellipsis-1">刘女士</div><div class="recruiter-title ellipsis-1">HRM</div>
</div>
<div class="job-card-pc-container">
 <a href="https://www.liepin.com/job/22222222.shtml" data-jobId="22222222">
  <div class="job-title-box"><div title="内容运营经理" class="ellipsis-1">内容运营经理</div>
    <div class="job-dq-box"><span class="dq-bracket">【</span><span class="ellipsis-1">海珠区</span><span class="dq-bracket">】</span></div>
  </div><span class="job-salary">20-30k</span>
  <div class="job-labels-box"><span class="labels-tag">5-10年</span><span class="labels-tag">大专</span></div>
 </a>
 <div class="job-detail-company-box">
   <span class="company-name ellipsis-1">某电商</span>
   <div class="company-tags-box ellipsis-1"><span>电子商务</span><span>融资未公开</span><span>1000-2000人</span></div>
 </div>
</div>
"""
parsed = S.DirectBackend._parse_liepin(mock, "https://www.liepin.com/city-gz/mock/")
check("解析出 2 条", len(parsed) == 2, f"({len(parsed)})")
check("职位名称取自 title 属性", parsed[0]["role"] == "品牌营销总监", f"({parsed[0]['role']})")
check("公司名字正确", parsed[1]["company"] == "某电商", f"({parsed[1]['company']})")
check("地区取自 job-dq-box", parsed[0]["city"] == "天河区", f"({parsed[0]['city']})")
check("公司属性三标签组合", parsed[0]["company_type"] == "互联网,广告/公关/会展 · 已上市 · 2000-5000人",
      f"({parsed[0]['company_type']})")
check("直链 /a/ 型可解析", parsed[0]["link"] == "https://www.liepin.com/a/11111111.shtml", parsed[0]["link"])
check("直链 /job/ 型可解析", parsed[1]["link"] == "https://www.liepin.com/job/22222222.shtml", parsed[1]["link"])
check("经验/学历解析", parsed[0]["exp"] == "10年以上" and parsed[0]["edu"] == "本科")
check("招聘人解析", parsed[0]["recruiter"] == "刘女士" and parsed[0]["recruiter_title"] == "HRM")
check("邮箱如实留空（平台不公开）", parsed[0]["email"] == "")
check("重复内容去重（同一岗位只保留 1 条）",
      len(S.DirectBackend._parse_liepin(mock + mock, "u")) == 2,
      f"({len(S.DirectBackend._parse_liepin(mock + mock, 'u'))})")
fallback = '<div class="job-list-item"><span class="ellipsis-1">测试岗</span><span class="job-salary">10-20k</span></div>'
check("无新容器时回落 job-list-item", len(S.DirectBackend._parse_liepin(fallback, "u")) == 1)

print("== 9. 进阶后端纯函数（不联网、不校验 key）==")
fake = ('好的：\n[{"company":"宝洁","role":"品牌经理","city":"广州","salary":"25-35k",'
        '"age_friendly":"⚠️","source":"https://x.com/a","source_type":"外企","foreign":true}]\n以上。')
arr = S.LLMSearchBackend._parse_json(fake)
check("LLM JSON 解析出 1 条", len(arr) == 1)
check("归一化补全 city/exp/apply_method", arr[0]["city"] == "广州" and "exp" in arr[0] and "apply_method" in arr[0])
check("live=True", arr[0]["live"] is True)
check("空响应不报错", S.LLMSearchBackend._parse_json("没有结果") == [])
check("LLM prompt 要求回填 apply_method",
      "apply_method" in S.LLMSearchBackend()._build_prompt("品牌", []))
p = S.SearchApiBackend._to_posting(
    {"title": "资深品牌经理 20-30k 外企", "snippet": "广州 外资", "link": "https://www.liepin.com/x"})
check("识别外企+薪资+平台", p["foreign"] is True and "20-30k" in p["salary"] and p["source_type"] == "主流平台")

print("== 10. 调度回落 ==")
check("auto 落在有效后端", eng.search("q", sources=["人社"], backend="auto") is not None)
check("非法后端回退 direct", eng.search("q", sources=["人社"], backend="zzz") is not None)

print("== 12. 地区中立（2026-09-21 新增：工具不绑定任何单一城市）==")
import region as R  # noqa: E402

check("识别城市（上海）", R.detect_city("上海 品牌经理") == "上海")
check("识别城市（无城市时回落兜底，不猜）",
      R.detect_city("品牌经理", default="") == "")
check("多城市取最长匹配", R.detect_city("佛山 深圳 品牌经理") in ("佛山", "深圳"))
check("城市参数映射", R.city_param("上海") == "sh" and R.city_param("深圳") == "sz")
check("未收录城市不编造参数（回落兜底）", R.city_param("火星城") == R.city_param(R.sample_city()))
check("未收录城市不编造本地网址（只给全国通用渠道）",
      all("mohrss.gov.cn" in c["url"] or "12333" in c["url"] for c in R.official_sources("火星城")))
check("已收录城市=专属渠道+全国渠道",
      len(R.official_sources(R.sample_city())) > len(R.official_sources("火星城")))
check("未收录城市给自助检索提示", bool(R.city_hint("火星城")) and not R.city_hint(R.sample_city()))
check("fallback_city 可被环境变量 JOB_CITY 覆盖",
      (lambda: (os.environ.__setitem__("JOB_CITY", "上海"), R.sample_city())[-1])() == "上海")
os.environ.pop("JOB_CITY", None)
check("落地页链接随城市变", "city-sh" in S._link_from_source("liepin zpppwco4i3", "上海")
      and "city-gz" in S._link_from_source("liepin zpppwco4i3", R.sample_city()))
check("城市名不参与词元打分（地区只由 city 字段承载）",
      "上海" not in S._tokens("上海 品牌") and "gz" not in [x.lower() for x in S._tokens("gz 品牌")])
r_sh = eng.search("上海 品牌经理", sources=[], backend="direct", max_results=5)
check("换城市搜索不报错（未收录/已收录均可）", isinstance(r_sh, list))
ch_sh = eng.channels(["人社", "外企"], "上海")
ch_xx = eng.channels(["人社", "外企"], "火星城")
check("渠道入口按城市变化", len(ch_sh) > 0 and len(ch_xx) > 0)
check("未收录城市渠道里带自助检索提示",
      any("AI 助手" in (c.get("duty") or "") for c in ch_xx),
      f"({len(ch_xx)} 个入口)")

print("== 13. 主流平台直连（真网络，允许失败）==")
try:
    lp = eng.search("品牌 内容", sources=["主流平台"], backend="direct", max_results=8)
    print(f"  ℹ️ 主流平台直连返回 {len(lp)} 条（被拦截时为 0，属预期）")
except Exception as e:
    print(f"  ℹ️ 主流平台直连异常（不应抛出）：{e}")

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
