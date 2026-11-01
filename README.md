# QueryWise

**Ask your data. Understand the answer.**

QueryWise is a local natural-language-to-SQL analytics app. Ask a question in plain English, inspect the generated SQLite or PostgreSQL query, and view its result as a table or chart.

The project includes a sales analytics demo using UCI Online Retail II, browser uploads for smaller CSV/Excel files, batch imports for large local CSV/Parquet files, and connectors for local SQLite files and PostgreSQL. Column names are inspected and normalized; you can tell QueryWise what one row represents and which numeric column is the main measure.

## What it does

- Converts questions into SQLite or PostgreSQL SELECT queries with a locally running Gemma model through Ollama.
- Shows the generated SQL before the result.
- Runs queries against a read-only SQLite connection restricted to the selected table.
- Displays tables and simple charts for suitable results.
- Supports the prepared Online Retail II sales database, browser uploads, and large local-file imports.
- Imports large local CSV and Parquet files into SQLite in batches, bypassing the browser upload limit.
- Connects to an existing local SQLite database in read-only mode and lets you select up to five tables.
- Connects to PostgreSQL and generates PostgreSQL-specific queries for selected tables.
- Supplies selected SQLite table schemas, sample values, and declared foreign-key relationships to the local model.
- Lets users auto-detect a heading row, select a specific heading row, or import a file with no headings.
- Lets users specify the meaning of a numeric measure for uploaded data.
- Accepts optional user descriptions for unclear or generic columns.
- Asks for clarification when the schema or metric is not clear.

## Stack

- Python
- Streamlit
- SQLite
- Ollama with Gemma 4 E2B
- pandas and PyArrow

The app does not train or fine-tune a model. It supplies the selected dataset schema and metric definitions to the local model at query time.

## Setup on Windows

Open PowerShell in the project folder.

### 1. Create the Python environment and install packages

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -r requirements.txt

If PowerShell blocks environment activation, run project commands with the environment's Python directly, for example:

    .\.venv\Scripts\python.exe -m streamlit run app.py

### 2. Install and prepare the local model

Install Ollama from [ollama.com/download](https://ollama.com/download), then download the model:

    ollama pull gemma4:e2b

Keep the Ollama app running while QueryWise is in use. The application talks to Ollama at http://localhost:11434; it does not need an API key.

### Start QueryWise

After setup, double-click `run_querywise.bat` in the project folder. It runs QueryWise with the project's virtual environment, or opens the existing app if it is already running. Keep the terminal window open while using the app; press `Ctrl+C` in that window to stop it.

### 3. Download and prepare the sales data

The dataset workbook is not committed to this repository. Download it from the [UCI Online Retail II page](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii), or use the included downloader:

    python download_data.py

Then prepare the Parquet file and SQLite database:

    python prepare_data.py
    python build_database.py

The build process creates indexes for common date, customer, and product queries.

### 4. Start QueryWise

    python -m streamlit run app.py

Open the Local URL printed by Streamlit, usually http://localhost:8501. Keep the terminal running while using the app. Use a second terminal for other project commands.

## Using another dataset

1. In the sidebar, select **Upload CSV or Excel**.
2. Choose a .csv, .xlsx, .xls, or .xlsm file. For Excel files, select a worksheet.
3. Keep **Auto-detect**, select a specific heading row, or choose **No header row**. Headerless columns are named column_1, column_2, and so on.
4. Review the detected or generated column names, types, and sample values.
5. Describe what one row represents. If useful, explain unclear columns and the meaning of the main numeric measure.
6. Ask a question in the main panel.

For example, an uploaded file with headers Order ID, Region, and Revenue ($) is normalized to order_id, region, and revenue. The model receives the normalized schema, source headers, a few sample values, the row description, and the selected measure meaning.

Uploads are processed locally and copied into a temporary SQLite database on this computer. QueryWise currently supports one table at a time; it does not infer joins across files or Excel worksheets. Raw text log files should first be parsed into a tabular format.

## Connecting to an existing SQLite database

1. In the sidebar, select **Connect to SQLite database**.
2. Enter the full path to a `.db`, `.sqlite`, or `.sqlite3` file on this computer.
3. Select up to five tables to make available to the model.
4. Review the table schemas and ask a question.

QueryWise opens the database read-only and restricts each query to the selected tables. The model receives column names, declared column types, a small number of sample rows, and declared foreign-key relationships. It only uses relationships declared in the database; it does not guess joins.

## Connecting to PostgreSQL

### Import the included sales data

The included `load_postgres.py` script creates a `querywise` database, imports `online_retail_clean.parquet` into a `sales` table, and creates a `querywise_reader` account that can read that table only.

1. Start the PostgreSQL 18 server and confirm it is listening on `localhost:5432`.
2. In PowerShell, open the project folder and activate its virtual environment if it is not already active:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

3. Run the importer:

   ```powershell
   python load_postgres.py
   ```

4. Enter the PostgreSQL `postgres` account password, then choose and confirm a password for the QueryWise read-only account. Password input is hidden and the script does not save passwords in the project.
5. In QueryWise, choose **Connect to PostgreSQL**. The host, port, database, and username are saved as local defaults; enter only the password you chose when prompted.
6. Select the `public.sales` table and ask a question.

The importer replaces the contents of `public.sales` each time it runs, so rerunning it refreshes the table without duplicating rows. Back up any data you put in that table before rerunning it.

The local `.streamlit/secrets.toml` stores only non-secret connection defaults and is ignored by Git. The password is intentionally not saved; enter it in QueryWise when connecting. Use `.streamlit/secrets.toml.example` as a template for another project copy.

For another PostgreSQL database, use an account with read-only `SELECT` access to only the tables you want QueryWise to analyze; do not use a superuser account in the app. Select up to five tables in the sidebar.

The app uses PostgreSQL SQL for this source. It opens connections with read-only transactions and a 60-second statement timeout. Keep the account's database permissions limited: PostgreSQL decides which tables that account can read. Connection credentials are used by the app to connect to PostgreSQL and are not included in the prompt sent to the local Ollama model. The connector supports reachable PostgreSQL servers; it does not currently connect to MySQL.

## Using a large local dataset

1. Put the CSV or Parquet file on the same computer where QueryWise is running.
2. In the sidebar, select **Large local CSV or Parquet** and paste the file's full path.
3. For CSV files, choose the heading-row option. Parquet files use their stored column names.
4. Describe the rows and measure, then ask questions as usual.

This mode reads CSV and Parquet data in batches of 50,000 rows into a temporary SQLite database. It does not send the file through the browser and does not build one full-file pandas DataFrame. Large Excel workbooks should be saved as CSV or Parquet first. The final displayed query result is still capped at 500 rows; use aggregates or filters for concise results. Processing time and disk space still depend on the computer and source file.

## Sales data rules

The Online Retail II workbook contains about 1.07 million transaction lines from December 2009 through December 2011.

The prepared sales table uses these definitions:

- line_amount = quantity × price; it includes eligible merchandise and charges.
- product_sales contains merchandise only. Known postage, fee, adjustment, discount, bank-charge, and sample codes are marked as non-products.
- Rows with non-positive prices and cancelled invoices are excluded. In this workbook, negative-quantity rows fall into one of those excluded groups, so the prepared demo contains no returns. Its sales totals do not subtract returns.
- Cancelled invoices, exact duplicate rows, rows without a positive price, and rows with invalid dates are excluded.
- Rows with missing customer IDs remain for overall sales analysis. Customer-level questions exclude missing IDs.
- A row with a missing product code is not classified as merchandise.

The current prepared database contains 1,007,913 rows. Its recorded totals are £20,476,260.45 in line amount and £19,660,000.86 in merchandise product sales. These numbers depend on the cleaning rules above.

## Safety and limitations

- The app refuses common write requests before asking the model.
- Generated SQL must begin with SELECT or WITH.
- SQLite is opened read-only, configured as query-only, and restricted by an authorizer to the selected table and read operations. PostgreSQL runs in a read-only transaction and should use a least-privilege read-only account.
- Queries have a time limit and display at most 500 result rows.
- These controls reduce risk but do not guarantee that every generated answer is correct.
- The local model can misunderstand questions, metrics, or schemas. Review the SQL and results.
- Browser uploads support tabular CSV or Excel (.xlsx, .xls, .xlsm) files and use Streamlit's 200 MB default limit. Large local-file mode supports CSV and Parquet. QueryWise does not automatically define business metrics; the user must clarify their meaning.
- The app handles one uploaded table at a time and does not join multiple files or worksheets.

## Evaluation

Manual development cases and known issues are recorded in evaluation_log.md.

Run the repeatable SQLite evaluation while Ollama is running:

    python run_evaluation.py

To evaluate the PostgreSQL connection instead, run:

    python run_evaluation.py --backend postgres

Run the eight-question validation set used to identify issues during this work:

    python run_evaluation.py --suite validation

Run the five-question cross-backend validation set:

    python run_evaluation.py --suite cross_backend

Evaluate that validation set on PostgreSQL:

    python run_evaluation.py --backend postgres --suite cross_backend

The PostgreSQL run uses the connection defaults in `.streamlit/secrets.toml` and asks for the `querywise_reader` password without displaying it. Add `--case top_products` with the development suite to run only that case; selected-case runs save to a separate `_selected.csv` file so they do not overwrite the full-suite report. The development suite has five questions, the validation suite has eight, and the cross-backend validation suite has five. Each run compares generated SQL results against hand-written reference SQL, rejects reference cases with no expected rows, checks required product filters, and checks that a write request was blocked. The CSV records generated SQL, expected and actual rows, timing, and the stage for each error. SQLite development results go to `evaluation_results.csv`; PostgreSQL development results go to `evaluation_results_development_postgres.csv`; other combinations include the suite and backend in the filename.

The development, validation, and cross-backend suites have all informed prompt or evaluator changes. None is an untouched holdout benchmark. They are small, locally authored checks, not a general accuracy guarantee.

See [PORTFOLIO.md](PORTFOLIO.md) for a concise project summary, architecture, evaluation results, and data limitations.

## Data attribution

Online Retail II was created by Daqing Chen and is provided by the [UCI Machine Learning Repository](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii), DOI [10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D). The dataset is licensed under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Include this attribution when redistributing or adapting the dataset.

