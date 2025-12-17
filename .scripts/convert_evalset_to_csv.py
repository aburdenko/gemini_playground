import argparse
import csv
import json
import pandas as pd
import os

def convert_evalset_to_csv(json_path, csv_path):
    """
    Converts an evalset JSON file to a CSV file, merging with existing data.
    - Pre-populates 'metric_type' with 'rouge' if it's empty.
    - Preserves manually entered 'metric_type' and 'reference' in the existing CSV.
    - Overwrites other fields from the JSON file.
    - Resets 'metric_value' to blank for updated/new rows.
    """
    with open(json_path, 'r') as jsonfile:
        eval_set = json.load(jsonfile)

    eval_set_id = eval_set.get("eval_set_id")
    new_csv_data = []
    for case in eval_set.get("eval_cases", []):
        case_eval_id = case.get("eval_id")
        conversation = case.get("conversation", [])
        
        for i, turn in enumerate(conversation):
            user_content = turn.get("user_content", {}).get("parts", [{}])[0].get("text", "")
            agent_response = turn.get("final_response", {}).get("parts", [{}])[0].get("text", "")
            reference_from_conv = turn.get("expected_final_response", {}).get("parts", [{}])[0].get("text", "")

            ground_truth = case.get("ground_truth", {})
            # If reference from conversation is empty, try to get from ground_truth
            reference = reference_from_conv if reference_from_conv else ground_truth.get("reference", "")

            # Set default metric_type if not present in ground_truth
            metric_type = ground_truth.get("metric_type") if ground_truth.get("metric_type") else "rouge"

            new_csv_data.append({
                "eval_set_id": eval_set_id,
                "eval_id": f"{case_eval_id}-{i}",
                "user_content": user_content,
                "agent_response": agent_response,
                "metric_type": metric_type,
                "metric_value": "",
                "reference": reference,
            })

    if not new_csv_data:
        print("No new data to process.")
        return

    new_df_from_json = pd.DataFrame(new_csv_data)

    fieldnames = ["eval_set_id", "eval_id", "user_content", "agent_response", "metric_type", "metric_value", "reference"]

    if os.path.exists(csv_path):
        existing_df = pd.read_csv(csv_path)
        
        # Ensure existing_df has all fieldnames, filling missing with pd.NA
        for col in fieldnames:
            if col not in existing_df.columns:
                existing_df[col] = pd.NA

        # Set eval_id as index for both new and existing dataframes
        new_df_from_json.set_index('eval_id', inplace=True)
        existing_df.set_index('eval_id', inplace=True)
        
        # Create a new DataFrame to store the updated/merged rows
        # Initialize final_df with existing data, making sure all fieldnames are present
        final_df = existing_df.copy()

        # Iterate over new_df_from_json to decide how to merge each row
        for eval_id, new_row in new_df_from_json.iterrows():
            if eval_id in final_df.index:
                # Row exists, update specific columns, preserve others
                updated_row = final_df.loc[eval_id].copy() # Start with existing row
                
                # Update fields that should always come from JSON
                updated_row['eval_set_id'] = new_row['eval_set_id']
                updated_row['user_content'] = new_row['user_content']
                updated_row['agent_response'] = new_row['agent_response']
                updated_row['metric_value'] = '' # Always reset metric_value
                
                # Preserve existing metric_type if not empty, otherwise use new_row's metric_type
                if pd.notna(updated_row['metric_type']) and updated_row['metric_type'] != '':
                    pass # Keep existing metric_type
                else:
                    updated_row['metric_type'] = new_row['metric_type']
                
                # Preserve existing reference if not empty, otherwise use new_row's reference
                if pd.notna(updated_row['reference']) and updated_row['reference'] != '':
                    pass # Keep existing reference
                else:
                    updated_row['reference'] = new_row['reference']
                
                final_df.loc[eval_id] = updated_row
            else:
                # Row is new, add it directly to final_df
                final_df = pd.concat([final_df, new_row.to_frame().T])
        
        combined_df = final_df.reset_index()
    else:
        combined_df = new_df_from_json.reset_index() if new_df_from_json.index.name == 'eval_id' else new_df_from_json

    # Ensure all fieldnames are present before writing to CSV
    for col in fieldnames:
        if col not in combined_df.columns:
            combined_df[col] = pd.NA # Add missing columns

    combined_df.to_csv(csv_path, index=False, columns=fieldnames)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert an evalset JSON file to a CSV file for evaluation, merging with existing data.")
    parser.add_argument("json_path", help="Path to the input evalset JSON file.")
    parser.add_argument("csv_path", help="Path to the output CSV file.")
    args = parser.parse_args()
    convert_evalset_to_csv(args.json_path, args.csv_path)
    print(f"Successfully converted and merged {args.json_path} to {args.csv_path}")