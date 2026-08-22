#!/usr/bin/env python3
"""
微信读书搜一搜 公众号文章发现器（mp 直链版）

原理：微信读书网页版「搜一搜」可搜索公众号文章，返回 mp.weixin.qq.com 原文直链。
这是 2026-07-29 微信关闭 MP 跨号接口后唯一稳定的公众号文章发现通道。

前置：已通过微信读书扫码登录，登录态保存在 /tmp/weread_state.json（或 ~/.config/weread_state.json）

用法：
  python3 fetch_weread_week.py                    # 全部公众号
  python3 fetch_weread_week.py --account 山东高法  # 指定公众号
  python3 fetch_weread_week.py --days 7           # 最近N天（默认7）
"""

import json
import sys
import re
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

BASE = Path(__file__).resolve().parent
OUT = BASE / "mp_articles_weread.json"
STATE_PATHS = [
    Path.home() / ".config" / "weread_state.json",  # 主登录态（weread_login.py 写入）
    Path("/tmp/weread_state.json"),                 # 历史遗留 fallback
]

ACCOUNTS = ["山东高法", "上海一中法院", "上海二中院", "中国应用法学"]

SEARCH_URL = "https://search.weixin.qq.com/cgi-bin/newsearchweb/userclientjump?path=page/search/weread&query={kw}&platform=pc"
EVALUATE_TIMEOUT = 45  # 秒：页面结构变化时不能无限卡住整批来源
ACCOUNT_TIMEOUT = 120  # 秒：单个来源卡住时继续后续来源，并显式报告


def parse_date(date_str: str) -> datetime:
    """解析微信读书的相对/绝对时间"""
    now = datetime.now(timezone(timedelta(hours=8)))
    if not date_str:
        return None
    m = re.search(r'(\d+)\s*分钟前', date_str)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r'(\d+)\s*小时前', date_str)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r'(\d+)\s*天前', date_str)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r'(\d+)\s*个月前', date_str)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))
    m = re.search(r'(\d+)\s*年前', date_str)
    if m:
        return now - timedelta(days=365 * int(m.group(1)))
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            return None
    return None


def load_state() -> list:
    """加载登录态 cookie，必须含有效 wr_vid（微信读书登录凭证），否则视为未登录"""
    for p in STATE_PATHS:
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            cookies = data.get("cookies", []) if isinstance(data, dict) else []
            if any(c.get("name") == "wr_vid" and c.get("value") for c in cookies):
                return cookies
    return []


async def search_account(context, account: str, days: int, scroll_rounds: int = 6) -> list:
    """搜索一个公众号的文章"""
    page = await context.new_page()

    kw = quote(account)
    await page.goto(SEARCH_URL.format(kw=kw), wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(8)

    # 滚动加载（最多 6 轮）
    prev = 0
    for r in range(scroll_rounds):
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        await asyncio.sleep(3)
        c = await page.evaluate("document.querySelectorAll('.search_list_item').length")
        if c == prev and r > 2:
            break
        prev = c
        if c > 500:
            break

    # 提取元数据
    arts_json = await asyncio.wait_for(page.evaluate("""(() => {
        const arts = [];
        document.querySelectorAll('.search_list_item').forEach((item, i) => {
            const t = item.querySelector('.article__title-text');
            const d = item.querySelector('.article__desc');
            const s = item.querySelector('.source__title');
            const dt = item.querySelector('.source__text.date');
            arts.push({
                idx: i,
                title: (t?.textContent || '').trim(),
                desc: (d?.textContent || '').trim().substring(0, 300),
                source: (s?.textContent || '').trim(),
                date: (dt?.textContent || '').trim(),
            });
        });
        return JSON.stringify(arts);
    })()"""), timeout=EVALUATE_TIMEOUT)
    arts = json.loads(arts_json)

    # 先按精确公众号名和时间窗筛掉无关结果，再打开原文链接。
    # 搜索页常混入转载/百科/旧文；逐条打开会将完整来源池抓取放大数十倍。
    cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)
    eligible = []
    for article in arts:
        pub = parse_date(article["date"])
        if article["source"] == account and pub is not None and pub >= cutoff:
            eligible.append(article["idx"])

    # 提取 mp 直链。2026-08 页面点击会创建新标签页，不再调用 window.open；
    # 因此逐项监听 context 的 page 事件并读取新页 URL。 
    url_map = {}
    items = page.locator(".search_list_item")
    for idx in eligible:
        popup_task = asyncio.create_task(context.wait_for_event("page", timeout=5000))
        try:
            await items.nth(idx).scroll_into_view_if_needed(timeout=5000)
            await items.nth(idx).click(timeout=5000)
            popup = await popup_task
            # 新标签创建时 URL 已经是公众号原文地址。无需等待微信正文加载：
            # 这既避免慢资源拖慢整个来源池，也只采集用户要求的原始链接。
            url_map[idx] = popup.url
            await popup.close()
        except Exception:
            popup_task.cancel()
            url_map[idx] = ""

    # URL 提取诊断：有元数据但直链全空 = 页面结构变化
    if eligible and not any(url_map.values()):
        print(f"  [WARN] {account}: 提取到 {len(arts)} 条元数据但 mp 直链全为空，疑似页面结构变化", file=sys.stderr)

    # 合并 + 过滤
    results = []
    seen = set()
    for a in arts:
        url = url_map.get(a["idx"], "")
        if not url or "mp.weixin.qq.com" not in url:
            continue
        # 严格公众号名匹配（搜索结果可能混入其他号）
        if a["source"] != account:
            continue
        if url in seen:
            continue
        seen.add(url)
        pub = parse_date(a["date"])
        if pub is None:
            continue  # 无法确定时间的文章跳过
        if pub < cutoff:
            continue
        results.append({
            "title": a["title"],
            "url": url,
            "publish_time": pub.strftime("%Y-%m-%d %H:%M"),
            "digest": a["desc"],
            "_source": account,
        })

    # 个别搜索页在关闭时会等待未完成的资源请求；关闭失败不能阻塞后续来源。
    try:
        await asyncio.wait_for(page.close(), timeout=5)
    except Exception:
        pass
    return results


async def main_async(accounts, days, scroll_rounds=6, output_path=OUT):
    from playwright.async_api import async_playwright

    cookies = load_state()
    if not cookies:
        print("❌ 未找到有效微信读书登录态（缺 wr_vid）。请重新扫码：python3 scripts/weread_login.py", file=sys.stderr)
        sys.exit(1)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:
            print(f"❌ 浏览器启动失败: {e}", file=sys.stderr)
            print("请先安装 playwright 浏览器：python3 -m playwright install chromium", file=sys.stderr)
            sys.exit(1)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        await context.add_cookies(cookies)

        # weread 备份标签页保持 session + 登录态过期检测
        keep = await context.new_page()
        await keep.goto("https://weread.qq.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        live = await context.cookies("https://weread.qq.com")
        if not any(c.get("name") == "wr_vid" and c.get("value") for c in live):
            print("❌ 微信读书登录态已过期。请重新扫码：python3 scripts/weread_login.py", file=sys.stderr)
            sys.exit(1)

        all_articles = []
        empty_accounts = []
        for account in accounts:
            print(f"搜索: {account} ...", flush=True)
            try:
                arts = await asyncio.wait_for(search_account(context, account, days, scroll_rounds), timeout=ACCOUNT_TIMEOUT)
                print(f"  -> {len(arts)} 篇", flush=True)
                if not arts:
                    empty_accounts.append(account)
                all_articles.extend(arts)
            except Exception as e:
                print(f"  [ERR] {account}: {e}", flush=True)
                empty_accounts.append(account)

        await browser.close()

    all_articles.sort(key=lambda x: x.get("publish_time", ""), reverse=True)
    output_path.write_text(json.dumps(all_articles, ensure_ascii=False, indent=2))
    print(f"\n总计 {len(all_articles)} 篇，已写入 {output_path}", flush=True)
    for a in all_articles[:10]:
        print(f"  [{a['_source']}] {a['title'][:45]} | {a['publish_time']}")
    if empty_accounts:
        print(f"⚠️ 无结果账号: {', '.join(empty_accounts)}")


def main():
    parser = argparse.ArgumentParser(description="微信读书 公众号文章发现")
    parser.add_argument("--account", type=str, help="只搜索指定公众号")
    parser.add_argument("--accounts", type=str, help="仅搜索逗号分隔的精确公众号名称")
    parser.add_argument("--days", type=int, default=7, help="最近N天")
    parser.add_argument("--quick", action="store_true", help="验收用：跳过额外滚动，仅验证首屏真实搜索结果")
    parser.add_argument("--output", type=Path, default=OUT, help="原始 JSON 产物路径（默认 scripts/mp_articles_weread.json）")
    args = parser.parse_args()

    # 个人版优先读取被 Git 忽略的 profile.local.yaml；缺失时保持原项目四号默认值。
    from profile_config import court_accounts
    accounts = court_accounts(ACCOUNTS)
    if args.account:
        accounts = [a for a in accounts if args.account.lower() in a.lower()]
        if not accounts:
            print(f"❌ 未找到已配置公众号: {args.account}", file=sys.stderr)
            sys.exit(1)
    if args.accounts:
        requested = {x.strip() for x in args.accounts.split(",") if x.strip()}
        unknown = requested - set(accounts)
        if unknown:
            print(f"❌ 未找到已配置公众号: {', '.join(sorted(unknown))}", file=sys.stderr)
            sys.exit(1)
        accounts = [a for a in accounts if a in requested]

    print(f"微信读书搜一搜: {len(accounts)} 个公众号, 最近 {args.days} 天")
    print()
    asyncio.run(main_async(accounts, args.days, 0 if args.quick else 6, args.output))


if __name__ == "__main__":
    main()
