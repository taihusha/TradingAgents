"""Debug: last attempt at Xueqiu + 10jqka + web search."""
import sys, re, json
sys.path.insert(0, "E:/codex-workspace/projects/TradingAgents")
from tradingagents.dataflows.cn_social import _safe_http_fetch, _UA

bare = "603773"

# ── Xueqiu last try: different API endpoints ──
print("=" * 60)
print("雪球 — alternative API endpoints")
for api_url, desc in [
    (f"https://xueqiu.com/v4/stock/quote.json?code=SH{bare}", "quote API"),
    (f"https://xueqiu.com/stock/forchartk/stocklist.json?symbol=SH{bare}&period=1d", "chart API"),
    (f"https://stock.xueqiu.com/v5/stock/realtime/quote.json?symbol=SH{bare}", "realtime API"),
]:
    text, err = _safe_http_fetch(api_url, timeout=10,
        headers={"User-Agent": _UA, "Accept": "application/json",
                 "Origin": "https://xueqiu.com", "Referer": "https://xueqiu.com/"})
    print(f"  {desc}: len={len(text) if text else 0}, err={err}")
    if text:
        print(f"    {text[:200]}")

# ── Try Bing search for stock discussions ──
print()
print("=" * 60)
print("Bing search")
search_url = f"https://www.bing.com/search?q={bare}+%E8%82%A1%E5%90%A7+%E8%AE%A8%E8%AE%BA&setlang=zh-cn"
text, err = _safe_http_fetch(search_url, timeout=10,
    headers={"User-Agent": _UA, "Accept": "text/html"})
print(f"  len={len(text) if text else 0}, err={err}")
if text and len(text) > 100:
    # Extract search result snippets
    for m in re.finditer(r'<li class="b_algo"[^>]*>(.*?)</li>', text, re.DOTALL):
        h2 = re.search(r'<h2[^>]*>(.*?)</h2>', m.group(1), re.DOTALL)
        desc = re.search(r'<p[^>]*>(.*?)</p>', m.group(1), re.DOTALL)
        if h2:
            title = re.sub(r'<[^>]+>', '', h2.group(1)).strip()
            snippet = re.sub(r'<[^>]+>', '', desc.group(1)).strip() if desc else ''
            print(f"  [{title[:80]}] {snippet[:120]}")
            print()

# ── Try 10jqka with iwencai API ──
print("=" * 60)
print("同花顺 iwencai")
try:
    import requests
    headers = {
        "User-Agent": _UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.iwencai.com/",
    }
    resp = requests.post(
        "https://www.iwencai.com/customized/chart/get-robot-data",
        data={
            "question": f"{bare} 讨论 社区",
            "perpage": 5,
            "page": 1,
            "secondary_intent": "stock",
        },
        headers=headers,
        timeout=10,
    )
    print(f"  Status: {resp.status_code}")
    data = resp.json()
    print(f"  Keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
except Exception as e:
    print(f"  Error: {e}")
