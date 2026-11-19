# 第三方组件与数据来源声明

本仓库是一个离线 KBQA 抽取式问答演示，开发中使用了以下开源组件，感谢原作者。
各组件版权归其各自作者所有，本声明不改变原许可证条款。

## 一、开源依赖

| 组件 | 许可证 | 用途 |
|---|---|---|
| Flask | BSD-3-Clause | Web 服务与页面路由 |
| requests | Apache-2.0 | 运行时依赖引入（本演示不发起外部网络请求） |
| pytest | MIT | 单元测试（仅开发/CI 使用） |

Python 标准库（`re`、`json`、`hashlib`、`sqlite3` 等）不在上表重复列出。

## 二、示例数据

1. `samples/kb/` 下 12 篇语料来自公开技术文档，逐篇来源、仓库文件、许可证与节选范围见
   `samples/corpus_manifest.json`，许可分布如下：

   | 许可证 | 文档 |
   |---|---|
   | CC BY 4.0 | Vue 中文文档、React 中文文档、Kubernetes 中文文档（Pod 生命周期 / Service） |
   | CC BY-SA 2.5 | MDN Web Docs（HTTP 概述 / 使用 Promise） |
   | MIT | Ant Design、FastAPI 中文文档 |
   | MIT OR Apache-2.0 | Rust 程序设计语言 中文版 |
   | Apache-2.0 | Apache APISIX、Apache Dubbo、Apache Doris |

   这些文档只做了去代码块/去短代码、合并软换行、按章节节选，未改动原意。
   再分发时请保留各项目的署名与许可声明；CC BY-SA 文档的衍生内容需沿用相同许可。

2. `samples/kb/qa_set*.jsonl` 为针对上述 12 篇语料自建的评测问题集，
   答案指向语料中的原文句子。

3. `samples/kb_legacy/` 下的 3 篇文档与问题集为本项目早期自写示例，不含第三方版权文本，
   仅保留用于复现旧版数字。

4. 示例中的技术名词（BM25、Proxy、Promise 等）仅为通用术语，不包含对应实现的代码或文档。

## 三、页面资源

页面样式与截图均为本项目自制，未引用外部字体、图标库或图片素材。
