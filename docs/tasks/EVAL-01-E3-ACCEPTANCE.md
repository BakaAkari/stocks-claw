# EVAL-01 E3 implementation and acceptance

E3开发范围已交付：交易会话日历、显式elapsed/session窗口、历史目标观测、质量原因和手动采集入口。非恢复上线授权；E4收益评价未实现。

- exchange_targets使用exchange-calendars 4.13.2，A股XSHG、美股XNYS、港股XHKG；连续窗口按小时。sessions计数为生成后严格未来的第N个收盘，不等同elapsed 24h/168h。日历覆盖外拒绝，不猜工作日。
- target_observation使用腾讯未复权day、Yahoo原始close、Binance历史1分钟close。仅查目标日期/分钟，绝不fallback当前价。股票日K observed_at由交易会话收盘时间推导，time_origin=exchange_calendar；不是声称provider提供了精确成交时间。加密分钟close相对target偏差明确保存。
- 股票elapsed窗口没有分钟provider时明确unavailable，不偷偷改成收盘。窗口定义由CLI JSON显式传入，没有修改既有策略窗口。
- observation_pipeline把观测存入E2事务账本，缺失先释放重试，deadline后无观测记expired；逾期仍能取得目标历史观测则允许回填。settled表示观测任务终态，不代表已算交易收益。
- collect_target_observations.py为手动入口，必须明确signals/database/windows三个路径；未注册cron，不读取旧settlements。

验证：新增9项测试通过，生产全量1502 passed / 7 skipped。真实CLI读取A/US/crypto历史报价，第一次settled=3，重复执行not_claimed=3，SQLite读回3条observed。证据在 /opt/data/workspace/stocks-claw-e1-remediation/e3-smoke/，测试信号为合成输入，价格为真实网络返回，不冒充生产新信号。

限制：三市场烟测不能证明长期99%覆盖率；XSHG内置假日目前止于2026，未来年份需更新依赖并验证。没有港股真实行情验收。Yahoo原始close可能存在拆股口径修订风险，E4收益评价必须处理企业行动和entry/exit价格基准一致性。历史JSONL不回填、不重算，旧反馈继续关闭。

旧observation_calendar为早期辅助函数；正式手动入口只走exchange_targets，不能把旧周末函数当交易所日历使用。
