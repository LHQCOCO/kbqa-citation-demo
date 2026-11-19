# kbqa-citation-demo：可回原文的抽取式知识库问答评测

[![tests](https://github.com/LHQCOCO/kbqa-citation-demo/actions/workflows/tests.yml/badge.svg)](https://github.com/LHQCOCO/kbqa-citation-demo/actions/workflows/tests.yml)

一个离线、可复现的抽取式知识库问答演示：混合检索命中原文片段，只摘原文句子作答，
每条结论都能回溯到字符偏移，覆盖不足就拒答。**不调用任何大模型**，索引与作答结果
在同一份代码下重复运行完全一致。

![页面](docs/page.png)

演示（约 12 秒）：[docs/demo.gif](docs/demo.gif)

## 快速开始

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
# 打开 http://127.0.0.1:8765/kbqa
```

输入问题会真实跑一遍「入库 → 混合检索 → 抽取句子 → 引用校验」：页面列出 Top-K 片段
（来源文档、字符偏移、分数），给出最高关键词覆盖率、是否作答与拒答原因码
（`NO_HITS` / `LOW_SCORE` / `LOW_COVERAGE`），并允许展开每条引用的原文 span。

## 语料：12 篇可公开分发的文档

`samples/kb/` 下 12 篇中文技术文档，全部来自公开项目，合计 **42,892 字符 / 185 个片段**
（旧版 3 篇内部示例语料为 2,178 字符 / 11 个片段，保留在 `samples/kb_legacy/` 用于复现旧数字）。
完整机器可读清单见 [samples/corpus_manifest.json](samples/corpus_manifest.json)。

| 文件 | 主题 | 来源 | 许可 | 字符 | 片段 |
| --- | --- | --- | --- | ---: | ---: |
| `vue-reactivity.md` | 响应式基础 | [Vue.js 中文文档](https://cn.vuejs.org/guide/essentials/reactivity-fundamentals.html) | CC BY 4.0 | 4,242 | 19 |
| `react-state.md` | State：组件的记忆 | [React 中文文档](https://zh-hans.react.dev/learn/state-a-components-memory) | CC BY 4.0 | 1,382 | 6 |
| `antd-intro.md` | Ant Design of React | [Ant Design](https://ant.design/docs/react/introduce-cn) | MIT | 1,049 | 5 |
| `apisix-architecture.md` | 架构与核心概念 | [Apache APISIX](https://apisix.apache.org/zh/docs/apisix/architecture-design/apisix/) | Apache-2.0 | 1,316 | 5 |
| `dubbo-intro.md` | Dubbo 介绍 | [Apache Dubbo](https://dubbo.apache.org/zh-cn/overview/what/) | Apache-2.0 | 1,749 | 6 |
| `doris-concepts.md` | 产品概念 | [Apache Doris](https://doris.apache.org/zh-CN/docs/gettingStarted/what-is-apache-doris) | Apache-2.0 | 7,342 | 33 |
| `rust-ownership.md` | Rust 所有权 | [Rust 程序设计语言 中文版](https://kaisery.github.io/trpl-zh-cn/ch04-01-what-is-ownership.html) | MIT OR Apache-2.0 | 6,115 | 23 |
| `fastapi-first-steps.md` | 第一步 | [FastAPI 中文文档](https://fastapi.tiangolo.com/zh/tutorial/first-steps/) | MIT | 4,019 | 28 |
| `k8s-pod-lifecycle.md` | Pod 的生命周期 | [Kubernetes 中文文档](https://kubernetes.io/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/) | CC BY 4.0 | 2,976 | 11 |
| `k8s-service.md` | 服务（Service） | [Kubernetes 中文文档](https://kubernetes.io/zh-cn/docs/concepts/services-networking/service/) | CC BY 4.0 | 2,550 | 8 |
| `mdn-http-overview.md` | HTTP 概述 | [MDN Web Docs](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Overview) | CC BY-SA 2.5 | 5,240 | 23 |
| `mdn-promise.md` | 使用 Promise | [MDN Web Docs](https://developer.mozilla.org/zh-CN/docs/Web/JavaScript/Guide/Using_promises) | CC BY-SA 2.5 | 4,912 | 18 |

获取方式与节选范围逐篇记录在 `samples/corpus_manifest.json`（`source_url`、`repo_file`、
`license`、`note`）。所有文档都做了三步处理：去掉代码块、图片与 MDX 短代码；合并中文段落内的
软换行；按章节节选，保留主题相关正文。**分块算法未改**：仍是按 Markdown 标题分段，
段内 400 字符滑窗、60 字符重叠。

> 许可提醒：CC BY / CC BY-SA 文档在再分发时需要保留署名与许可声明，详见
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本仓只做检索评测，未改动原文语义。

## 实测数字

两套各 30 条问题（照抄原文 / 同义改写），`top_k=8`、`min_score=0.12`、
`min_coverage=0.40`、`max_claims=2`。四组结果如下：

| 语料 | 问题集 | 检索命中 | 答案命中 | 引用正确 | 不可回答拒答 |
| --- | --- | --- | --- | --- | --- |
| 旧 3 篇（2,178 字符 / 11 片段） | 照抄 30 条 | 30/30 = 100.0% | 30/30 = 100.0% | 36/36 = 100.0% | 20/20 = 100.0% |
| 旧 3 篇 | 同义 30 条 | 29/30 = 96.7% | 9/30 = 30.0% | 13/13 = 100.0% | 20/20 = 100.0% |
| **新 12 篇（42,892 字符 / 185 片段）** | **照抄 30 条** | **30/30 = 100.0%** | **29/30 = 96.7%** | **47/47 = 100.0%** | **16/20 = 80.0%** |
| **新 12 篇** | **同义 30 条** | **30/30 = 100.0%** | **16/30 = 53.3%** | **26/26 = 100.0%** | **16/20 = 80.0%** |

读法：

- 扩语料后检索仍能命中，但**答案命中明显变难看**：照抄从 100% 掉到 96.7%，
  同义只有 53.3%。这是预期内的——12 篇语料里同一主题有更多竞争片段，
  覆盖率阈值会淘汰掉一部分本来能答的句子。
- **不可回答拒答从 20/20 掉到 16/20**。4 条被误答：`文档入库时会自动生成摘要吗？`、
  `APISIX 和 Dubbo 在服务发现上的设计取舍有什么不同？`、`Ant Design 一共提供了多少个组件？`、
  `Rust 编译器的默认优化等级是几？`。原因码全部是 `LOW_COVERAGE`，没有 `NO_HITS` 或 `LOW_SCORE`。
- 旧 3 篇两行与仓库上一版完全一致，说明旧数字可复现；但**新旧问题集不同**，
  只能横向看同一语料下的两套问法，不能把「30% → 53.3%」当成严格对照实验。
- 引用正确率在所有组都是 100%：引文都能精确回到原文 span；这只证明可回溯，不证明答对。

复现四组：

```bash
# 旧 3 篇 × 照抄 / 同义
python -m cls.scenarios.kbqa.cli --docs samples/kb_legacy \
  --eval samples/kb_legacy/qa_set.jsonl \
  --unanswerable samples/kb_legacy/qa_set_unanswerable.jsonl
python -m cls.scenarios.kbqa.cli --docs samples/kb_legacy \
  --eval samples/kb_legacy/qa_set_paraphrase.jsonl \
  --unanswerable samples/kb_legacy/qa_set_unanswerable.jsonl

# 新 12 篇 × 照抄 / 同义
python -m cls.scenarios.kbqa.cli --docs samples/kb \
  --eval samples/kb/qa_set.jsonl \
  --unanswerable samples/kb/qa_set_unanswerable.jsonl
python -m cls.scenarios.kbqa.cli --docs samples/kb \
  --eval samples/kb/qa_set_paraphrase.jsonl \
  --unanswerable samples/kb/qa_set_unanswerable.jsonl

python -m pytest -q    # 43 passed
```

### 阈值敏感性（min_coverage，默认仍是 0.40）

同一份 12 篇语料、同一批问题，只改 `--min-coverage`：

| min_coverage | 照抄作答 | 照抄答对 | 同义作答 | 同义答对 | 不可回答拒答 | 引用正确 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.30 | 30/30 | 29/30 | 25/30 | 21/30 | 11/20 | 100% |
| 0.35 | 30/30 | 29/30 | 20/30 | 18/30 | 15/20 | 100% |
| **0.40（当前默认）** | **30/30** | **29/30** | **17/30** | **16/30** | **16/20** | **100%** |
| 0.45 | 29/30 | 28/30 | 14/30 | 14/30 | 18/20 | 100% |
| 0.50 | 29/30 | 28/30 | 14/30 | 14/30 | 18/20 | 100% |
| 0.55 | 28/30 | 27/30 | 9/30 | 9/30 | 20/20 | 100% |

**建议：保留 0.40，不自行改默认值。** 0.45 能把拒答从 16/20 提到 18/20，但照抄少答 1 题、
同义少答 3 题；0.55 能到 20/20，代价是照抄掉到 27/30、同义只剩 9/30。0.40 是这批语料上
「能答的尽量答、答不了就拒」的平衡点。是否改成 0.45 需要项目所有者确认，本仓不做默认值变更。

## 真实失败案例：**Pod 正常关闭前会预留多久？**

这是新语料上稳定复现的一次失败，已放进页面示例：

- 检索命中了 `k8s-pod-lifecycle.md`，`top_score = 0.4303`；
- 但最佳句子的关键词覆盖率只有 **0.3333**，低于 `min_coverage=0.40`；
- 系统返回 `LOW_COVERAGE`，拒答。

原文写的是「Pod 被赋予一个可以体面终止的期限，默认为 30 秒。」问法把「体面终止」换成
「正常关闭」、把「30 秒」换成「多久」，字符级检索对不上，覆盖率被拉低。
这和上一版「Agent 内核由哪些模块构成」是同一类失败：**问法同义、但没有共享足够的关键词**。
换成 `Pod 终止前默认等待多长时间？` 又能答对（覆盖 0.4286），说明边界很窄。

## 为什么默认 min_coverage = 0.40

0.40 是按拒答优先选的默认值：调低会放宽作答，多答一些本该拒答的问题；
调高会更早拒答，也会压掉本来能答的题。上面的敏感性表就是同一份语料上的实测，
本仓不把某个语料上的扫描结果当成通用结论，也不为了让数字好看而改默认值。

## 已知缺陷

1. **引用校验只验偏移，不验答对**。`doc.text[start:end] == quote` 只能证明引文可回溯，
   不能证明这句话回答了问题。实际运行中标题行也会被当成句子引用（例如 APISIX 的成功案例
   会附带一条 `# Apache APISIX 架构与核心概念`），偏移正确但信息量为零。
2. **覆盖率达标不代表答对**：问数字的题目即使覆盖率过阈值，命中的句子也可能答非所问。
   本次 4 条不可回答题被误答，都是「词面命中但语义不匹配」，阈值压不住。
3. **不可回答拒答不是满分**：12 篇语料下为 16/20，4 条误答已在上文列出。
   问题集仍是手写的，不能估计置信区间。
4. **阈值是权衡，不是最优解**。0.30 会多答也会多误答，0.55 能全拒但会压掉大量可答题；
   0.40 只是当前语料上的取舍。
5. **没有语义检索**。向量通道是字符 n-gram 哈希，不是语义向量；同义改写让答案命中
   从 96.7% 掉到 53.3%，换掉核心词仍然拒答。页面保留的失败案例就是这种情况。
6. **关键词提取会留下伪词**。无词典双字切片会产生 `认预`、`数分` 这类跨词边界的
   关键词，继续占分母，压低覆盖率。
7. **边界值会被放行**：覆盖率恰好等于 `min_coverage` 时不拒答；来自无关文档的
   高覆盖率句子也可能被选中。`NO_HITS` 只在问句与全部 185 个片段零字符重叠时触发。
8. 全部数字来自本仓 12 篇示例文档与 30 + 30 + 20 条手写问题，**不能外推到真实业务知识库**。

## 目录

```
app.py                     Flask 入口（只注册 /kbqa）
cls/agent/                 运行时、工具注册、轨迹与证据校验
cls/scenarios/kbqa/        检索、分块、抽取作答、评测、CLI、页面蓝图
samples/kb/                12 篇公开语料 + 新问题集（照抄 / 同义 / 不可回答）
samples/kb_legacy/         旧 3 篇内部示例语料 + 旧问题集（仅用于复现旧数字）
samples/corpus_manifest.json  语料来源、许可、字符数与片段数
templates/kbqa.html        页面
static/                    样式
tests/                     43 个 KBQA 测试
docs/                      页面截图与演示 GIF
THIRD_PARTY_NOTICES.md     第三方组件与语料许可
```

## 许可与第三方声明

本仓库源码仅用于作品展示，**保留所有权利**；未使用 MIT / Apache-2.0 等开源许可证，
不授权复制、修改、分发或商业使用。

`samples/kb/` 下的 12 篇语料来自公开文档，按各自许可证使用（CC BY 4.0、CC BY-SA 2.5、
MIT、Apache-2.0），来源与许可逐篇列在上表与 `samples/corpus_manifest.json`。
第三方组件（Flask、requests、pytest）及其许可证见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
