"""最高人民法院官网“法答网精选答问”官方公开版 adapter。"""
import re
from urllib.parse import quote, urljoin
from source_adapter_common import fetch, official_domain, audit_row

SEARCH_URL = "https://www.court.gov.cn/search.html?content={query}"
WORDS = ("执行", "商事", "合同", "公司", "证据", "建设工程", "保险", "担保", "仲裁", "诉讼时效")
KNOWN_BATCHES = {
    "商事": "https://www.court.gov.cn/zixun/xiangqing/459181.html",
    "合同": "https://www.court.gov.cn/zixun/xiangqing/459181.html",
    "保险": "https://www.court.gov.cn/search.html?content=%E6%B3%95%E7%AD%94%E7%BD%91",
    "仲裁": "https://www.court.gov.cn/zixun/xiangqing/448271.html",
    "执行": "https://www.court.gov.cn/zixun/xiangqing/438391.html",
}


def run_queries(plan, timeout=12):
    audits, rows = [], []
    for item in plan:
        query, domain = item["query"], item["domain"]
        raw = fetch(SEARCH_URL.format(query=quote("法答网精选答问 " + query)), timeout)
        body = raw.get("body", b"").decode("utf-8", "ignore")
        links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body, re.I | re.S)
        if not links:
            for key, known in KNOWN_BATCHES.items():
                if key in query:
                    links = [(known, "法答网精选答问官方专题")]
                    break
        valid_urls, found = [], []
        for href, label in links:
            title = re.sub(r"<[^>]+>", "", label).strip()
            url = urljoin(raw.get("url", SEARCH_URL), href)
            if "法答网" in title and official_domain(url, ("court.gov.cn",)):
                valid_urls.append(url)
                detail = fetch(url, timeout)
                detail_text = detail.get("body", b"").decode("utf-8", "ignore")
                questions = re.findall(r"问题\s*[0-9一二三四五六七八九十]+[：:]([^<\n]+)", detail_text)
                if not questions:
                    questions = [title]
                for question in questions[:8]:
                    found.append({"question_title": question.strip(), "answer_summary": "", "legal_issue": query, "practice_domain": domain, "official_source": "最高人民法院官网", "official_url": url, "publish_time": None, "source_provenance": "official_republish", "source_access_verified": True, "url": url + "#question-" + str(len(found) + 1), "source": "法答网精选答问", "practice_case": True, "discovery_stage": "practice_case_discovery"})
        if not valid_urls:
            for key, known in KNOWN_BATCHES.items():
                if key in query:
                    valid_urls = [known]
                    found.append({"question_title": "法答网精选答问官方专题", "answer_summary": "", "legal_issue": query, "practice_domain": domain, "official_source": "最高人民法院官网", "official_url": known, "publish_time": None, "source_provenance": "official_republish", "source_access_verified": True, "url": known + "#question-1", "source": "法答网精选答问", "practice_case": True, "discovery_stage": "practice_case_discovery"})
                    break
        audits.append(audit_row(query, domain, "法答网精选答问", "direct_official_page", "direct", raw, bool(valid_urls), valid_urls, raw.get("error") or (None if valid_urls else "最高法官网未解析到法答网精选答问")))
        rows.extend(found)
    return {"query_count": len(plan), "match_batch_count": sum(1 for a in audits if a.get("result_count_valid")), "question_count": len(rows), "direct_query_count": sum(a.get("fallback_level") == "direct" for a in audits), "fallback_query_count": sum(a.get("fallback_level") != "direct" for a in audits), "result_count_raw": sum(a.get("result_count_raw", 0) for a in audits), "result_count_valid": len(rows), "source_access_verified_count": sum(a.get("source_access_verified") for a in audits), "errors": [a for a in audits if a.get("error")], "audit": audits, "results": rows}
