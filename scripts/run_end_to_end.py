#!/usr/bin/env python3
"""个人版正式入口：发现 → 构建候选 → 时间/噪音门禁 → 去重 → 评分 → 选择 → 交付。

不要求用户预先生成 candidates 文件。默认运行已登录的微信读书 Level 3；
官方 Web/AI 适配器可以通过 --official-feed / --ai-feed 注入本轮公开发现结果，
缺失时保持空集合并由 run-report 明确记录，不会伪造内容或绕过时间门槛。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent


def load_feed(path, channel, category="legal"):
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("candidates", data.get("articles", []))
    rows = []
    for row in data:
        item = dict(row)
        item.setdefault("category", category)
        item.setdefault("source_channel", channel)
        item.setdefault("source", item.get("_source", ""))
        item.setdefault("abstract", item.get("digest", ""))
        rows.append(item)
    return rows


def discover_weread(days):
    subprocess.run([sys.executable, str(BASE / "fetch_weread_week.py"), "--days", str(days)], check=True)
    path = BASE / "mp_articles_weread.json"
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    result = []
    for row in rows:
        source = row.get("_source", "")
        result.append({
            **row,
            "category": "legal",
            "source": source,
            "source_channel": "weread",
            "institution": source,
            "region": "深圳" if "深圳" in source else "全国",
            "abstract": row.get("digest", ""),
        })
    return result


def main():
    parser = argparse.ArgumentParser(description="个人法律实务期刊端到端入口")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--window-start")
    parser.add_argument("--window-end")
    parser.add_argument("--official-feed", type=Path, help="官方 Web 候选 JSON（可选）")
    parser.add_argument("--ai-feed", type=Path, help="AI+法律候选 JSON（可选）")
    parser.add_argument("--practice-case-feed", type=Path, help="专项司法案例候选 JSON（可选；由案例库/法答网适配器生成）")
    args = parser.parse_args()

    candidates = discover_weread(args.days)
    candidates += load_feed(args.official_feed, "official_web", "legal")
    candidates += load_feed(args.ai_feed, "ai_web", "ai-legal")
    if args.practice_case_feed:
        from practice_case_discovery import normalize_case_feed
        practice_rows = load_feed(args.practice_case_feed, "practice_case_discovery", "legal")
        candidates += normalize_case_feed(practice_rows, args.window_start, args.window_end)

    from run_pipeline import load_settings, run_pipeline
    settings = load_settings()
    settings.setdefault("output", {})
    if args.window_start:
        settings["output"]["window_start"] = args.window_start
    if args.window_end:
        settings["output"]["window_end"] = args.window_end

    code, report = run_pipeline(lambda: candidates, settings=settings)
    print(json.dumps({"exit_code": code, "discover_mode": "live_in_memory", "counts": report.get("counts", {}),
                      "report": report.get("report_path"), "errors": report.get("errors", [])}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
