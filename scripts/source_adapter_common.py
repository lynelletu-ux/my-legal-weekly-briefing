"""来源专用 adapter 的最小公共层：访问、域名核验、逐条审计。"""
import json
import time
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


def fetch(url, timeout=12):
    started = time.monotonic()
    try:
        req = Request(url, headers={"User-Agent": "legal-weekly-briefing/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {"status": "ok", "status_code": getattr(resp, "status", 200), "url": resp.geturl(), "body": body, "elapsed": round(time.monotonic() - started, 3), "error": None}
    except Exception as exc:
        return {"status": "error", "status_code": None, "url": url, "body": b"", "elapsed": round(time.monotonic() - started, 3), "error": str(exc)}


def official_domain(url, domains):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def audit_row(query, domain, target, mode, fallback_level, result, valid, urls, error=None):
    final_url = result.get("url", "") if result else ""
    return {
        "query": query, "practice_domain": domain, "source_target": target,
        "execution_mode": mode, "fallback_level": fallback_level,
        "source_access_verified": bool(valid and final_url),
        "final_domain": urlparse(final_url).hostname or "",
        "request_status": result.get("status") if result else "error",
        "result_count_raw": len(urls), "result_count_valid": len(urls) if valid else 0,
        "result_urls": urls if valid else [], "error": error or (result or {}).get("error"),
        "elapsed": (result or {}).get("elapsed"),
    }
