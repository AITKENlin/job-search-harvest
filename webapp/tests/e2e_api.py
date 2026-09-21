"""端到端接口测试：用 Flask test_client 打通 / /search /search/backends /analyze。

运行：cd webapp && python tests/e2e_api.py
预期：16 通过 / 0 失败。注意第 3 项（MNC 目录）不做中文串断言——
Flask tojson 默认 ASCII 转义，中文公司名以 \\uXXXX 输出，属正常。
"""
import os
import re
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A  # noqa: E402

c = A.app.test_client()
ok, fail = 0, 0


def check(n, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {n} {extra}")
    else:
        fail += 1
        print(f"  ❌ {n} {extra}")


print("== GET / ==")
html = c.get("/").get_data(as_text=True)
check("首页 200", len(html) > 0)
check("含实时搜索面板", "实时搜索" in html)
check("零配置提示可见", "不需要任何配置" in html)
check("进阶设置默认折叠", "<details" in html)
check("列头六件套齐备",
      all(k in html for k in ["公司名字", "公司属性", "地区", "职位名称", "直链网址", "投简历的邮箱"]))
check("投递列已扩展为『邮箱 / 报名方式』", "报名方式" in html)
check("含状态徽标渲染（在招/即将开始报名）", "即将开始报名" in html)
check("含渠道入口分区渲染函数", "renderChannels" in html)
check("人社渠道标注含报名邮箱", "含报名邮箱" in html)
m = re.search(r"const MNC = (\[.*?\]);", html, re.S)
mnc = json.loads(m.group(1)) if m else []
check("MNC JSON 已注入", len(mnc) > 0, f"({len(mnc)} 家)")

print("== GET /search/backends（零配置恒可用）==")
r = c.get("/search/backends")
b = r.get_json()
check("200", r.status_code == 200)
check("direct 恒可用", b["direct"] is True)
check("标注 zero_config_ok", b.get("zero_config_ok") is True)

print("== GET /search（人社渠道：应带真实投递邮箱）==")
d = c.get("/search?q=广州&sources=人社&backend=direct").get_json()
check("count>0", d["count"] > 0, f"({d['count']})")
mails = [x for x in d["results"] if (x.get("email") or "").strip()]
check("至少 1 条带真实邮箱", len(mails) > 0, f"({len(mails)} 条)")
check("邮箱格式合法", all(re.match(r"^[\w.+-]+@[\w-]+\.[\w.]+$", x["email"]) for x in mails))
check("带邮箱条目均有直链",
      all((x.get("link") or "").startswith("http") for x in mails))
for x in mails[:2]:
    print(f"  ℹ️ {x['company']} | {x['role']} | {x['city']} | {x['email']}")

print("== GET /search（地区中立：地区由用户输入决定，不绑定任何城市）==")
d = c.get("/search?q=上海 品牌经理&backend=direct").get_json()
check("query 里的城市被识别", d.get("city") == "上海", f"(city={d.get('city')})")
check("返回对应城市参数", d.get("city_param") == "sh", f"(param={d.get('city_param')})")
d2 = c.get("/search?q=品牌经理&city=深圳&backend=direct").get_json()
check("city 参数可显式指定", d2.get("city") == "深圳" and d2.get("city_param") == "sz")
d3 = c.get("/search?q=品牌经理&city=火星城&backend=direct").get_json()
check("未收录城市不报错、仍返回结果", d3.get("count") is not None and d3.get("city_known") is False)
check("未收录城市给出自助检索提示", bool(d3.get("city_hint")))
check("未收录城市的渠道不含编造的地方网址",
      all(("mohrss.gov.cn" in (x.get("link") or "") or "12333" in (x.get("link") or ""))
          for x in d3.get("channels", []) if x.get("source_type") == "人社"),
      f"({len(d3.get('channels', []))} 个入口)")

print("== GET /search（外企+主流平台：邮箱应为空，不得编造）==")
d = c.get("/search?q=品牌 内容&sources=外企,主流平台&backend=direct").get_json()
check("200 且有条目", d["count"] > 0, f"({d['count']})")
check("这些渠道邮箱为空（如实留空）",
      all(not (x.get("email") or "").strip() for x in d["results"]))
check("六件套前五项完整",
      all((x.get("company") or "").strip() and (x.get("company_type") or "").strip()
          and (x.get("city") or "").strip() and (x.get("role") or "").strip()
          and (x.get("link") or "").strip() for x in d["results"]))

print("== GET /search：query 必须真正生效（防回归核心）==")
db = c.get("/search?q=品牌&sources=主流平台&backend=direct").get_json()
da = c.get("/search?q=会计&sources=主流平台&backend=direct").get_json()
check("品牌 query 有结果", db["count"] > 0, f"({db['count']})")
check("回显 matched_terms 且非空", len(db.get("matched_terms") or []) > 0, str(db.get("matched_terms", [])[:6]))
check("query_applied=True", db.get("query_applied") is True)
n_role_brand = sum(1 for x in db["results"] if "品牌" in (x.get("role") or ""))
check("品牌结果中 role 含『品牌』≥3 条", n_role_brand >= 3, f"({n_role_brand}/{db['count']})")
check("品牌 / 会计 的接口结果集不同（旧版完全相同＝缺陷）",
      [x.get("link") for x in db["results"]] != [x.get("link") for x in da["results"]])
check("相关度排序：第 1 条 role 应含『品牌』", "品牌" in (db["results"][0].get("role") or ""),
      f"({db['results'][0].get('role')})")

print("== GET /search：channels 与 results 分离 ==")
dall = c.get("/search?q=品牌&backend=direct").get_json()   # 全渠道
check("响应含 channels 字段", isinstance(dall.get("channels"), list))
check("全渠道时 channels 非空", (dall.get("channel_count") or 0) > 0, f"({dall.get('channel_count')} 个)")
check("channels 条目均自称『入口』（明确不是岗位）",
      all("入口" in (c0.get("role") or "") for c0 in dall["channels"]))
check("results 里不混入渠道占位岗",
      not any("入口" in (x.get("role") or "") for x in dall["results"]))
check("只勾主流平台时 channels 应为空（渠道与勾选一致）",
      (db.get("channel_count") or 0) == 0, f"({db.get('channel_count')})")

print("== GET /search：邮箱计数与报名方式 ==")
check("响应含 email_count", "email_count" in db)
dh = c.get("/search?q=行政辅助&sources=人社&backend=direct").get_json()
check("人社渠道 email_count>0", dh["email_count"] > 0, f"({dh['email_count']})")
check("无邮箱条目必须带 apply_method",
      all((x.get("email") or "").strip() or (x.get("apply_method") or "").strip() for x in dh["results"]))
check("含'即将开始报名'状态的公告岗可用",
      any(x.get("status") == "即将开始报名" for x in dh["results"]),
      str([x.get("status") for x in dh["results"]][:5]))

print("== GET /search（空 q → 400）==")
r = c.get("/search?q=")
check("400", r.status_code == 400, f"(got {r.status_code})")

print("== POST /search/import（Agent 推入结果 → 零配置可检索）==")
live = os.path.join(ROOT, "positions_live.json")
backup = None
if os.path.exists(live):                      # 保护既有数据，测完还原
    with open(live, encoding="utf-8") as fh:
        backup = fh.read()
    os.remove(live)                           # 先清干净，断言才确定（否则残留会让 added 变 0）
r = c.post("/search/import", json={"results": [
    {"company": "宝洁 P&G", "role": "品牌经理", "city": "广州", "salary": "30-45k",
     "source": "https://www.pg.com.cn/careers", "source_type": "外企", "foreign": True},
]})
d = r.get_json()
check("导入成功", r.status_code == 200 and d.get("added") == 1, str(d))
d2 = c.get("/search?q=品牌&sources=外企").get_json()
check("auto 命中导入结果", any("宝洁" in (x.get("company") or "") for x in d2["results"]),
      f"(backend={d2.get('backend')})")
check("空 results → 400", c.post("/search/import", json={"results": []}).status_code == 400)
if backup is None:
    os.path.exists(live) and os.remove(live)   # 清理测试写入
else:
    with open(live, "w", encoding="utf-8") as fh:
        fh.write(backup)
check("测试后运行时文件已清理/还原", (not os.path.exists(live)) if backup is None else True,
      f"(backup={'有' if backup is not None else '无'})")

print("== GET /search（主流平台 direct, 真网络）==")
d = c.get("/search?q=品牌 内容&sources=主流平台&backend=direct").get_json()
check("200", "count" in d)
print(f"  ℹ️ 主流平台返回 {d.get('count')} 条")

print("== POST /analyze（粘贴简历文本）==")
resume = """示例候选人，1980年生，46岁，广州。20年品牌营销与内容运营经验。
曾任某4A广告集团策略群总监、某制造企业品牌总监。
服务行业：汽车、快消、家电。
擅长品牌策略、内容营销、短视频、AI Agent 交付。
设计工程硕士，汉语言文学学士。持 C1 驾照。
"""
d = c.post("/analyze", data={"resume_text": resume, "age": "46", "city": "广州",
                            "salary_flex": "1", "location_flex": "1",
                            "constraints": "英语不作为工作语言"}).get_json()
check("有推荐列表", len(d.get("recommendations", [])) > 0, f"(Top{len(d.get('recommendations', []))})")
check("有策略建议", len(d.get("strategy", [])) > 0)
check("有 HR 需求对标", len(d.get("hr_demands", [])) > 0)
check("有激励文案", len(d.get("encouragement", [])) > 0, f"({len(d.get('encouragement', []))} 条)")
top = (d.get("recommendations") or [{}])[0]
print(f"  ℹ️ Top1: {top.get('role')} · {top.get('company')} · {top.get('salary')} ({top.get('tier')})")

print("== POST /analyze（空 → 400）==")
check("400", c.post("/analyze", data={"resume_text": ""}).status_code == 400)

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
