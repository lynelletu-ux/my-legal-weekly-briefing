"""深圳法院来源专用 adapter，保留具体法院名称，不使用泛称。"""
from urllib.parse import quote
from source_adapter_common import fetch, official_domain, audit_row

COURTS = ["深圳市中级人民法院", "深圳市福田区人民法院", "深圳市罗湖区人民法院", "深圳市南山区人民法院", "深圳市盐田区人民法院", "深圳市宝安区人民法院", "深圳市龙岗区人民法院", "深圳市龙华区人民法院", "深圳市坪山区人民法院", "深圳市光明区人民法院", "深圳前海合作区人民法院"]
OFFICIAL_DOMAIN = "court.gov.cn"


def run_query(item, court, timeout=12):
    query, domain = item["query"], item["domain"]
    host = "www.szcourt.gov.cn" if court == "深圳市中级人民法院" else "www.szcourt.gov.cn"
    direct = fetch("https://" + host + "/search?keyword=" + quote(court + " " + query), timeout)
    verified = direct.get("status") == "ok" and official_domain(direct.get("url", ""), (OFFICIAL_DOMAIN,))
    audit = audit_row(query, domain, court, "direct_source", "direct", direct, verified, [], direct.get("error") or (None if verified else "未解析到具体法院官方结果"))
    if verified:
        return audit, []
    fallback = fetch("https://www.baidu.com/s?wd=" + quote("site:court.gov.cn " + court + " " + query), timeout)
    audit = audit_row(query, domain, court, "official_domain_search", "official_domain_search", fallback, False, [], fallback.get("error") or "未验证具体法院结果")
    return audit, []


def run_queries(plan, timeout=12):
    audits, results = [], []
    for i, item in enumerate(plan):
        court = COURTS[i % len(COURTS)]
        audit, rows = run_query(item, court, timeout=timeout); audits.append(audit); results.extend(rows)
    return {"query_count": len(plan), "direct_query_count": sum(a.get("fallback_level") == "direct" for a in audits), "fallback_query_count": sum(a.get("fallback_level") != "direct" for a in audits), "hit_courts": sorted({a["source_target"] for a in audits if a["source_access_verified"]}), "result_count_raw": sum(a["result_count_raw"] for a in audits), "result_count_valid": sum(a["result_count_valid"] for a in audits), "source_access_verified_count": sum(a["source_access_verified"] for a in audits), "errors": [a for a in audits if a.get("error")], "audit": audits, "results": results}
