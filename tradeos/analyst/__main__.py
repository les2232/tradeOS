"""Command line: python -m tradeos.analyst {sessions,explain} --db data/paper.sqlite3"""

import argparse
import json
import sys

from tradeos.analyst import service
from tradeos.analyst.claude import AnalystUnavailable, ClaudeExplainer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tradeos.analyst", description="Explain recorded paper sessions.")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("sessions", help="List recorded paper sessions, newest first.")
    listing.add_argument("--db", default="data/paper.sqlite3")

    explain = sub.add_parser("explain", help="Explain a session, a symbol, or a research comparison.")
    explain.add_argument("--db", default="data/paper.sqlite3")
    explain.add_argument("--session", default="latest", help="Session id, 'latest' or 'unsessioned'.")
    explain.add_argument("--symbol", help="Explain why this symbol bought, sold or held.")
    explain.add_argument("--compare", metavar="EXPERIMENT_ID", help="Check comparability with a research result.")
    explain.add_argument("--research-memory", default="research_memory/experiments.jsonl")
    explain.add_argument("--claude", action="store_true", help="Also ask Claude to write the explanation (sends the evidence package to the Anthropic API).")
    explain.add_argument("--json", action="store_true", help="Print the full result, evidence included, as JSON.")

    args = parser.parse_args(argv)
    try:
        if args.command == "sessions":
            print(json.dumps(service.sessions(args.db), indent=2))
            return 0
        kind = "comparison" if args.compare else "symbol" if args.symbol else "session"
        result = service.analyze(
            args.db,
            kind=kind,
            session_id=args.session,
            symbol=args.symbol,
            experiment_id=args.compare,
            research_memory=args.research_memory,
            explainer=ClaudeExplainer() if args.claude else None,
        )
    except (ValueError, AnalystUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(_render(result))
    return 0


def _render(result: dict) -> str:
    lines = [f"Q: {result['question']}", ""]
    for label, answer in (("Local evidence", result["local"]), ("Claude", result["ai"])):
        if answer is None:
            continue
        explanation = answer["explanation"]
        header = label if answer["model"] is None else f"{label} ({answer['model']})"
        lines += [f"== {header} ==", explanation["summary"], ""]
        lines += [f"- {item['statement']}  [{', '.join(item['refs'])}]" for item in explanation["findings"]]
        if explanation["hypotheses"]:
            lines += ["", "Hypotheses (not proven):"]
            lines += [f"- {item['statement']}  Check: {item['how_to_check']}" for item in explanation["hypotheses"]]
        if explanation["caveats"]:
            lines += ["", "Caveats:"] + [f"- {item}" for item in explanation["caveats"]]
        if explanation["next_steps"]:
            lines += ["", "Next steps:"] + [f"- {item}" for item in explanation["next_steps"]]
        if answer["rejected"]:
            lines += ["", f"{len(answer['rejected'])} statement(s) removed for citing evidence that is not in the package."]
        lines.append("")
    comparison = result["comparison"]
    if comparison is not None:
        lines.append(f"== Comparison with {comparison['experiment_id']}: {'valid' if comparison['valid'] else 'NOT valid'} ==")
        lines += [f"- [{'ok' if item['ok'] else 'FAIL'}] {item['check']}: {item['detail']}" for item in comparison["checks"]]
        if comparison["metrics"]:
            lines += [f"- {name}: session {pair['session']} vs research {pair['research']}" for name, pair in comparison["metrics"].items()]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
