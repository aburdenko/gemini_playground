import csv
import json
import argparse

def create_evalset_from_csv(csv_path, json_path):
    eval_cases = []
    eval_set_id = "generated_from_csv"  # Default
    with open(csv_path, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        rows = list(reader)
        if rows:
            # Check if 'eval_set_id' column exists
            if 'eval_set_id' in rows[0]:
                eval_set_id = rows[0].get("eval_set_id", eval_set_id)

        for row in rows:
            eval_cases.append({
                "eval_id": row["eval_id"],
                "eval_set_id": row.get("eval_set_id", eval_set_id),
                "conversation": [
                    {
                        "invocation_id": f"e-{row['eval_id']}",
                        "user_content": {"parts": [{"text": row["user_content"]}]},
                        "final_response": {"parts": [{"text": row["agent_response"]}]}
                    }
                ],
                "session_input": {
                    "app_name": "rag-agent",
                    "user_id": "test_user"
                },
            })

            # Determine the metric and metric_value
            metric = row.get("metric_type", "").strip()
            ground_truth = {
                "reference": row.get("reference", ""),
            }
            if metric:
                ground_truth["metric_type"] = metric
            else:
                ground_truth["metric_type"] = None # No metric to calculate

            # Construct the ground_truth object
            eval_cases[-1]["ground_truth"] = ground_truth

    eval_set = {
        "eval_set_id": eval_set_id,
        "eval_cases": eval_cases
    }

    with open(json_path, 'w') as jsonfile:
        json.dump(eval_set, jsonfile, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an evalset JSON file from a CSV file.")
    parser.add_argument("csv_path", help="Path to the input CSV file.")
    parser.add_argument("json_path", help="Path to the output JSON file.")
    args = parser.parse_args()
    create_evalset_from_csv(args.csv_path, args.json_path)
