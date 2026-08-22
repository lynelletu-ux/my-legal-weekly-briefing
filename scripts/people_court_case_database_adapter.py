"""人民法院案例库来源专用 adapter。

只把最终 URL 落在人民法院案例库官方域名且能解析到案例记录的结果标记为
source_access_verified；公告网、知识产权法庭或普通法院页面不会冒充案例库。
"""
import re
from urllib.parse import quote
from source_adapter_common import fetch, official_domain, audit_row

CASE_DB_DOMAINS = ("rmfyalk.court.gov.cn", "rmfyalk.court.gov.cn")
DIRECT_SEARCH = "https://rmfyalk.court.gov.cn/search?keyword={query}"
FALLBACK_SEARCH = "https://www.baidu.com/s?wd=site%3Armfyalk.court.gov.cn+{query}"


def run_query(item, timeout=12):
    query, domain = item["query"], item["domain"]
    raw = fetch(DIRECT_SEARCH.format(query=quote(query)), timeout)
    audit = audit_row(query, domain, "人民法院案例库", "direct_source", "direct", raw, False, [], raw.get("error"))
    if raw.get("status") == "ok" and official_domain(raw.get("url", ""), CASE_DB_DOMAINS):
        urls = re.findall(r"https?://rmfyalk\.court\.gov\.cn[^\"'\s<>]+", raw.get("body", b"").decode("utf-8", "ignore"))
        valid = bool(urls)
        audit = audit_row(query, domain, "人民法院案例库", "direct_source", "direct", raw, valid, urls)
        return audit, [{"url": u, "source": "人民法院案例库", "source_access_verified": True, "practice_domain": domain, "query": query} for u in urls]
    fallback = fetch(FALLBACK_SEARCH.format(query=quote(query)), timeout)
    audit = audit_row(query, domain, "人民法院案例库", "official_domain_search", "official_domain_search", fallback, False, [], fallback.get("error") or "未发现案例库官方结果")
    return audit, []


def run_queries(plan, timeout=12):
    audits, results = [], []
    for item in plan:
        audit, rows = run_query(item, timeout=timeout)
        audits.append(audit); results.extend(rows)
    return {"query_count": len(plan), "direct_query_count": sum(a["fallback_level"] == "direct" for a in audits), "fallback_query_count": sum(a["fallback_level"] != "direct" for a in audits), "result_count_raw": sum(a["result_count_raw"] for a in audits), "result_count_valid": sum(a["result_count_valid"] for a in audits), "source_access_verified_count": sum(a["source_access_verified"] for a in audits), "errors": [a for a in audits if a.get("error")], "audit": audits, "results": results}
