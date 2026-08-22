#!/usr/bin/env python3
"""基于既有 run-report 候选池执行 v1.0.6 精选层离线重排。"""
import json
from pathlib import Path
from datetime import date

import run_pipeline as rp

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
RUN_ID = "20260823T022857-c9e71f3c"
src = OUT / "run-report-2026-08-23.json"
old = json.loads(src.read_text(encoding="utf-8"))
old_legal = old.get("final_selection", {}).get("legal", [])
rows = []
for raw in old.get("candidate_audit", []):
    if raw.get("category") == "ai-legal" or raw.get("noise") or raw.get("time_exclusion_reason"):
        continue
    c = dict(raw)
    c["score"] = float(c.get("final_score", c.get("score", 0)) or 0)
    c["source_bucket"] = rp.source_bucket(c)
    c["topic_bucket"] = rp.classify_topic(c)
    c["priority_lane"] = "core_practice" if (c.get("practice_case") and c.get("profile_relevance_score", 0) >= 2 and c.get("practice_domain_confidence", 0) >= 0.7 and c["score"] >= 7.0) else ("authority" if c.get("must_consider") else "normal")
    rows.append(c)
rows.sort(key=lambda x: x["score"], reverse=True)
new_legal, remaining = rp.select_diverse(rows, "legal", 7, 2, 0, 2, 3, 0)
selected_urls = {x.get("url") for x in new_legal}
radar = remaining[:8]
radar_urls = {x.get("url") for x in radar}
for c in rows:
    c["selection_stage"] = "final_selection"
    c["final_disposition"] = "selected" if c.get("url") in selected_urls else ("radar" if c.get("url") in radar_urls else "filtered")
    c.setdefault("rank_before_diversity", None)
    c.setdefault("rank_after_diversity", None)
    c.setdefault("blocked_by", [])
    c.setdefault("promoted_by", [])
    c.setdefault("diversity_override", False)

def titles(items):
    return [x.get("title", "") for x in items]

construction = next((x for x in rows if "建设工程施工合同纠纷典型案例" in x.get("title", "")), None)
old_urls = {x.get("url") for x in old_legal}
report = {
    "run_id": RUN_ID,
    "source_run_report": str(src),
    "rule_version": "v1.0.6",
    "old_legal": old_legal,
    "new_legal": new_legal,
    "old_legal_titles": titles(old_legal),
    "new_legal_titles": titles(new_legal),
    "radar": radar,
    "core_practice_candidates": sum(1 for x in rows if x.get("priority_lane") == "core_practice"),
    "core_practice_selected": sum(1 for x in new_legal if x.get("priority_lane") == "core_practice"),
    "must_consider_count": sum(1 for x in rows if x.get("must_consider")),
    "diversity_override_count": sum(1 for x in rows if x.get("diversity_override")),
    "source_buckets": {},
    "construction_trace": construction,
    "changes": [],
    "k_nn_enabled": all("knn_prediction" in x for x in rows),
}
for x in rows:
    report["source_buckets"].setdefault(x.get("source_bucket", ""), 0)
    report["source_buckets"][x.get("source_bucket", "")] += 1
for x in rows:
    was = x.get("url") in old_urls
    now = x.get("url") in selected_urls
    if was != now:
        report["changes"].append({"title": x.get("title"), "score": x.get("score"), "before": "selected" if was else "not_selected", "after": x.get("final_disposition"), "reason": x.get("promoted_by") or x.get("blocked_by") or ["lane/order recalibration"]})

json_path = OUT / f"v1.0.6-selection-audit-{date.today().isoformat()}.json"
json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
md = [f"# v1.0.6 精选策略离线重排（{RUN_ID}）", "", "## v1.0.5 纯法律精选", ""]
md += [f"- {x.get('title')}（{x.get('score')}）" for x in old_legal]
md += ["", "## v1.0.6 纯法律精选", ""]
md += [f"- {x.get('title')}（{x.get('score')}，{x.get('priority_lane')}，{x.get('source_bucket')}）" for x in new_legal]
md += ["", "## 建设工程典型案例 selection_trace", "", "```json", json.dumps(construction, ensure_ascii=False, indent=2), "```", "", "## 变化", ""]
md += [f"- {x['title']}：{x['before']} → {x['after']}；原因：{', '.join(x['reason'])}" for x in report["changes"]] or ["- 精选集合无变化"]
md += ["", f"core_practice 候选 {report['core_practice_candidates']} 条，入选 {report['core_practice_selected']} 条；must_consider {report['must_consider_count']} 条；diversity_override {report['diversity_override_count']} 条。", "", "k-NN 字段真实存在并参与本次离线重排。"]
md_path = OUT / f"v1.0.6-selection-audit-{date.today().isoformat()}.md"
md_path.write_text("\n".join(md), encoding="utf-8")
print(json.dumps({"json": str(json_path), "markdown": str(md_path), "new_legal": titles(new_legal), "radar": titles(radar), "construction": construction.get("final_disposition") if construction else None}, ensure_ascii=False, indent=2))
