# global_intelligence_watch 设计

> 状态:设计稿,待 S3-E 真实试运行结束后立项
> 目的:在现有 8 次持仓推送之外,新增每小时全量新闻/宏观情报刮削与分析推送

---

## 1. 定位

`global_intelligence_watch` 是一条与持仓推送并行的情报线,不是持仓推送的附属。

- 保持现有 8 次定时持仓推送不变
- 新增每小时一次的全量新闻/宏观数据刮削、事件聚合、分析与产物生成
- 覆盖 VIX、原油、美债、黄金、比特币、美元指数等全市场信号
- 允许对非持仓标的给出买入/卖出/观察建议
- 夜间正常放行,不受 quiet_hours 限制
- 产物复用 `ScheduledAnalysisRun v1` 格式,内容聚焦新闻与宏观分析

---

## 2. API 组合与免费限额

| 用途 | API | 免费限额 | 说明 |
|---|---|---|---|
| 新闻主源 | GNews API | 用户已有 key | 比 RSS 更低延迟,支持关键词/语言/时间窗口筛选 |
| 新闻辅助 | Google News RSS | 无限制 | 按关键词生成 RSS,无需 key,作为 GNews 降级 |
| 一手新闻补充 | Finnhub Market News | 60 次/分钟 | 已持有 key,可覆盖 general/commodity/crypto |
| 美股/ETF 行情 | Finnhub Quote | 60 次/分钟 | 已接入,支持 us 与 crypto 市场 |
| 比特币 | Binance Spot API | 1200 weight/分钟 | 已接入,无需 key |
| 美债收益率 | FRED API | 约 120 次/日 | 用户单独提供 key,免费 |
| 黄金/原油/美元指数 | 优先用 ETF 代理:GLD/USO/UUP | 走 Finnhub Quote | 如 Finnhub 不支持部分现货,用公开网页抓取降级 |

---

## 3. 轮询频率与抓取数量

假设每小时运行一次 `global_intelligence_watch`:

- GNews API:按 6-8 个关键词各抓 top 10-15 条,每小时 6-8 次请求
- Google News RSS:无限额,按 6-8 个关键词各抓 top 10-15 条,作为 GNews 降级
- Finnhub Market News:每小时 1-2 次请求,类别包括 general 与 commodity/crypto
- Finnhub Quotes:每小时约 12-15 个标的,包括 SPY/QQQ/IWM/VIXY/GLD/USO/UUP 与持仓 5 只
- Binance:每小时 1-2 次请求,覆盖 BTCUSDT,可选 ETHUSDT
- FRED:因 120 次/日限制,美債 10Y/2Y/美元指数改为每 4 小时集中抓取一次,即每天 6 次,每次 3 个 series

每小时总请求约 25-30 次,远低于 Finnhub 60 次/分钟上限。
24 小时运行可行,但 FRED 必须独立限频。

---

## 4. 运行时段

排除低价值时段:北京时间 02:00-07:00 跳过。

理由:美股收盘后、亚洲早盘前,新闻与价格变动极少,运行产出价值低。
实际运行窗口:每天 19 次,北京时间 07:00-次日 02:00。

---

## 5. 覆盖标的

| 类别 | 标的/代理 | 数据源 |
|---|---|---|
| 美股宽基 | SPY, QQQ, IWM | Finnhub Quote |
| 波动率 | VIXY | Finnhub Quote |
| 黄金 | GLD | Finnhub Quote |
| 原油 | USO | Finnhub Quote |
| 美元指数 | UUP | Finnhub Quote |
| 比特币 | BTCUSDT | Binance |
| 美债 10Y | FRED DGS10 | FRED API |
| 美债 2Y | FRED DGS2 | FRED API |
| 原持仓 | XLE, NVDA, ITA, NEM, SGOV | Finnhub Quote |

已验证 Finnhub 支持 GLD/USO/UUP/VIXY 等 ETF 实时报价,无需额外网页抓取降级。
如未来某个标的失效,在 data_quality 中显式标注并启用公开网页抓取降级。

---

## 6. 数据保留策略

存储根目录:`.local/news_intelligence/`

- `hourly/YYYY-MM-DD/HHMMSS.json`:原始每小时快照,保留 7 天
- `events/YYYY-MM-DD/event_cluster.json`:聚合后事件簇,保留 7 天
- `signals/YYYY-MM-DD/signal.json`:生成的操作建议摘要,保留 7 天
- `archive/`:超过 7 天的数据自动 gzip 压缩并移入,保留至 30 天
- 超过 30 天:删除

归档任务由同一 `global_intelligence_watch` session 在生成产物后执行,或独立每日清理 cron 执行。

---

## 7. 产物内容

`global_intelligence_watch` 产物为 `ScheduledAnalysisRun v1` 格式,内容聚焦:

- 本小时采集量与源状态
- 识别的重大事件簇(地缘、货币政策、财报季、科技、能源、宏观数据)
- 市场影响摘要(股市/债市/油市/汇市/中国资产)
- 非持仓操作信号(买入/卖出/观察),附带目标方向、周期、证伪点、风险
- 与 watchlist 持仓的关联提示
- `data_quality`

风格:平衡、非保守。利好信号允许明确建议买入,利空信号允许明确建议卖出/减持。每条建议必须附带证伪点与风险来源。

---

## 8. 与现有架构关系

- 不修改现有 8 次持仓推送 session
- 新增 `IntelligenceHarvester` 与 `IntelligenceAnalyzer` 两个组件
- 新增 `global_intelligence_watch` 配置项到 `scheduled_sessions.json`
- 不修改 `AnalysisContext` 主结构,可选新增 `recent_intelligence_events` 字段供持仓 session 读取
- 产物写入 `.local/scheduled_runs/` 与 `.local/news_intelligence/`
- 不自动交易、不自动写建议/执行/预测台账

---

## 9. 新增组件

### IntelligenceHarvester

- 每小时并行采集新闻 RSS、Finnhub Market News、宏观数据、行情快照
- 对新闻去重、打标签、计算 urgency、记录来源与 timestamp
- 将原始数据写入 `.local/news_intelligence/hourly/`

### IntelligenceAnalyzer

- 读取最近 6 小时原始数据
- 事件聚合:同一主题的多条新闻合并为事件簇
- 主题检测:地缘、货币政策、财报季、科技、能源、宏观数据
- 市场影响评估:对股市、债市、油市、汇市、中国资产的方向判断
- 生成操作建议:买入/卖出/观察,附带目标方向、周期、证伪点、风险

### NewsIntelligenceStore

- 管理 `.local/news_intelligence/` 目录结构
- 提供写入、读取、归档、删除接口
- 按 7 天/30 天策略自动释放

---

## 10. 验收标准

- 每小时自动生成一份产物
- 7 天后自动清理过期数据
- 重大事件被正确识别并聚合
- 非持仓标的可以给出买入/卖出建议
- 夜间也能放行推送
- 不影响现有 498 项测试
- 24 小时运行不触发任何 API 免费限额

---

## 11. 未解决依赖

- 用户提供 FRED API key
- 确认 Finnhub 是否支持 GLD/USO/UUP 等 ETF 实时报价
- 若不支持,需实现公开网页抓取降级源并标注 data_quality
