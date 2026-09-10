#!/usr/bin/env python3
"""
myrss —— 在墙外抓取 RSS，生成静态 XML，供墙内客户端订阅。

架构
----
GitHub Actions 每天运行本脚本，输出到 docs/ 并 commit 回仓库。
墙内客户端（newsboat 等）从 raw.githubusercontent.com 读取，不依赖任何第三方实例。

抓取逻辑
--------
BBC Learning English 的选择器沿用 RSSHub（github.com/DIYgod/RSSHub）
lib/routes/bbc/learningenglish.ts 中已验证的实现。
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = "https://www.bbc.co.uk"
OUT_DIR = Path(__file__).resolve().parent / "docs"
TIMEOUT = 30
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9,zh-CN;q=0.8"}

# BBC Learning English 中文版栏目。
# 只保留仍在更新的——以下栏目已停更，故移除（2026-09-10 实测）：
#   todays-phrase  今日短语  最后更新 2022-12-29
#   english-at-work 白领英语 最后更新 2019-11-01
#   phrasal-verbs  短语动词  最后更新 2025-09-30
CHANNELS: dict[str, str] = {
    "take-away-english": "随身英语",
    "english-in-a-minute": "一分钟英语",
    "authentic-real-english": "地道英语",
    "lingohack": "英语大破解",
    "q-and-a": "你问我答",
    "media-english": "媒体英语",
}

# 最新条目超过这么多天就告警——栏目可能在无声无息地停更
STALE_DAYS = 90

# 文章配套音频。BBC 把 mp3 放在 downloads.bbc.co.uk（Akamai CDN），
# 与 www.bbc.co.uk 一样在墙内可达，可以直接给客户端当 enclosure。
MP3_RE = re.compile(r'https?://downloads\.bbc\.co\.uk/[^\s"\'<>]+\.mp3')

# 日期格式候选。BBC 页面格式未知，逐个尝试；命中不了的会记进日志供排查。
DATE_FORMATS = (
    "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%Y-%m-%d",
    "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B, %Y",
)

# BBC 的 .details h3 内容形如 "Episode 260907/ 07 Sep 2026"——前半的 260907
# 是剧集编号里的日期，整串无法直接 strptime。先正则抠出后半再解析。
# （RSSHub 正是在这里出错，把 260907 当成了日期，生成 260907 年的 pubDate。）
DATE_IN_TEXT = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})")

log = logging.getLogger("myrss")


def parse_date(text: str) -> datetime:
    """解析 BBC 的日期文本；失败则退回当前时间，并把原文记进日志。"""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())

    candidates: list[str] = []
    if m := DATE_IN_TEXT.search(cleaned):
        candidates.append(f"{m.group(1)} {m.group(2)} {m.group(3)}")
    candidates.append(cleaned)

    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).replace(
                    hour=12, tzinfo=timezone.utc
                )
            except ValueError:
                continue

    log.warning("日期解析失败，回退为当前时间: %r", cleaned)
    return datetime.now(timezone.utc)


def absolute(href: str | None) -> str | None:
    if not href:
        return None
    return href if href.startswith("http") else f"{ROOT}{href}"


def fetch_detail(session: requests.Session, url: str) -> tuple[str, str | None]:
    """抓文章详情页，返回 (正文 HTML, 音频 URL)。

    正文取自 .widget-richtext；音频不在这个容器里——BBC 用单独的
    .widget-audio 渲染播放器，所以要在整页上找 mp3。
    失败不影响条目本身，只是 description/enclosure 为空。
    """
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        node = BeautifulSoup(resp.text, "html.parser").select_one(".widget-richtext")
        return (
            node.decode_contents() if node else "",
            next(iter(MP3_RE.findall(resp.text)), None),
        )
    except Exception as exc:  # noqa: BLE001 — 单条失败不该拖垮整个源
        log.warning("详情页抓取失败 %s: %s", url, exc)
        return "", None


def scrape_channel(session: requests.Session, channel: str) -> list[dict]:
    """抓一个栏目，返回条目列表。沿用 RSSHub 的选取策略。"""
    page_url = f"{ROOT}/learningenglish/chinese/features/{channel}"
    resp = session.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items: list[dict] = []

    # 头条：页面顶部大图位。
    # 注意属性值必须加引号——soupsieve 比 cheerio 严格，`[data-widget-index=4]`
    # 会抛 Malformed attribute selector（数字不是合法 CSS 标识符）。
    first = soup.select_one('[data-widget-index="4"]')
    if first and first.select_one("h2"):
        date_node = first.select_one(".details h3")
        items.append(
            {
                "title": first.select_one("h2").get_text(strip=True),
                "link": absolute(
                    first.select_one("h2 a").get("href")
                    if first.select_one("h2 a")
                    else None
                ),
                "date": parse_date(date_node.get_text(strip=True) if date_node else ""),
            }
        )

    # 其余条目：三栏列表，最多再取 10 条
    for li in soup.select(".threecol li")[:10]:
        title_node = li.select_one("h2")
        if not title_node:
            continue
        link_node = li.select_one("h2 a")
        date_node = li.select_one(".details h3")
        items.append(
            {
                "title": title_node.get_text(strip=True),
                "link": absolute(link_node.get("href") if link_node else None),
                "date": parse_date(date_node.get_text(strip=True) if date_node else ""),
            }
        )

    # 并发补详情页正文与音频
    targets = [i for i in items if i.get("link")]
    if targets:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(fetch_detail, session, i["link"]): i for i in targets
            }
            for fut in as_completed(futures):
                futures[fut]["content"], futures[fut]["audio"] = fut.result()

    if not items:
        # 选择器失效时给出足够线索，免得只能靠猜
        log.warning(
            "解析出 0 条 — 诊断: hero命中=%s, .threecol li=%d 个, 页面标题=%r",
            first is not None,
            len(soup.select(".threecol li")),
            soup.title.get_text(strip=True) if soup.title else None,
        )

    return [i for i in items if i.get("title") and i.get("link")]


def build_rss(channel: str, label: str, items: list[dict]) -> str:
    """生成 RSS 2.0。"""
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")

    page_url = f"{ROOT}/learningenglish/chinese/features/{channel}"
    ET.SubElement(ch, "title").text = f"BBC英语学习-{label}"
    ET.SubElement(ch, "link").text = page_url
    ET.SubElement(ch, "description").text = (
        f"BBC Learning English 中文版「{label}」— 由 myrss 每日镜像"
    )
    ET.SubElement(ch, "language").text = "zh-cn"
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )

    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        node = ET.SubElement(ch, "item")
        ET.SubElement(node, "title").text = it["title"]
        ET.SubElement(node, "link").text = it["link"]
        ET.SubElement(node, "guid", {"isPermaLink": "true"}).text = it["link"]
        ET.SubElement(node, "pubDate").text = format_datetime(it["date"])
        if it.get("content"):
            ET.SubElement(node, "description").text = it["content"]
        if it.get("audio"):
            # length 未知。RSS 规范要求该属性存在，填 0 各客户端都能处理。
            ET.SubElement(
                node,
                "enclosure",
                {"url": it["audio"], "type": "audio/mpeg", "length": "0"},
            )

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        rss, encoding="unicode"
    )


def write_index() -> None:
    """生成浏览页。

    按磁盘上的文件列举，而非本次抓取结果——某栏目本次失败时，
    上一次的成果仍在，这里也应该显示出来。
    """
    entries = []
    for path in sorted(OUT_DIR.glob("bbc-*.xml")):
        label = CHANNELS.get(path.stem.removeprefix("bbc-"), path.stem)
        try:
            count = len(re.findall(r"<item>", path.read_text(encoding="utf-8")))
        except OSError:
            count = 0
        entries.append(
            f'      <li><a href="{path.name}">{html.escape(label)}</a> '
            f'<span class="n">{count} 条</span></li>'
        )
    rows = "\n".join(entries)
    (OUT_DIR / "index.html").write_text(
        f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>myrss</title>
<style>
 body{{font:15px/1.7 system-ui,sans-serif;max-width:42rem;margin:3rem auto;padding:0 1rem}}
 li{{margin:.4rem 0}} .n{{color:#888;font-size:.85em}}
</style>
<h1>myrss</h1>
<p>墙外抓取、墙内订阅的静态 RSS 镜像。把 <code>raw.githubusercontent.com</code>
 链接加进 newsboat 即可。</p>
<ul>
{rows}
</ul>
<p class="n">更新时间 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</p>
</html>
""",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append", help="只抓指定栏目（可重复）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    channels = args.channel or list(CHANNELS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    # BBC 偶发瞬时 404/5xx（同一 URL 前后两次请求结果可能不同），重试几次再放弃
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=3,
                backoff_factor=1.5,
                status_forcelist=[404, 429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            )
        ),
    )
    results: list[tuple[str, str, int]] = []
    failed: list[str] = []

    for channel in channels:
        label = CHANNELS.get(channel, channel)
        try:
            items = scrape_channel(session, channel)
            if not items:
                raise RuntimeError("页面解析出 0 条，选择器可能已失效")
            slug = f"bbc-{channel}"
            (OUT_DIR / f"{slug}.xml").write_text(
                build_rss(channel, label, items), encoding="utf-8"
            )
            results.append((slug, f"BBC英语学习-{label}", len(items)))
            # 音频覆盖率一并报出来——BBC 改版把播放器挪走后，这里会先掉下来
            audio = sum(1 for i in items if i.get("audio"))
            newest = max(i["date"] for i in items)
            age = (datetime.now(timezone.utc) - newest).days
            if age > STALE_DAYS:
                log.warning(
                    "⚠️  %-24s %d 条（音频 %d），但最新一条已是 %d 天前（%s），栏目可能已停更",
                    label, len(items), audio, age, newest.strftime("%Y-%m-%d"),
                )
            else:
                log.info("✅ %-24s %d 条（音频 %d），最新 %s", label, len(items),
                         audio, newest.strftime("%Y-%m-%d"))
        except Exception as exc:  # noqa: BLE001 — 单栏目失败不影响其他栏目
            failed.append(channel)
            log.error("❌ %-24s %s", label, exc)

    write_index()

    # 清掉已从 CHANNELS 移除的栏目残留文件。
    # 注意这里是按「配置」而非「本次结果」判断——否则某个栏目一次瞬时抓取失败
    # 就会把上一次的好数据删掉。
    keep = {f"bbc-{ch}.xml" for ch in CHANNELS}
    for stale in OUT_DIR.glob("bbc-*.xml"):
        if stale.name not in keep:
            stale.unlink()
            log.info("清理残留: %s", stale.name)

    log.info("完成：%d 个栏目成功，%d 个失败", len(results), len(failed))
    if failed:
        log.warning("失败栏目: %s", ", ".join(failed))

    # 全部失败才让 job 挂掉，部分失败仍然发布已有内容
    return 1 if not results else 0


if __name__ == "__main__":
    sys.exit(main())
