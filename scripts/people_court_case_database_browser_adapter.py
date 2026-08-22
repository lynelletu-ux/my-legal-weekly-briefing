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
        await page.get_by_role("textbox").first.fill(query)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1500)
        urls = await page.locator("a").evaluate_all("els => els.map(a => a.href).filter(u => u.includes('rmfyalk.court.gov.cn'))")
        await browser.close()
    return {"query": query, "practice_domain": practice_domain, "execution_mode": "direct_source_browser", "source_access_verified": bool(urls), "request_status": "ok", "result_urls": sorted(set(urls)), "error": None}
