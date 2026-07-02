# 数据可靠性审计(2026-07-02)— 证据留存

> ARCHIVED 2026-07-02:审计产物,结论已吸收进 `PLAN.md` v2.8 与 `EXECUTION_PLAN.md` Phase D 任务卡;本文**无准则效力**,仅作证据留存与追溯依据。执行 Agent 以 EXECUTION_PLAN D 组为准,勿按本文行事。

## 0. 审计链路

三方交叉:① 外部 GPT 对系统的实测审计(5 条结论);② Claude 对 5 条结论的独立核验(代码逐行审查 + 单元级代码复现 + `.local/` 运行残留物取证);③ 现场网络诊断(`.local/verify_data_sources.py` 于用户 Mac 真实网络执行)。三方结论互相印证,无一条被推翻。

## 1. 五条审计结论的核验判定

| # | 结论 | 判定 | 关键证据 |
|---|------|------|---------|
| 1 | 美股/加密只有 Finnhub 单源 | 成立,且 fallback 配置形同虚设 | `engine.yaml:29-30` `fallback.us:[finnhub]` 备用=主源自身,`_pick_fallback_provider` 排除同名后候选为空;crypto 无 fallback 键;`markets.json` us/crypto 主源均 finnhub |
| 2 | A 股历史仅东方财富,当次 6/6 失败,单 bar 指标仍标 ok | 成立(代码复现 + 磁盘取证) | `history_provider.py` a 市场只路由 Eastmoney 无降级链;`.local/history/a_*.json` 六文件各 1 条 realtime 记录 vs `us_AAPL.json` 61 条 provider 日 K;单 bar 复现:`TechnicalIndicators.calculate()` 全 None + `data_points:1` → `_collect_technical_indicators`(context_builder.py:614)判字典真值 → `status:"ok"` → `_indicator_quality` → `ok/fresh` |
| 3 | 新闻源少、偏泛财经、缺一手源 | 结构性缺口成立;"仅两源贡献"为当次偶发 | 配置仅 3 个启用 RSS 源,无公告/财报/监管源;实测三源均有产出(中新网 30 / Google News 100 / Yahoo Finance 42 条) |
| 4 | 宏观全部为 Yahoo 报价代理,无权威统计 | 成立 | `macro_data.py:85` `_YAHOO_TICKERS` 仅 6 个市场报价;无 CPI/就业/PMI/央行/日历 |
| 5 | 行情无源时间,质量层以生成时间冒充 as_of | 成立,贯穿全链路 | `context_builder.py:387` `"as_of": generated_at if quote_count else None`(已执行确认相等);三个 Quote 构造点(tencent_a.py:74 / eastmoney_a.py:71 / finnhub_quote.py:167)均不写 source/as_of;`history_cache.record()` 的 `if quote.as_of:` 跨天保护为死代码;freshness 恒 fresh |

## 2. 核验新增发现(审计原文未覆盖)

1. warm 全部失败也执行 `self._history_warmed = True`(engine/__init__.py:552-564),进程内永不重试;warm 返回值被丢弃,`data_quality` 无回填节点——系统对"历史回填 6/6 失败"零上报。
2. `CompositeMacroProvider` "第一个提供者有任意字段即整体返回":Yahoo 只回 1 个字段也会短路 static_config 兜底。
3. Tencent 实时用 `s_` 简版格式(`_build_symbol`),简版响应本身无时间字段——补 as_of 需换完整格式或如实留 None。
4. Eastmoney 实时请求 fields(eastmoney_a.py:46)未含 f86 时间戳字段,补一个字段即可取到源时间。
5. 当日 01:00 UTC test_run 快照 A 股指标齐全、09:32 UTC 缓存只剩单 bar → eastmoney 日 K 为**间歇性失败**,非接口永久变更;单源不可接受的直接论据。

## 3. 现场网络诊断汇总(2026-07-02,用户 Mac)

```
eastmoney 日K:   0/6   全部 RemoteDisconnected(40-60ms 即断)
腾讯 日K:        6/6   60-61 bars —— A股第二历史源选型定案
新浪 日K:        接口通但每支仅 2 bars(窗口异常)—— 不选
Yahoo 日K:       0/5   全部 HTTP 429 —— 美/crypto 历史主链路当前已断
Yahoo 宏观:      0/6   全部 HTTP 429 —— 宏观主链路当前已断
新闻 RSS:        中新网 30 条 / Google News 100 条 / Yahoo Finance 42 条(36kr 停用态仍可达 30 条)
Finnhub:         quote 正常(AAPL c=294.38, t=1782936000=2026-07-01 20:00 UTC);/calendar/earnings 可用
一手事件源连通:  巨潮 / SEC EDGAR full-index / EDGAR submissions / 上交所 / Binance / FRED fredgraph.csv 全部可达
```

诊断脚本:`.local/verify_data_sources.py`(可重复执行,Phase D 各任务卡的【验证前提】与出口验收均引用它)。

## 4. 结论与去向

系统骨架(降级记录、数据质量层、建议回看闭环)设计正确,但数据质量层当前会把"瞎了"报告成"健康";修复方向为"先诚实,再博学"。全部整改任务已按仓库执行协议落卡:

- D0 可信度硬化(4 卡):指标按 data_points 判级、三 Provider 补源时间戳、回填结果显性上报、fallback 语义修正。
- D1 关键源冗余(3 卡):A 股腾讯第二历史源、美/crypto 第二行情与历史源(Finnhub candle 需实测门槛,否则 Stooq;crypto 走 Binance)、FRED 权威宏观 + 按字段降级合并。
- D2 第一方事件源(3 卡):Finnhub 财报日历、SEC EDGAR/巨潮公告、持仓定向新闻。

见 `EXECUTION_PLAN.md` D 组;方向级决定见 `PLAN.md` v2.8 决策日志 2026-07-02 条目。
