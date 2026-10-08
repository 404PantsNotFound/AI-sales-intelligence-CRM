import json

import pytest

from app.agent.tools.context import AgentToolContext
from app.agent.tools.enrichment_tools import build_enrichment_tools


class FakeStructuredModel:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def invoke(self, messages):
        if self.error:
            raise self.error
        return self.result


class FakeChatModel:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def with_structured_output(self, schema):
        return FakeStructuredModel(
            result=self.result,
            error=self.error,
        )


def _tool():
    return build_enrichment_tools(db=None)[0]


def _result(tool_output: str) -> dict:
    return json.loads(tool_output)


def test_valid_enrichment_is_returned(monkeypatch):
    fake_result = {
        "sales_stage": "qualified",
        "customer_status": "active",
        "enquiry_priority": "high",
        "enquiry_status": "in_progress",
        "estimated_value": 25000,
        "reasoning": [
            "Customer confirmed that the requirement is genuine.",
            "Customer requested a proposal.",
        ],
    }

    monkeypatch.setattr(
        "app.agent.tools.enrichment_tools.create_chat_model",
        lambda: FakeChatModel(result=fake_result),
    )

    output = _tool().invoke(
        {
            "customer_id": 1,
            "enquiry_id": 10,
            "source_type": "call",
            "source_text": (
                "Customer confirmed the requirement and requested a proposal "
                "for approximately 25,000."
            ),
        }
    )

    data = _result(output)

    assert data["data"]["customer_id"] == 1
    assert data["data"]["enquiry_id"] == 10
    assert data["data"]["requires_confirmation"] is True

    enrichment = data["data"]["enrichment"]

    assert enrichment["sales_stage"] == "qualified"
    assert enrichment["customer_status"] == "active"
    assert enrichment["enquiry_priority"] == "high"
    assert enrichment["enquiry_status"] == "in_progress"
    assert enrichment["estimated_value"] == "25000"


def test_no_confident_changes_returns_null_fields(monkeypatch):
    fake_result = {
        "sales_stage": None,
        "customer_status": None,
        "enquiry_priority": None,
        "enquiry_status": None,
        "estimated_value": None,
        "reasoning": [],
    }

    monkeypatch.setattr(
        "app.agent.tools.enrichment_tools.create_chat_model",
        lambda: FakeChatModel(result=fake_result),
    )

    output = _tool().invoke(
        {
            "customer_id": 1,
            "source_type": "call",
            "source_text": "Customer said they will think about it.",
        }
    )

    data = _result(output)
    enrichment = data["data"]["enrichment"]

    assert enrichment["sales_stage"] is None
    assert enrichment["customer_status"] is None
    assert enrichment["enquiry_priority"] is None
    assert enrichment["enquiry_status"] is None
    assert enrichment["estimated_value"] is None
    assert enrichment["reasoning"] == []


def test_invalid_enrichment_value_is_rejected(monkeypatch):
    fake_result = {
        "sales_stage": "not_a_real_stage",
        "customer_status": "active",
        "enquiry_priority": "high",
        "enquiry_status": "open",
        "estimated_value": 10000,
        "reasoning": ["Invalid test value."],
    }

    monkeypatch.setattr(
        "app.agent.tools.enrichment_tools.create_chat_model",
        lambda: FakeChatModel(result=fake_result),
    )

    output = _tool().invoke(
        {
            "customer_id": 1,
            "source_type": "call",
            "source_text": "Customer discussed the requirement.",
        }
    )

    data = _result(output)

    assert "error" in data
    assert data["error"]["code"] == "llm_provider_error"


def test_provider_failure_is_mapped_safely(monkeypatch):
    monkeypatch.setattr(
        "app.agent.tools.enrichment_tools.create_chat_model",
        lambda: FakeChatModel(
            error=RuntimeError("simulated provider failure")
        ),
    )

    output = _tool().invoke(
        {
            "customer_id": 1,
            "source_type": "call",
            "source_text": "Customer discussed the requirement.",
        }
    )

    data = _result(output)

    assert "error" in data
    assert data["error"]["code"] == "llm_provider_error"
    assert "simulated provider failure" not in data["error"]["message"]


def test_customer_scope_is_enforced():
    context = AgentToolContext(
        locked_customer_id=1,
        customer_ids={1},
    )

    tool = build_enrichment_tools(
        db=None,
        context=context,
    )[0]

    output = tool.invoke(
        {
            "customer_id": 2,
            "source_type": "call",
            "source_text": "Customer discussed the requirement.",
        }
    )

    data = _result(output)

    assert data["error"]["code"] == "customer_scope_violation"


def test_enquiry_must_be_retrieved_before_enrichment():
    context = AgentToolContext(
        locked_customer_id=1,
        customer_ids={1},
        enquiries_by_customer={1: {10}},
    )

    tool = build_enrichment_tools(
        db=None,
        context=context,
    )[0]

    output = tool.invoke(
        {
            "customer_id": 1,
            "enquiry_id": 99,
            "source_type": "call",
            "source_text": "Customer discussed the requirement.",
        }
    )

    data = _result(output)

    assert data["error"]["code"] == "record_not_retrieved"


def test_customer_must_be_retrieved_before_enrichment():
    context = AgentToolContext(
        locked_customer_id=1,
        customer_ids=set(),
    )

    tool = build_enrichment_tools(
        db=None,
        context=context,
    )[0]

    output = tool.invoke(
        {
            "customer_id": 1,
            "source_type": "call",
            "source_text": "Customer discussed the requirement.",
        }
    )

    data = _result(output)

    assert data["error"]["code"] == "record_not_retrieved"
