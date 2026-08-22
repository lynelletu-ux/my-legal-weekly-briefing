#!/usr/bin/env python3
"""人民法院案例库本机登录态初始化；不读取或记录用户名、密码。"""
import asyncio, json
from pathlib import Path

STATE = Path.home() / ".config" / "rmfyalk_state.json"
URL = "https://rmfyalk.court.gov.cn/"


async def main():
    from playwright.async_api import async_playwright
    STATE.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("人民法院案例库页面已打开。请在本机浏览器完成登录；不要把用户名或密码发送给 Codex。")
        input("登录完成后按回车保存本机登录态：")
        await context.storage_state(path=str(STATE))
        print(f"登录态已保存到 {STATE}（仅本机，已加入 .gitignore）")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
