# README.md - Personal Investment Advisor

这个系统的核心，不是“收集信息然后转发”。

它的核心是：

- 维护用户长期金融记忆
- 维护用户投资偏好与约束
- 读取市场新闻与基础行情
- 提供最小脚手架与健壮兜底
- **主要依靠 LLM 基于完整上下文给出个人投资建议**

换句话说：

**没有 LLM 的判断能力，这个系统就只剩信息搬运；那没什么意义。**

## 系统定位

这是一个运行在 OpenClaw 内的 **personal investment advisor**。

它要解决的问题是：

- 用户的资产结构是什么
- 用户的风险偏好和约束是什么
- 当前市场对这套结构意味着什么
- 哪些暴露更受益、哪些更承压
- 更适合买什么、卖什么、观察什么

它不是：

- 市场日报生成器
- 固定栏目简报器
- 重型金融平台
- 脱离 OpenClaw 的独立产品

## 核心设计原则

### 1. LLM 是主分析引擎

- 程序不应该先把结论写死，再让 LLM 改写
- LLM 应直接读取完整资产、用户偏好、用户约束、新闻、结构信号和近期变化
- 最终面向用户的价值，应主要来自 LLM 的综合判断

### 2. 结构化数据是脚手架，不是天花板

程序负责：

- 数据读取
- 数据清洗
- 轻量归类
- 必要的快照 compare
- 最小市场归纳
- 风险兜底

但程序**不负责替 LLM 抢最终分析权**。

### 3. 先服务建议质量，再谈体系完整

- 输入给 LLM 的上下文应尽量完整
- 中间层保持轻量，够用就好
- 不为未来假设继续横向扩张
- 当前优先级是让建议更有用，而不是让架构更完整

### 4. 先理解用户组合，再解释市场影响

系统首先要理解：

- 用户资产结构
- 用户风险偏好
- 用户约束
- 长期锁定资产与高流动资产的区别
- 防守层、成长层、缓冲层、流动性层之间的关系

然后才分析当天市场对这些结构的影响。

### 5. 健壮性和完整性必须保留

强调 LLM 驱动，不等于放弃工程约束。

系统仍必须保证：

- 用户资产只能由用户确认更新
- 外部新闻不污染长期金融记忆
- 轻量脚手架可以为 LLM 提供最低限度的稳定信号
- 失败时有兜底、快照、日志与可追踪路径

## 当前主链路

1. `FinancialMemoryService`：读取长期金融记忆、偏好与约束
2. `PersonalInsightService`：输出完整资产、偏好、备注、新闻原始上下文
3. `ThemeAnalysisService` / `MarketStateService`：生成市场观察与最小状态
4. `PortfolioMappingService` / `AdvisoryService`：输出轻量脚手架
5. `PersonalLLMReportService`：基于完整上下文生成个人投资建议文本
6. OpenClaw cron：定时触发与 Feishu 直发

## 当前收口决策

当前 `stocks` 已进入收口阶段。

这意味着：

- 主线只保留“金融记忆 + 外部输入 + 轻量脚手架 + LLM + 投递”
- 暂停独立软件化、多宿主平台化和更多中间层扩张
- 优先做真正影响用户价值的部分：资产维护、用户约束、建议质量、关键链路测试

收口方案见：`docs/stocks-convergence-plan.md`
文档对齐见：`docs/stocks-doc-alignment-plan.md`

## 当前范围说明

当前主线只保留：
- 个人资产记忆与偏好/约束
- 新闻与最小市场输入
- 个人投资建议生成与投递
- 单标的查询

已删除/冻结：
- 旧日报生成链路
- 旧 research context / legacy report service
- 大量历史报告产物与迁移文档


当前只保留主线文档：

- `VISION.md`：产品定位与目标
- `ARCHITECTURE.md`：系统分层与主链路
- `DATA_MODEL.md`：当前核心数据对象
- `MEMORY_RULES.md`：金融记忆规则
- `NEWS_INPUT_RULES.md`：新闻输入规则
- `ANALYSIS_RULES.md`：建议生成规则
- `LLM_DRIVEN_DESIGN.md`：为什么这个系统必须以 LLM 为驱动核心
- `REFACTOR_PRINCIPLES.md`：当前收口约束
- `ROADMAP.md`：当前未完成事项与优先级

历史迁移文档已删除，不再作为设计依据。

## 新增功能（Phase 1/2 已完成）

- **配置热重载**：修改 `financial_assets.json`、`watchlist.json` 后立即生效，无需重启
- **冷却去重**：60 分钟内相似内容自动跳过投递，避免重复推送
- **健康巡检**：检查数据新鲜度（market_quotes、news_feed），发现问题告警
- **事件日志**：记录今日报告历史，提取关键主题供 LLM 参考

## 常用命令

### 主链路
- `python3 stocks/cli/send_llm_report.py --refresh-news --save` - 生成并保存报告
- `bash scripts/personal-report-delivery.sh` - 定时任务入口

### 调试工具
- `python3 stocks/cli/build_personal_llm_report.py` - 本地生成 LLM 报告（不投递）
- `python3 stocks/cli/build_personal_report.py` - 查看脚手架层输出
- `python3 stocks/cli/personal_insight_context.py` - 查看输入 LLM 的原始上下文

### 健康检查
- `python3 stocks/cli/health_check.py` - 系统健康巡检
- `python3 stocks/services/event_log_service.py --summary` - 查看今日事件摘要

### 测试
- `python3 -m unittest discover stocks/tests -v` - 运行全部测试
- `python3 stocks/tests/test_personal_report_pipeline_smoke.py` - 主链路冒烟测试

## 系统状态

**当前版本**：Phase 1/2 已完成，核心链路稳定运行

- ✅ 金融记忆层（资产、偏好、约束）
- ✅ 市场数据（A股/美股 quote）
- ✅ 新闻输入（RSS + GNews）
- ✅ LLM 建议生成（PersonalLLMReportService）
- ✅ 投递链路（OpenClaw cron + Feishu）
- ✅ 配置热重载
- ✅ 冷却去重机制
- ✅ 健康巡检
- ✅ 极简事件日志
