# Agent 运行时概览

本项目的 Agent 运行时把场景、计划、执行器与 trace 装配成一次可回放的运行。
它由 Planner、Executor、ToolRegistry、Trace 与 EvidenceChecker 五个组件组成。
Planner 只产出步骤序列，不直接执行工具，也不读取业务数据。
Executor 按 depends_on 声明的依赖顺序执行步骤，并统一处理预算、重试与失败隔离。
ToolRegistry 在调用工具前做参数校验，并统计每个工具的调用次数。
Trace 把一次运行写成 JSONL，每一行是一个事件。
EvidenceChecker 只做证据定位与一致性判断，不参与规则评分。

## 预算与停止条件

默认预算是 20 个步骤、60 次工具调用、180 秒墙钟时间与 20000 个 token。
预算耗尽时，执行器停止后续步骤，并把已有产物按 partial 状态返回。
如果没有任何产物，运行状态记为 failed。
单个工具的超时由 ToolSpec.timeout_s 控制，默认值是 10 秒。
工具超时后返回 TOOL_TIMEOUT，不会向执行器抛出异常。

## 错误码

工具未注册时返回 TOOL_NOT_FOUND。
参数不符合 JSON Schema 时返回 INVALID_ARGS。
工具内部异常统一收敛为 TOOL_ERROR。
工具调用总数超限返回 TOOL_BUDGET_EXCEEDED。
依赖步骤未完成时，后续步骤跳过并记录 SKIPPED_DEP。

## 计划与展开

计划中的每一步必须指定 tool 或 role，二者只能选一个。
foreach 可以把上一步返回的列表展开成多个子步骤。
foreach 默认最多展开 12 条，超出部分会被截断并记录 TRUNCATED 事件。
同一个 foreach 产生的子步骤共享一次步数配额。
