# 在线数据与市场研判验收 — 2026-10-19

## 结论与范围

公开市场研究链路已取得真实在线样本：行情/历史 → 技术指标/新闻/宏观 → 中美市场 LLM 研判。
情报聚类与独立报告模型也完成真实请求。它可用于带来源、时间和缺口的辅助研究；
不能解释为所有供应商可用、输出每句话自动正确、个性化交易建议已验收或 NAS 已部署。

使用沪深300、黄金ETF、AAPL、SPY 等固定公开标的，不读取真实资产资料。
账户、配置、缓存和运行产物在新建的仓库外目录；无消息发送、金融记忆写入、订单、
cron/NAS 变更或提交推送。HARDEN-03 与此前独立未提交修改继续保留。

## 在线数据源清单

38 个检查项：30 项返回样本、2 项停用、2 项空样本、1 项失败、2 项缺凭据、1 项用户跳过。
“返回样本”不等于全覆盖或实时；每个来源的时间仍须单独解释。RSS 每源最多5条，历史通常60条。
下面记录完整一轮的原始状态，后续定向复查不覆盖原记录。

| 检查项 | 说明 | 本轮状态 | 样本数 |
| --- | --- | --- | --- |
| CnMacroProvider | — | 返回样本 | 10 |
| FredMacroProvider | — | 返回样本 | 7 |
| YahooFinanceMacroProvider | — | 返回样本 | 6 |
| calendar_akshare_earnings | — | 空样本 | 0 |
| calendar_finnhub_earnings | — | 返回样本 | 1 |
| calendar_static_config | — | 返回样本 | 3 |
| cninfo_filings | — | 返回样本 | 3 |
| fund_nav | — | 返回样本 | 1 |
| gnews | — | 未配置 | — |
| history_BinanceKLineProvider | — | 返回样本 | 60 |
| history_EastmoneyKLineProvider | — | 空样本 | 0 |
| history_NasdaqKLineProvider | — | 返回样本 | 60 |
| history_TencentKLineProvider | — | 返回样本 | 61 |
| history_YahooKLineProvider | — | 返回样本 | 60 |
| intelligence_finnhub | — | 返回样本 | 9 |
| intelligence_google_rss | — | 返回样本 | 3 |
| quote_binance | — | 返回样本 | 1 |
| quote_eastmoney | — | 请求失败 | — |
| quote_finnhub | — | 返回样本 | 1 |
| quote_polygon | — | 未配置 | — |
| quote_tencent | — | 返回样本 | 1 |
| rss_0 | 中新网财经 | 返回样本 | 5 |
| rss_1 | Google News 中文财经 | 返回样本 | 5 |
| rss_10 | Google News 英文宏观 | 返回样本 | 5 |
| rss_11 | Google News 加密 | 返回样本 | 5 |
| rss_12 | Google News 中国政策 | 返回样本 | 5 |
| rss_13 | Google News 地缘冲突 | 返回样本 | 5 |
| rss_14 | 36kr | 停用 | — |
| rss_2 | Yahoo Finance | 返回样本 | 5 |
| rss_3 | Fed Press Releases | 返回样本 | 5 |
| rss_4 | 证监会发布 | 停用 | — |
| rss_5 | EIA Today in Energy | 返回样本 | 5 |
| rss_6 | Oilprice.com | 返回样本 | 5 |
| rss_7 | Al Jazeera | 返回样本 | 5 |
| rss_8 | SCMP | 返回样本 | 5 |
| rss_9 | Google News Reuters宏观 | 返回样本 | 5 |
| sec_edgar | Contact-bearing SEC User-Agent not configured; user requested skip. | 按用户要求跳过 | — |
| watchlist_news | — | 返回样本 | 6 |

### 保留的明确缺口

- 东方财富行情完整轮及定向复查失败；历史完整轮为空、复查返回样本，表现为间歇性故障。
  主流程本轮由腾讯/Finnhub提供4个标的行情，历史和技术指标均可用；不宣称东方财富恢复稳定。
- Polygon、GNews 缺凭据，未尝试虚构密钥。现有主源可用，这两项是可选备用/补充；
  想补齐对应覆盖时再通过本地凭据渠道配置，不必把密钥发到聊天中。
- SEC 按用户明确要求跳过，真实联系邮箱/User-Agent 未配置；公告覆盖缺口保留。
- CSRC 旧 URL 返回 HTML 而非 RSS，已停用。Google 新闻政策报道只是间接补充，不能冒充官方公告。
- AkShare 财报日历样本为空，不能据此宣称未来 A 股财报日覆盖通过；没有结果不等于没有事件。
- 商务部社融接口返回的最新期间为2026-04，已超龄；主分析拒绝使用，保持宏观 partial。
- 静态事件日历并非实时采集服务，当前仅含2026年日程。人工核对近期节点：
  [FOMC 官方日历](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)、
  [BLS 2026 日程](https://www.bls.gov/schedule/2026/)；10/27–28 FOMC、11/6就业、11/10 CPI相符。
  苹果11/2日期来自供应商，仅按估计展示，未作为公司确认日程验收。

## 已修复的实证问题

1. Yahoo RSS 404：改用实测可用的标普500新闻 feed（不是整个市场完整新闻覆盖）。
   RSS 的HTTP错误、解析错误及HTML伪装不再静默变成成功空列表，观察列表聚合保留错误。
2. 中国宏观的过时报告/字段名已替换为在线实证字段；主宏观流水线接入独立 `cn_` 字段，
   不覆盖美国指标。人民币中间价改用SAFE官方表格、按每100美元换算并保留来源日期，
   不再拿离岸市场报价冒充中间价。声明HTML解析依赖并更新锁文件。
3. 移除 `DTWEXBGS`→DXY 的错误映射；优先使用Yahoo市场报价，FRED保留匹配字段的备用与官方统计。
   月度统计与市场行情按各自观测时间进入统一快照，超龄社融明确排除。
4. 隔离运行的fallback日志遵循指定本地目录；公共快照重读恢复新闻/事件的日期类型，支持复查。
5. 情报抓取真正并发，区分缺配置、空、失败、部分成功；RSS子源失败保留。
   模型分析去除重复、超7天、未来超过60秒及缺日期的新闻；来源时间不再冒充模型运行时间。
   无效引用拒绝，不能用任意前三条新闻假装证据；受影响市场从模型提供的情绪维度读取。
   不将 reasoning_content 或截断响应当成完整最终答案。
6. 修正情报报告模型名与真实 `/models` 清单不一致的问题：本地服务当前为 `glm-5.3-flash`。
   实际报告返回236字；这只是无账户报告生成测试，不是飞书交付验收。
7. 市场研判约束强调名义/实际收益率、期货/现货、估值缺失、市场范围与供应商估算日期。
   对本次实测出现的“无账户却降低敞口”及“黄金期货标成现货金”加入保守拦截，
   拒绝原提案并保留原始响应，不改写模型判断。这是有限规则，不是通用语义事实检查器。

## 真实模型验收

- 初轮格式/引用契约通过，但人工发现DXY输入语义错误、实际利率混淆、无证据“错杀”结论，
  **不将初轮作为最终内容验收通过**。后续修复后使用同一公开快照复测，保留各轮证据。
- 最终 A 股样本 `cn-outlook-recheck.json`：正确区分超卖与低估，说明估值/盈利证据缺失，
  名义收益率标注正确；在最终拦截器上回放未报错。
- 美股中间样本 `us-outlook-recheck.json`：虽契约通过，但夹带降低暴露、现货金两种错误；
  新拦截器回放确认两项均拒绝。最终 `us-outlook-final.json` 实际重新请求成功，
  改用方向性研究，GC=F明确为期货，AAPL财报注明供应商估计，actions/hold_decisions为空。
- 抽核快照中的主要数字：SPY763.25、AAPL343.46（10/16收盘），沪深3004174.53、
  黄金ETF8.553（10/19来源观测），DXY101.913、10年名义收益率5.098%、VIX18.64；
  对应MA、RSI和布林阈值均可回溯。不能把阈值当作未来价格事实。
- 情报最终实际输入8条新闻（12条原始样本剔除4条重复/过期），输出5个事件簇、0条信号，
  `analysis_mode=llm`；未强行凑交易信号，没有落入规则备用。
- **内容验收为有限样本人工核对，不是全面自动事实认证**：最终美股限制段仍把A股观测称为
  “盘中”，而08:14/08:15 UTC已是正常收盘之后；保留准确时间戳，以其为准。
  类似市场时段措辞、归因、置信度与方向一致性仍需人工审阅。新闻摘要也不等于阅读全文验证。
  当前没有收益/预测质量长期评价，不能以接口和单测通过证明投资结论正确。

## 验证与复现

- 本轮所测快照全量测试：1921 passed / 7 skipped，`/tmp/stocks-harden04-pytest-final.log`。
- 修改Python文件Ruff通过；最终全仓40条非本轮诊断（原39条，加并行MCP修改的unused json），不宣称全仓Ruff通过。
  收尾检测到其他工作新增mcp_server.py/test_mcp_server.py，已原样保留；本轮计数不代表这些后续改动已验证。
- compileall、diff whitespace检查通过；SAFE解析定向测试5通过，依赖锁解析成功。
- 在线完整轮执行 `scripts/check_online_market.py --output-dir <仓库外新目录> --with-llm`。
  后续只定向复查失败源/修复后的模型路径，没有反复扫描全部来源。
- 命令成功退出只说明诊断已完成，必须读取 `health.json`，不能把退出码当所有源健康。
- `--with-llm` 使用已有配置调用两次市场研判和一次情报分析，可能产生API费用。
  缺少密钥时返回缺口；不会请求账户资料或发送报告。

证据根目录：`/var/folders/ws/0nm32g8s78s72wsc2pt5l6xw0000gn/T/stocks-harden04-final-hj75th26/online`

关键文件：`sources/health.json`、`market/context.json`、`market/cn-outlook-recheck.json`、
`market/us-outlook-final.json`、`narrative-guard-replay.json`、`intelligence-final/analysis.json`、
`eastmoney-recheck.json`、`report-render-endpoint.json`、`report-render.txt`。
原始运行日志：`/tmp/stocks-harden04-online.log`；定向最终日志：
`/tmp/stocks-harden04-market-final.log`、`/tmp/stocks-harden04-intel-final.log`。
临时目录可能被系统清理；本报告保留结论和复现方法，不提交含完整提示的运行产物。

## 尚未验收的产品能力

真实账户核对、真实费用预算、组合约束投影、新标的买入、生产定时交付与NAS部署未因此完成。
本阶段无需真实账户资料；后续接入仍先只读核对，再逐项确认写入。
