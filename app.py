from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from data_manager import (
    get_excel_sheets,
    infer_header_row,
    infer_header_row_from_path,
    prepare_large_local_dataset,
    prepare_uploaded_dataset,
)
from query_engine import (
    DEFAULT_MODEL,
    RETAIL_SCHEMA_CONTEXT,
    ReadOnlyRequestError,
    generate_sql,
    list_postgres_tables,
    postgres_schema_context,
    run_postgres_read_only_query,
    run_read_only_query,
)

ROOT = Path(__file__).resolve().parent
RETAIL_DATABASE = ROOT / "online_retail.db"


def list_sqlite_tables(database_file: Path) -> list[str]:
    """List user tables from a local SQLite database using read-only access."""
    uri = f"{database_file.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def sqlite_schema_context(database_file: Path, selected_tables: list[str]) -> str:
    """Describe selected SQLite tables, sample values, and declared foreign keys."""
    uri = f"{database_file.resolve().as_uri()}?mode=ro"
    lines = [
        f"Local SQLite database: {database_file.name}.",
        "Use only the selected tables and columns listed below.",
        "Only use joins supported by declared foreign-key relationships listed below.",
    ]

    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only = ON")
        for table in selected_tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(
                f"PRAGMA table_info({quoted_table})"
            ).fetchall()
            column_names = [column[1] for column in columns]
            lines.append(f"\nTable: {table}")
            lines.append("Columns:")
            for column in columns:
                column_name = column[1]
                column_type = column[2] or "unknown type"
                lines.append(f"- {column_name} ({column_type})")

            foreign_keys = connection.execute(
                f"PRAGMA foreign_key_list({quoted_table})"
            ).fetchall()
            for foreign_key in foreign_keys:
                lines.append(
                    f"Declared relationship: {table}.{foreign_key[3]} -> "
                    f"{foreign_key[2]}.{foreign_key[4]}"
                )

            if column_names:
                sample_rows = connection.execute(
                    f"SELECT * FROM {quoted_table} LIMIT 2"
                ).fetchall()
                if sample_rows:
                    lines.append("Sample values:")
                    for sample in sample_rows:
                        values = []
                        for name, value in zip(column_names, sample):
                            text_value = "NULL" if value is None else str(value)
                            if len(text_value) > 80:
                                text_value = text_value[:77] + "..."
                            values.append(f"{name}={text_value!r}")
                        lines.append("- " + "; ".join(values))
    finally:
        connection.close()

    return "\n".join(lines)


@st.cache_data(show_spinner=False)
def prepare_upload_cached(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None,
    header_row_number: int | None,
    has_header: bool,
) -> dict:
    return prepare_uploaded_dataset(
        file_bytes,
        filename,
        sheet_name,
        header_row_number,
        has_header,
    )


@st.cache_data(show_spinner=False)
def prepare_large_local_cached(
    file_path: str,
    file_size: int,
    modified_ns: int,
    header_row_number: int | None,
    has_header: bool,
) -> dict:
    # Size and modified time participate in Streamlit's cache key so edits to
    # the local file trigger a fresh batch import.
    return prepare_large_local_dataset(
        file_path,
        header_row_number,
        has_header,
    )


def uploaded_schema_context(
    profile: dict,
    row_description: str,
    metric_column: str | None,
    metric_description: str,
    column_notes: str,
) -> str:
    lines = [
        f"Dataset file: {profile['filename']}.",
        f"Table: {profile['table_name']}.",
        f"Number of rows: {profile['row_count']:,}.",
        f"Each row represents: {row_description or 'not specified by the user'}.",
        "Columns:",
    ]

    for column in profile["columns"]:
        examples = ", ".join(column["samples"]) if column["samples"] else "no non-empty examples"
        lines.append(
            f"- {column['name']} ({column['dtype']}), original header "
            f"{column['original_name']!r}; sample values: {examples}"
        )

    if metric_column:
        lines.append(
            f"User-selected measure: {metric_column}. "
            f"Meaning: {metric_description or 'not specified by the user'}."
        )
        lines.append(
            f"For questions about this measure, aggregate {metric_column} only "
            "when the user asks for a total or ranking."
        )
    else:
        lines.append(
            "The user has not identified a primary measure. If the question "
            "requires choosing among multiple numeric columns, ask for clarification."
        )

    if column_notes.strip():
        lines.append(f"User-provided column meanings: {column_notes.strip()}")

    return "\n".join(lines)


def load_history_query(sql: str, question: str) -> None:
    """Load a previous query into the editable SQL workspace."""
    st.session_state["querywise_sql_draft"] = sql
    st.session_state["querywise_sql_editor"] = sql
    st.session_state["querywise_current_question"] = question
    st.session_state.pop("querywise_result", None)


def render_chart(results: pd.DataFrame) -> None:
    numeric_columns = list(results.select_dtypes(include="number").columns)
    if len(results) < 2 or not numeric_columns or len(results) > 50:
        return

    metric_words = ("sales", "amount", "revenue", "total", "quantity", "count")
    metric_columns = [
        column
        for column in numeric_columns
        if any(word in column.lower() for word in metric_words)
    ]
    value_column = metric_columns[0] if metric_columns else numeric_columns[-1]
    label_columns = [column for column in results.columns if column != value_column]

    if not label_columns:
        return

    label_column = label_columns[0]
    chart_data = results[[label_column, value_column]].copy()
    chart_data.columns = ["category", "value"]
    chart_data["category"] = chart_data["category"].astype(str)

    label_lower = label_column.lower()
    is_monthly = "month" in label_lower or "date" in label_lower

    st.subheader("Chart")
    if is_monthly:
        st.line_chart(chart_data, x="category", y="value")
    else:
        st.bar_chart(chart_data, x="category", y="value")


st.set_page_config(
    page_title="QueryWise",
    page_icon="📊",
    layout="wide",
)

st.title("QueryWise")
st.caption("Ask a dataset a question. Review the SQL and the result.")

with st.sidebar:
    st.subheader("Choose a data source")
    data_source = st.radio(
        "Data source",
        [
            "Online Retail II demo",
            "Upload CSV or Excel",
            "Large local CSV or Parquet",
            "Connect to SQLite database",
            "Connect to PostgreSQL",
        ],
        label_visibility="collapsed",
    )

    st.divider()
    st.subheader("Local model")
    st.caption(f"Ollama · {DEFAULT_MODEL}")
    st.caption("The model runs locally. No API key is used.")

database_path: Path | str
table_name: str
allowed_tables: set[str]
schema_context: str
sql_dialect = "SQLite"
postgres_connection_settings: dict | None = None

if data_source == "Online Retail II demo":
    if not RETAIL_DATABASE.exists():
        st.error(
            "The Online Retail II database was not found. Run "
            "prepare_data.py, then build_database.py in the project folder."
        )
        st.stop()

    database_path = RETAIL_DATABASE
    table_name = "sales"
    allowed_tables = {"sales"}
    schema_context = RETAIL_SCHEMA_CONTEXT

    with st.sidebar:
        st.success("Online Retail II is ready")
        st.caption("1,007,913 prepared transaction lines")
        st.caption(
            "Product sales use product_sales; overall transaction value "
            "uses line_amount."
        )
        st.markdown(
            "Dataset source: [UCI Online Retail II]"
            "(https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii) · "
            "CC BY 4.0"
        )

elif data_source == "Upload CSV or Excel":
    if "upload_widget_version" not in st.session_state:
        st.session_state["upload_widget_version"] = 0

    with st.sidebar:
        active_upload_db = st.session_state.get("active_upload_db")
        if active_upload_db and Path(active_upload_db).exists():
            if st.button("Remove current uploaded data"):
                old_path = Path(active_upload_db)
                if old_path.name.startswith("querywise_upload_"):
                    old_path.unlink(missing_ok=True)
                st.session_state.pop("active_upload_db", None)
                for key in (
                    "uploaded_row_description",
                    "uploaded_metric",
                    "uploaded_metric_description",
                ):
                    st.session_state.pop(key, None)
                st.session_state["upload_widget_version"] += 1
                st.rerun()

        uploader_key = f"uploaded_file_{st.session_state['upload_widget_version']}"
        uploaded_file = st.file_uploader(
            "Choose a CSV or Excel file",
            type=["csv", "xlsx", "xls", "xlsm"],
            help="Your uploaded file is processed locally on this computer.",
            key=uploader_key,
        )

    if uploaded_file is None:
        st.info(
            "Upload a CSV or Excel workbook in the sidebar. "
            "QueryWise will inspect its columns and create a local read-only dataset."
        )
        st.stop()

    file_bytes = uploaded_file.getvalue()
    sheet_name = None

    if Path(uploaded_file.name).suffix.lower() in {".xlsx", ".xls", ".xlsm"}:
        try:
            sheets = get_excel_sheets(file_bytes)
        except Exception as error:
            st.error(f"Could not read this Excel workbook: {error}")
            st.stop()

        with st.sidebar:
            sheet_name = st.selectbox("Worksheet", sheets)

    try:
        detected_header_row = infer_header_row(
            file_bytes,
            uploaded_file.name,
            sheet_name,
        )
    except Exception as error:
        st.error(f"Could not inspect the file headings: {error}")
        st.stop()

    with st.sidebar:
        header_mode = st.selectbox(
            "Column headings",
            ["Auto-detect", "Choose row number", "No header row"],
            help=(
                "Auto-detect handles headings below a title. Choose a row if "
                "the detected headings are wrong, or select no header row for "
                "data files without column names."
            ),
        )
        if header_mode == "Auto-detect":
            header_row_number = detected_header_row
            has_header = True
            st.caption(f"Detected headings on row {header_row_number}.")
        elif header_mode == "Choose row number":
            header_row_number = st.number_input(
                "Heading row in the file",
                min_value=1,
                max_value=10000,
                value=max(1, min(detected_header_row, 10000)),
                step=1,
            )
            header_row_number = int(header_row_number)
            has_header = True
        else:
            header_row_number = None
            has_header = False
            st.caption("Columns will be named column_1, column_2, and so on.")

    try:
        with st.spinner("Inspecting the file and preparing a local database..."):
            profile = prepare_upload_cached(
                file_bytes,
                uploaded_file.name,
                sheet_name,
                header_row_number,
                has_header,
            )
            if not Path(profile["database_path"]).exists():
                profile = prepare_uploaded_dataset(
                    file_bytes,
                    uploaded_file.name,
                    sheet_name,
                    header_row_number,
                    has_header,
                )
    except Exception as error:
        st.error(f"Could not prepare this file: {error}")
        st.stop()

    previous_upload = st.session_state.get("active_upload_db")
    current_upload = profile["database_path"]
    if previous_upload and previous_upload != current_upload:
        previous_path = Path(previous_upload)
        if previous_path.name.startswith("querywise_upload_"):
            previous_path.unlink(missing_ok=True)
    st.session_state["active_upload_db"] = current_upload

    database_path = profile["database_path"]
    table_name = profile["table_name"]
    allowed_tables = {table_name}

    with st.sidebar:
        st.success(
            f"Loaded {profile['row_count']:,} rows and "
            f"{len(profile['columns'])} columns"
        )
        if profile.get("header_row_number"):
            st.caption(
                f"Detected column headings on file row "
                f"{profile['header_row_number']}."
            )
        row_description = st.text_input(
            "What does one row represent?",
            placeholder="For example: one store sale",
            key="uploaded_row_description",
        )

        numeric_options = ["Not selected"] + profile["numeric_columns"]
        selected_metric = st.selectbox(
            "Main numeric measure",
            numeric_options,
            help="Choose the number users will ask to total or compare.",
            key="uploaded_metric",
        )

        metric_description = ""
        if selected_metric != "Not selected":
            metric_description = st.text_input(
                "What does this measure mean?",
                placeholder="For example: net revenue in dollars",
                key="uploaded_metric_description",
            )
            metric_column = selected_metric
        else:
            metric_column = None

        column_notes = st.text_area(
            "Column meanings (optional)",
            placeholder=(
                "For headerless files: column_1 = person number; "
                "column_2 = first name"
            ),
            help="Describe unclear or generic columns so QueryWise can use them correctly.",
            key="uploaded_column_notes",
        )

        schema_context = uploaded_schema_context(
            profile,
            row_description,
            metric_column,
            metric_description,
            column_notes,
        )

    with st.expander("Review detected columns and sample values"):
        st.dataframe(
            pd.DataFrame(profile["columns"]),
            use_container_width=True,
            hide_index=True,
        )

elif data_source == "Large local CSV or Parquet":
    with st.sidebar:
        local_path_text = st.text_input(
            "Full path to a local CSV or Parquet file",
            placeholder=r"C:\Users\you\Downloads\large_data.csv",
            key="large_local_file_path",
            help=(
                "This local-file mode bypasses the browser upload limit and "
                "imports the file in batches."
            ),
        )

    if not local_path_text.strip():
        st.info(
            "Enter the full path to a CSV or Parquet file on this computer. "
            "For a large Excel workbook, save it as CSV or Parquet first."
        )
        st.stop()

    local_path = Path(local_path_text.strip()).expanduser()
    if not local_path.is_file():
        st.error(f"Could not find this local file: {local_path}")
        st.stop()

    local_suffix = local_path.suffix.lower()
    if local_suffix not in {".csv", ".parquet", ".pq"}:
        st.error(
            "Large local-file mode supports CSV and Parquet. "
            "Save large Excel workbooks as CSV or Parquet first."
        )
        st.stop()

    local_header_row = None
    local_has_header = True
    if local_suffix == ".csv":
        try:
            detected_local_header = infer_header_row_from_path(local_path)
        except Exception as error:
            st.error(f"Could not inspect this CSV file: {error}")
            st.stop()

        with st.sidebar:
            local_header_mode = st.selectbox(
                "Column headings",
                ["Auto-detect", "Choose row number", "No header row"],
                key="large_local_header_mode",
            )
            if local_header_mode == "Auto-detect":
                local_header_row = detected_local_header
                st.caption(f"Detected headings on row {local_header_row}.")
            elif local_header_mode == "Choose row number":
                local_header_row = int(
                    st.number_input(
                        "Heading row in local file",
                        min_value=1,
                        max_value=10000,
                        value=max(1, min(detected_local_header, 10000)),
                        step=1,
                        key="large_local_header_row",
                    )
                )
            else:
                local_has_header = False
                st.caption("Columns will be named column_1, column_2, and so on.")

    file_stat = local_path.stat()
    try:
        with st.spinner("Importing the local file into SQLite in batches..."):
            profile = prepare_large_local_cached(
                str(local_path.resolve()),
                file_stat.st_size,
                file_stat.st_mtime_ns,
                local_header_row,
                local_has_header,
            )
    except Exception as error:
        st.error(f"Could not prepare this local file: {error}")
        st.stop()

    previous_local_db = st.session_state.get("active_local_db")
    current_local_db = profile["database_path"]
    if previous_local_db and previous_local_db != current_local_db:
        previous_path = Path(previous_local_db)
        if previous_path.name.startswith("querywise_local_"):
            previous_path.unlink(missing_ok=True)
    st.session_state["active_local_db"] = current_local_db

    database_path = profile["database_path"]
    table_name = profile["table_name"]
    allowed_tables = {table_name}

    with st.sidebar:
        st.success(
            f"Loaded {profile['row_count']:,} rows and "
            f"{len(profile['columns'])} columns"
        )
        st.caption("Imported in 50,000-row batches; the file was not browser-uploaded.")
        row_description = st.text_input(
            "What does one row represent?",
            placeholder="For example: one transaction",
            key="large_local_row_description",
        )

        numeric_options = ["Not selected"] + profile["numeric_columns"]
        selected_metric = st.selectbox(
            "Main numeric measure",
            numeric_options,
            help="Choose the number users will ask to total or compare.",
            key="large_local_metric",
        )

        metric_description = ""
        if selected_metric != "Not selected":
            metric_description = st.text_input(
                "What does this measure mean?",
                placeholder="For example: net revenue in dollars",
                key="large_local_metric_description",
            )
            metric_column = selected_metric
        else:
            metric_column = None

        column_notes = st.text_area(
            "Column meanings (optional)",
            placeholder="For example: customer_id = unique buyer identifier",
            key="large_local_column_notes",
        )
        schema_context = uploaded_schema_context(
            profile,
            row_description,
            metric_column,
            metric_description,
            column_notes,
        )

    with st.expander("Review detected columns and sample values"):
        st.dataframe(
            pd.DataFrame(profile["columns"]),
            use_container_width=True,
            hide_index=True,
        )

elif data_source == "Connect to SQLite database":
    with st.sidebar:
        sqlite_path_text = st.text_input(
            "Full path to a SQLite database",
            placeholder=r"C:\path\to\database.db",
            key="sqlite_database_path",
            help="QueryWise opens the database in read-only mode.",
        )

    if not sqlite_path_text.strip():
        st.info("Enter the full path to a local SQLite database file.")
        st.stop()

    sqlite_database_path = Path(sqlite_path_text.strip()).expanduser()
    if not sqlite_database_path.is_file():
        st.error(f"Could not find this database file: {sqlite_database_path}")
        st.stop()

    try:
        available_tables = list_sqlite_tables(sqlite_database_path)
    except Exception as error:
        st.error(f"Could not read this SQLite database: {error}")
        st.stop()

    if not available_tables:
        st.error("This database does not contain any user tables.")
        st.stop()

    with st.sidebar:
        selected_tables = st.multiselect(
            "Tables to query (up to 5)",
            options=available_tables,
            default=available_tables[:5],
            max_selections=5,
            help="Only selected tables are available to generated SQL.",
        )

    if not selected_tables:
        st.info("Select at least one table in the sidebar.")
        st.stop()

    database_path = sqlite_database_path.resolve()
    table_name = selected_tables[0] if len(selected_tables) == 1 else ", ".join(selected_tables)
    allowed_tables = set(selected_tables)
    try:
        schema_context = sqlite_schema_context(database_path, selected_tables)
    except Exception as error:
        st.error(f"Could not inspect the selected tables: {error}")
        st.stop()

    with st.sidebar:
        st.success(f"Connected to {len(selected_tables)} table(s) read-only")
        st.caption("QueryWise only permits reads from the selected tables.")

else:
    postgres_defaults = st.secrets.get("postgres", {})
    with st.sidebar:
        st.caption("Connect to a PostgreSQL server using a read-only database account.")
        pg_host = st.text_input(
            "Host", value=postgres_defaults.get("host", "localhost"), key="pg_host"
        )
        pg_port = int(
            st.number_input(
                "Port",
                min_value=1,
                max_value=65535,
                value=int(postgres_defaults.get("port", 5432)),
                step=1,
                key="pg_port",
            )
        )
        pg_database = st.text_input(
            "Database", value=postgres_defaults.get("database", ""), key="pg_database"
        )
        pg_user = st.text_input(
            "Username", value=postgres_defaults.get("username", ""), key="pg_user"
        )
        pg_password = st.text_input("Password", type="password", key="pg_password")

    if not pg_database.strip() or not pg_user.strip() or not pg_password:
        st.info("Enter the PostgreSQL database name, username, and password in the sidebar.")
        st.stop()

    postgres_connection_settings = {
        "host": pg_host.strip(),
        "port": pg_port,
        "dbname": pg_database.strip(),
        "user": pg_user.strip(),
        "password": pg_password,
    }
    try:
        postgres_tables = list_postgres_tables(postgres_connection_settings)
    except Exception as error:
        st.error(
            f"Could not connect to PostgreSQL: {error}. "
            "Check that PostgreSQL is running and the connection details are correct."
        )
        st.stop()

    if not postgres_tables:
        st.error("This PostgreSQL database has no tables visible to this account.")
        st.stop()

    table_lookup = {
        f"{schema}.{table}": (schema, table)
        for schema, table in postgres_tables
    }
    with st.sidebar:
        selected_table_labels = st.multiselect(
            "Tables to query (up to 5)",
            options=list(table_lookup),
            default=list(table_lookup)[:5],
            max_selections=5,
            help="Select the tables to describe to the model.",
        )

    if not selected_table_labels:
        st.info("Select at least one PostgreSQL table in the sidebar.")
        st.stop()

    selected_postgres_tables = [table_lookup[label] for label in selected_table_labels]
    try:
        schema_context = postgres_schema_context(
            postgres_connection_settings,
            selected_postgres_tables,
        )
    except Exception as error:
        st.error(f"Could not inspect the selected PostgreSQL tables: {error}")
        st.stop()

    sql_dialect = "PostgreSQL"
    with st.sidebar:
        st.success(f"Connected to PostgreSQL · {pg_database.strip()}")
        st.caption("Queries run in a read-only transaction with a 60-second timeout.")
        st.caption("Use a database account with SELECT access only to the intended tables.")

if sql_dialect == "PostgreSQL":
    current_source_key = (
        f"postgres://{postgres_connection_settings['host']}:{postgres_connection_settings['port']}"
        f"/{postgres_connection_settings['dbname']}::{','.join(sorted(selected_table_labels))}"
    )
else:
    current_source_key = (
        f"{Path(database_path).resolve()}::{','.join(sorted(allowed_tables))}"
    )
previous_source_key = st.session_state.get("querywise_source_key")
if previous_source_key and previous_source_key != current_source_key:
    st.session_state.pop("querywise_sql_draft", None)
    st.session_state.pop("querywise_sql_editor", None)
    st.session_state.pop("querywise_result", None)
st.session_state["querywise_source_key"] = current_source_key

st.subheader("Ask your data")
with st.form("question_form"):
    question = st.text_input(
        "Your question",
        placeholder="For example: Which five products had the highest sales?",
    )
    submitted = st.form_submit_button("Ask QueryWise", type="primary")

st.caption(
    "Examples: “Show totals by month” · "
    "“Which country had the highest product sales?”"
)

if submitted:
    if not question.strip():
        st.warning("Enter a question first.")
    else:
        # A new question replaces the previous draft and result.
        st.session_state["querywise_current_question"] = question.strip()
        st.session_state.pop("querywise_result", None)
        try:
            with st.spinner("Generating SQL with Gemma..."):
                model_result = generate_sql(
                    question,
                    schema_context,
                    dialect=sql_dialect,
                )

            if model_result["kind"] == "clarify":
                st.session_state.pop("querywise_sql_draft", None)
                st.session_state.pop("querywise_sql_editor", None)
                st.info(model_result["text"] or "Please clarify the question.")
            else:
                st.session_state["querywise_sql_draft"] = model_result["sql"]
                st.session_state["querywise_sql_editor"] = model_result["sql"]
        except ReadOnlyRequestError as error:
            st.warning(str(error))
        except FileNotFoundError as error:
            st.error(str(error))
        except ValueError as error:
            st.warning(str(error))
        except Exception as error:
            st.error(
                f"Could not generate SQL: {error}. "
                "Check that Ollama is running and the selected database is available."
            )

if st.session_state.get("querywise_sql_draft"):
    st.subheader("SQL workspace")
    st.caption(
        "Review or edit the query, then run it. QueryWise only permits read-only SQL."
    )
    st.text_area(
        "Generated SQL (editable)",
        key="querywise_sql_editor",
        height=190,
        help="You can adjust the SQL before running it against the selected dataset.",
    )

    run_col, clear_col = st.columns([1, 5])
    with run_col:
        run_sql_clicked = st.button("Run SQL", type="primary")
    with clear_col:
        clear_sql_clicked = st.button("Clear query")

    if clear_sql_clicked:
        st.session_state.pop("querywise_sql_draft", None)
        st.session_state.pop("querywise_sql_editor", None)
        st.session_state.pop("querywise_result", None)
        st.rerun()

    if run_sql_clicked:
        edited_sql = st.session_state.get("querywise_sql_editor", "").strip()
        if not edited_sql:
            st.warning("Enter a SELECT query before running it.")
        else:
            try:
                with st.spinner("Running a read-only query..."):
                    if sql_dialect == "PostgreSQL":
                        results, truncated = run_postgres_read_only_query(
                            postgres_connection_settings,
                            edited_sql,
                        )
                    else:
                        results, truncated = run_read_only_query(
                            database_path,
                            edited_sql,
                            allowed_tables,
                        )
                st.session_state["querywise_result"] = {
                    "frame": results,
                    "truncated": truncated,
                }
                history = st.session_state.get("querywise_history", [])
                history.append(
                    {
                        "question": st.session_state.get(
                            "querywise_current_question", "SQL query"
                        ),
                        "sql": edited_sql,
                        "source": current_source_key,
                        "ran_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
                        "row_count": len(results),
                        "truncated": truncated,
                    }
                )
                st.session_state["querywise_history"] = history[-10:]
            except ReadOnlyRequestError as error:
                st.warning(str(error))
            except FileNotFoundError as error:
                st.error(str(error))
            except ValueError as error:
                st.warning(str(error))
            except Exception as error:
                st.error(f"Could not run this query: {error}")

if st.session_state.get("querywise_result"):
    query_result = st.session_state["querywise_result"]
    result_frame = query_result["frame"]
    st.subheader("Results")
    st.dataframe(
        result_frame,
        use_container_width=True,
        hide_index=True,
    )
    csv_data = result_frame.to_csv(index=False).encode("utf-8-sig")
    excel_buffer = BytesIO()
    result_frame.to_excel(excel_buffer, index=False, engine="openpyxl")
    csv_col, excel_col = st.columns(2)
    with csv_col:
        st.download_button(
            "Download shown results as CSV",
            data=csv_data,
            file_name="querywise_results.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with excel_col:
        st.download_button(
            "Download shown results as Excel",
            data=excel_buffer.getvalue(),
            file_name="querywise_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    render_chart(result_frame)
    if query_result["truncated"]:
        st.info("Showing and exporting the first 500 rows of this result.")

history_for_source = [
    item
    for item in st.session_state.get("querywise_history", [])
    if item.get("source") == current_source_key
]
with st.expander("Recent query history", expanded=False):
    st.caption("The last 10 successful queries for this dataset, kept for this app session.")
    if not history_for_source:
        st.write("No successful queries yet for this dataset.")
    else:
        for history_index, item in enumerate(reversed(history_for_source)):
            with st.container(border=True):
                st.markdown(f"**{item['question']}**")
                row_note = f"{item['row_count']:,} displayed rows · {item['ran_at']}"
                if item["truncated"]:
                    row_note += " · result was limited to 500 rows"
                st.caption(row_note)
                st.code(item["sql"], language="sql")
                st.button(
                    "Load into SQL workspace",
                    key=f"load_query_history_{history_index}",
                    on_click=load_history_query,
                    args=(item["sql"], item["question"]),
                )
        if st.button("Clear recent history", key="clear_querywise_history"):
            st.session_state["querywise_history"] = []
            st.rerun()

