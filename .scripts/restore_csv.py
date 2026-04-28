import os
import sys
import pandas as pd

# Add the '.scripts' directory to the Python path
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".scripts")
    ),
)

from eval_agent import get_logs_for_evaluation

def restore_csv():
    """
    Restores the eval_test_cases.csv file from the logs.
    """
    agent_df, simple_df = get_logs_for_evaluation(None)

    combined_df_list = []
    if agent_df is not None and not agent_df.empty:
        combined_df_list.append(agent_df)
    if simple_df is not None and not simple_df.empty:
        combined_df_list.append(simple_df)

    if combined_df_list:
        combined_df = pd.concat(combined_df_list, ignore_index=True)
        required_columns = [
            "eval_id",
            "user_content",
            "agent_response",
            "reference",
            "metric_type",
            "metric_value",
            "eval_set_id",
        ]
        
        # Add missing columns with default empty values
        for col in required_columns:
            if col not in combined_df.columns:
                combined_df[col] = ""

        # Make sure 'eval_set_id' is properly populated from 'session_id' if it's missing
        if 'eval_set_id' not in combined_df.columns or combined_df['eval_set_id'].isnull().all():
            combined_df['eval_set_id'] = combined_df['session_id']


        df_to_export = combined_df[required_columns].copy()
        df_to_export = df_to_export[
            df_to_export["user_content"].astype(bool)
            & df_to_export["agent_response"].astype(bool)
        ]
        df_to_export.dropna(
            subset=["user_content", "agent_response"], how="all", inplace=True
        )

        output_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "agents",
            "rag-agent",
            "eval_sets",
            "eval_test_cases.csv",
        )
        df_to_export.to_csv(output_path, index=False)
        print(f"Successfully restored {output_path}")
    else:
        print("No logs found to restore.")

if __name__ == "__main__":
    restore_csv()
