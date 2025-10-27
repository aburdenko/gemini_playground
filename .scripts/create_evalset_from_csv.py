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
                                        "conversation": [
                                            {
                                                "user_content": {"parts": [{"text": row["user_content"]}]},
                                                "final_response": {"parts": [{"text": row["reference"]}]}
                                            }
                                        ],                "session_input": {
                    "app_name": "rag-agent",
                    "user_id": "test_user"
                },
                "ground_truth": {
                    "reference": row["reference"],  # Add a generic 'reference' key
                    row["metric"]: row["reference"],
                    "metric_type": row["metric"]
                }
            })

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
