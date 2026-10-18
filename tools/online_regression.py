"""真实在线网页访问复测（P21–P23 网络层端到端验收）。

背景：历史会话标注"出站网络 TCP 443 被阻断"，2026-10-18 实测该限制
已解除。本脚本用项目自身的网络层（HttpClient + render_document_from_url）
访问真实 URL，验证：
  1. TLS 握手 + 证书校验（ssl.create_default_context）
  2. HTTP/1.1 GET + 重定向 + gzip 解码
  3. HTML 解析 + CSS 级联 + 布局 + 渲染到像素
  4. 不崩、产出合理尺寸位图、提取标题 / 链接数

纯只读访问，不修改任何项目文件，也不写缓存。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

# 把项目根目录加进 sys.path，使本脚本可从 tools/ 直接运行。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from toy_browser.network import net
from toy_browser.app import browser


TARGETS = [
    "https://example.com",
    "https://www.baidu.com",
    "https://www.iana.org/domains/reserved",
]


def probe_one(url: str, client: "net.HttpClient") -> dict:
    out = {"url": url, "ok": False}
    t0 = time.time()
    try:
        resp = client.get(url, follow_redirects=True)
        out["status"] = resp.status
        out["final_url"] = resp.final_url
        out["content_type"] = resp.content_type
        out["ok"] = resp.ok and resp.is_html
    except Exception as e:
        out["ok"] = False
        out["net_error"] = f"{type(e).__name__}: {e}"
    out["net_ms"] = round((time.time() - t0) * 1000, 1)

    if not out.get("ok"):
        return out

    # 端到端渲染
    rt0 = time.time()
    try:
        bmp = browser.render_document_from_url(
            url, width=900, height=600,
            load_subresources=False, client=client,
        )
        out["render"] = True
        out["bmp_size"] = f"{bmp.width}x{bmp.height}"
        out["render_ms"] = round((time.time() - rt0) * 1000, 1)
        # 提取渲染上下文
        html, _ = net.fetch_html(url, client=client, follow_redirects=True)
        from toy_browser.document import dom as dom_mod

        def _count_nodes(n):
            return 1 + sum(_count_nodes(c) for c in n.children)

        tree = dom_mod.parse(html)
        out["dom_nodes"] = _count_nodes(tree)

        def _title(n):
            if n.tag == "title":
                return n.text_content().strip()
            for c in n.children:
                r = _title(c)
                if r:
                    return r
            return ""
        out["title"] = _title(tree)
    except Exception:
        out["render"] = False
        out["render_error"] = traceback.format_exc(limit=3).strip()
    return out


def main() -> int:
    client = net.HttpClient(timeout=15, verify_tls=True)
    results = [probe_one(u, client) for u in TARGETS]
    client.close_pool()

    print("=" * 70)
    print("toy-browser-py 真实在线网页访问复测（P21–P23 端到端）")
    print("=" * 70)
    passed = 0
    for r in results:
        mark = "PASS" if r.get("ok") and r.get("render") else "FAIL"
        if mark == "PASS":
            passed += 1
        print(f"\n[{mark}] {r['url']}")
        print(f"      HTTP {r.get('status', '?')} "
              f"({r.get('content_type', '?')}) "
              f"network {r.get('net_ms', '?')}ms")
        if "net_error" in r:
            print(f"      NET ERROR: {r['net_error']}")
        if r.get("ok"):
            print(f"      final_url={r.get('final_url')}")
            print(f"      dom_nodes={r.get('dom_nodes', '?')} "
                  f"title={r.get('title', '?')!r}")
        if "render" in r:
            if r["render"]:
                print(f"      rendered to {r.get('bmp_size')} "
                      f"in {r.get('render_ms')}ms")
            else:
                print(f"      RENDER ERROR:\n{r.get('render_error')}")
        print("-" * 70)

    print(f"\n结果：{passed}/{len(results)} 端到端通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
