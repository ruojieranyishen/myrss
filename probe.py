#!/usr/bin/env python3
"""一次性探针：查 BBC Learning English 文章页里的音频资源结构。

bbc.co.uk 在墙内不可达，只能借 GitHub runner 跑。
用完即删。
"""

import re
import sys

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9,zh-CN;q=0.8"}
ROOT = "https://www.bbc.co.uk"


def main() -> int:
    session = requests.Session()

    # 先拿栏目页，取第一篇文章链接
    listing = f"{ROOT}/learningenglish/chinese/features/take-away-english"
    html = session.get(listing, headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    link = None
    for li in soup.select(".threecol li"):
        a = li.select_one("h2 a")
        if a and a.get("href"):
            link = a["href"]
            break
    if not link:
        print("!! 没找到文章链接")
        return 1
    if not link.startswith("http"):
        link = ROOT + link

    print(f"文章页: {link}\n")
    page = session.get(link, headers=HEADERS, timeout=30).text
    psoup = BeautifulSoup(page, "html.parser")

    print("=== <audio> 标签 ===")
    for a in psoup.find_all("audio"):
        print(" ", a.attrs)
        for s in a.find_all("source"):
            print("   source:", s.attrs)

    print("\n=== 所有含 .mp3 的字符串（去重，前 20）===")
    found = sorted(set(re.findall(r'https?://[^\s"\'<>]+\.mp3', page)))
    for u in found[:20]:
        print(" ", u)
    if not found:
        print("  (无)")

    print("\n=== 含 audio/player/media 的 class 名（去重，前 30）===")
    classes = set()
    for el in psoup.find_all(attrs={"class": True}):
        for c in el.get("class", []):
            if re.search(r"audio|player|media|listen|sound", c, re.I):
                classes.add(c)
    for c in sorted(classes)[:30]:
        print(" ", c)

    print("\n=== data-* 属性里含 audio/mp3 的（前 20）===")
    n = 0
    for el in psoup.find_all(attrs=True):
        for k, v in el.attrs.items():
            if k.startswith("data-") and isinstance(v, str) and re.search(r"mp3|audio", v, re.I):
                print(f"  {el.name} {k}={v[:120]}")
                n += 1
                if n > 20:
                    break
    if n == 0:
        print("  (无)")

    print("\n=== 正文容器 .widget-richtext 里是否有播放器 ===")
    node = psoup.select_one(".widget-richtext")
    if node:
        inner = node.decode_contents()
        print(f"  长度 {len(inner)}")
        for pat in ("audio", "mp3", "player", "iframe"):
            print(f"  含 {pat!r}: {len(re.findall(pat, inner, re.I))} 次")
    else:
        print("  未找到 .widget-richtext")

    return 0


if __name__ == "__main__":
    sys.exit(main())
