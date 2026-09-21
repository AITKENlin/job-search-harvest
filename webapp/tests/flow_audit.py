"""全流程顺畅度体检：模拟一个 40+ 求职者的真实使用路径，逐环节找不符合需求处。

模拟路径（与实际用户操作一致）：
  1. 打开首页 → 2. 上传/粘贴简历 → 3. 分析得建议 → 4. 搜索岗位（多组关键词）
  → 5. 逐条检查列表六个字段是否齐备 → 6. 检查邮箱可得性 → 7. 检查渠道是否被误当岗位

注：本用例使用**虚构的示例简历**，不锚定任何真实个人。
"""
import os
import re
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app as A  # noqa: E402

c = A.app.test_client()
issues = []


def log(step, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {step} {detail}")
    if not ok:
        issues.append(f"{step} {detail}")


print("=" * 84)
print("STEP 1  打开首页")
print("=" * 84)
h = c.get("/").get_data(as_text=True)
log("首页返回", len(h) > 1000, f"({len(h)} 字节)")
log("六列表头齐备", all(k in h for k in ["公司名字", "公司属性", "地区", "职位名称", "直链网址"]))
log("投递列表头存在", "投简历的邮箱" in h)
# 发布自检：确认页面里没有把**你自己的**姓名 / 公司硬编码进去。
# 把你自己的个人信息词写进 webapp/.forbidden_terms（每行一个，该文件已被 .gitignore 忽略，
# 不会随仓库发布）。文件不存在时本项自动跳过——这样同一份代码在本地和公开仓库都能跑。
FORBIDDEN_TERMS = []
_fp = os.path.join(ROOT, ".forbidden_terms")
if os.path.exists(_fp):
    with open(_fp, encoding="utf-8") as _f:
        FORBIDDEN_TERMS = [x.strip() for x in _f if x.strip() and not x.startswith("#")]
log("首页未硬编码个人信息（无 .forbidden_terms 时跳过）",
    not any(k in h for k in FORBIDDEN_TERMS), f"({len(FORBIDDEN_TERMS)} 个待查词)")
log("首页含激励首屏", "hero-lead" in h and "判断力" in h)
log("首页声明不锚定个人", "不锚定任何特定个人" in h)
log("首页不预设城市（地区可选、可自动识别）",
    "不预设城市" in h and "citylist" in h and "广州 品牌内容" not in h)

print()
print("=" * 84)
print("STEP 2-3  粘贴简历 → 分析并给建议")
print("=" * 84)
resume = """示例候选人，1980年生，46岁，广州。20年品牌营销与内容运营经验。
某4A广告集团策略群总监、某制造企业品牌总监（带过20人以上团队）。
服务行业：汽车、快消、家电。
擅长品牌策略、内容营销、短视频、AI Agent 交付。设计工程硕士，汉语言文学学士。
硬约束：英语不作为工作语言、不接受长期出差。"""
d = c.post("/analyze", data={"resume_text": resume, "age": "46", "city": "广州",
                            "salary_flex": "1", "location_flex": "1",
                            "constraints": "英语不作为工作语言"}).get_json()
log("返回激励文案", len(d.get("encouragement", [])) > 0, f"({len(d.get('encouragement', []))} 条)")
log("激励文案不含硬编码个人信息",
    not any(k in "".join(d.get("encouragement", [])) for k in FORBIDDEN_TERMS))
log("年资识别正确（不把出生年份当工龄）", d["profile"].get("years") == 20,
    f"(years={d['profile'].get('years')})")
log("返回推荐列表", len(d.get("recommendations", [])) > 0, f"(Top{len(d.get('recommendations', []))})")
log("返回求职策略", len(d.get("strategy", [])) > 0)
log("推荐项含匹配理由", all(r.get("reason") for r in d.get("recommendations", [])[:3]))
print(f"     ℹ️ Top1 推荐：{d['recommendations'][0]['role']} · {d['recommendations'][0]['company']}")

print()
print("=" * 84)
print("STEP 4-6  搜索岗位（模拟用户实际会输入的多组关键词）")
print("=" * 84)
QUERIES = ["品牌", "品牌相关工作", "内容营销", "出海 品牌", "咨询顾问", "培训讲师", "文宣 宣传"]
report = []
for q in QUERIES:
    r = c.get(f"/search?q={q}&backend=direct").get_json()
    rows = r.get("results", [])
    mail = r.get("email_count", 0)
    # 六个字段齐备度
    fields = {}
    for f in ["company", "company_type", "city", "role", "link"]:
        fields[f] = sum(1 for x in rows if (x.get(f) or "").strip())
    fields["email_or_apply"] = sum(
        1 for x in rows if (x.get("email") or "").strip() or (x.get("apply_method") or "").strip())
    fields["email"] = mail
    n = len(rows) or 1
    # 相关性：role 中命中关键词词元
    toks = [t for t in (r.get("matched_terms") or [])][:6]
    rel = sum(1 for x in rows if any(t in (x.get("role") or "") for t in toks))
    report.append((q, len(rows), mail, rel, fields))
    print(f"\n  ── 关键词「{q}」→ {len(rows)} 条岗位，带邮箱 {mail} 条，role 命中关键词 {rel} 条")
    if rows:
        print(f"     第1条：{rows[0]['company']} | {rows[0].get('company_type','')} | "
              f"{rows[0]['city']} | {rows[0]['role']}")
        print(f"     投递：{rows[0].get('email') or rows[0].get('apply_method') or '（空）'}")
    log(f"  字段齐备（公司名/属性/地区/职位/直链）",
        all(fields[f] == len(rows) for f in ["company", "company_type", "city", "role", "link"])
        or len(rows) == 0,
        f"(公司名{fields['company']}/{len(rows)} 属性{fields['company_type']}/{len(rows)} "
        f"地区{fields['city']}/{len(rows)} 职位{fields['role']}/{len(rows)} 直链{fields['link']}/{len(rows)})")
    log(f"  相关性（role 命中关键词）", rel > 0 or len(rows) == 0, f"({rel}/{len(rows)})")
    log(f"  投递信息可得（邮箱或报名方式）",
        fields["email_or_apply"] == len(rows) or len(rows) == 0,
        f"({fields['email_or_apply']}/{len(rows)})")

print()
print("=" * 84)
print("STEP 7  渠道入口是否被误当岗位")
print("=" * 84)
r = c.get("/search?q=品牌&backend=direct").get_json()
log("岗位列表不含渠道入口", not any("入口" in (x.get("role") or "") for x in r["results"]))
log("渠道入口单独返回", r.get("channel_count", 0) > 0, f"({r.get('channel_count')} 个)")
log("渠道条目自称『入口』", all("入口" in (x.get("role") or "") for x in r.get("channels", [])))

print()
print("=" * 84)
print("体检结论")
print("=" * 84)
if issues:
    print(f"仍存在 {len(issues)} 处不符合项：")
    for i in issues:
        print("  ❌", i)
else:
    print("✅ 全流程 7 个环节全部符合需求")

print()
print("邮箱可得性汇总（这是历史痛点）：")
for q, n, mail, rel, f in report:
    print(f"  「{q}」→ {n} 条中 {mail} 条有邮箱，{f['email_or_apply']} 条给出了投递方式")
sys.exit(1 if issues else 0)
