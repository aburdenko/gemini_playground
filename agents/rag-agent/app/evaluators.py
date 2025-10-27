from google.adk.evaluation.evaluator import Evaluator
from google.adk.evaluation.eval_metrics import EvalMetric, EvalMetricResult
from google.adk.evaluation.response_evaluator import ResponseEvaluator
from google.adk.evaluation.trajectory_evaluator import TrajectoryEvaluator
from google.adk.models import Gemini
from google.adk.agents import Agent


class Groundedness(Evaluator):
    """
    A custom evaluator to check if the agent's response is grounded in the
    provided context. This is crucial for RAG agents.
    """

    def __init__(self, llm: Gemini):
        self._llm = llm

    def evaluate(self, agent: Agent, test_case: dict) -> list[EvalMetricResult]:
        response = test_case.get("output", {}).get("response")
        # For RAG, context is often part of the output
        context = test_case.get("output", {}).get("context")

        if not response or not context:
            return [EvalMetricResult(name="groundedness", value=0.0, rationale="Missing response or context for evaluation.")]

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

        return [EvalMetricResult(name="groundedness", value=score, rationale=rationale)]


class ContainsWords(Evaluator):
    """
    A custom evaluator to check if the agent's response contains specific words.
    """

    def evaluate(self, agent: Agent, test_case: dict) -> list[EvalMetricResult]:
        response = test_case.get("response", "")
        ground_truth = test_case.get("ground_truth", {})

        if ground_truth.get("metric_type") != "contains_words":
            return [] # Not applicable for this metric type

        if not response:
            return [EvalMetricResult(name="contains_words", value=0.0, rationale="Missing response for evaluation.")]

        expected_words_str = ground_truth.get("reference", "")
        if not expected_words_str:
            return [EvalMetricResult(name="contains_words", value=0.0, rationale="No reference words provided for evaluation.")]

        words_to_check = [word.strip() for word in expected_words_str.split(' ') if word.strip()]
        contains_all_words = all(word in response for word in words_to_check)

        actual_score = 1.0 if contains_all_words else 0.0
        expected_score = ground_truth.get("contains_words_expected_value", 0.0)

        if actual_score == expected_score:
            rationale = f"Correctly identified that the response should {'' if expected_score else 'not '}contain the words: '{expected_words_str}'."
            score = 1.0
        else:
            rationale = f"Incorrectly identified that the response should {'' if expected_score else 'not '}contain the words: '{expected_words_str}'."
            score = 0.0

        return [EvalMetricResult(name="contains_words", value=score, rationale=rationale)]


def get_evaluators(llm: Gemini) -> list[Evaluator]:
    """
    Returns a list of all evaluators to be run for the RAG agent.
    """
    return [
        # Out-of-the-box evaluators
        ResponseSummarizationQuality(llm),
        ToolUsageQuality(llm),
        # Custom RAG-specific evaluator
        Groundedness(llm),
        ContainsWords(),
    ]