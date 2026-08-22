#!/usr/bin/env python3
"""周报生成流水线编排层（开源化：失败容错 + 自检断言 + 结构化日志）

职责：
- 串联：内容发现 → 去重 → 评分 → 写简报 → IMA 导入
- 每步 try/except：RETRYABLE（限流/网络/超时 → 退避重试）vs FATAL（依赖缺失 → 告警退出）
- 自检改为退出码断言（候选池≥min_candidates、导入数≥交付数、MD 非空）
- 写 run-report.json + .workbuddy/runs/<date>.jsonl 日志
- MP 不可用 → 跳过 MP 阶段，标"MP 缺失"继续

注意：内容发现（WebSearch/MP 拉取）由调用方完成并写入 candidates 文件，本文件负责
去重→评分→写简报→IMA队列的确定性编排。CLI 契约：

    python3 run_pipeline.py candidates.jsonl

candidates.jsonl 每行: {"title":..., "url":..., "category":"legal|ai-legal", "features":{...}}
输出: 周报_<date>.md + ima_import_queue.jsonl + run-report.json
"""
import json, time, sys, re
from pathlib import Path
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    yaml = None

BASE = Path(__file__).resolve().parent
SETTINGS = BASE.parent / "assets" / "config" / "settings.yaml"
RUNS_DIR = BASE / ".workbuddy" / "runs"


class PipelineError(Exception):
    pass


class RetryableError(PipelineError):
    pass


class FatalError(PipelineError):
    pass


def load_settings():
    if yaml is None or not SETTINGS.exists():
        return {}
    with open(SETTINGS) as f:
        return yaml.safe_load(f) or {}


def output_dir(settings):
    """解析用户可直接读取的交付目录，并在运行时创建它。"""
    raw = (settings.get('output', {}) or {}).get('artifacts_dir', '../outputs')
    path = Path(raw)
    if not path.is_absolute():
        path = BASE / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_publish_time(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def resolve_window(settings):
    out = settings.get("output", {}) or {}
    end = date.fromisoformat(str(out.get("window_end", date.today().isoformat())))
    start = date.fromisoformat(str(out.get("window_start", (end - timedelta(days=6)).isoformat())))
    return start, end


def preflight_channels() -> dict:
    """四层降级链前置检查（P4 新增，替代原 MP session 检查）。

    层级（自上而下优先）：
      1. weread 登录态  → fetch_weread_week.py（微信读书，主通道）
      2. yuanbao 登录态 → fetch_yuanbao_supplement.py（元宝，补充通道）
      3. TokenHub key   → fetch_hunyuan_week.py（API 兜底）
      4. WebSearch      → 手动/Agent 搜索构建候选（最后降级）

    返回 {"levels": [...], "active": "weread|yuanbao|tokenhub|websearch", "missing": [...]}
    """
    home = Path.home()

    def check_state(p, vid_name=None):
        if not p.exists():
            return False
        try:
            cookies = json.loads(p.read_text()).get("cookies", [])
            if vid_name:
                return any(c.get("name") == vid_name and c.get("value") for c in cookies)
            return len(cookies) > 0
        except Exception:
            return False

    levels = [
        {
            "name": "weread",
            "ok": check_state(home / ".config" / "weread_state.json", "wr_vid"),
            "desc": "微信读书登录态（主通道）",
            "script": "fetch_weread_week.py",
        },
        {
            "name": "yuanbao",
            "ok": check_state(home / ".config" / "yuanbao_state.json"),
            "desc": "元宝登录态（补充通道）",
            "script": "fetch_yuanbao_supplement.py",
        },
        {
            "name": "tokenhub",
            "ok": (home / ".config" / "tencentcloud" / "tokenhub_api_key").exists(),
            "desc": "TokenHub API 密钥（兜底通道）",
            "script": "fetch_hunyuan_week.py",
        },
    ]
    missing = [lv["name"] for lv in levels if not lv["ok"]]
    active = next((lv["name"] for lv in levels if lv["ok"]), "websearch")
    return {"levels": levels, "active": active, "missing": missing}


def log_stage(report, stage, **kw):
    entry = {"ts": time.time(), "stage": stage, **kw}
    report["stages"].append(entry)
    # 结构化日志落盘
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    logfile = RUNS_DIR / f"{date.today().isoformat()}.jsonl"
    with open(logfile, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def run_with_retry(fn, max_retries=3, backoff=2):
    """包装可重试步骤。RETRYABLE 异常退避重试；FATAL 立即抛出。"""
    last = None
    for attempt in range(max_retries):
        try:
            return fn()
        except RetryableError as e:
            last = e
            time.sleep(backoff ** attempt)
    raise last


def self_check(report, settings):
    """退出码断言：返回 (ok, failures)。"""
    out = settings.get('output', {})
    min_c = out.get('min_candidates', 10)
    failures = []
    n_candidates = report.get('counts', {}).get('candidates', 0)
    if n_candidates < min_c:
        failures.append(f"候选池 {n_candidates} < 最小 {min_c}")
    report_path = report.get('report_path')
    if report_path and not Path(report_path).exists():
        failures.append("周报 MD 文件未生成")
    return (len(failures) == 0, failures)


def classify_source(candidate):
    """从候选条目的 source/title/url 推断归一化来源标识。

    返回归一化来源字符串（如 '山东高法'、'上海一中院'、'Artificial Lawyer'）。
    用于 diversity-aware selection 的同源识别。
    """
    src = candidate.get('source', '') or ''
    title = candidate.get('title', '') or ''
    url = candidate.get('url', '') or ''

    # 法院公众号（精确匹配）
    if '山东高法' in src or '山东高法' in title:
        return '山东高法'
    if '上海一中' in src or '上海一中' in title:
        return '上海一中院'
    if '上海二中' in src or '上海二中' in title:
        return '上海二中院'
    if '中国应用法学' in src or '中国应用法学' in title:
        return '中国应用法学'
    if '最高法' in src or '最高人民法院' in src or 'court.gov.cn' in url:
        return '最高法'
    if '全国人大' in src:
        return '全国人大'
    if '国务院' in src or '人社部' in src or 'gov.cn' in url:
        return '国务院/部委'
    # 国际法律科技源
    if 'Artificial Lawyer' in src:
        return 'Artificial Lawyer'

    # Fallback: source 字段的第一段，或域名
    if src:
        return src.split('/')[0].strip().split('|')[0].strip()
    try:
        domain = urlparse(url).netloc
        return domain or '未知来源'
    except Exception:
        return '未知来源'


def classify_topic(candidate):
    """按法律主题优先级归类；AI 单独使用 ai_topic_cluster。"""
    text = " ".join(str(candidate.get(k, "") or "") for k in ("title", "abstract", "digest"))
    if candidate.get("category") == "ai-legal":
        return classify_ai_topic(candidate)
    clusters = [
        ("环境资源", ("生态", "环境资源", "污染", "绿美", "碳排放", "自然资源", "环境损害")),
        ("劳动与社会保障", ("住房公积金", "劳动", "工伤", "工资", "社会保障", "社保", "竞业")),
        ("建设工程房地产", ("建设工程", "施工", "工程款", "违法分包", "分包", "房地产", "房屋", "征收")),
        ("公司股权", ("公司", "股东", "股权", "治理", "法人", "董事", "商事")),
        ("执行保全", ("执行", "被执行人", "执行联动", "保全", "冻结", "查封", "执行异议")),
        ("民事程序与证据", ("证据", "举证", "证明", "民事管辖", "鉴定", "二审", "庭审", "诉讼")),
        ("合同债权", ("合同", "债权", "债务", "违约", "履行", "委托", "服务", "附随义务")),
        ("知识产权与平台", ("著作权", "商标", "专利", "平台", "网络")),
        ("数据与个人信息", ("个人信息", "数据处理", "数据", "隐私")),
        ("侵权保险", ("保险", "侵权", "交通事故", "人身损害", "损害赔偿", "赔偿", "责任")),
        ("行政与监管", ("行政", "行政处罚", "行政许可", "监管", "公告", "答记者问", "域外管辖", "补贴调查", "住房公积金管理条例")),
        ("刑事", ("刑事", "犯罪", "公诉", "受贿")),
        ("婚姻家事", ("婚姻", "离婚", "继承", "抚养", "遗嘱")),
    ]
    for name, keywords in clusters:
        if any(k in text for k in keywords):
            return name
    return "其他法律实务"


def classify_ai_topic(candidate):
    text = " ".join(str(candidate.get(k, "") or "") for k in ("title", "abstract", "digest"))
    clusters = [
        ("AI监管与职业责任", ("监管", "职业责任", "professional", "SRA", "伦理", "负责使用")),
        ("律所AI落地", ("律所", "law firm", "全所", "部署", "法律服务")),
        ("AI Agent / 工作流", ("Agent", "工作流", "MCP", "事务系统", "litigation")),
        ("法律检索与知识库", ("检索", "知识库", "precedent", "research")),
        ("数据安全与AI治理", ("数据安全", "治理", "权限", "隐私")),
        ("司法智能化", ("法院", "司法机关", "法庭", "审判智能")),
        ("法律AI产品", ("法律AI", "Legal AI", "Harvey", "Lexis", "Clio", "vLex")),
    ]
    for name, keywords in clusters:
        if any(k.lower() in text.lower() for k in keywords):
            return name
    return "其他AI法律"


def noise_reason(candidate):
    """评分前噪音门禁：宣传/活动稿需有可迁移规则才可进入评分。"""
    title = str(candidate.get("title", "") or "")
    body = str(candidate.get("abstract", "") or "") + " " + str(candidate.get("digest", "") or "")
    text = title + " " + body
    event_markers = (
        "工作推进会", "部署会", "总结会", "召开会议", "全国性会议", "召开全市法院", "会议释放",
        "党建", "党组会", "中央政治局会议", "参观", "调研", "文体活动", "周年", "双向奔赴", "文化巡礼",
        "普法进企业", "普法活动", "普法", "讲座", "巡回庭审", "司法服务活动", "庭审开放日", "开放日", "宣传科", "法治文化",
        "点赞", "青训营", "志愿服务", "征稿启事", "征稿", "栏目回顾",
    )
    substantive_markers = (
        "裁判规则", "裁判要旨", "司法解释", "法释", "指导性案例", "案例库", "法答网",
        "证据规则", "举证责任", "证明标准", "审查标准", "审理规则", "程序规则",
        "责任认定", "法律规定", "条文", "典型案例", "判决", "裁定", "入选案例",
    )
    if any(k in title for k in ("征稿启事", "征稿")):
        return "宣传/活动噪音：征稿/栏目宣传，不是可直接迁移的裁判规则"
    if any(k in text for k in event_markers) and not any(k in text for k in substantive_markers):
        hit = next(k for k in event_markers if k in text)
        return f"宣传/活动噪音：命中“{hit}”且未发现可迁移裁判规则或规范变化"
    return ""


def authority_tier(candidate):
    """按内容法律效力/裁判指导层级返回 A=3/B=2/C=1/D=0。"""
    text = " ".join(str(candidate.get(k, "") or "") for k in ("title", "abstract", "digest"))
    source = str(candidate.get("source", "") or "")
    if any(k in text for k in ("司法解释", "法释", "行政法规", "国务院令", "指导性案例", "人民法院案例库", "正式规范性文件")):
        return 3
    if ("法答网" in text and "征稿" not in text) or (source in ("最高人民法院", "最高人民检察院") and "典型案例" in text) or source in ("广东省高级人民法院", "司法部"):
        return 2
    if "典型案例" in text or "裁判要旨" in text:
        return 1
    if source == "中国应用法学" or "人民法院报" in source or "中级人民法院" in source:
        return 1
    return 0


def authority_calibrate(candidate, quality_before_authority):
    """k-NN 后置校准：保留原分数，同时保证明确全国规范层级优先。"""
    tier = authority_tier(candidate)
    candidate["authority_rank"] = tier
    candidate["authority_tier"] = {3: "A", 2: "B", 1: "C", 0: "D"}[tier]
    if tier == 3:
        # 全国正式规范/高价值案例设最低优先级，但不把所有官方文章统一加分。
        adjusted = max(quality_before_authority, 9.1)
        candidate["authority_adjustment_reason"] = "全国正式司法解释/指导性案例/案例库信号，设置 authority floor 9.1"
    elif tier == 2:
        adjusted = max(quality_before_authority, 8.7)
        candidate["authority_adjustment_reason"] = "明确典型案例或裁判要旨，设置较低 priority floor 8.7"
    else:
        adjusted = quality_before_authority
        candidate["authority_adjustment_reason"] = "无明确全国规范层级信号，保留 k-NN 与个人地域排序"
    candidate["authority_adjustment"] = round(adjusted - quality_before_authority, 1)
    return round(adjusted, 1)


def select_diverse(scored, category, count, max_per_source, score_floor=0.0, max_per_topic=0, min_profile=0, max_authority=0):
    """多样性感知选择：同源和同主题簇均受上限约束。

    scored: 已按分数降序排列的候选列表（含 score, category 等字段）
    category: 'ai-legal' | 'legal'（筛选条件）
    count: 目标条数
    max_per_source: 同一来源最大条数（0=不限制）
    score_floor: 精选评分下限，低于此分不进精选（宁缺毋滥，防低质条目混入）

    返回: (selected, remaining) — selected 是入选的 N 条，remaining 是未入选的（可用于 IMA 导入）
    """
    cat_items = [c for c in scored if c.get('category') == category or (category == 'legal' and c.get('category') != 'ai-legal')]
    if category == 'legal' and min_profile:
        # 仅在质量门槛内优先画像相关候选；不降低 score_floor，也不突破 source/topic 上限。
        cat_items = sorted(cat_items, key=lambda x: (x.get('authority_rank', 0) >= 2, x.get('score', 0) >= score_floor and x.get('profile_relevance_score', 0) >= 2, x.get('score', 0)), reverse=True)
    if not max_per_source or max_per_source <= 0:
        selected = [c for c in cat_items[:count] if c.get('score', 0) >= score_floor]
        remaining = cat_items[len(selected):]
        return selected, remaining

    source_counts = {}
    topic_counts = {}
    authority_count = 0
    selected = []
    remaining = []
    for item in cat_items:
        # 评分下限：低于 floor 不进精选（宁缺毋滥）
        if item.get('score', 0) < score_floor:
            remaining.append(item)
            continue
        s = classify_source(item)
        topic = classify_topic(item)
        if len(selected) >= count:
            remaining.append(item)
            continue
        if (source_counts.get(s, 0) < max_per_source and (not max_per_topic or topic_counts.get(topic, 0) < max_per_topic)
                and (not max_authority or item.get("authority_rank", 0) < 2 or authority_count < max_authority)):
            selected.append(item)
            source_counts[s] = source_counts.get(s, 0) + 1
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if item.get("authority_rank", 0) >= 2:
                authority_count += 1
        else:
            remaining.append(item)

    # 如果选不够 count 条（候选太少），允许同源重复——但补位仍须满足评分下限
    if len(selected) < count:
        overflow = []
        for item in remaining:
            if len(selected) >= count:
                break
            if item.get('score', 0) < score_floor:
                continue  # 宁缺毋滥：低分条不补位进精选
            topic = classify_topic(item)
            s = classify_source(item)
            if (source_counts.get(s, 0) >= max_per_source or (max_per_topic and topic_counts.get(topic, 0) >= max_per_topic)
                    or (max_authority and item.get("authority_rank", 0) >= 2 and authority_count >= max_authority)):
                continue
            selected.append(item)
            source_counts[s] = source_counts.get(s, 0) + 1
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if item.get("authority_rank", 0) >= 2:
                authority_count += 1
            overflow.append(item)
        remaining = [r for r in remaining if r not in overflow]

    # 按分数降序重排（diversity-aware selection 可能打乱顺序）
    selected.sort(key=lambda x: x.get('score', 0), reverse=True)
    return selected, remaining


def default_write_report(candidates, scored, settings_override=None):
    """简报写入：diversity-aware 选择 + 分数降序排列，返回 (路径, ai_selected, legal_selected, legal_remaining)。

    Stage 4.5 HTML 渲染复用全部 legal 条目（selected + remaining），remaining 供雷达区使用。
    """
    settings = settings_override or load_settings()
    out = settings.get('output', {})
    template = out.get('report_template', '周报_{date}.md')
    max_per_source = out.get('max_per_source', 2)
    max_per_topic = out.get('max_per_topic', 2)
    ai_count = out.get('ai_legal_count', 3)
    legal_count = out.get('legal_count', 7)
    path = output_dir(settings) / template.format(date=date.today().isoformat())
    window_start, window_end = resolve_window(settings)

    # Diversity-aware selection
    score_floor = out.get('select_score_floor', 0)
    ai_selected, ai_remaining = select_diverse(scored, 'ai-legal', ai_count, max_per_source, score_floor, 0)
    legal_selected, legal_remaining = select_diverse(scored, 'legal', legal_count, max_per_source, score_floor, max_per_topic, 3, 2)

    # AI+法律 signal_strength 标签映射
    signal_labels = {1: '格局级', 2: '应用落地级', 3: '融资动态级'}

    # 分类器（轻量导入，避免循环依赖）
    from ima_importer import classify as _classify

    with open(path, 'w') as f:
        f.write(f"# 📰 我的法律实务期刊｜{window_start.strftime('%Y.%m.%d')}—{window_end.strftime('%m.%d')}｜Codex试行版\n\n")
        f.write("## AI + 法律\n\n")
        for c in ai_selected:
            score = c.get('score', 0)
            title = c.get('title', '')
            url = c.get('url', '')
            src = classify_source(c)
            sig = c.get('features', {}).get('signal_strength', 2)
            sig_label = signal_labels.get(sig, '')
            abstract = c.get('abstract', '')
            recommend = c.get('recommend', '')

            f.write(f"### 【{score}】{title}\n\n")
            f.write(f"📡 {sig_label} · {src}\n\n")
            if abstract:
                f.write(f"{abstract}\n\n")
            if recommend:
                f.write(f"💡 {recommend}\n\n")
            f.write(f"🔗 {url}\n\n")
            f.write("---\n\n")

        f.write("## 纯法律\n\n")
        for c in legal_selected:
            score = c.get('score', 0)
            title = c.get('title', '')
            url = c.get('url', '')
            src = classify_source(c)
            cat, _, _ = _classify(title)
            cat_tag = cat or ''
            abstract = c.get('abstract', '')
            recommend = c.get('recommend', '')

            f.write(f"### 【{score}】{title}\n\n")
            parts = [src]
            if cat_tag:
                parts.append(cat_tag)
            f.write(f"📂 {' · '.join(parts)}\n\n")
            if abstract:
                f.write(f"{abstract}\n\n")
            if recommend:
                f.write(f"💡 {recommend}\n\n")
            f.write(f"🔗 {url}\n\n")
            f.write("---\n\n")

        # 雷达区（其他领域速览，2026-08-01 补齐 md 第三板块——SKILL.md 交付格式要求）：
        # 与 HTML 雷达区同规则：未进精选 且 分数低于精选最低分（评分不如精选）
        featured_scores = [c.get('score', 0) for c in legal_selected]
        radar_floor = min(featured_scores) if featured_scores else 7.0
        radar_rows = [c for c in legal_remaining if c.get('score', 0) < radar_floor]
        if radar_rows:
            f.write("## 其他领域速览（雷达区）\n\n")
            for c in radar_rows[:8]:
                f.write(f"### 【{c.get('score')}】{c.get('title', '')}\n\n")
                f.write(f"📂 {classify_source(c)}\n\n")
                f.write(f"🔗 {c.get('url', '')}\n\n")
                f.write("---\n\n")

    return str(path), ai_selected, legal_selected, legal_remaining


def _infer_features(c):
    """轻量特征兜底（2026-08-01 对抗审查新增）。

    新通道（微信读书/元宝）候选无 features 字段时，全部条目会拿到评分引擎默认值
    → 评分同质化，精选/导入排序失真。此处从标题/摘要启发式提取可区分的老四维
    （author_tier/platform_tier/depth/relevance），与 scoring_engine.normalize_features
    的键映射对齐。Agent 精修候选时仍建议用 build_candidates.py 的完整七维分类器。
    """
    t = (c.get("title", "") or "") + " " + (c.get("abstract", "") or "")
    feat = {"author_tier": 2, "platform_tier": 3, "depth": 1, "relevance": 2}
    if any(k in t for k in ["案例", "裁判", "判决", "被告", "原告", "诉"]):
        feat["depth"] = 2
    if any(k in t for k in ["规则", "要旨", "指引", "要点", "解读", "分析", "探析"]):
        feat["depth"] = 3
    if any(k in t for k in ["最高法", "司法解释", "民法典", "公司法", "法释", "劳动法"]):
        feat["platform_tier"] = 1
    elif any(k in t for k in ["省高院", "高院", "典型案例", "公报"]):
        feat["platform_tier"] = 2
    return feat


def run_pipeline(discover_fn, write_report_fn=None, import_fn=None, settings=None, candidates_raw=None):
    """主入口。

    discover_fn: () -> list[dict]  # 返回候选条目（已合并 MP+WebSearch）
    candidates_raw: list[dict]     # 或直接从文件载入的候选（CLI 模式）
    write_report_fn: (candidates, scored) -> str  # 写 MD，返回路径（默认 default_write_report）
    import_fn: (candidates) -> list[dict]  # IMA 导入（默认调用 ima_importer 写队列）

    返回: (exit_code, report)
    """
    settings = settings or load_settings()
    pipeline_cfg = settings.get('pipeline', {})
    max_retries = pipeline_cfg.get('max_retries', 3)
    backoff = pipeline_cfg.get('backoff', 2)
    if write_report_fn is None:
        write_report_fn = lambda candidates, scored: default_write_report(candidates, scored, settings)

    window_start, window_end = resolve_window(settings)
    report = {"date": date.today().isoformat(), "window_start": window_start.isoformat(), "window_end": window_end.isoformat(), "stages": [], "counts": {}, "errors": []}

    # Stage 0: 通道前置检查（四层降级链，P4 新增）
    ch = preflight_channels()
    log_stage(report, "preflight", active=ch["active"], missing=ch["missing"])
    for lv in ch["levels"]:
        mark = "✓" if lv["ok"] else "✗"
        print(f"  [{mark}] {lv['name']:8s} {lv['desc']}")
    if ch["missing"]:
        print(f"  ⚠️ 缺失通道: {', '.join(ch['missing'])} → 降级至 {ch['active']}")
    if ch["active"] == "websearch":
        report["errors"].append("所有自动通道不可用，降级 WebSearch（内容发现由调用方完成）")

    # Stage 1: 内容发现（含 MP 拉取，失败可降级）
    if candidates_raw is not None:
        candidates_raw = candidates_raw
        log_stage(report, "discover", count=len(candidates_raw), mode="from_file")
    else:
        try:
            def _discover():
                items = discover_fn()
                if not items:
                    raise RetryableError("内容发现返回空")
                return items
            candidates_raw = run_with_retry(_discover, max_retries, backoff)
            log_stage(report, "discover", count=len(candidates_raw))
        except RetryableError as e:
            candidates_raw = []
            report["errors"].append(f"discover 降级: {e}")
            log_stage(report, "discover", status="degraded", error=str(e))

    # Stage 2: 硬时间门槛（窗口外内容先退出当期期刊；carryover 仅显式标记时允许）
    raw_discovered_count = len(candidates_raw)
    raw_channel_counts = {}
    for item in candidates_raw:
        channel = item.get("source_channel", "unknown")
        raw_channel_counts[channel] = raw_channel_counts.get(channel, 0) + 1
    time_excluded = []
    time_filtered = []
    for item in candidates_raw:
        pub = parse_publish_time(item.get("publish_time") or item.get("date"))
        if item.get("carryover") is True:
            item = dict(item)
            item["carryover"] = True
            time_filtered.append(item)
        elif pub is None or not (window_start <= pub <= window_end):
            excluded = dict(item)
            excluded["noise"] = False
            excluded["carryover"] = False
            excluded["time_exclusion_reason"] = "缺少可解析发布时间" if pub is None else f"发布时间 {pub.isoformat()} 不在窗口 {window_start.isoformat()}—{window_end.isoformat()}"
            time_excluded.append(excluded)
        else:
            time_filtered.append(item)
    candidates_raw = time_filtered
    report["time_excluded"] = time_excluded
    report["counts"]["time_excluded"] = len(time_excluded)
    log_stage(report, "time_gate", before=raw_discovered_count, after=len(candidates_raw), excluded=len(time_excluded), window_start=window_start.isoformat(), window_end=window_end.isoformat())

    # Stage 3: 评分前噪音过滤（活动/宣传稿不因来源权威而自动获得高分）
    noise_items = []
    filtered_raw = []
    for item in candidates_raw:
        reason = noise_reason(item)
        if reason:
            item = dict(item)
            item["noise"] = True
            item["noise_reason"] = reason
            noise_items.append(item)
        else:
            filtered_raw.append(item)
    candidates_raw = filtered_raw
    report["noise"] = noise_items
    report["counts"]["noise_removed"] = len(noise_items)
    log_stage(report, "noise_filter", before=len(noise_items) + len(candidates_raw), after=len(candidates_raw), removed=len(noise_items))

    # Stage 3: 去重
    from dedupe import dedupe_items
    candidates = dedupe_items(candidates_raw)

    # 字段兜底（P4 新增）：新通道 5 字段候选补全 pipeline 必需字段
    # abstract ← digest；category 默认 legal；features 空 dict 由 _infer_features 启发式兜底
    for c in candidates:
        c.setdefault("noise", False)
        c.setdefault("carryover", False)
        if not c.get("abstract") and c.get("digest"):
            c["abstract"] = c["digest"]
        c.setdefault("category", "legal")
        c.setdefault("source", c.get("_source", ""))
        if not c.get("features"):
            c["features"] = _infer_features(c)

    report["counts"]["candidates"] = len(candidates)
    report["counts"]["input_candidates"] = raw_discovered_count
    report["counts"]["dedupe_removed"] = len(candidates_raw) - len(candidates)
    report["counts"]["by_channel_raw"] = raw_channel_counts
    report["counts"]["by_channel"] = {}
    for item in candidates_raw:
        channel = item.get("source_channel", "unknown")
        report["counts"]["by_channel"][channel] = report["counts"]["by_channel"].get(channel, 0) + 1
    log_stage(report, "dedupe", before=len(candidates_raw), after=len(candidates))

    # Stage 4: 评分（调用 scoring_engine.predict）+ 规范层级后置校准
    from scoring_engine import predict, linear_fallback, get_weights, load_settings as load_scoring_settings
    from profile_config import effective_geographic_bonus, geographic_bonus, personalization_bonus, profile_relevance_score
    scoring_settings = load_scoring_settings()
    scored = []
    for c in candidates:
        cat = c.get('category', 'legal')
        entry = {
            "features": c.get('features', {}), "title": c.get('title', ''),
            "source": c.get('source', ''), "abstract": c.get('abstract', ''),
        }
        base_quality = linear_fallback(entry, cat, get_weights(cat, scoring_settings))
        knn_prediction, conf = predict(entry, cat, include_bonuses=False)
        interest_bonus, long_matches, dynamic_matches = personalization_bonus(c.get('title', ''), c.get('abstract', ''))
        region_bonus_raw = geographic_bonus(c.get('title', ''), c.get('source', ''), c.get('abstract', '')) if cat == 'legal' else 0.0
        region_bonus = effective_geographic_bonus(c.get('title', ''), c.get('source', ''), c.get('abstract', '')) if cat == 'legal' else 0.0
        raw_knn_adjustment = round(knn_prediction - base_quality, 1)
        knn_adjustment = min(raw_knn_adjustment, 0.8)
        quality_after_knn = round(base_quality + knn_adjustment, 1)
        c['base_quality_score'] = round(base_quality, 1)
        c['knn_prediction'] = round(knn_prediction, 1)
        c['knn_score'] = round(knn_prediction, 1)  # 兼容旧字段
        c['knn_adjustment'] = round(knn_adjustment, 1)
        c['personalization_bonus'] = interest_bonus
        c['interest_bonus'] = interest_bonus
        c['long_term_profile_matches'] = long_matches
        c['dynamic_profile_matches'] = dynamic_matches
        c['region_bonus_raw'] = round(region_bonus_raw, 1)
        c['region_bonus'] = round(region_bonus, 2)
        c['effective_region_bonus'] = round(region_bonus, 2)
        c['profile_relevance_score'] = profile_relevance_score(c.get('title', ''), c.get('abstract', '')) if cat == 'legal' else 0
        c['topic_cluster'] = classify_topic(c)
        if cat == 'ai-legal':
            c['ai_topic_cluster'] = c['topic_cluster']
        c['quality_after_knn'] = quality_after_knn
        c['score'] = authority_calibrate(c, quality_after_knn + interest_bonus + region_bonus)
        c['final_score'] = c['score']
        c['score_adjustment'] = round(c['final_score'] - c['base_quality_score'], 1)
        c['confidence'] = conf
        scored.append(c)
    # 全国正式规范存在时，地域加成只作为同层级微调：普通地方稿不因 +0.8/+0.6 超过规范层级稿。
    has_formal_authority = any(c.get("authority_rank", 0) == 3 for c in scored)
    if has_formal_authority:
        for c in scored:
            if c.get("authority_rank", 0) < 3 and c.get("region") in ("深圳", "广东", "粤港澳大湾区", "湖南") and c.get("score", 0) > 9.0:
                old = c["score"]
                c["score"] = 9.0
                c["final_score"] = c["score"]
                c["authority_adjustment"] = round(c["score"] - (c.get("quality_after_knn", old) + c.get("interest_bonus", 0) + c.get("region_bonus", 0)), 1)
                c["score_adjustment"] = round(c["score"] - c.get("base_quality_score", old), 1)
                c["authority_adjustment_reason"] = "存在全国正式规范候选，限制普通地域稿的地域加成排序上限为9.0"
    scored.sort(key=lambda x: x.get('score', 0), reverse=True)
    log_stage(report, "score", count=len(scored))

    # Stage 4: 写简报（返回 path + ai_selected + legal_selected）
    report_path, ai_selected, legal_selected, legal_remaining = write_report_fn(candidates, scored)
    selected_urls = {c.get("url") for c in ai_selected + legal_selected}
    radar_urls = {c.get("url") for c in legal_remaining[:8]}
    for item in scored:
        if item.get("url") in selected_urls:
            profile_note = "，画像相关候选优先" if item.get("profile_relevance_score", 0) >= 2 else ""
            item["selection_reason"] = "达到精选门槛，并通过来源/主题多样性与规范层级校准" + profile_note
        elif item.get("url") in radar_urls:
            item["selection_reason"] = "未进入精选，保留为 Radar（评分/主题覆盖价值）"
        else:
            item["selection_reason"] = "未进入交付区：排序靠后或受到来源/主题多样性上限约束"
    report["report_path"] = report_path
    log_stage(report, "write_report", path=report_path)

    # Stage 4.5: HTML 渲染（复用脚本同目录的 render_html.py，浅色简报风）
    try:
        sys.path.insert(0, str(BASE))
        from render_html import render_html as _render
        html_articles = []
        # Radar 对外交付最多 8 条；其余未入选候选仍保留在评分后的运行数据中。
        radar_items = legal_remaining[:8]
        for c in ai_selected + legal_selected + radar_items:
            html_articles.append({
                "title": c.get("title", ""),
                "url": c.get("url", ""),
                "category": c.get("category", "legal"),
                "source": c.get("source", ""),
                "source_category": c.get("source_category", ""),
                "date": c.get("date", ""),
                "score": c.get("score", 0),
                "tags": c.get("tags", []),
                "abstract": c.get("abstract", ""),
                "recommend": c.get("recommend", ""),
            })
        html_out = _render(html_articles, date.today().strftime('%Y年%m月%d日'))
        html_path = output_dir(settings) / f"周报_{date.today().isoformat()}.html"
        html_path.write_text(html_out, encoding="utf-8")
        report["html_path"] = str(html_path)
        log_stage(report, "render_html", path=str(html_path))
    except Exception as e:
        report["errors"].append(f"render_html 失败: {e}")
        log_stage(report, "render_html", status="degraded", error=str(e))

    # Stage 5: IMA 导入。个人版默认禁用，保留全部导入代码供日后显式启用。
    ima_enabled = bool((settings.get('ima', {}) or {}).get('enabled', False))
    if not ima_enabled:
        results = []
        report["counts"]["imported"] = 0
        log_stage(report, "import", status="disabled", queued=0, total=0)
    else:
        if import_fn is None:
            from ima_importer import import_one
            def import_fn(items):
                return [import_one(c['url'], c.get('title', '')) for c in items]
        threshold = (settings.get('output', {}) or {}).get('ima_import_threshold', 0)
        court_sources = {'山东高法', '上海一中院', '上海二中院', '中国应用法学', '最高法', '国务院/部委'}
        importable = [c for c in scored
                      if c.get('score', 0) >= threshold
                      and classify_source(c) in court_sources]
        results = import_fn(importable)
        queued = sum(1 for r in results if r.get('status') in ('imported', 'queued'))
        report["counts"]["imported"] = queued
        log_stage(report, "import", queued=queued, total=len(results))

    # ChatGPT/自动化可直接消费的完整机器可读交付物。
    report["articles"] = ai_selected + legal_selected
    # 机器审计池包含评分交付、Radar、noise 和时间剔除候选，便于逐条追溯。
    for excluded in report.get("noise", []) + report.get("time_excluded", []):
        excluded.setdefault("noise", False)
        excluded.setdefault("carryover", False)
        excluded.setdefault("profile_relevance_score", 0)
        excluded.setdefault("long_term_profile_matches", [])
        excluded.setdefault("dynamic_profile_matches", [])
        excluded.setdefault("interest_bonus", 0.0)
        excluded.setdefault("personalization_bonus", 0.0)
        excluded.setdefault("region_bonus", 0.0)
        excluded.setdefault("effective_region_bonus", 0.0)
        excluded.setdefault("authority_tier", "D")
        excluded.setdefault("topic_cluster", classify_topic(excluded))
        for field in ("base_quality_score", "knn_prediction", "knn_score", "knn_adjustment", "final_score", "score"):
            excluded.setdefault(field, None)
        excluded.setdefault("selection_reason", excluded.get("noise_reason", excluded.get("time_exclusion_reason", "未进入评分")))
    report["candidate_audit"] = scored + report.get("noise", []) + report.get("time_excluded", [])
    profile_qualified = [c for c in scored if c.get("category") != "ai-legal" and c.get("score", 0) >= (settings.get("output", {}) or {}).get("select_score_floor", 0) and c.get("profile_relevance_score", 0) >= 2]
    selected_profile = [c for c in legal_selected if c.get("profile_relevance_score", 0) >= 2]
    report["counts"]["profile_qualified"] = len(profile_qualified)
    report["counts"]["profile_selected"] = len(selected_profile)
    report["profile_qualified_shortage"] = len(profile_qualified) < 3
    if report["profile_qualified_shortage"]:
        report["errors"].append(f"profile_qualified_shortage: 仅 {len(profile_qualified)} 条画像相关候选达到精选门槛")
    report["counts"]["selected_by_channel"] = {}
    for item in report["articles"]:
        channel = item.get("source_channel", "unknown")
        report["counts"]["selected_by_channel"][channel] = report["counts"]["selected_by_channel"].get(channel, 0) + 1
    report["radar"] = legal_remaining[:8]
    report["counts"]["radar"] = len(report["radar"])
    report["ima_enabled"] = ima_enabled

    # 自检
    ok, failures = self_check(report, settings)
    report["self_check"] = {"ok": ok, "failures": failures}

    # 写 run-report.json
    with open(BASE / "run-report.json", 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    run_report_artifact = output_dir(settings) / f"run-report-{date.today().isoformat()}.json"
    run_report_artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["run_report_path"] = str(run_report_artifact)
    machine_template = (settings.get('output', {}) or {}).get('machine_report_template', 'weekly-briefing-{date}.json')
    machine_path = output_dir(settings) / machine_template.format(date=date.today().isoformat())
    machine_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    report["machine_report_path"] = str(machine_path)

    exit_code = 0 if ok else 1
    return exit_code, report


def load_candidates(path):
    """从 JSONL 或 JSON 文件载入候选。每行/每项: {title, url, category, features}"""
    p = Path(path)
    items = []
    if p.suffix == '.jsonl':
        for line in open(p):
            line = line.strip()
            if line:
                items.append(json.loads(line))
    else:
        data = json.loads(open(p).read())
        items = data if isinstance(data, list) else data.get('candidates', [])
    return items


if __name__ == '__main__':
    # 用法：
    #   python3 run_pipeline.py                      # 演示 dry-run
    #   python3 run_pipeline.py candidates.jsonl     # CLI 模式：从候选文件跑全流程
    if len(sys.argv) > 1:
        candidates = load_candidates(sys.argv[1])
        cli_settings = load_settings()
        cli_settings.setdefault("output", {})
        for flag in ("window-start", "window-end"):
            if f"--{flag}" in sys.argv:
                idx = sys.argv.index(f"--{flag}")
                if idx + 1 < len(sys.argv):
                    cli_settings["output"][flag.replace("-", "_")] = sys.argv[idx + 1]
        code, rep = run_pipeline(None, candidates_raw=candidates, settings=cli_settings)
        print(f"exit_code={code}, candidates={rep['counts'].get('candidates')}, "
              f"imported={rep['counts'].get('imported')}, self_check={rep['self_check']}")
        print(f"report={rep.get('report_path')}")
        print(f"ima_queue=ima_import_queue.jsonl")
    else:
        # 演示 dry-run
        demo = [
            {"title": "公司股东出资纠纷", "url": "https://mp.weixin.qq.com/s/a", "category": "legal",
             "features": {"author_tier": 2, "platform_tier": 3, "depth": 1, "relevance": 1}},
            {"title": "AI 法律助手发布", "url": "https://example.com/b", "category": "ai-legal",
             "features": {"first_hand": 1, "depth": 1, "relevance": 1}},
        ]
        code, rep = run_pipeline(None, candidates_raw=demo)
        print(f"exit_code={code}, candidates={rep['counts'].get('candidates')}, imported={rep['counts'].get('imported')}")
