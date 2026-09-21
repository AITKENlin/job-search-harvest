"""40+ 求职建议 · 网页互动版（地区中立）

上传简历 → 解析 → 简短问卷 → 分析器输出建议；并可实时搜索岗位。

**零配置即可用**：实时搜索默认走"Agent 落盘结果"或"直连已知源"，不需要任何 API key。
**地区中立**：检索地区来自用户自己输入的需求（如"上海 品牌经理"）；未写城市时用
样本城市兜底，也可用环境变量 `JOB_CITY` 指定（例如 `JOB_CITY=上海 python app.py`）。
运行：pip install -r requirements.txt && python app.py  →  http://127.0.0.1:5000
"""
import os
import json
from flask import Flask, request, render_template, jsonify

import region
from resume_parser import extract_text
from analyzer import analyze_resume
from search import SearchEngine, _tokens, _relevance

app = Flask(__name__)
ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, 'positions.json'), encoding='utf-8') as f:
    POSITIONS = json.load(f)

# 样本数据集所在城市作为兜底（用户没写城市时才用），不改变"地区由用户决定"的设计。
region.FALLBACK_CITY = (POSITIONS.get('meta', {}).get('sample_city')
                        or region.FALLBACK_CITY)

# 实时搜索引擎：零配置优先。
#   - 岗位库目录 mnc_directory 随 positions.json 走，保持单一数据源
#   - positions_live.json 由"运行本 skill 的 Agent"写入（或 POST /search/import 推入）
ENGINE = SearchEngine(
    mnc_path=os.path.join(ROOT, 'positions.json'),
    live_path=os.path.join(ROOT, 'positions_live.json'),
)


@app.route('/')
def index():
    return render_template(
        'index.html',
        mnc=POSITIONS.get('mnc_directory', []),
        sample_city=region.sample_city(),
        city_options=sorted(region.all_city_names()),
    )


@app.route('/analyze', methods=['POST'])
def analyze():
    f = request.files.get('resume')
    text = extract_text(f) if f and f.filename else request.form.get('resume_text', '')
    q = {k: request.form.get(k, '') for k in
         ['age', 'city', 'industry', 'constraints', 'salary_flex', 'location_flex', 'years']}
    if not text.strip():
        return jsonify({'error': '请上传简历文件，或在文本框粘贴简历内容。'}), 400
    result = analyze_resume(text, q, POSITIONS)
    return jsonify(result)


@app.route('/search')
def search():
    """实时搜索：GET /search?q=关键词&sources=人社,外企,主流平台[&city=上海]

    返回分两块：
      - `results`  —— **岗位**（已按 q 相关度过滤 + 排序）
      - `channels` —— **渠道入口**（人社官网 / 外企 careers 目录），不是岗位

    **地区**：优先取 query 里出现的城市；没有就取 `city` 参数；都没有才用样本城市兜底。
    默认零配置：优先用 Agent 落盘的实时结果，否则直连已知源。无需任何 API key。
    进阶用户可传 backend=llm|search_api（须自行配置），或设 SEARCH_BACKEND 环境变量。
    """
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': '请填写搜索需求，如"上海 品牌内容 资深"'}), 400
    sources = [s for s in (request.args.get('sources') or '').split(',') if s]
    backend = request.args.get('backend') or None
    city = (request.args.get('city') or '').strip() \
        or region.detect_city(q, default=region.sample_city())
    results = ENGINE.search(q, sources=sources, backend=backend, max_results=40)
    channels = ENGINE.channels(sources, city)
    toks = _tokens(q)
    return jsonify({
        'query': q,
        'backend': backend or os.environ.get('SEARCH_BACKEND', 'auto'),
        'sources': sources,
        # 地区信息：让前端如实告诉用户"这次是按哪个城市在找"
        'city': city,
        'city_param': region.city_param(city),
        'city_known': region.is_known_city(city),
        'city_hint': region.city_hint(city),
        'sample_city': region.sample_city(),
        'count': len(results),
        'results': results,
        'channels': channels,
        'channel_count': len(channels),
        # 命中的检索词 + 是否真的用 query 过滤过，便于前端如实说明
        'matched_terms': toks,
        'query_applied': bool(results) and any(_relevance(r, toks) > 0 for r in results),
        'email_count': sum(1 for r in results if (r.get('email') or '').strip()),
    })


@app.route('/search/import', methods=['POST'])
def search_import():
    """供「运行本 skill 的 Agent」把实时搜索结果推入（零配置路径）。

    POST JSON：{"results": [{company, role, city, salary, ..., source}, ...]}
    结果会去重合并进 positions_live.json，随后 /search 即可直接检索到。
    """
    payload = request.get_json(silent=True) or {}
    results = payload.get('results') if isinstance(payload, dict) else payload
    if not isinstance(results, list) or not results:
        return jsonify({'error': '请提交 {"results": [...]} 数组'}), 400
    added, total = ENGINE.ingest(results)
    return jsonify({'added': added, 'total': total, 'live_file': 'positions_live.json'})


@app.route('/search/backends')
def search_backends():
    """返回后端可用性（不泄露任何 key 内容）。零配置后端恒为 true。"""
    return jsonify({
        'agent': ENGINE.backends['agent'].has_data(),
        'direct': True,
        'llm': bool(os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')),
        'search_api': bool(os.environ.get('SEARCH_API_KEY')),
        'zero_config_ok': True,
    })


if __name__ == '__main__':
    # 默认只绑本机回环，避免把含个人简历的服务暴露到局域网。
    # 确需局域网访问时显式设置 APP_HOST=0.0.0.0（自行评估风险）。
    host = os.environ.get('APP_HOST', '127.0.0.1')
    port = int(os.environ.get('APP_PORT', '5000'))
    app.run(host=host, port=port, debug=False)
