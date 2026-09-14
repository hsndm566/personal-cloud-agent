from dataclasses import dataclass

import pytest

from model_gateway import AliasGateway, ModelAlias, ModelRoute


@dataclass
class FakeClient:
    calls: list[dict]

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return {"model": kwargs["model"]}


@pytest.mark.asyncio
async def test_alias_gateway_resolves_provider_model_and_fallbacks():
    client = FakeClient([])
    gateway = AliasGateway(
        {ModelAlias.AGENT_DEFAULT: ModelRoute("qwen/configured-default", ("qwen/configured-fallback",))},
        client,
    )

    response = await gateway.complete(ModelAlias.AGENT_DEFAULT, [{"role": "user", "content": "hi"}])

    assert response == {"model": "qwen/configured-default"}
    assert client.calls[0]["fallbacks"] == ("qwen/configured-fallback",)


def test_alias_gateway_fails_closed_for_missing_route():
    gateway = AliasGateway({}, FakeClient([]))

    with pytest.raises(ValueError, match="No model route configured"):
        gateway.resolve("agent-default")
