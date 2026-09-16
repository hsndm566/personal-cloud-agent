"""Keep worker agent persistence resources alive for the worker lifetime."""

from contextlib import asynccontextmanager

from agents import get_agent, load_agent
from memory import initialize_database, initialize_store


@asynccontextmanager
async def initialized_worker_agent(agent_id: str):
    async with initialize_database() as saver, initialize_store() as store:
        if hasattr(saver, "setup"):
            await saver.setup()
        if hasattr(store, "setup"):
            await store.setup()
        await load_agent(agent_id)
        agent = get_agent(agent_id)
        previous_saver, previous_store = agent.checkpointer, agent.store
        agent.checkpointer = saver
        agent.store = store
        try:
            yield agent
        finally:
            agent.checkpointer = previous_saver
            agent.store = previous_store
