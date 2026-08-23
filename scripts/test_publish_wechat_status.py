#!/usr/bin/env python3
"""Publish Layer 微信原文 URL 与正文验证状态语义回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publish"))
from publish_layer import sanitize

MP = "https://mp.weixin.qq.com/s?__biz=test&mid=1"

cases = [
    ("url_only", {"title": "微信稿", "url": MP, "source_channel": "weread", "source_access_verified": False},
     {"original_url_obtained": True, "original_url_type": "wechat", "content_access_verified": False, "verification_method": "weread", "url": MP}),
    ("official_mirror", {"title": "微信稿", "url": MP, "source_channel": "weread", "source_access_verified": False, "mirror_urls": ["https://example.gov.cn/article"]},
     {"original_url_obtained": True, "content_access_verified": False, "verification_method": "official_mirror", "url": MP}),
    ("direct", {"title": "微信稿", "url": MP, "source_channel": "weread", "source_access_verified": True},
     {"original_url_obtained": True, "content_access_verified": True, "verification_method": "direct"}),
    ("title_only", {"title": "仅标题", "source_channel": "weread"},
     {"original_url_obtained": False, "verification_method": "unverified"}),
]
for name, raw, expected in cases:
    got = sanitize(raw)
    for key, value in expected.items():
        assert got.get(key) == value, f"{name}: {key}={got.get(key)!r}, expected {value!r}"
    if name == "official_mirror":
        assert got["mirror_urls"] == ["https://example.gov.cn/article"]
        assert got["url"] == MP
print("4/4 微信 URL/正文验证语义测试通过")
