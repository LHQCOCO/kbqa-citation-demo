"""知识库问答 CLI：单次问答与批量评测，都走同一套 AgentRuntime。

用法：
    python -m cls.scenarios.kbqa.cli --docs samples/kb --question "默认预算上限是多少？"
    python -m cls.scenarios.kbqa.cli --docs samples/kb --eval samples/kb/qa_set.jsonl
"""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from ...agent.runtime import AgentRuntime
from ...agent.types import Budget
from .answer import DEFAULT_MAX_CLAIMS, DEFAULT_MIN_COVERAGE, DEFAULT_MIN_SCORE
from .documents import DOCS
from .evaluate import evaluate, format_report
from .manifest import KBQA_SCENARIO


def _print_answer(result, as_json: bool) -> None:
    summary = result.artifacts.get("summary") or {}
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    print(f"问题：{summary.get('question', '')}")
    print(f"入库：{(summary.get('stats') or {}).get('docs', 0)} 篇 / "
          f"{(summary.get('stats') or {}).get('chunks', 0)} 个片段")
    if summary.get("answered"):
        print("结论：已回答")
        print("答案：")
        print(summary.get("answer", ""))
        print("引用：")
        for i, claim in enumerate(summary.get("claims") or [], 1):
            doc = DOCS.get(claim.get("doc_id", ""))
            span = claim.get("span") or {}
            print(f"{i}. [{doc.name if doc else claim.get('doc_id', '')}] "
                  f"字符 {span.get('start')}–{span.get('end')}：{claim.get('quote', '')}")
    else:
        refusal = summary.get("refusal") or {}
        thresholds = refusal.get("thresholds") or {}
        observed = refusal.get("observed") or {}
        print(f"结论：已拒答（{refusal.get('reason_code', '')}）")
        print(f"原因：{refusal.get('detail', '')}")
        print(f"阈值：min_score={thresholds.get('min_score')} "
              f"min_coverage={thresholds.get('min_coverage')}")
        print(f"实测：top_score={observed.get('top_score')} "
              f"best_coverage={observed.get('best_coverage')}")

    retrieval = summary.get("retrieval") or {}
    print(f"检索：命中 {retrieval.get('hit_count', 0)} 个片段，"
          f"top_score={retrieval.get('top_score', 0)}")
    print(f"trace：{result.trace_path}")


def _run_once(args, question: str) -> None:
    run_id = "kbqa-" + uuid.uuid4().hex[:8]
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    runtime = AgentRuntime(KBQA_SCENARIO, data_dir=trace_dir)
    result = runtime.run(
        {
            "paths": args.docs,
            "question": question,
            "top_k": args.top_k,
            "chunk_chars": args.chunk_chars,
            "overlap": args.overlap,
            "min_score": args.min_score,
            "min_coverage": args.min_coverage,
            "max_claims": args.max_claims,
            "write": True,
        },
        budget=Budget(max_steps=10, max_tool_calls=24),
        run_id=run_id,
    )
    _print_answer(result, args.json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线私有知识库问答")
    parser.add_argument("--docs", nargs="+", default=["samples/kb"],
                        help="md/txt 文件或目录")
    parser.add_argument("--question", default="", help="单次问题")
    parser.add_argument("--eval", default="", help="问答集 JSONL 路径")
    parser.add_argument("--unanswerable", default="", help="不可回答问题 JSONL 路径")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--chunk-chars", type=int, default=400)
    parser.add_argument("--overlap", type=int, default=60)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument("--max-claims", type=int, default=DEFAULT_MAX_CLAIMS)
    parser.add_argument("--trace-dir", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.question and not args.eval:
        parser.error("需要 --question 或 --eval")

    if args.eval:
        stats = evaluate(args.docs, args.eval, args.unanswerable or None,
                         top_k=args.top_k, min_score=args.min_score,
                         min_coverage=args.min_coverage,
                         max_claims=args.max_claims)
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            print(format_report(stats))
            for row in stats["rows"]:
                mark = "命中" if row["retrieval_hit"] else "未命中"
                state = "作答" if row["answered"] else (
                    "拒答:" + row["refusal"] if row["refusal"] else "未作答")
                print(f"- {row['id']} 检索{mark} / {state} / "
                      f"引用 {row['valid_claims']}/{row['claims']} / "
                      f"top={row['top_score']}")
        return 0

    _run_once(args, args.question)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
