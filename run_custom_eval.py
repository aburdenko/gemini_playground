import asyncio

from google.adk.evaluate import Evaluation
from agents.rag_agent.app.agent import root_agent
from custom_evaluators import get_custom_evaluators, after_eval_callback
from google.adk.models import Llm

async def main():
    """Runs the custom evaluation.
    """
    # 1. Define your test case(s)
    test_cases = [
        {
            "prompt": "What are the latest advancements in AI?",
            # Add any other relevant data for your test case
        }
    ]

    # 2. Get your custom evaluators
    # You might need to pass a model for the evaluators that need one
    llm = Llm("gemini-1.5-flash")
    custom_evaluators = get_custom_evaluators(llm)

    # 3. Create an Evaluation instance
    evaluation = Evaluation(
        agent=root_agent,
        evaluators=custom_evaluators,
        test_cases=test_cases,
        after_eval_callback=after_eval_callback
    )

    # 4. Run the evaluation
    print("Running custom evaluation...")
    eval_result = await evaluation.run_async()

    # 5. Print the results
    print("\n--- Evaluation Summary ---")
    print(eval_result.summary())
    print("\n--- Evaluation Results ---")
    for result in eval_result.results:
        print(result)
    
    print("\nEvaluation complete. Check the 'Artifacts' tab in the ADK web UI.")

if __name__ == "__main__":
    asyncio.run(main())
