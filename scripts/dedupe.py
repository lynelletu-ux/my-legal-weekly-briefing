#!/usr/bin/env python3
"""候选池去重工具

用法：
    python3 dedupe.py < candidates.jsonl > deduped.jsonl
    # 或直接调用 dedupe_items(items, threshold=0.85) -> list

输入：每行 JSON {"title":..., "url":..., "source":...}
输出：去重后候选列表（保留首次出现）
"""
import json, sys, re
from pathlib import Path

STOPWORDS = set("的 了 是 在 与 和 及 对 关于 最高人民法院 人民法院 法院 公布 发布 关于 印发 通知".split())


def normalize_title(title):
    """去停用词 + 去标点 + 小写化，用于相似度比较。"""
    if not title:
        return ""
    t = re.sub(r'[^\w\u4e00-\u9fff]', '', title)
    for w in STOPWORDS:
        t = t.replace(w, '')
    return t.lower()


def canonical_url(url):
    """URL 规范化：去参数、去 trailing slash、统一 scheme。

    注意：mp.weixin.qq.com 链接保留 query（__biz/mid/sn 是文章身份标识，
    砍掉会把不同文章误判为同一 URL）。"""
    if not url:
        return ""
    u = url.split('#')[0].rstrip('/').lower()
    if "mp.weixin.qq.com" in u:
        return u
    return u.split('?')[0]


def similarity(a, b):
    """基于字符 bigram 的 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s) - 1))
    sa, sb = bigrams(a), bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedupe_items(items, threshold=0.85):
    """对候选列表去重。同一 URL 或标题相似度 >= threshold 视为重复。"""
    seen = []
    result = []
    for item in items:
        title = normalize_title(item.get('title', ''))
        url = canonical_url(item.get('url', ''))
        dup = False
        for s in seen:
            if url and s['url'] == url:
                dup = True
                break
            if title and s['title'] and similarity(title, s['title']) >= threshold:
                dup = True
                break
        if not dup:
            seen.append({'title': title, 'url': url})
            result.append(item)
        else:
            # 保留同一案例在案例库、法院官网、公众号或转载页的追溯地址，
            # 但只输出一个主记录进入评分和精选。
            for existing in result:
                et = normalize_title(existing.get('title', ''))
                eu = canonical_url(existing.get('url', ''))
                if (url and eu == url) or (title and et and similarity(title, et) >= threshold):
                    # 主记录优先保留权威来源，但把专项案例标记、完整特征和时间窗口
                    # 从重复渠道合并进来，避免官方/公众号先出现导致案例画像丢失。
                    for key in ("practice_case", "practice_domain", "practice_domain_confidence", "case_window", "carryover", "discovery_stage", "features"):
                        if item.get(key) is not None and (not existing.get(key) or key == "features" and len(item.get(key) or {}) > len(existing.get(key) or {})):
                            existing[key] = item[key]
                    alternates = list(existing.get('alternate_urls') or [])
                    if item.get('url') and item.get('url') != existing.get('url') and item.get('url') not in alternates:
                        alternates.append(item.get('url'))
                    for alt in item.get('alternate_urls') or []:
                        if alt not in alternates and alt != existing.get('url'):
                            alternates.append(alt)
                    if alternates:
                        existing['alternate_urls'] = alternates
                    break
    return result


if __name__ == '__main__':
    items = []
    for line in sys.stdin:
        line = line.strip()
        if line:
            items.append(json.loads(line))
    for it in dedupe_items(items):
        print(json.dumps(it, ensure_ascii=False))
