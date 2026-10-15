# EVAL-01 E1 验收

状态：E1 按生成侧数据契约范围完成；E2 未开始，系统继续暂停。

范围：明确 market:code 身份、生成价/来源/as_of/stale 快照、不可评价原因、旧记录只读兼容、正常待结算过滤。未改变交易规则，未引入时效阈值或交易日历。scorable 仅表示生成数据契约满足，不是交易可执行或策略可靠的证明。

证据：三市场真实 provider 读取及 engine producer 写读验证在 /opt/data/workspace/stocks-claw-e1-remediation/e1-live-_yjdj_8y/result.json。美股上一收盘价时间原样保留，provider stale 标志不等于独立时效验证。

情报接入：test_intelligence_entry_wiring.py 执行实际 scheduled_analysis 中的信号记录 AST 代码块，输入覆盖 A/US/crypto 和缺报价，检查真实 tracker 写读。上游分析器/网络用受控输入替代，不声称完整 LLM 采集生成链在线跑通。生成侧专项22通过。此前全量1474通过/7跳过；新增4项为上述 wiring 测试。

测试污染已修复：全局 fixture 将默认 tracker 重定向临时目录；33行污染取证保留，原账本恢复并匹配 E0 manifest。修复后全量通过且未再写生产账本。

时效策略实验不合入；未来评价窗口按E3处理，不扩张E1边界。历史信号不自动补价、重新结算；不恢复旧胜率反馈。

下一步E2：唯一任务、事务写入、并发/重复/中断恢复验证。未Commit/Push，NAS差异后续需回流正式源码。
