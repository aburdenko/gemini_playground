## gemini_playground

Run . .scripts/configure.sh first.

You can ground Gemini with either a RAG Corpus or a Vector Search index.

If you want the fastest, most managed path to ground Gemini on your documents, use the RAG Engine and create a RAG Corpus.  
If you have a custom data pipeline or already have your vector embeddings, use Vector Search directly and point the Gemini model to your index.  
The tools in your project are well-designed to support both modern approaches.

Summary: Which "Search" to Use When?  
Use Vertex AI Search (and its RAG Engine feature) when your primary goal is to quickly ground an LLM on your unstructured documents (like PDFs, TXT files, etc.) without worrying about the details of the data pipeline. This is the most direct and managed approach. Your create_vector_store_from_gcs.py script is the perfect example of this.

Use Vector Search directly when you have more advanced needs, such as:

You already have your own vector embeddings generated from a custom model.  
You need a specific, custom document chunking strategy.  
You are building a similarity search application that doesn't involve an LLM (e.g., product recommendation, image search).  
In short, Vertex AI Search is the product, RAG Engine is the feature that makes RAG easy, and Vector Search is the powerful engine under the hood that makes it all possible.

./.scripts/create_vector_store_from_gcs.py   
--corpus_display_name "agentspace_hcls_demo-corpus"   
--source_gcs_bucket "agentspace_hcls_demo"

Run the following to eval the agent running in ADK Web:  
./.scripts/eval_agent.py --export-sessions

## Evaluation Workflow

This section describes how to run the full evaluation cycle, from extracting evaluation data from Cloud Logs to running the evaluation.

### Step 1: Extract Evaluation Data from Cloud Logs

To extract the latest evaluation data from Cloud Logs and generate the `eval_test_cases.csv` file, run the following script:

```bash
./.scripts/export_eval_to_csv.sh
```
This will create or update the file at `agents/rag-agent/eval_sets/eval_test_cases.csv`.

### Step 2: Run the Full Evaluation

Once the evaluation data has been extracted, you can run the full evaluation workflow using the following script:

```bash
./.scripts/run_full_evaluation.sh
```
This script will:
1.  Use the `eval_test_cases.csv` file to generate a new `.evalset.json` file.
2.  Run the evaluation on the generated `.evalset.json` file.
3.  Output the results to the console and generate a radar chart of the metrics.