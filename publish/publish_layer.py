#!/usr/bin/env python3
"""Publish Layer v1.0：只读转换后端 final_selection，不重新评分或选择。"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISH = ROOT / "publish"


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def selected_id(item):
    # 与后端 selected_id 保持完全一致：稳定 hash，不使用 URL 作为公开 ID。
    raw = json.dumps({"url": item.get("url", ""), "title": item.get("title", "")}, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


PUBLIC_FIELDS = (
    "id", "title", "url", "source", "source_system", "source_bucket", "published_at",
    "publish_time", "category", "practice_case", "practice_domain", "practice_domain_confidence",
    "case_window", "carryover", "score", "final_score", "quality_score", "base_quality_score",
    "profile_relevance_score", "authority_tier", "priority_lane", "topic_cluster", "abstract",
    "selection_reason", "source_access_verified", "long_term_profile_matches",
    "dynamic_profile_matches", "institution", "region", "alternate_urls", "mirror_urls",
    "discovery_channel", "original_url", "original_url_obtained", "original_url_type",
    "content_access_verified", "verification_method",
)


def clean_match(value):
    if not isinstance(value, list):
        return value
    # 画像命中字段只允许抽象主题，不传播内部案件事实或当事人信息。
    return [str(x) for x in value if isinstance(x, (str, int, float))][:12]


def sanitize(item):
    out = {}
    for key in PUBLIC_FIELDS:
        if key in item:
            out[key] = item[key]
    out["id"] = selected_id(item)
    source = str(item.get("source", "") or "")
    channel = str(item.get("source_channel", "") or "")
    original_url = str(item.get("original_url") or item.get("url") or "")
    is_wechat = original_url.startswith("https://mp.weixin.qq.com/")
    out["url"] = original_url
    out["original_url"] = original_url
    out["original_url_obtained"] = bool(original_url.startswith(("https://", "http://")))
    out["original_url_type"] = "wechat" if is_wechat else (
        "court_database" if "rmfyalk.court.gov.cn" in original_url else
        "fada" if "法答网" in source else
        "official_web" if ".gov.cn" in original_url or ".court.gov.cn" in original_url else
        "other"
    )
    out["discovery_channel"] = item.get("discovery_channel") or channel or "unknown"
    out["source_system"] = out.get("source_system") or {
        "weread": "weread", "practice_case_discovery": "people_court_case_database",
        "ai_web": "ai_web", "official_web": "official_web",
    }.get(channel, "official_web")
    if "fada" in source or "法答网" in source:
        out["source_system"] = "fada"
    elif "深圳" in source:
        out["source_system"] = "shenzhen_court"
    elif "人民法院案例库" in source:
        out["source_system"] = "people_court_case_database"
    # 旧字段保留为兼容别名：仅表示是否直连原始 URL 验证到正文，绝不代表 URL 是否已取得。
    direct_verified = bool(item.get("content_access_verified", item.get("source_access_verified", False)))
    out["content_access_verified"] = direct_verified
    out["source_access_verified"] = direct_verified
    mirrors = item.get("mirror_urls") or []
    out["mirror_urls"] = [str(x) for x in mirrors if str(x).startswith(("https://", "http://"))]
    if out["content_access_verified"]:
        out["verification_method"] = "direct"
    elif out["mirror_urls"]:
        out["verification_method"] = "official_mirror"
    elif is_wechat and channel == "weread":
        out["verification_method"] = "weread"
    elif out["original_url_obtained"]:
        out["verification_method"] = "metadata_only"
    else:
        out["verification_method"] = "unverified"
    if "published_at" not in out:
        out["published_at"] = item.get("publish_time") or item.get("date")
    if "quality_score" not in out:
        out["quality_score"] = item.get("base_quality_score")
    out["long_term_profile_matches"] = clean_match(out.get("long_term_profile_matches", []))
    out["dynamic_profile_matches"] = clean_match(out.get("dynamic_profile_matches", []))
    # 明确移除潜在内部字段，即使未来上游候选扩展也不会透传。
    return out


def source_status(report):
    counts = report.get("counts", {})
    channels = counts.get("by_channel", {})
    adapters = (report.get("practice_case_discovery") or {}).get("source_adapters", {}).get("by_adapter", {})
    def adapter(name, default="degraded"):
        return adapters.get(name, {})
    db = adapter("people_court_case_database_browser_adapter")
    fada = adapter("fada_spc_official_adapter")
    sz = adapter("shenzhen_court_adapter")
    return {
        "weread": {"status": "ok" if channels.get("weread", 0) > 0 else "degraded", "discovered_count": channels.get("weread", 0)},
        "people_court_case_database": {"status": "ok" if db.get("source_access_verified_count", 0) > 0 else "degraded", "source_access_verified": bool(db.get("source_access_verified_count", 0)), "discovered_count": db.get("result_count_valid", 0)},
        "fada": {"status": "ok" if fada.get("source_access_verified_count", 0) > 0 else "degraded", "source_access_verified": bool(fada.get("source_access_verified_count", 0)), "discovered_count": fada.get("result_count_valid", 0)},
        "shenzhen_courts": {"status": "ok" if sz.get("source_access_verified_count", 0) > 0 else "degraded", "source_access_verified": bool(sz.get("source_access_verified_count", 0)), "discovered_count": sz.get("result_count_valid", 0)},
        "ai_web": {"status": "ok" if channels.get("ai_web", 0) > 0 else "degraded", "discovered_count": channels.get("ai_web", 0)},
    }


def consistency(payload, report):
    fs = report.get("final_selection", {})
    for key in ("ai", "legal", "radar"):
        expected = [selected_id(x) for x in fs.get(key, [])]
        actual = [x.get("id") for x in payload.get({"ai": "ai_selected", "legal": "legal_selected", "radar": "radar"}[key], [])]
        if expected != actual:
            return False, f"{key} selected IDs mismatch"
    for key in ("run_id", "input_hash", "candidate_pool_hash"):
        if payload.get(key) != report.get(key):
            return False, f"{key} mismatch"
    return True, ""


def render_md(payload):
    lines = [
        "# Codex 法律实务期刊｜Publish Layer v1.0", "",
        f"- run_id：`{payload['run_id']}`",
        f"- 生成时间：{payload['generated_at']}",
        f"- 时间窗口：{payload['window_start']} — {payload['window_end']}",
        f"- 后端状态：`{payload['pipeline_status']}`；publish_ready：`{payload['publish_ready']}`", "",
        "## AI精选", "",
    ]
    for x in payload["ai_selected"]:
        lines.append(f"- **{x.get('title','')}**（{x.get('score', x.get('final_score',''))}）｜{x.get('url','')}")
    lines += ["", "## 法律精选", ""]
    for x in payload["legal_selected"]:
        lines.append(f"- **{x.get('title','')}**（{x.get('score', x.get('final_score',''))}）｜{x.get('url','')}")
    lines += ["", "## Radar", ""]
    for x in payload["radar"]:
        lines.append(f"- **{x.get('title','')}**（{x.get('score', x.get('final_score',''))}）｜{x.get('url','')}")
    m = payload["metrics"]
    lines += ["", "## 专项案例统计", "", f"- 候选：{m['practice_case_candidates']}；入选：{m['selected_practice_cases']}；命中率：{m['profile_case_hit_rate']}", f"- 噪音过滤：{m['noise_removed']}；去重：{m['dedupe_removed']}", "", "## 来源状态", ""]
    for k, v in payload["source_status"].items():
        lines.append(f"- {k}：`{v['status']}`，发现 {v.get('discovered_count', 0)}")
    return "\n".join(lines) + "\n"


def publish(report_path: Path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    errors = report.get("errors", []) or []
    self_ok = (report.get("self_check") or {}).get("ok", report.get("self_check") is True)
    artifact_ok = bool(report.get("artifact_consistency_check"))
    if not (self_ok and artifact_ok and not errors):
        status = {"latest_attempt_run_id": report.get("run_id", ""), "latest_attempt_status": "failed", "last_successful_run_id": "", "last_successful_generated_at": "", "failure_reason": errors or ["self_check/artifact_consistency_check failed"]}
        atomic_write(PUBLISH / "status.json", json.dumps(status, ensure_ascii=False, indent=2))
        return {"publish_ready": False, "status_path": str(PUBLISH / "status.json"), "failure_reason": status["failure_reason"]}
    fs = report.get("final_selection", {})
    payload = {
        "schema_version": "1.2", "run_id": report.get("run_id", ""), "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": report.get("window_start", ""), "window_end": report.get("window_end", ""), "pipeline_status": "success", "publish_ready": True,
        "checks": {"self_check": True, "artifact_consistency_check": True, "errors": []},
        "field_semantics": {
            "original_url_obtained": "是否可靠取得原始发布页 URL；与正文直接访问状态无关。",
            "content_access_verified": "是否通过原始 URL 直接访问/解析正文。",
            "source_access_verified": "兼容字段，等同 content_access_verified；不表示 original_url_obtained。",
            "mirror_urls": "仅用于交叉核验，绝不替换 url/original_url。",
        },
        "source_status": source_status(report),
        "metrics": {"total_candidates": report.get("counts", {}).get("candidates", 0), "unique_candidate_count": report.get("counts", {}).get("candidates", 0), "wechat_candidates": report.get("counts", {}).get("by_channel_raw", {}).get("weread", 0), "practice_case_candidates": (report.get("practice_case_discovery") or {}).get("practice_case_candidates", 0), "selected_practice_cases": (report.get("practice_case_discovery") or {}).get("selected_practice_cases", 0), "profile_case_hit_rate": (report.get("practice_case_discovery") or {}).get("profile_case_hit_rate", 0), "practice_case_shortage": (report.get("practice_case_discovery") or {}).get("practice_case_shortage", False), "noise_removed": report.get("counts", {}).get("noise_removed", 0), "dedupe_removed": report.get("counts", {}).get("dedupe_removed", 0)},
        "ai_selected": [sanitize(x) for x in fs.get("ai", [])], "legal_selected": [sanitize(x) for x in fs.get("legal", [])], "radar": [sanitize(x) for x in fs.get("radar", [])],
        "input_hash": report.get("input_hash"), "candidate_pool_hash": report.get("candidate_pool_hash"), "selected_ids": report.get("selected_ids", {}),
    }
    ok, reason = consistency(payload, report)
    payload["publish_consistency_check"] = {"ok": ok, "reason": reason}
    if not ok:
        atomic_write(PUBLISH / "status.json", json.dumps({"latest_attempt_run_id": report.get("run_id", ""), "latest_attempt_status": "failed", "last_successful_run_id": "", "last_successful_generated_at": "", "failure_reason": [reason]}, ensure_ascii=False, indent=2))
        return {"publish_ready": False, "status_path": str(PUBLISH / "status.json"), "failure_reason": [reason]}
    js = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    md = render_md(payload)
    day = report.get("date") or report.get("window_end", "")[:10]
    archive = PUBLISH / "archive" / day
    atomic_write(PUBLISH / "latest.json", js); atomic_write(PUBLISH / "latest.md", md)
    atomic_write(archive / f"{report['run_id']}.json", js); atomic_write(archive / f"{report['run_id']}.md", md)
    status = {"latest_attempt_run_id": report["run_id"], "latest_attempt_status": "success", "last_successful_run_id": report["run_id"], "last_successful_generated_at": payload["generated_at"], "failure_reason": []}
    atomic_write(PUBLISH / "status.json", json.dumps(status, ensure_ascii=False, indent=2))
    return {"publish_ready": True, "latest_json": str(PUBLISH / "latest.json"), "latest_md": str(PUBLISH / "latest.md"), "status_path": str(PUBLISH / "status.json"), "archive": str(archive), "publish_consistency_check": payload["publish_consistency_check"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("report", type=Path); args = ap.parse_args(); print(json.dumps(publish(args.report), ensure_ascii=False, indent=2))
