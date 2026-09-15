"""Bind durable run messages to the agent extension boundary."""

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from agents import AgentGraph, get_agent, load_agent
from control_plane.supabase import ControlPlane
from worker.dispatch import RunMessage

logger = logging.getLogger(__name__)


def build_agent_run_handler(control_plane: ControlPlane, agent_id: str):
    async def handle(message: RunMessage) -> None:
        run = await control_plane.get_run(message.run_id)
        await control_plane.set_run_status(run_id=run.id, owner_id=run.owner_id, status="running")
        await load_agent(agent_id)
        agent: AgentGraph = get_agent(agent_id)
        config = RunnableConfig(
            configurable={"thread_id": run.thread_id, "user_id": run.owner_id},
            metadata={"user_id": run.owner_id, "agent_id": agent_id, "run_id": str(run.id)},
        )
        try:
            response_events: list[tuple[str, Any]] = await agent.ainvoke(  # type: ignore[arg-type]
                input={"messages": [HumanMessage(content=run.goal)]},
                config=config,
                stream_mode=["updates", "values"],
            )
        except Exception as exc:
            await control_plane.append_event(
                run_id=run.id,
                owner_id=run.owner_id,
                event_type="error",
                state="running",
                payload={"error": str(exc), "agent_id": agent_id},
            )
            raise

        response_type, response = response_events[-1]
        if "__interrupt__" in response:
            status = "interrupted"
            output_content = response["__interrupt__"][0].value
        elif response_type == "values":
            status = "completed"
            output_content = response["messages"][-1].content
        else:
            raise ValueError(f"Unexpected response type from agent: {response_type}")
        await control_plane.record_artifact(
            run_id=run.id,
            owner_id=run.owner_id,
            kind="final_output",
            content={"content": output_content, "agent_id": agent_id},
        )
        await control_plane.set_run_status(
            run_id=run.id, owner_id=run.owner_id, status=status, payload={"agent_id": agent_id}
        )
        logger.info("run %s finished with status=%s", run.id, status)

    return handle
