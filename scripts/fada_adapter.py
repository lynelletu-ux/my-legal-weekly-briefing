"""法答网来源链 adapter；普通文章关键词命中不计入。"""
import re
from urllib.parse import quote
from source_adapter_common import fetch, official_domain, audit_row

OFFICIAL_DOMAINS = ("court.gov.cn", "rmfyalk.court.gov.cn")
DIRECT_SEARCH = "https://www.court.gov.cn/search.html?keyword={query}"
FALLBACK_SEARCH = "https://www.baidu.com/s?wd=site%3Acourt.gov.cn+法答网+{query}"


def run_query(item, timeout=12):
    query, domain = item["query"], item["domain"]
    raw = fetch(DIRECT_SEARCH.format(query=quote("法答网 " + query)), timeout)
    urls = re.findall(r"https?://(?:www\.)?court\.gov\.cn[^\"'\s<>]+", raw.get("body", b"").decode("utf-8", "ignore")) if raw.get("status") == "ok" else []
    urls = [u for u in urls if "法答网" in raw.get("body", b"").decode("utf-8", "ignore")]
    valid = bool(urls) and official_domain(raw.get("url", ""), OFFICIAL_DOMAINS)
    audit = audit_row(query, domain, "法答网精选答问", "direct_source", "direct", raw, valid, urls, None if valid else (raw.get("error") or "未解析到可验证法答网官方内容"))
    if valid:
        return audit, [{"question_title": query, "answer_summary": "", "practice_domain": domain, "official_source": "法答网", "official_url": u, "publish_time": None, "source_provenance": "direct", "source_access_verified": True, "url": u, "source": "法答网精选答问", "practice_case": True, "discovery_stage": "practice_case_discovery"} for u in urls]
    fallback = fetch(FALLBACK_SEARCH.format(query=quote(query)), timeout)
    audit = audit_row(query, domain, "法答网精选答问", "web_fallback", "web_fallback", fallback, False, [], fallback.get("error") or "fallback未发现可验证法答网内容")
    return audit, []


def run_queries(plan, timeout=12):
    audits, results = [], []
    for item in plan:
        audit, rows = run_query(item, timeout=timeout); audits.append(audit); results.extend(rows)
    return {"query_count": len(plan), "direct_query_count": sum(a["fallback_level"] == "direct" for a in audits), "fallback_query_count": sum(a["fallback_level"] != "direct" for a in audits), "result_count_raw": sum(a["result_count_raw"] for a in audits), "result_count_valid": sum(a["result_count_valid"] for a in audits), "source_access_verified_count": sum(a["source_access_verified"] for a in audits), "errors": [a for a in audits if a.get("error")], "audit": audits, "results": results}
