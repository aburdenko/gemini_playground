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


import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

# --- Hierarchical .env loading ---
# This logic traverses up from the script's directory, loading any .env files
# it finds. This allows for a cascading configuration where more specific
# .env files can override variables from parent directories.

current_dir = Path(__file__).parent
project_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

env_files_to_load = []

# Collect .env files from the script's location up to the project root
while current_dir != current_dir.parent and current_dir.is_relative_to(project_root):
    env_file = current_dir / ".env"
    if env_file.is_file():
        env_files_to_load.append(env_file)
    current_dir = current_dir.parent

# Load the .env files, starting from the most specific and going up to the root.
# The `override=True` parameter ensures that variables in more specific .env files
# (e.g., in 'utils/') take precedence over those in parent directories (e.g., the project root).
for env_path in env_files_to_load:
    print(f"Loading environment variables from: {env_path}")
    load_dotenv(dotenv_path=env_path, override=True)


def load_csv_to_bigquery(data_project_id,
                         dataset_name,
                         table_name,
                         csv_filepath,
                         schema=None,
                         quote_character='"',
                         allow_jagged_rows=False,
                         allow_quoted_newlines=False):
    """Loads a CSV file into a BigQuery table.

    Args:
        data_project_id: GCP Project for BQ data.
        dataset_name: The name of the BigQuery dataset.
        table_name: The name of the BigQuery table.
        csv_filepath: The path to the CSV file.
        schema: The schema of the table.
        quote_character: The character used to quote fields.
        allow_jagged_rows: Whether to allow missing trailing optional columns.
        allow_quoted_newlines: Whether to allow newlines in quoted fields.
    """

    client = bigquery.Client(project=data_project_id)

    dataset_ref = client.dataset(dataset_name)
    table_ref = dataset_ref.table(table_name)

    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,  # Skip the header row
        autodetect=False if schema else True,
        quote_character=quote_character,
        allow_jagged_rows=allow_jagged_rows,
        allow_quoted_newlines=allow_quoted_newlines,
    )

    with open(csv_filepath, "rb") as source_file:
        job = client.load_table_from_file(
            source_file, table_ref, job_config=job_config
        )

    job.result()  # Wait for the job to complete

    print(f"Loaded {job.output_rows} rows into "
          f"{dataset_name}.{table_name}")


def create_dataset_if_not_exists(compute_project_id,
                                 data_project_id,
                                 dataset_name):
    """Creates a BigQuery dataset if it does not already exist.

    Args:
        compute_project_id: GCP Project for BQ compute.
        data_project_id: GQP Project for BQ data.
        dataset_name: The name of the BigQuery dataset.
    """
    client = bigquery.Client(project=compute_project_id)
    dataset_full_name = f"{data_project_id}.{dataset_name}"

    try:
        client.get_dataset(dataset_full_name)  # Make an API request.
        print(f"Dataset {dataset_full_name} already exists")
    except Exception:
        dataset = bigquery.Dataset(dataset_full_name)
        dataset.location = "US"  # Set the location (e.g., "US", "EU")
        dataset = client.create_dataset(dataset, timeout=30)  # Make an API request.
        print(f"Created dataset {dataset_full_name}")


def main():

    current_directory = os.getcwd()
    print(f"Current working directory: {current_directory}")

    """Main function to load CSV files into BigQuery."""
    data_project_id = os.getenv("BQ_DATA_PROJECT_ID")
    compute_project_id = os.getenv("BQ_COMPUTE_PROJECT_ID")
    if not data_project_id:
        raise ValueError("BQ_DATA_PROJECT_ID environment variable not set.")
    if not compute_project_id:
        raise ValueError("BQ_COMPUTE_PROJECT_ID environment variable not set.")

    dataset_name = "flights_dataset"

    # Construct absolute paths to the data files relative to this script's location.
    script_dir = Path(__file__).parent.parent
    cymbalair_policies_filepath = script_dir / "flights_dataset" / "cymbalair_policies_table.csv"
    ticket_sales_history_filepath = script_dir / "flights_dataset" / "ticket_sales_history_table.csv"
    flight_history_filepath = script_dir / "flights_dataset" / "flight_history_table.csv"


    # Create the dataset if it doesn't exist
    print("Creating dataset.")
    create_dataset_if_not_exists(compute_project_id,
                                 data_project_id,
                                 dataset_name)

    # Load the tables
    print("Loading cymbalair_policies_table.")
    schema_cymbalair = [
        bigquery.SchemaField("id", "INTEGER"),
        bigquery.SchemaField("content", "STRING"),
        bigquery.SchemaField("embedding", "STRING"),
    ]
    load_csv_to_bigquery(data_project_id,
                         dataset_name,
                         "cymbalair_policies_table",
                         cymbalair_policies_filepath,
                         schema=schema_cymbalair,
                         quote_character='"',
                         allow_jagged_rows=True,
                         allow_quoted_newlines=True)

    print("Loading ticket_sales_history_table.")
    schema_ticket_sales = [
        bigquery.SchemaField("ticket_id", "INTEGER"),
        bigquery.SchemaField("flight_id", "INTEGER"),
        bigquery.SchemaField("booking_time", "TIMESTAMP"),
        bigquery.SchemaField("flight_date", "TIMESTAMP"),
        bigquery.SchemaField("customer_id", "INTEGER"),
        bigquery.SchemaField("fare_class", "STRING"),
        bigquery.SchemaField("base_fare", "FLOAT"),
        bigquery.SchemaField("taxes", "FLOAT"),
        bigquery.SchemaField("total_fare", "FLOAT"),
        bigquery.SchemaField("booking_channel", "STRING"),
        bigquery.SchemaField("days_to_departure_at_booking", "INTEGER"),
    ]
    load_csv_to_bigquery(data_project_id,
                         dataset_name,
                         "ticket_sales_history_table",
                         ticket_sales_history_filepath,
                         schema=schema_ticket_sales)

    print("Loading flight_history_table.")
    schema_flight_history = [
        bigquery.SchemaField("flight_id", "INTEGER"),
        bigquery.SchemaField("airline", "STRING"),
        bigquery.SchemaField("flight_number", "STRING"),
        bigquery.SchemaField("departure_airport_iata", "STRING"),
        bigquery.SchemaField("departure_airport_city", "STRING"),
        bigquery.SchemaField("arrival_airport_iata", "STRING"),
        bigquery.SchemaField("arrival_airport_city", "STRING"),
        bigquery.SchemaField("scheduled_departure", "TIMESTAMP"),
        bigquery.SchemaField("departure_delay_minutes", "INTEGER"),
        bigquery.SchemaField("scheduled_arrival", "TIMESTAMP"),
        bigquery.SchemaField("arrival_delay_minutes", "INTEGER"),
    ]
    load_csv_to_bigquery(data_project_id,
                         dataset_name,
                         "flight_history_table",
                         flight_history_filepath,
                         schema=schema_flight_history)


if __name__ == "__main__":
    main()
