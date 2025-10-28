import csv
import json
import argparse

def create_evalset_from_csv(csv_path, json_path):
    eval_cases = []
    with open(csv_path, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            eval_cases.append({
                "eval_id": row["eval_id"],
                "eval_set_id": row["eval_id"], # Use eval_id as eval_set_id
                                        "conversation": [
                                            {
                                                "user_content": {"parts": [{"text": row["user_content"]}]},
                                                "final_response": {"parts": [{"text": row["reference"]}]}
                                            }
                                        ],                "session_input": {
                    "app_name": "rag-agent",
                    "user_id": "test_user"
                },
            })

            # Determine the metric, defaulting to 'bleu' if the column is missing
            metric = row.get("metric", "bleu").strip()
            if not metric: # Handle cases where the metric column exists but is empty
                metric = "bleu"

            # Construct the ground_truth object
            eval_cases[-1]["ground_truth"] = {
                "reference": row.get("reference", ""),
                metric: row.get("reference", ""),
                "metric_type": metric
            }

    eval_set = {
        "eval_set_id": "generated_from_csv",
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
