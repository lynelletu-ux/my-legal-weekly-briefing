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


def profile_matches(title="", abstract=""):
    """规则词典+稳定语义映射，返回可审计的画像领域标签。"""
    text = f"{title or ''} {abstract or ''}"
    long_map = {
        "合同与债权债务": ("合同", "债权", "债务", "违约", "履行", "委托", "服务", "附随义务", "合同目的", "免责条款"),
        "公司商事与股权治理": ("公司", "商事", "股东", "股权", "治理", "法人", "董事"),
        "强制执行": ("强制执行", "被执行人", "执行联动", "执行案件"),
        "执行异议与追加变更": ("执行异议", "追加被执行人", "变更被执行人", "追加", "变更被执行"),
        "保全调查与股权冻结": ("财产保全", "财产线索", "财产调查", "股权冻结", "查封", "冻结"),
        "民事诉讼程序": ("民事诉讼", "民事程序", "民事管辖", "二审", "庭审", "法官询问"),
        "证据与证明": ("证据", "电子证据", "举证", "证明标准", "证明责任", "事实核验"),
        "建设工程": ("建设工程", "施工", "工程款", "违法分包", "分包"),
        "侵权与损害赔偿": ("侵权", "损害赔偿", "损害", "赔偿", "事故", "保险", "免责条款"),
        "担保与追偿": ("担保", "保证", "追偿"),
    }
    dynamic_map = {
        "执行异议及追加变更": ("执行异议", "追加被执行人", "变更被执行人", "执行联动", "股东责任"),
        "强制执行中的股东责任": ("强制执行", "股东责任", "被执行人股东"),
        "保全与财产线索": ("财产保全", "财产线索", "财产调查", "查封", "冻结"),
        "股权冻结": ("股权冻结", "股权查封"),
        "合同履行与违约": ("合同履行", "违约责任", "合同目的", "合同解释"),
        "服务与委托合同": ("服务合同", "委托合同", "服务/委托", "附随义务"),
        "违法分包与施工损害": ("违法分包", "施工损害", "建设工程责任"),
        "保险责任与侵权交叉": ("保险责任", "保险免责", "侵权责任", "事故损害", "损害赔偿"),
        "民事证据审查": ("民事证据", "证据审查", "电子证据", "举证责任", "证明标准"),
        "一审事实核验与法官询问": ("事实核验", "法官询问", "一审庭审"),
        "二审答辩与审查范围": ("二审答辩", "二审审查", "审查范围"),
    }
    long_term = [label for label, words in long_map.items() if any(w in text for w in words)]
    dynamic = []
    for label, words in dynamic_map.items():
        if label == "保险责任与侵权交叉":
            if "保险" in text and any(w in text for w in ("侵权", "事故", "损害", "赔偿")):
                dynamic.append(label)
        elif any(w in text for w in words):
            dynamic.append(label)
    return long_term, dynamic


def personalization_bonus(title="", abstract=""):
    """长期70%+动态30%的可解释画像加成，最高保留原项目0.3量级。"""
    long_term, dynamic = profile_matches(title, abstract)
    mix = load_profile().get("scoring", {}).get("discovery_mix", {})
    long_weight = float(mix.get("long_term", 0.70))
    dynamic_weight = float(mix.get("dynamic_case", 0.30))
    raw = 0.3 * min(1.0, long_weight * bool(long_term) + dynamic_weight * bool(dynamic))
    return round(raw, 1), long_term, dynamic


def effective_geographic_bonus(title="", source="", abstract=""):
    """将原始地域偏好压缩为同质量层微调，不改变 profile.local.yaml 原始配置。"""
    raw = geographic_bonus(title, source, abstract)
    return {0.8: 0.4, 0.6: 0.3, 0.5: 0.25, 0.3: 0.15}.get(round(raw, 1), min(raw, 0.4))


def profile_relevance_score(title="", abstract=""):
    """0—3：动态直接命中=3，长期核心=2，跨案件迁移价值=1。"""
    long_term, dynamic = profile_matches(title, abstract)
    text = f"{title or ''} {abstract or ''}"
    if dynamic:
        return 3
    if long_term:
        return 2
    if any(k in text for k in ("裁判规则", "裁判方法", "责任认定", "证明", "规则", "审查路径", "法律适用")):
        return 1
    return 0


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
