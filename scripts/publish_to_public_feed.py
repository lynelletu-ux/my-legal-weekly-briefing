#!/usr/bin/env python3
"""将本地 Publish Layer 安全同步到独立 Public Feed checkout。"""
import argparse, json, re, shutil, subprocess
from pathlib import Path

SENSITIVE = ("cookie", "token", "storage state", "storage_state", "session", "password", "credential", "/users/", "\\users\\")

def scan(path: Path):
    hits = []
    for f in [path / "latest.json", path / "latest.md", path / "status.json"] + list((path / "archive").glob("**/*")):
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            for marker in SENSITIVE:
                if marker in text:
                    hits.append(f"{f.name}: {marker}")
    return sorted(set(hits))

def run(cmd, cwd):
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--checkout", type=Path, required=True); ap.add_argument("--remote", default="https://github.com/lynelletu-ux/my-legal-weekly-briefing.git"); args = ap.parse_args()
    local = Path(__file__).resolve().parent.parent / "publish"
    latest = json.loads((local / "latest.json").read_text(encoding="utf-8"))
    checks = latest.get("checks", {})
    if not (latest.get("publish_ready") is True and checks.get("self_check") is True and checks.get("artifact_consistency_check") is True and checks.get("errors") == [] and latest.get("publish_consistency_check", {}).get("ok") is True):
        shutil.copy2(local / "status.json", args.checkout / "status.json")
        return {"status": "failed", "reason": "publish readiness checks failed; latest was not copied"}
    hits = scan(local)
    if hits:
        return {"status": "failed", "reason": "privacy scan failed", "hits": hits}
    args.checkout.mkdir(parents=True, exist_ok=True)
    if not (args.checkout / ".git").exists():
        r = run(["git", "init"], args.checkout)
        if r.returncode: raise SystemExit(r.stderr)
        run(["git", "remote", "add", "origin", args.remote], args.checkout)
    for name in ("latest.json", "latest.md", "status.json"):
        shutil.copy2(local / name, args.checkout / name)
    archive = local / "archive"
    if archive.exists():
        shutil.copytree(archive, args.checkout / "archive", dirs_exist_ok=True)
    hits = scan(args.checkout)
    if hits: return {"status": "failed", "reason": "public checkout privacy scan failed", "hits": hits}
    run(["git", "add", "latest.json", "latest.md", "status.json", "archive"], args.checkout)
    diff = run(["git", "diff", "--cached", "--quiet"], args.checkout)
    if diff.returncode == 0: return {"status": "unchanged", "checkout": str(args.checkout)}
    commit = run(["git", "commit", "-m", f"legal briefing feed {latest['run_id']}"], args.checkout)
    if commit.returncode: return {"status": "failed", "reason": commit.stderr.strip()}
    push = run(["git", "push", "-u", "origin", "HEAD"], args.checkout)
    if push.returncode: return {"status": "auth_required", "reason": push.stderr.strip(), "checkout": str(args.checkout), "commit": latest["run_id"]}
    return {"status": "pushed", "checkout": str(args.checkout), "run_id": latest["run_id"]}

if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
