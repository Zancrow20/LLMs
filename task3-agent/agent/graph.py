"""Граф агентов: router → orchestrator → media | analyst → validator.

Запуск из task3-agent/: uv run python -m agent.graph "вопрос"
"""
import os
import re
import sys
import time
from pathlib import Path
from typing import Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command
from typing_extensions import TypedDict

from . import models, telemetry, tools
from .tools import ANALYST_TOOLS, MEDIA_TOOLS

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
ROOT = Path(__file__).resolve().parents[1]
MAX_RETRIES = 2
MAX_TOOL_CALLS = 4
FACTS_THRESHOLD = 0.5
# тот же класс символов, что в evals/llm_bench.py: правило в промпте не держится на gemma4
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def _read(p: Path) -> str:
    return p.read_text().strip()


def _prompt(name: str, persona_keys: list[str]) -> str:
    """Persona впереди (неизменная часть — KV-кеш префикса), файл промпта в конец."""
    parts = [_read(ROOT / "persona" / f"{k}.md") for k in persona_keys]
    parts.append(_read(ROOT / "prompts" / f"{name}.md"))
    return "\n\n".join(parts)


_PERSONA = {p.stem for p in (ROOT / "persona").glob("*.md")}
ORCHESTRATOR = _prompt("orchestrator", ["SOUL", "IDENTITY", "AGENTS", "USER"])
MEDIA = _prompt("media", ["SOUL", "IDENTITY", "TOOLS"]) + "\n\n# Каталог скиллов\n" + tools.skill_catalog()
ANALYST = _prompt("analyst", ["SOUL", "IDENTITY", "TOOLS"]) + "\n\n# Каталог скиллов\n" + tools.skill_catalog()
VALIDATOR = _prompt("validator", ["SOUL", "IDENTITY"])
MEMORY_WRITER = _read(ROOT / "prompts" / "memory_writer.md")  # подключается в разделе 13
FACTS = {"facts": {"type": "noul", "instructions": "Ответ содержит факты или числа, которые нужно проверить"}}
# gemma4:e4b на MLX иногда отвечает пустой строкой после результата тула; явная просьба это лечит (JOURNAL.md)
NUDGE = "Сформулируй ответ на мой вопрос по результату тула."


class Verdict(TypedDict):
    verdict: Literal["pass", "fail"]
    unsupported_claims: list[str]
    fix_hint: str


class State(MessagesState):
    tier: str
    kind: str
    route: dict          # ответы decision-модели; в разделе 8 уйдут в метаданные трейса
    evidence: list[str]  # выводы тулов для валидатора
    retries: int
    verdict: dict


def route(state: State) -> dict:
    a = models.decide(state["messages"][-1].content, models.ROUTE)
    tier = models.pick_tier(a["hard"]["noul"])
    return {"tier": tier, "kind": a["kind"]["choice"], "route": a, "evidence": [], "retries": 0, "verdict": {}}


def orchestrate(state: State) -> dict:
    llm = models.llm(state["tier"])
    if state["verdict"].get("verdict") == "fail":
        fix = (f"Валидатор отклонил ответ: {state['verdict']['fix_hint']}\n"
               f"Выводы тулов: {state['evidence']}\nИсправь ответ.")
        return {"messages": [llm.invoke([SystemMessage(ORCHESTRATOR), *state["messages"], HumanMessage(fix)])]}
    if state["kind"] == "chat":
        return {"messages": [llm.invoke([SystemMessage(ORCHESTRATOR), *state["messages"]])]}
    return {}  # media и analyst отвечают сами


def next_step(state: State) -> str:
    return "validator" if isinstance(state["messages"][-1], AIMessage) else state["kind"]


def specialist(tools: list, prompt: str):
    def node(state: State) -> dict:
        # агент создаётся на каждый вызов: модель зависит от tier запроса
        llm = models.llm(state["tier"])
        # лимит тулов: без него агент повторял run_python, пока тот возвращал ошибку
        agent = create_agent(llm, tools, system_prompt=prompt,
                             middleware=[ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="continue")])
        n = len(state["messages"])
        new = agent.invoke({"messages": state["messages"]})["messages"][n:]
        
        last = new[-1]
        if isinstance(last, AIMessage) and not last.tool_calls and not last.content.strip():
            messages = [SystemMessage(prompt), *state["messages"], *new[:-1], HumanMessage(NUDGE)]
            answer = llm.invoke(messages)
            new[-1] = answer

        return {"messages": new, "evidence": state["evidence"] + [m.content for m in new if isinstance(m, ToolMessage)]}
    return node


def validate(state: State) -> dict:
    answer = state["messages"][-1]
    if not state["evidence"] and models.decide(answer.content, FACTS)["facts"]["noul"] < FACTS_THRESHOLD:
        return {"verdict": {"verdict": "pass", "skipped": True}}
    v = models.validator_llm().with_structured_output(Verdict, method="json_schema").invoke(
        [SystemMessage(VALIDATOR), HumanMessage(f"Выводы тулов: {state['evidence'] or 'нет'}\n\nОтвет: {answer.content}")])
    retries = state["retries"] + (v["verdict"] == "fail")
    update = {"verdict": dict(v), "retries": retries}
    if v["verdict"] == "fail" and retries > MAX_RETRIES:
        update["messages"] = [AIMessage(f"{answer.content}\n\n(Ответ не проверен валидатором.)", id=answer.id)]
    return update


def after_validation(state: State) -> str:
    return "orchestrator" if state["verdict"]["verdict"] == "fail" and state["retries"] <= MAX_RETRIES else END


def build() -> StateGraph:
    g = StateGraph(State)
    g.add_node("router", route)
    g.add_node("orchestrator", orchestrate)
    g.add_node("media", specialist([*MEDIA_TOOLS, tools.SKILL_TOOL], MEDIA))
    g.add_node("analyst", specialist([*ANALYST_TOOLS, tools.SKILL_TOOL], ANALYST))
    g.add_node("validator", validate)
    g.add_edge(START, "router")
    g.add_edge("router", "orchestrator")
    g.add_conditional_edges("orchestrator", next_step, ["media", "analyst", "validator"])
    g.add_edge("media", "orchestrator")
    g.add_edge("analyst", "orchestrator")
    g.add_conditional_edges("validator", after_validation, ["orchestrator", END])
    return g


def main():
    question = " ".join(sys.argv[1:]) or "Что ты умеешь?"
    thread = os.environ.get("THREAD", "cli")
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 25}
    h = telemetry.handler()
    if h:
        config["callbacks"] = [h]
        config["metadata"] = {"thread_id": thread, "entry": question[:80]}
    WORKSPACE.mkdir(exist_ok=True)
    with SqliteSaver.from_conn_string(str(WORKSPACE / "short.sqlite")) as cp:
        app = build().compile(checkpointer=cp)
        t0 = time.perf_counter()
        out = app.invoke({"messages": [HumanMessage(question)]}, config)
        while out.get("__interrupt__"):
            req = out["__interrupt__"][0].value
            telemetry.log("gate_interrupt", trace_id=h.last_trace_id if h else "", risk=req["risk"], code=req["code"])
            print(f"Гейт: риск {req['risk']}. Код:\n{req['code']}")
            out = app.invoke(Command(resume=input("Выполнить? [y/N] ").strip().lower() == "y"), config)
        trace_id = h.last_trace_id if h else None
        telemetry.record(out, time.perf_counter() - t0, trace_id)
        hard, conf = out["route"]["hard"]["noul"], out["route"]["kind"]["confidence"]
        print(f"[tier={out['tier']} kind={out['kind']} hard={hard:.2f} conf={conf:.2f} "
              f"verdict={out['verdict'].get('verdict')} retries={out['retries']}]")
        answer = out["messages"][-1].content
        if EMOJI.search(answer):  # второй барьер: правило промпта не удержало модель
            answer = EMOJI.sub("", answer).strip()
            telemetry.send_scores(trace_id, {"emoji_filter": 1.0})
            telemetry.log("emoji_filtered", trace_id=trace_id or "")
            print("[emoji filter] сработал на финальном ответе")
        print(answer)
    telemetry.shutdown()


if __name__ == "__main__":
    main()
