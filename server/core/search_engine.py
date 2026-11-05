"""Multi-source, captcha-free web search engine for AI Agents (powered by WARP + multi-engine aggregation).

Aggregates genuine results across major search indexers without datacenter IP poisoning:
1. DuckDuckGo HTML / Lite via local Cloudflare WARP tunnel (`http://127.0.0.1:40001`)
2. Yahoo Search (`search.yahoo.com`, backed by Microsoft Bing index without datacenter poisoning)
3. Yahoo Japan Search (`search.yahoo.co.jp`, backed by Google index without reCAPTCHA)
4. Sogou Web (`sogou.com/web`, fast native Chinese index)
5. HackerNews Algolia JSON API & Wikipedia REST Search API
"""
from __future__ import annotations
import asyncio, re, socket
from urllib.parse import quote_plus, unquote
import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


def _warp_proxy_url() -> str | None:
    try:
        with socket.create_connection(('127.0.0.1', 40001), timeout=0.25):
            return 'http://127.0.0.1:40001'
    except OSError:
        return None


async def _search_sogou(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f'https://www.sogou.com/web?query={quote_plus(query)}'
    try:
        r = await client.get(
            url,
            headers={
                'User-Agent': USER_AGENT,
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://www.sogou.com/',
            },
            timeout=8.0,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for vr in soup.select('.vrwrap, .rb')[:10]:
            h3 = vr.select_one('h3.vr-title, h3.pt, h3')
            if not h3:
                continue
            a = h3.select_one('a') or vr.select_one('a')
            if not a:
                continue
            title = h3.get_text(' ', strip=True)
            href = a.get('href', '')
            if href.startswith('/link?url='):
                href = 'https://www.sogou.com' + href
            if not href.startswith('http') or len(title) < 2:
                continue
            snip_el = vr.select_one('.star-wiki, .str-text-info, .ft, .attribute-centent, p')
            snippet = snip_el.get_text(' ', strip=True) if snip_el else vr.get_text(' ', strip=True)
            if snippet.startswith(title):
                snippet = snippet[len(title):].strip()
            results.append({
                'title': title[:200],
                'url': href,
                'snippet': snippet[:300],
                'engine': 'sogou',
            })
        return results
    except Exception:
        return []


async def _search_yahoo_bing(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Yahoo Search uses the live Microsoft Bing index without datacenter IP search poisoning."""
    url = f'https://search.yahoo.com/search?p={quote_plus(query)}&ei=UTF-8'
    try:
        r = await client.get(
            url,
            headers={
                'User-Agent': USER_AGENT,
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            },
            timeout=9.0,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for algo in soup.select('div.algo, div.dd.algo')[:10]:
            h3 = algo.select_one('h3.title, h3')
            a = algo.select_one('a')
            if not h3 or not a:
                continue
            title = h3.get_text(' ', strip=True)
            # Strip trailing URL breadcrumb that Yahoo appends inside <h3>
            title = re.sub(r'^.*?\bhttps?://[^\s]+\s*', '', title).strip() or h3.get_text(' ', strip=True)
            href = a.get('href', '')
            m_ru = re.search(r'/RU=([^/]+)/RK=', href)
            if m_ru:
                href = unquote(m_ru.group(1))
            if not href.startswith('http') or 'yahoo.com' in href:
                continue
            snip_el = algo.select_one('.compText, p')
            snippet = snip_el.get_text(' ', strip=True) if snip_el else ''
            results.append({
                'title': title[:200],
                'url': href,
                'snippet': snippet[:300],
                'engine': 'bing_via_yahoo',
            })
        return results
    except Exception:
        return []


async def _search_ddg_html(client: httpx.AsyncClient, query: str, region: str = 'wt-wt') -> list[dict]:
    url = f'https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl={region}'
    try:
        r = await client.get(
            url,
            headers={
                'User-Agent': USER_AGENT,
                'Referer': 'https://html.duckduckgo.com/',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            },
            timeout=9.0,
        )
        if r.status_code != 200 or 'anomaly-modal' in r.text:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for block in soup.select('.result')[:10]:
            a = block.select_one('.result__a')
            if not a:
                continue
            title = a.get_text(' ', strip=True)
            href = a.get('href', '')
            if 'uddg=' in href:
                m = re.search(r'uddg=([^&]+)', href)
                if m:
                    href = unquote(m.group(1))
            if not href.startswith('http'):
                continue
            snip_el = block.select_one('.result__snippet')
            snippet = snip_el.get_text(' ', strip=True) if snip_el else ''
            results.append({
                'title': title[:200],
                'url': href,
                'snippet': snippet[:300],
                'engine': 'duckduckgo',
            })
        return results
    except Exception:
        return []


async def _search_ddg_lite(client: httpx.AsyncClient, query: str, region: str = 'wt-wt') -> list[dict]:
    url = f'https://lite.duckduckgo.com/lite/?q={quote_plus(query)}&kl={region}'
    try:
        r = await client.get(
            url,
            headers={
                'User-Agent': USER_AGENT,
                'Referer': 'https://lite.duckduckgo.com/',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            },
            timeout=9.0,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        links = soup.select('a.result-link')
        snippets = soup.select('td.result-snippet')
        for idx, a in enumerate(links[:8]):
            title = a.get_text(' ', strip=True)
            href = a.get('href', '')
            if 'uddg=' in href:
                m = re.search(r'uddg=([^&]+)', href)
                if m:
                    href = unquote(m.group(1))
            if not href.startswith('http'):
                continue
            snip = snippets[idx].get_text(' ', strip=True) if idx < len(snippets) else ''
            if title:
                results.append({
                    'title': title[:200],
                    'url': href,
                    'snippet': snip[:300],
                    'engine': 'duckduckgo_lite',
                })
        return results
    except Exception:
        return []


async def _search_hn_algolia(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f'https://hn.algolia.com/api/v1/search?query={quote_plus(query)}&hitsPerPage=5'
    try:
        r = await client.get(url, timeout=7.0)
        if r.status_code != 200:
            return []
        data = r.json()
        results = []
        for hit in (data.get('hits') or [])[:5]:
            title = (hit.get('title') or hit.get('story_title') or '').strip()
            link = (hit.get('url') or hit.get('story_url') or f"https://news.ycombinator.com/item?id={hit.get('objectID','')}").strip()
            snippet = (hit.get('comment_text') or hit.get('story_text') or '').strip()
            if snippet:
                snippet = BeautifulSoup(snippet, 'html.parser').get_text(' ', strip=True)[:240]
            points = hit.get('points') or 0
            created = (hit.get('created_at') or '')[:10]
            if title and link:
                results.append({
                    'title': title,
                    'url': link,
                    'snippet': f'[HN {created} · {points} pts] {snippet}'.strip(),
                    'engine': 'hn_algolia',
                })
        return results
    except Exception:
        return []


async def _search_wikipedia(client: httpx.AsyncClient, query: str) -> list[dict]:
    has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in query)
    lang = 'zh' if has_cjk else 'en'
    url = f'https://{lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote_plus(query)}&utf8=1&format=json&srlimit=4'
    try:
        r = await client.get(url, headers={'User-Agent': 'PulseAgent/2.8 (+https://github.com/uiwcvh-art/proactive-personal-agent)'}, timeout=7.0)
        if r.status_code != 200:
            return []
        items = ((r.json().get('query') or {}).get('search')) or []
        results = []
        for it in items[:4]:
            title = it.get('title', '')
            snip = BeautifulSoup(it.get('snippet', ''), 'html.parser').get_text(' ', strip=True)
            if title:
                results.append({
                    'title': f'{title} - Wikipedia',
                    'url': f'https://{lang}.wikipedia.org/wiki/{quote_plus(title.replace(" ", "_"))}',
                    'snippet': snip[:280],
                    'engine': f'wikipedia_{lang}',
                })
        return results
    except Exception:
        return []


async def multi_web_search(query: str, max_results: int = 14) -> dict:
    q = (query or '').strip()
    if not q:
        return {'query': '', 'results': [], 'text': '查询词为空'}

    has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in q)
    warp_url = _warp_proxy_url()

    async with httpx.AsyncClient(proxy=warp_url, follow_redirects=True, trust_env=False) as warp_client, \
               httpx.AsyncClient(follow_redirects=True, trust_env=False) as direct_client:
        ddg_res, ddg_lite_res, yahoo_bing_res, sogou_res, hn_res, wiki_res = await asyncio.gather(
            _search_ddg_html(warp_client, q, 'wt-wt'),
            _search_ddg_lite(warp_client, q, 'wt-wt'),
            _search_yahoo_bing(warp_client, q),
            _search_sogou(direct_client, q),
            _search_hn_algolia(direct_client, q),
            _search_wikipedia(warp_client, q),
        )

    merged: list[dict] = []
    seen_urls: set[str] = set()
    ordered_lists = (
        [ddg_res, yahoo_bing_res, sogou_res, ddg_lite_res, wiki_res, hn_res]
        if has_cjk
        else [ddg_res, yahoo_bing_res, ddg_lite_res, hn_res, sogou_res, wiki_res]
    )

    for lst in ordered_lists:
        for item in lst:
            u = item['url']
            if u in seen_urls:
                continue
            seen_urls.add(u)
            merged.append(item)

    top_items = merged[:max_results]
    text_lines = []
    for idx, item in enumerate(top_items, 1):
        text_lines.append(f"{idx}. [{item['title']}]({item['url']}) ({item['engine']})\n   {item['snippet']}")

    return {
        'query': q,
        'count': len(top_items),
        'results': top_items,
        'links': [{'title': x['title'], 'url': x['url']} for x in top_items],
        'text': '\n\n'.join(text_lines) if text_lines else '未找到直接匹配的网页结果，请尝试更换更简短的关键词。',
    }
