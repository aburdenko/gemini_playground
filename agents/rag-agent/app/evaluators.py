from google.adk.evaluate import (
    Evaluation,
    Evaluator,
    Metric,
    ResponseSummarizationQuality,
    ToolUsageQuality,
)
from google.adk.models import Llm
from google.adk.agents import Agent


class Groundedness(Evaluator):
    """
    A custom evaluator to check if the agent's response is grounded in the
    provided context. This is crucial for RAG agents.
    """

    def __init__(self, llm: Llm):
        self._llm = llm

    def evaluate(self, agent: Agent, test_case: dict) -> list[Metric]:
        response = test_case.get("output", {}).get("response")
        # For RAG, context is often part of the output
        context = test_case.get("output", {}).get("context")

        if not response or not context:
            return [Metric(name="groundedness", value=0.0, rationale="Missing response or context for evaluation.")]

        prompt = f"""
        You are an expert evaluator. Your task is to determine if the 'Response' is factually supported by the 'Context'.
        A response is considered grounded if all claims it makes can be verified from the information present in the context.

        - Score 1.0 if the response is fully supported by the context.
        - Score 0.0 if the response contains information not found in the context.
        - Score 0.5 for partially supported responses.

        Provide a brief rationale for your score.
        Format your output as a JSON object with "score" and "rationale" keys.

        Context:
        {context}

        Response:
        {response}
        """
        eval_response = self._llm.predict(prompt)

        try:
            result = eval_response.json()
            score = float(result.get("score", 0.0))
            rationale = result.get("rationale", "Could not parse rationale from LLM.")
        except (ValueError, AttributeError):
            score = 0.0
            rationale = "Failed to parse evaluation response from LLM."

        return [Metric(name="groundedness", value=score, rationale=rationale)]


def get_evaluators(llm: Llm) -> list[Evaluator]:
    """
    Returns a list of all evaluators to be run for the RAG agent.
    """
    return [
        # Out-of-the-box evaluators
        ResponseSummarizationQuality(llm),
        ToolUsageQuality(llm),
        # Custom RAG-specific evaluator
        Groundedness(llm),
    ]