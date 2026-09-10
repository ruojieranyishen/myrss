# myrss

墙外抓取、墙内订阅的静态 RSS 镜像。

## 为什么需要它

BBC 官方 RSS 的 CDN `feeds.bbci.co.uk` 在大陆网络不可达（实测连接超时，
HTTP 000）。GitHub Actions 的 runner 在墙外，可以直连；抓取结果作为静态 XML
提交回本仓库后，墙内客户端通过 `raw.githubusercontent.com` 读取——
实测 0.3 秒，且不依赖任何第三方 RSS 代理实例。

注：BBC **主站** `www.bbc.co.uk` 和音频 CDN `downloads.bbc.co.uk` 其实是可达的
（实测 HTTP 200），所以本脚本在本机也能跑通。走 GitHub Actions 是为了稳定性：
GFW 策略会变，runner 恒在墙外。

## 使用

订阅地址形如：

```
https://raw.githubusercontent.com/ruojieranyishen/myrss/main/docs/bbc-take-away-english.xml
```

全部可用源见 [`docs/index.html`](docs/index.html)。

newsboat 里直接把这行加进 `urls` 文件即可。

## 当前源

| 文件 | 栏目 | 音频 |
|------|------|------|
| `bbc-take-away-english.xml` | 随身英语 | ✅ |
| `bbc-authentic-real-english.xml` | 地道英语 | ✅ |
| `bbc-q-and-a.xml` | 你问我答 | ✅ |
| `bbc-media-english.xml` | 媒体英语 | ✅ |
| `bbc-english-in-a-minute.xml` | 一分钟英语 | ❌ 视频栏目 |
| `bbc-lingohack.xml` | 英语大破解 | ❌ 视频栏目 |

内容为中英对照（BBC 中文版），含词汇标注与英文原文。

有音频的条目带 `<enclosure type="audio/mpeg">`，指向
`downloads.bbc.co.uk` 上的 mp3，客户端可直接流播。

以下栏目已停更，不再抓取（脚本内置 `STALE_DAYS=90` 告警，栏目再次停更时
会在 Actions 日志里提示）：

| 栏目 | 最后更新 |
|------|----------|
| 今日短语 | 2022-12-29 |
| 白领英语 | 2019-11-01 |
| 短语动词 | 2025-09-30 |

## 更新机制

`.github/workflows/fetch.yml` 每天 22:23 UTC（北京时间 06:23）运行一次，
也可在 Actions 页面手动触发。单栏目抓取失败不会中断整体流程，
已有内容照常发布。

## 本地运行

```bash
pip install -r requirements.txt
python fetch.py                    # 全部栏目
python fetch.py --channel lingohack -v   # 单栏目，带调试日志
```

本机实测可跑通（6 个栏目全部成功），因为 `www.bbc.co.uk` 在墙内可达。
但官方定时任务仍跑在 GitHub Actions 上——不依赖本机开机，也不受本机网络波动影响。

## 抓取逻辑来源

BBC Learning English 的页面选择器沿用
[RSSHub](https://github.com/DIYgod/RSSHub) 的
`lib/routes/bbc/learningenglish.ts`（已验证有效的实现），
并修正了其日期解析缺陷。

## 版权

本仓库仅镜像 BBC 公开 RSS 的条目（标题、链接、摘要），供个人学习使用。
内容版权归 BBC 所有。
