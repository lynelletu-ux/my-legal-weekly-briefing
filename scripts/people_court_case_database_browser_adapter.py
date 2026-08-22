"""人民法院案例库 Playwright 登录态检索 adapter（登录后启用）。"""
import asyncio, json
from pathlib import Path

STATE = Path.home() / ".config" / "rmfyalk_state.json"
URL = "https://rmfyalk.court.gov.cn/"


async def search(query, practice_domain=""):
    if not STATE.exists():
        return {"query": query, "practice_domain": practice_domain, "execution_mode": "browser_login_required", "source_access_verified": False, "request_status": "not_logged_in", "result_urls": [], "error": "请先运行 case_database_login.py"}
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(STATE))
        page = await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        # 页面结构以实际登录后版本为准；保留明确的官方域名核验，不接受搜索引擎摘要。
        await page.locator('input[placeholder="请输入要检索的内容"]').first.fill(query)
        # 官方页面通过 target=_blank 打开结果列表，必须捕获新页面。
        async with context.expect_page() as page_info:
            await page.locator("button.general-search-submit").click()
        result_page = await page_info.value
        await result_page.wait_for_load_state("domcontentloaded", timeout=30000)
        await result_page.wait_for_timeout(1200)
        urls = await result_page.locator("a").evaluate_all(
            "els => els.map(a => ({href:a.href,text:(a.innerText||'').trim()}))"
            ".filter(x => x.href.includes('rmfyalk.court.gov.cn/view/content.html') && x.text)"
        )
        await browser.close()
    unique = {}
    for item in urls:
        unique[item["href"]] = item["text"]
    return {"query": query, "practice_domain": practice_domain, "source_target": "人民法院案例库", "execution_mode": "direct_source_browser", "fallback_level": "direct", "source_access_verified": bool(unique), "request_status": "ok", "result_urls": sorted(unique), "result_titles": unique, "result_count_raw": len(urls), "result_count_valid": len(unique), "error": None}


async def search_many(items):
    """复用同一登录态浏览器，逐条实际打开官方结果列表，避免重复启动 Chromium。"""
    if not STATE.exists():
        return [{"query": x["query"], "practice_domain": x.get("domain", ""), "execution_mode": "browser_login_required", "source_access_verified": False, "request_status": "not_logged_in", "result_urls": [], "error": "请先运行 case_database_login.py"} for x in items]
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(STATE))
        page = await context.new_page()
        for item in items:
            query, domain = item["query"], item.get("domain", "")
            try:
                await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                await page.locator('input[placeholder="请输入要检索的内容"]').first.fill(query)
                async with context.expect_page(timeout=15000) as page_info:
                    await page.locator("button.general-search-submit").click()
                result_page = await page_info.value
                await result_page.wait_for_load_state("domcontentloaded", timeout=30000)
                await result_page.wait_for_timeout(900)
                links = await result_page.locator("a").evaluate_all("els => els.map(a => ({href:a.href,text:(a.innerText||'').trim()})).filter(x => x.href.includes('rmfyalk.court.gov.cn/view/content.html') && x.text)")
                unique = {x["href"]: x["text"] for x in links}
                out.append({"query": query, "practice_domain": domain, "source_target": "人民法院案例库", "execution_mode": "direct_source_browser", "fallback_level": "direct", "source_access_verified": bool(unique), "request_status": "ok", "result_urls": sorted(unique), "result_titles": unique, "result_count_raw": len(links), "result_count_valid": len(unique), "error": None})
                await result_page.close()
            except Exception as exc:
                out.append({"query": query, "practice_domain": domain, "source_target": "人民法院案例库", "execution_mode": "direct_source_browser", "fallback_level": "direct", "source_access_verified": False, "request_status": "error", "result_urls": [], "result_titles": {}, "result_count_raw": 0, "result_count_valid": 0, "error": str(exc)})
        await browser.close()
    return out
