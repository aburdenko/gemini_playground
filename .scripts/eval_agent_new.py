
def run_evaluation_from_evalsets(args):
    print("Moving evalset files to the eval_sets folder...")
    script_dir = os.path.dirname(__file__)
    move_script_path = os.path.join(script_dir, "move_evalsets.sh")
    if not os.path.exists(move_script_path):
        move_script_path = (
            "/home/user/gemini_playground/.scripts/move_evalsets.sh"
        )
    if os.path.exists(move_script_path):
        os.system(move_script_path)
    else:
        print(f"Warning: move_evalsets.sh not found.")

    files_to_process = (
        [args.evalset_file]
        if args.evalset_file
        else os.listdir(
            os.path.join(
                os.path.dirname(__file__), "..", "agents", "rag-agent", "eval_sets"
            )
        )
    )
    
    main_eval_df = pd.DataFrame()
    if args.output_csv_path and os.path.exists(args.output_csv_path):
        main_eval_df = pd.read_csv(args.output_csv_path)

    all_results_dfs = []

    for filename in files_to_process:
        if not (filename.endswith(".json") and (".evalset." in filename or "generated_evalset" in filename)):
            continue

        filepath = (
            filename
            if args.evalset_file
            else os.path.join(
                os.path.dirname(__file__),
                "..",
                "agents",
                "rag-agent",
                "eval_sets",
                filename,
            )
        )
        if not os.path.isfile(filepath):
            print(f"Skipping directory: {os.path.basename(filepath)}")
            continue

        print(f"Processing evalset file: {os.path.basename(filepath)}")
        with open(filepath, "r") as f:
            eval_set = json.load(f)
            eval_set_id = eval_set.get("eval_set_id", os.path.basename(filepath).replace(".json", ""))
            current_eval_cases = []
            for case in eval_set.get("eval_cases", []):
                conversation = case.get("conversation", [])
                ground_truth = case.get("ground_truth", {})
                case_reference = ground_truth.get(
                    "reference", ""
                ) or conversation[-1].get("expected_final_response", {}).get("parts", [{}])[0].get("text", "")

                if conversation:
                    current_eval_cases.append(
                        {
                            "conversation": conversation,
                            "session_id": case.get("eval_id"),
                            "response": conversation[-1]
                            .get("final_response", {})
                            .get("parts", [{}])[0]
                            .get("text", ""),
                            "prompt": conversation[0]
                            .get("user_content", {})
                            .get("parts", [{}])[0]
                            .get("text", ""),
                            "reference": case_reference,
                            "ground_truth": ground_truth,
                        }
                    )
            if current_eval_cases:
                current_eval_df = pd.DataFrame(current_eval_cases).drop_duplicates(
                    subset=["session_id"]
                )
                session_id_from_df = (
                    current_eval_df["session_id"].iloc[0] if not current_eval_df.empty else None
                )
                _, final_combined_df = run_evaluation_and_generate_artifacts(
                    eval_df=current_eval_df, all_time=args.all_time, session_id=session_id_from_df, eval_set_id=eval_set_id
                )

                if final_combined_df is not None and not final_combined_df.empty:
                    final_combined_df_renamed = final_combined_df.rename(
                        columns={"session_id": "eval_id"}
                    )
                    final_combined_df_renamed["eval_set_id"] = eval_set_id
                    all_results_dfs.append(final_combined_df_renamed)

    if all_results_dfs:
        updates_df = pd.concat(all_results_dfs, ignore_index=True)
        if not main_eval_df.empty:
            # Update existing rows
            main_eval_df.set_index(['eval_id', 'eval_set_id', 'metric_type'], inplace=True)
            updates_df.set_index(['eval_id', 'eval_set_id', 'metric_type'], inplace=True)
            main_eval_df.update(updates_df)
            main_eval_df.reset_index(inplace=True)
        else:
            main_eval_df = updates_df

    if not main_eval_df.empty and args.output_csv_path:
        main_eval_df.to_csv(args.output_csv_path, index=False)
        print(f"Consolidated test cases saved to CSV: {args.output_csv_path}")

