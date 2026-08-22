"""个人画像覆盖层：仅从被 Git 忽略的 assets/config/profile.local.yaml 读取。"""
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

BASE = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE / "assets" / "config" / "profile.local.yaml"


def load_profile():
    if yaml is None or not PROFILE_PATH.exists():
        return {}
    with PROFILE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def court_accounts(default):
    configured = load_profile().get("sources", {}).get("court_accounts", [])
    return configured or default


def interest_keywords(default):
    scoring = load_profile().get("scoring", {})
    long_term = scoring.get("long_term_keywords", [])
    dynamic = scoring.get("dynamic_case_keywords", [])
    return long_term + dynamic if (long_term or dynamic) else default


def geographic_bonus(title="", source="", abstract=""):
    """按个人画像的地域规则返回额外分；全国权威来源不因地域缺失扣分。"""
    geo = load_profile().get("geography", {})
    text = " ".join((title or "", source or "", abstract or ""))
    if any(k in text for k in geo.get("national_authority_indicators", [])):
        return 0.0
    for rule in geo.get("bonus_rules", []):
        if any(k in text for k in rule.get("keywords", [])):
            return float(rule.get("bonus", 0))
    return 0.0
