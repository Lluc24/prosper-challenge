"""Entry point: uv run -m eval

Runs all scenarios and exits 0 if all pass, 1 otherwise.
"""

import asyncio
import sys

from .runner import run_scenario
from .scenarios.book_new_patient import SCENARIO


async def main() -> int:
    scenarios = [SCENARIO]
    all_passed = True

    for scenario in scenarios:
        print(f"\n{'='*60}")
        print(f"Scenario: {scenario['name']}")
        print("=" * 60)

        result = await run_scenario(scenario)

        print(f"\nResult:            {'PASS ✓' if result.passed else 'FAIL ✗'}")
        print(f"Turns:             {result.turns}")
        print(f"Terminated cleanly: {result.terminated_cleanly}")

        print("\nJudge scores:")
        for s in result.judge_scores:
            mark = "✓" if s["score"] == 1 else "✗"
            print(f"  {mark} {s['criterion']}")
            print(f"    → {s['reasoning']}")

        print("\nDB assertions:")
        for a in result.db_assertions:
            mark = "✓" if a["passed"] else "✗"
            line = f"  {mark} {a['name']}"
            if a["error"]:
                line += f": {a['error']}"
            print(line)

        if not result.passed:
            all_passed = False

    print(f"\n{'='*60}")
    print(f"Overall: {'PASS ✓' if all_passed else 'FAIL ✗'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
