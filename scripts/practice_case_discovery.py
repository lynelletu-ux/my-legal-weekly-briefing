"""重点实务司法案例专项发现池。

本模块不绑定某一个搜索供应商：正式入口可把人民法院案例库、法答网、法院
公开检索结果适配成统一候选后传入 ``normalize_case_feed``。这样保留现有
微信读书/Web/AI 通道，同时让案例发现拥有独立查询计划、时间回溯和统计。
"""
from datetime import date, datetime, timedelta

PRACTICE_DOMAINS = {
    "合同与债权债务": ("合同效力", "合同解除", "违约责任", "损失认定", "服务合同", "委托合同", "买卖合同", "民间借贷", "担保保证"),
    "公司商事与股权": ("股东出资责任", "追加股东", "公司人格否认", "股权转让", "公司决议", "股东知情权", "法定代表人责任", "公司担保", "对赌协议"),
    "强制执行与财产保全": ("追加变更被执行人", "执行异议", "执行异议之诉", "股权冻结", "财产线索", "到期债权执行", "保全错误", "执行和解", "夫妻共同财产执行"),
    "民事诉讼程序与证据": ("举证责任", "证明标准", "微信聊天记录", "电子证据", "自认", "鉴定", "诉讼请求", "管辖", "法官释明", "二审审查范围"),
    "建设工程": ("实际施工人", "违法分包", "转包", "工程价款", "工程结算", "优先受偿权", "工程质量责任", "施工损害"),
    "侵权与保险": ("责任比例", "安全保障义务", "雇主责任", "交通事故", "保险免责", "保险赔偿", "侵权责任交叉"),
    "担保与追偿": ("保证", "抵押", "共同债务", "追偿权", "担保责任"),
}

SOURCE_PRIORITY = (
    "人民法院案例库", "法答网精选答问", "最高人民法院", "广东省高级人民法院",
    "深圳市中级人民法院", "深圳基层法院", "广东其他法院", "其他高院中院",
    "中国应用法学", "人民法院报",
)


def query_plan():
    """返回可审计的专项查询计划，供 WebSearch/站内搜索适配器执行。"""
    return [{"domain": d, "query": q, "sources": list(SOURCE_PRIORITY)}
            for d, queries in PRACTICE_DOMAINS.items() for q in queries]


def build_query_audit(execution_mode="adapter_feed", status="not_verified_by_pipeline"):
    """为57条专项查询生成逐条审计骨架；适配器应覆盖 result/error 字段。"""
    return [{"query": row["query"], "practice_domain": row["domain"],
             "target_source": row["sources"][0], "execution_mode": execution_mode,
             "request_status": status, "result_count_raw": 0,
             "result_count_valid": 0, "result_urls": [], "error": None,
             "elapsed": None} for row in query_plan()]


def _parse(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip().replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def assign_case_window(item, window_start, window_end):
    """给专项案例分配 7d/30d/90d；回溯案例显式标记 carryover。"""
    row = dict(item)
    published = _parse(row.get("publish_time") or row.get("date"))
    start = _parse(window_start) or date.today() - timedelta(days=6)
    end = _parse(window_end) or date.today()
    if published and start <= published <= end:
        row["case_window"] = "7d"
        row["carryover"] = False
    elif published and published >= end - timedelta(days=29):
        row["case_window"] = "30d"
        row["carryover"] = True
    elif published and published >= end - timedelta(days=89):
        row["case_window"] = "90d"
        row["carryover"] = True
    else:
        row["case_window"] = None
        row["carryover"] = False
    return row


def normalize_case_feed(rows, window_start, window_end):
    """将专项适配器结果标准化为主流水线候选。"""
    result = []
    for raw in rows or []:
        row = dict(raw)
        row.setdefault("category", "legal")
        row.setdefault("source_channel", "practice_case_discovery")
        row.setdefault("discovery_stage", "practice_case_discovery")
        row.setdefault("practice_case", True)
        row.setdefault("source", row.get("institution") or row.get("_source", ""))
        row.setdefault("abstract", row.get("digest", ""))
        row = assign_case_window(row, window_start, window_end)
        row.setdefault("practice_domain_confidence", domain_confidence(row))
        # 专项池要求候选必须是可复核裁判材料；对明确的典型案例/案例库材料补齐
        # 七维特征的“有规则、有案例、可操作”信号，不改变普通资讯评分。
        text = f"{row.get('title', '')} {row.get('abstract', '')}"
        if any(k in text for k in ("典型案例", "人民法院案例库", "裁判要旨", "裁判规则")):
            feat = dict(row.get("features") or {})
            feat.update({"case_density": 1, "norm_anchoring": 1, "actionability": 1,
                         "author_empirical_depth": min(feat.get("author_empirical_depth", 2), 2),
                         "framework_quality": min(feat.get("framework_quality", 2), 2),
                         "relevance_halflife": min(feat.get("relevance_halflife", 2), 2)})
            row["features"] = feat
        if row.get("case_window"):
            result.append(row)
    return result


def domain_confidence(item):
    """防止跨领域关键词把行为保全/IP平台责任冒充核心民商事实务。"""
    text = f"{item.get('title', '')} {item.get('abstract', '')}"
    domain = item.get("practice_domain", "")
    if "行为保全" in text and not any(k in text for k in ("执行", "被执行人", "财产保全", "股权冻结")):
        return 0.4
    if "知识产权" in text or "专利" in text or "著作权" in text:
        if domain not in ("合同与债权债务", "民事诉讼程序与证据"):
            return 0.4
    if domain in PRACTICE_DOMAINS and any(k in text for k in PRACTICE_DOMAINS[domain]):
        return 0.9
    return 0.6 if domain in PRACTICE_DOMAINS else 0.0


def summarize(rows, selected=None):
    selected_urls = {x.get("url") for x in (selected or [])}
    by_domain, by_source, by_window = {}, {}, {"7d": 0, "30d": 0, "90d": 0}
    for row in rows or []:
        domain = row.get("practice_domain") or row.get("domain") or "未标注"
        source = row.get("source") or "未标注"
        window = row.get("case_window") or "未分配"
        by_domain[domain] = by_domain.get(domain, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        if window in by_window:
            by_window[window] += 1
    result = {
        "practice_case_candidates": len(rows or []),
        "by_practice_domain": by_domain,
        "by_case_source": by_source,
        "case_windows": by_window,
        "selected_practice_cases": sum(1 for x in (selected or []) if x.get("url") in selected_urls and x.get("practice_case")),
        "profile_case_hit_rate": round(sum(1 for x in (selected or []) if x.get("practice_case") and x.get("profile_relevance_score", 0) >= 2) / max(1, sum(1 for x in (selected or []) if x.get("practice_case"))), 3),
        "practice_case_shortage": len(rows or []) < 12,
    }
    result["case_window_7d"] = by_window["7d"]
    result["case_window_30d"] = by_window["30d"]
    result["case_window_90d"] = by_window["90d"]
    if rows:
        top = max(by_source.values())
        result["source_concentration"] = round(top / len(rows), 3)
        result["practice_case_source_concentration"] = top / len(rows) > 0.4
    else:
        result["source_concentration"] = 0.0
        result["practice_case_source_concentration"] = False
    return result


def run_source_specific_adapters(plan=None, timeout=3):
    """按来源分流执行三类专用 adapter；任何失败只进入审计，不伪造候选。"""
    plan = plan or query_plan()
    from fada_spc_official_adapter import run_queries as run_fada
    from shenzhen_court_adapter import run_queries as run_shenzhen
    buckets = [plan[0::3], plan[1::3], plan[2::3]]
    outputs = [run_fada(buckets[1], timeout=timeout), run_shenzhen(buckets[2], timeout=timeout)]
    audit = []
    results = []
    pending_case_db = [{"query": x["query"], "practice_domain": x["domain"], "source_target": "人民法院案例库", "execution_mode": "browser_login_required", "fallback_level": "pending", "source_access_verified": False, "request_status": "not_run_until_user_login", "result_count_raw": 0, "result_count_valid": 0, "result_urls": [], "error": "等待人民法院案例库本机扫码/登录态", "elapsed": None} for x in buckets[0]]
    audit.extend(pending_case_db)
    for out in outputs:
        audit.extend(out.get("audit", [])); results.extend(out.get("results", []))
    return {"query_count": len(plan), "direct_query_count": sum(1 for a in audit if a.get("fallback_level") == "direct"), "fallback_query_count": sum(1 for a in audit if a.get("fallback_level") not in ("direct", "pending")), "result_count_raw": sum(a.get("result_count_raw", 0) for a in audit), "result_count_valid": sum(a.get("result_count_valid", 0) for a in audit), "source_access_verified_count": sum(1 for a in audit if a.get("source_access_verified")), "errors": [a for a in audit if a.get("error") and a.get("fallback_level") != "pending"], "audit": audit, "results": results, "by_adapter": {"people_court_case_database_adapter": {"status": "requires_browser_login", "query_count": len(buckets[0]), "result_count_valid": 0, "source_access_verified_count": 0}, "fada_spc_official_adapter": outputs[0], "shenzhen_court_adapter": outputs[1]}}
