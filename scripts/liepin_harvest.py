# -*- coding: utf-8 -*-
"""猎聘 SEO 落地页岗位采集器：解析服务端渲染的职位卡片。"""
import re, html, json, sys

def clean(x):
    return html.unescape(re.sub(r'<[^>]+>', '', x)).strip()

def parse_liepin(path_or_text, is_text=False):
    s = path_or_text if is_text else open(path_or_text, encoding='utf-8', errors='ignore').read()
    # 按 job-list-item 切块
    blocks = re.split(r'<div class="job-list-item"', s)[1:]
    jobs = []
    for b in blocks:
        b = b[:6000]
        def g(pat, flags=re.S):
            m = re.search(pat, b, flags)
            return clean(m.group(1)) if m else ''
        title = g(r'<div title="([^"]+)" class="ellipsis-1">')
        if not title:
            title = g(r'<div class="ellipsis-1">([^<]+)</div>')
        loc = g(r'<span class="ellipsis-1">([^<]+)</span>\s*<span class="dq-bracket">】')
        salary = g(r'<span class="job-salary">([^<]+)</span>')
        tags = re.findall(r'<span class="labels-tag"[^>]*>([^<]+)</span>', b)
        company = g(r'<span class="company-name ellipsis-1">([^<]+)</span>')
        ctags = re.findall(r'<div class="company-tags-box ellipsis-1">(.*?)</div>', b, re.S)
        ctags = [clean(t) for t in re.findall(r'<span>([^<]+)</span>', ctags[0])] if ctags else []
        rec = g(r'<div class="recruiter-name ellipsis-1"[^>]*>([^<]+)</div>')
        rtitle = g(r'<div class="recruiter-title ellipsis-1"[^>]*>([^<]+)</div>')
        url = g(r'href="(https://www\.liepin\.com/(?:a|job)/[^"]+)"')
        if not title:
            continue
        jobs.append({
            'title': title, 'loc': loc, 'salary': salary,
            'exp': tags[0] if len(tags) > 0 else '',
            'edu': tags[1] if len(tags) > 1 else '',
            'company': company, 'ctags': ctags,
            'recruiter': rec, 'rtitle': rtitle, 'url': url,
        })
    # 去重
    seen, out = set(), []
    for j in jobs:
        k = (j['title'], j['salary'], j['company'])
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out


if __name__ == '__main__':
    for f in sys.argv[1:]:
        js = parse_liepin(f)
        print('### %s  共 %d 条' % (f, len(js)))
        for j in js:
            print('%-28s %-10s %-12s %-6s %-6s | %s | %s %s' % (
                j['title'][:28], j['loc'], j['salary'], j['exp'], j['edu'],
                j['company'], j['recruiter'], j['rtitle']))
