
from vertexai import types

def get_rubric_evaluators():
    """
    Returns a list of rubric-based evaluators.
    """
    return [
        types.RubricMetric.GENERAL_QUALITY,
        types.RubricMetric.TEXT_QUALITY,
        types.RubricMetric.INSTRUCTION_FOLLOWING,
        types.RubricMetric.GROUNDING,
        types.RubricMetric.SAFETY,
    ]
