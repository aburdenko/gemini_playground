# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ruff: noqa

from kfp.dsl import Dataset, Input, component

from google_cloud_pipeline_components.types.artifact_types import BQTable


@component(
    base_image="us-docker.pkg.dev/production-ai-template/starter-pack/data_processing:0.2"
)
def ingest_data(
    project_id: str,
    location: str,
    vector_search_index: str,
    vector_search_index_endpoint: str,
    vector_search_data_bucket_name: str,
    schedule_time: str,
    ingestion_batch_size: int,
    input_table: Input[BQTable],
    is_incremental: bool = True,
    look_back_days: int = 1,
) -> None:
    """Process and ingest documents into Vertex AI Vector Search.

    Args:
        project_id: Google Cloud project ID
    """
    import logging
    from datetime import datetime, timedelta

    import bigframes.pandas as bpd
    from google.cloud import aiplatform
    from langchain_google_vertexai import VectorSearchVectorStore
    from langchain_google_vertexai import VertexAIEmbeddings

    # Initialize logging
    logging.basicConfig(level=logging.INFO)

    # Initialize clients
    logging.info("Initializing clients...")
    bpd.options.bigquery.project = project_id
    bpd.options.bigquery.location = location
    logging.info("Clients initialized.")

    # Set date range for data fetch
    schedule_time_dt: datetime = datetime.fromisoformat(
        schedule_time.replace("Z", "+00:00")
    )
    if schedule_time_dt.year == 1970:
        logging.warning(
            "Pipeline schedule not set. Setting schedule_time to current date."
        )
        schedule_time_dt = datetime.now()

    # Note: The following line sets the schedule time 5 years back to allow sample data to be present.
    # For your use case, please comment out the following line to use the actual schedule time.
    schedule_time_dt = schedule_time_dt - timedelta(days=5 * 365)

    START_DATE: datetime = schedule_time_dt - timedelta(
        days=look_back_days
    )  # Start date for data processing window
    END_DATE: datetime = schedule_time_dt  # End date for data processing window

    logging.info(f"Date range set: START_DATE={START_DATE}, END_DATE={END_DATE}")

    dataset = input_table.metadata["datasetId"]
    table = input_table.metadata["tableId"]

    query = f"""
        SELECT
            question_id
            , last_edit_date
            , full_text_md
            , text_chunk
            , chunk_id
            , embedding
        FROM  {project_id}.{dataset}.{table}
        WHERE TRUE
                {f'AND DATETIME(creation_timestamp) BETWEEN DATETIME("{START_DATE}") AND DATETIME("{END_DATE}")' if is_incremental else ""}
    """
    df = (
        bpd.read_gbq(query)
        .sort_values("last_edit_date", ascending=False)
        .drop_duplicates("question_id")
        .reset_index(drop=True)
    )

    aiplatform.init(
        project=project_id,
        location=location,
        staging_bucket=vector_search_data_bucket_name,
    )

    embedding_model = VertexAIEmbeddings(model_name="text-embedding-005")
    my_index = aiplatform.MatchingEngineIndex(vector_search_index)
    my_index_endpoint = aiplatform.MatchingEngineIndexEndpoint(
        vector_search_index_endpoint
    )
    vector_store = VectorSearchVectorStore.from_components(
        project_id=project_id,
        region=location,
        gcs_bucket_name=vector_search_data_bucket_name.replace("gs://", ""),
        index_id=my_index.name,
        endpoint_id=my_index_endpoint.name,
        embedding=embedding_model,
        stream_update=True,
    )

    for batch_num, start in enumerate(range(0, len(df), ingestion_batch_size)):
        ids = (
            df.iloc[start : start + ingestion_batch_size]
            .question_id.astype(str)
            .tolist()
        )
        texts = df.iloc[start : start + ingestion_batch_size].text_chunk.tolist()
        embeddings = df.iloc[start : start + ingestion_batch_size].embedding.tolist()
        metadatas = (
            df.iloc[start : start + ingestion_batch_size]
            .drop(columns=["embedding", "last_edit_date"])
            .to_dict(orient="records")
        )
        vector_store.add_texts_with_embeddings(
            ids=ids,
            texts=texts,
            embeddings=embeddings,
            metadatas=metadatas,
            is_complete_overwrite=True,
        )
