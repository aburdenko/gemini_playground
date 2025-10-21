from google.adk.evaluate import Evaluator, Metric, CallbackContext
from google.adk.models import Model
from google.adk.agents import Agent
from scripts import eval_agent
import base64

class ArtifactGeneratorEvaluator(Evaluator):
    """
    A custom evaluator that runs the original evaluation and then
    generates a radar chart and a metrics CSV as artifacts.
    """

    def __init__(self, llm: Model):
        self._llm = llm

    def evaluate(self, agent: Agent, test_case: dict) -> list[Metric]:
        """
        This method calls the refactored functions from
        eval_agent.py to generate artifacts.
        """
        artifacts = eval_agent.run_evaluation_and_generate_artifacts()
        
        # Store artifacts in the test case so the callback can access them.
        if "artifacts" not in test_case:
            test_case["artifacts"] = {}
        test_case["artifacts"].update(artifacts)
        
        return []

def after_eval_callback(callback_context: CallbackContext):
    """
    Saves the generated artifacts to the ADK web UI.
    """
    test_case = callback_context.test_case
    if "artifacts" in test_case:
        for name, content in test_case["artifacts"].items():
            if name.endswith("_chart"):
                # Decode the base64 PNG and save it
                callback_context.save_artifact(f"{name}.png", base64.b64decode(content))
            elif name.endswith("_csv"):
                # Save the CSV content as a file
                callback_context.save_artifact(f"{name}.csv", content.encode("utf-8"))

def get_custom_evaluators(llm: Model) -> list[Evaluator]:
    """
    Returns a list of all evaluators, including our new custom one.
    """
    from evaluators import get_evaluators
    
    existing_evaluators = get_evaluators(llm)
    
    return existing_evaluators + [
        ArtifactGeneratorEvaluator(llm),
    ]
