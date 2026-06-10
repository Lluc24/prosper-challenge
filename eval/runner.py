"""LLM-to-LLM conversation simulation engine.

Runs an agent LLM (with EHR tools) against a patient LLM (persona),
scores the result with a judge LLM, and verifies DB state.
"""

import json
import pathlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from agent_core import OPENAI_TOOLS, call_ehr_tool, get_system_prompt
from ehr.database import SessionLocal

load_dotenv(override=True)

REPORT_PATH = pathlib.Path(__file__).parent / "report.json"
MAX_TURNS = 20
AGENT_MODEL = "gpt-4.1"
PATIENT_MODEL = "gpt-4.1"
JUDGE_MODEL = "gpt-4.1"

OPENING_PROMPT = (
    "Say hello and briefly introduce yourself as a digital assistant from the Prosper Health clinic."
)


@dataclass
class ScenarioResult:
    scenario_name: str
    passed: bool
    turns: int
    terminated_cleanly: bool
    judge_scores: list[dict]
    db_assertions: list[dict]
    transcript: list[dict]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


async def run_scenario(scenario: dict) -> ScenarioResult:
    """Run a single scenario end-to-end and return the result.

    scenario dict shape:
        name: str
        persona_prompt: str
        setup_fixture: async callable () -> None
        judge_criteria: list[str]
        db_assertions: list[callable(db: Session) -> None]
    """
    logger.info(f"Setting up fixture for scenario: {scenario['name']}")
    await scenario["setup_fixture"]()

    client = AsyncOpenAI()

    agent_messages: list[dict] = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "system", "content": OPENING_PROMPT},
    ]
    patient_messages: list[dict] = [
        {"role": "system", "content": scenario["persona_prompt"]}
    ]

    terminated = False
    turn_count = 0

    # Initial agent greeting (no patient input yet)
    greeting_text = await _agent_text_turn(client, agent_messages)
    logger.info(f"Agent greeting: {greeting_text[:80]}...")
    patient_messages.append({"role": "user", "content": greeting_text})

    while turn_count < MAX_TURNS and not terminated:
        turn_count += 1

        # Patient speaks
        p_resp = await client.chat.completions.create(
            model=PATIENT_MODEL,
            messages=patient_messages,  # type: ignore[arg-type]
        )
        patient_text = p_resp.choices[0].message.content or ""
        patient_messages.append({"role": "assistant", "content": patient_text})
        agent_messages.append({"role": "user", "content": patient_text})
        logger.info(f"[turn {turn_count}] Patient: {patient_text[:80]}")

        # Agent inner loop — exhaust tool calls before producing text
        agent_text, terminated = await _agent_tool_loop(client, agent_messages)

        if agent_text:
            patient_messages.append({"role": "user", "content": agent_text})
            logger.info(f"[turn {turn_count}] Agent: {agent_text[:80]}")

        if terminated:
            logger.info(f"Agent called end_conversation after {turn_count} turns")
            break

    if not terminated:
        logger.warning(f"Scenario reached MAX_TURNS={MAX_TURNS} without end_conversation")

    logger.info("Running LLM judge...")
    judge_scores = await _run_judge(client, agent_messages, scenario["judge_criteria"])

    logger.info("Running DB assertions...")
    db_assertion_results = _run_db_assertions(scenario["db_assertions"])

    judge_passed = all(s["score"] == 1 for s in judge_scores)
    db_passed = all(a["passed"] for a in db_assertion_results)
    passed = judge_passed and db_passed and terminated

    result = ScenarioResult(
        scenario_name=scenario["name"],
        passed=passed,
        turns=turn_count,
        terminated_cleanly=terminated,
        judge_scores=judge_scores,
        db_assertions=db_assertion_results,
        transcript=agent_messages,
    )

    _append_report(result)
    logger.info(f"Scenario '{scenario['name']}': {'PASS' if passed else 'FAIL'}")
    return result


async def _agent_text_turn(client: AsyncOpenAI, agent_messages: list[dict]) -> str:
    """Get a plain text response from the agent (no tool calls expected)."""
    resp = await client.chat.completions.create(
        model=AGENT_MODEL,
        messages=agent_messages,  # type: ignore[arg-type]
        tools=OPENAI_TOOLS,  # type: ignore[arg-type]
    )
    msg = resp.choices[0].message
    agent_messages.append(msg.model_dump())
    return msg.content or ""


async def _agent_tool_loop(
    client: AsyncOpenAI, agent_messages: list[dict]
) -> tuple[str, bool]:
    """Run the agent until it produces a text response, dispatching tool calls along the way.

    Returns (agent_text, terminated). terminated=True when end_conversation was called.
    """
    terminated = False
    agent_text = ""

    while True:
        resp = await client.chat.completions.create(
            model=AGENT_MODEL,
            messages=agent_messages,  # type: ignore[arg-type]
            tools=OPENAI_TOOLS,  # type: ignore[arg-type]
        )
        choice = resp.choices[0]
        agent_messages.append(choice.message.model_dump())

        if choice.finish_reason == "tool_calls":
            for tc in choice.message.tool_calls or []:
                tool_name = tc.function.name  # type: ignore[union-attr]
                if tool_name == "end_conversation":
                    terminated = True
                    agent_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps({"status": "conversation_ended"}),
                        }
                    )
                    logger.info("Tool call: end_conversation")
                    break
                args = json.loads(tc.function.arguments)  # type: ignore[union-attr]
                logger.info(f"Tool call: {tool_name}({args})")
                result = await call_ehr_tool(tool_name, args)
                agent_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            if terminated:
                break
            # Re-query after tool results
            continue

        # finish_reason == "stop"
        agent_text = choice.message.content or ""
        break

    return agent_text, terminated


async def _run_judge(
    client: AsyncOpenAI, transcript: list[dict], criteria: list[str]
) -> list[dict]:
    """Score the transcript against each criterion using an LLM judge."""
    criteria_text = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
    system = (
        "You are evaluating a scheduling assistant conversation. "
        "Score each criterion 0 (fail) or 1 (pass) with a brief reasoning. "
        'Return JSON: {"scores": [{"criterion": "...", "score": 0, "reasoning": "..."}]}\n\n'
        f"Criteria:\n{criteria_text}"
    )
    user = f"Full conversation transcript:\n{json.dumps(transcript, indent=2, default=str)}"

    resp = await client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],  # type: ignore[arg-type]
        response_format={"type": "json_object"},  # type: ignore[arg-type]
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return data.get("scores", [])


def _run_db_assertions(assertions: list) -> list[dict]:
    """Execute each DB assertion callable, catching AssertionError."""
    db: Session = SessionLocal()
    results = []
    try:
        for fn in assertions:
            try:
                fn(db)
                results.append({"name": fn.__name__, "passed": True, "error": None})
            except AssertionError as e:
                results.append({"name": fn.__name__, "passed": False, "error": str(e)})
    finally:
        db.close()
    return results


def _append_report(result: ScenarioResult) -> None:
    """Append the result to eval/report.json."""
    if REPORT_PATH.exists():
        existing: list = json.loads(REPORT_PATH.read_text())
    else:
        existing = []
    existing.append(asdict(result))
    REPORT_PATH.write_text(json.dumps(existing, indent=2, default=str))
    logger.info(f"Report written to {REPORT_PATH}")
