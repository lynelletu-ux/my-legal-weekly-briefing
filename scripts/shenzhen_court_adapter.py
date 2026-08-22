"""深圳法院固定栏目 adapter，优先直接抓取 szcourt.gov.cn 官方页面。"""
import re
from urllib.parse import urljoin
from source_adapter_common import fetch, official_domain, audit_row

COURTS = ["深圳市中级人民法院", "深圳市福田区人民法院", "深圳市罗湖区人民法院", "深圳市南山区人民法院", "深圳市盐田区人民法院", "深圳市宝安区人民法院", "深圳市龙岗区人民法院", "深圳市龙华区人民法院", "深圳市坪山区人民法院", "深圳市光明区人民法院", "深圳前海合作区人民法院"]
OFFICIAL_DOMAIN = "szcourt.gov.cn"
FIXED_COLUMNS = [
    "https://www.szcourt.gov.cn/dxal/index.html",
    "https://www.szcourt.gov.cn/",
]
PRACTICE_WORDS = ("合同", "公司", "股东", "股权", "执行异议", "被执行人", "保全", "建设工程", "证据", "侵权", "保险", "担保", "法官说法", "案件通报")


def run_query(item, court, timeout=12):
    query, domain = item["query"], item["domain"]
    return discover_fixed_columns(query, domain, court, timeout)


def discover_fixed_columns(query="", domain="", court="深圳市中级人民法院", timeout=12):
    audits, rows = [], []
    for page_url in FIXED_COLUMNS:
        direct = fetch(page_url, timeout)
        verified = direct.get("status") == "ok" and official_domain(direct.get("url", ""), (OFFICIAL_DOMAIN,))
        body = direct.get("body", b"").decode("utf-8", "ignore")
        links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body, re.I | re.S)
        urls = []
        for href, label in links:
            title = re.sub(r"<[^>]+>", "", label).strip()
            url = urljoin(direct.get("url", page_url), href)
            if verified and title and any(k in title or k in query for k in PRACTICE_WORDS) and official_domain(url, (OFFICIAL_DOMAIN,)):
                urls.append(url)
                rows.append({"title": title, "url": url, "publish_time": None, "court": court, "source": court, "case_type": "典型案例", "practice_domain": domain or "其他法律实务", "abstract": title, "case_database_flag": False, "source_access_verified": True, "source_channel": "practice_case_discovery", "practice_case": True, "discovery_stage": "practice_case_discovery"})
        audits.append(audit_row(query, domain, court, "direct_official_page", "direct", direct, bool(urls), urls, direct.get("error") or (None if urls else "栏目页可访问但未解析到匹配案例")))
    return audits[0] if len(audits) == 1 else {"query": query, "practice_domain": domain, "source_target": court, "execution_mode": "direct_official_page", "fallback_level": "direct", "source_access_verified": any(a["source_access_verified"] for a in audits), "request_status": "ok" if any(a["request_status"] == "ok" for a in audits) else "error", "result_count_raw": sum(a["result_count_raw"] for a in audits), "result_count_valid": sum(a["result_count_valid"] for a in audits), "result_urls": [u for a in audits for u in a["result_urls"]], "error": None if rows else "; ".join(a.get("error", "") for a in audits), "elapsed": sum(a.get("elapsed") or 0 for a in audits)}, rows


def run_queries(plan, timeout=12):
    audits, results = [], []
    for i, item in enumerate(plan):
        court = COURTS[i % len(COURTS)]
        audit, rows = run_query(item, court, timeout=timeout)
        if isinstance(audit, dict) and audit.get("audit"):
            audits.extend(audit["audit"])
        else:
            audits.append(audit)
        results.extend(rows)
    unique = {r.get("url"): r for r in results if r.get("url")}
    return {"query_count": len(plan), "direct_query_count": sum(a.get("fallback_level") == "direct" for a in audits), "fallback_query_count": sum(a.get("fallback_level") != "direct" for a in audits), "hit_courts": sorted({a["source_target"] for a in audits if a["source_access_verified"]}), "result_count_raw": sum(a["result_count_raw"] for a in audits), "result_count_valid": len(unique), "source_access_verified_count": sum(a["source_access_verified"] for a in audits), "errors": [a for a in audits if a.get("error")], "audit": audits, "results": list(unique.values())}
