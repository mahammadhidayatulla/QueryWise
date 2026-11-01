from __future__ import annotations

import hashlib
import numbers
import re
import sqlite3
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as parquet


UPLOAD_TABLE = "uploaded_data"
SUPPORTED_UPLOADS = {".csv", ".xlsx", ".xls", ".xlsm"}


def get_excel_sheets(file_bytes: bytes) -> list[str]:
    """Return worksheet names for an uploaded Excel workbook."""
    return pd.ExcelFile(BytesIO(file_bytes)).sheet_names


def _infer_header_row(preview: pd.DataFrame) -> int:
    """Find a likely header row from a small, header-free preview."""
    populated_per_row = preview.notna().sum(axis=1)
    if populated_per_row.empty or int(populated_per_row.max()) == 0:
        return 0

    minimum_cells = max(2, int(populated_per_row.max() * 0.5))
    candidates = []
    for row_index, row in preview.iterrows():
        values = [value for value in row.tolist() if pd.notna(value)]
        if len(values) < minimum_cells:
            continue
        text_ratio = sum(isinstance(value, str) for value in values) / len(values)
        candidates.append((text_ratio, len(values), -int(row_index), int(row_index)))

    if not candidates:
        return int(populated_per_row.idxmax())

    # Headers are usually mostly text, while data rows tend to include numbers
    # or dates. Prefer text-rich rows with enough populated cells; ties go to
    # the earliest row to avoid choosing a later all-text data record.
    return max(candidates)[3]


def infer_header_row(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | int | None = None,
) -> int:
    """Return the likely 1-based header row for a CSV or Excel worksheet."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            preview = pd.read_csv(BytesIO(file_bytes), header=None, nrows=25)
        except UnicodeDecodeError:
            preview = pd.read_csv(
                BytesIO(file_bytes), encoding="latin-1", header=None, nrows=25
            )
    elif suffix in {".xlsx", ".xls", ".xlsm"}:
        preview = pd.read_excel(
            BytesIO(file_bytes),
            sheet_name=sheet_name or 0,
            header=None,
            nrows=25,
        )
    else:
        raise ValueError("Use a CSV, XLSX, XLS, or XLSM file.")
    return _infer_header_row(preview) + 1


def normalize_column_names(columns: list[Any]) -> tuple[list[str], list[str]]:
    """Create safe, unique SQL identifiers and preserve original headers."""
    original_names = [str(column).strip() for column in columns]
    normalized: list[str] = []
    used: set[str] = set()

    for index, original in enumerate(original_names, start=1):
        name = re.sub(r"[^a-zA-Z0-9]+", "_", original.lower()).strip("_")
        if not name:
            name = f"column_{index}"
        if name[0].isdigit():
            name = f"column_{name}"

        base = name
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1

        normalized.append(name)
        used.add(name)

    return normalized, original_names


def _read_uploaded_dataframe(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None,
    header_row_number: int | None,
    has_header: bool,
) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOADS:
        supported = ", ".join(sorted(SUPPORTED_UPLOADS))
        raise ValueError(f"Unsupported file type. Use: {supported}")

    buffer = BytesIO(file_bytes)
    if suffix == ".csv":
        header = None
        if has_header:
            header = (
                header_row_number - 1
                if header_row_number
                else infer_header_row(file_bytes, filename) - 1
            )
        try:
            frame = pd.read_csv(buffer, header=header, low_memory=False)
        except UnicodeDecodeError:
            frame = pd.read_csv(
                BytesIO(file_bytes),
                encoding="latin-1",
                header=header,
                low_memory=False,
            )
        frame.attrs["header_row_number"] = header_row_number if has_header else None
    else:
        selected_sheet = sheet_name or 0
        header_row = None
        if has_header:
            header_row = (
                header_row_number - 1
                if header_row_number
                else infer_header_row(file_bytes, filename, selected_sheet) - 1
            )
        frame = pd.read_excel(
            buffer,
            sheet_name=selected_sheet,
            header=header_row,
        )
        frame.attrs["header_row_number"] = header_row_number if has_header else None

    if not has_header:
        frame.columns = [f"column_{index}" for index in range(1, len(frame.columns) + 1)]

    if frame.empty:
        raise ValueError("The selected file or worksheet has no data rows.")
    if len(frame.columns) == 0:
        raise ValueError("The selected file has no columns.")

    return frame


def prepare_uploaded_dataset(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None = None,
    header_row_number: int | None = None,
    has_header: bool = True,
) -> dict[str, Any]:
    """
    Load an uploaded CSV or Excel workbook into a local temporary SQLite database.

    The upload remains on this computer. The table name is fixed and headers
    are normalized into unique SQL-safe identifiers.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOADS:
        supported = ", ".join(sorted(SUPPORTED_UPLOADS))
        raise ValueError(f"Unsupported file type. Use: {supported}")

    sheet_key = sheet_name or ""
    digest = hashlib.sha256(
        file_bytes
        + f"|{filename}|{sheet_key}|{header_row_number}|{has_header}|header-options-v1".encode("utf-8")
    ).hexdigest()[:20]
    database_path = Path(tempfile.gettempdir()) / f"querywise_upload_{digest}.db"

    original_frame = _read_uploaded_dataframe(
        file_bytes,
        filename,
        sheet_name,
        header_row_number,
        has_header,
    )
    header_row_number = original_frame.attrs.get("header_row_number")
    sql_columns, original_columns = normalize_column_names(list(original_frame.columns))
    frame = original_frame.copy()
    frame.columns = sql_columns

    # Excel may mark a mostly numeric column as object when it has a text
    # summary/footer row. Recover numeric columns from their underlying cells.
    candidate_numeric = []
    for column in frame.columns:
        values = frame[column].dropna().tolist()
        if not values:
            continue
        typed_numeric_count = sum(
            isinstance(value, numbers.Number) and not isinstance(value, bool)
            for value in values
        )
        if typed_numeric_count / len(values) >= 0.8:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            candidate_numeric.append(column)

    # Remove summary/footer rows that contain labels but no values in numeric
    # fields. They are not observations and otherwise break numeric ranking.
    if candidate_numeric:
        frame = frame.dropna(subset=candidate_numeric, how="all").reset_index(drop=True)

    # SQLite cannot store Python containers directly; preserve them as JSON-like text.
    for column in frame.columns:
        if frame[column].dtype == "object":
            frame[column] = frame[column].map(
                lambda value: str(value)
                if isinstance(value, (dict, list, tuple, set))
                else value
            )

    if not database_path.exists():
        try:
            with sqlite3.connect(database_path) as connection:
                frame.to_sql(
                    UPLOAD_TABLE,
                    connection,
                    if_exists="replace",
                    index=False,
                    chunksize=10_000,
                )
        except Exception:
            database_path.unlink(missing_ok=True)
            raise

    column_info = []
    numeric_columns = []

    for index, sql_name in enumerate(sql_columns):
        series = frame[sql_name]
        dtype = str(series.dtype)
        if pd.api.types.is_numeric_dtype(series.dtype) and not pd.api.types.is_bool_dtype(series.dtype):
            numeric_columns.append(sql_name)

        samples = []
        for value in series.dropna().head(3).tolist():
            sample = str(value)
            samples.append(sample[:80])

        column_info.append(
            {
                "name": sql_name,
                "original_name": original_columns[index],
                "dtype": dtype,
                "samples": samples,
            }
        )

    return {
        "database_path": str(database_path),
        "table_name": UPLOAD_TABLE,
        "filename": filename,
        "sheet_name": sheet_name,
        "header_row_number": header_row_number,
        "row_count": int(len(frame)),
        "columns": column_info,
        "numeric_columns": numeric_columns,
    }


def infer_header_row_from_path(file_path: str | Path) -> int:
    """Infer a 1-based CSV header row by reading only a small preview."""
    path = Path(file_path).expanduser().resolve()
    try:
        preview = pd.read_csv(path, header=None, nrows=25, encoding="utf-8-sig")
    except UnicodeDecodeError:
        preview = pd.read_csv(path, header=None, nrows=25, encoding="latin-1")
    return _infer_header_row(preview) + 1


def _chunk_profile(
    frame: pd.DataFrame,
    original_columns: list[str],
    sql_columns: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    details = []
    numeric_columns = []
    for index, sql_name in enumerate(sql_columns):
        series = frame[sql_name]
        is_numeric = (
            pd.api.types.is_numeric_dtype(series.dtype)
            and not pd.api.types.is_bool_dtype(series.dtype)
        )
        if is_numeric:
            numeric_columns.append(sql_name)
        samples = [str(value)[:80] for value in series.dropna().head(3).tolist()]
        details.append(
            {
                "name": sql_name,
                "original_name": original_columns[index],
                "dtype": str(series.dtype),
                "samples": samples,
            }
        )
    return details, numeric_columns


def _write_batch(
    frame: pd.DataFrame,
    database_path: Path,
    mode: str,
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        frame.to_sql(
            UPLOAD_TABLE,
            connection,
            if_exists=mode,
            index=False,
            chunksize=10_000,
        )
    finally:
        connection.close()


def prepare_large_local_dataset(
    file_path: str | Path,
    header_row_number: int | None = None,
    has_header: bool = True,
    batch_size: int = 50_000,
) -> dict[str, Any]:
    """Stream a local CSV or Parquet file into SQLite in batches."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in {".csv", ".parquet", ".pq"}:
        raise ValueError(
            "Large local-file mode supports CSV and Parquet. "
            "Save large Excel workbooks as CSV or Parquet first."
        )

    stat = path.stat()
    cache_key = hashlib.sha256(
        f"{path}|{stat.st_size}|{stat.st_mtime_ns}|{header_row_number}|{has_header}|batch-v1".encode("utf-8")
    ).hexdigest()[:20]
    database_path = Path(tempfile.gettempdir()) / f"querywise_local_{cache_key}.db"

    if suffix == ".csv":
        if has_header and not header_row_number:
            header_row_number = infer_header_row_from_path(path)
        header = header_row_number - 1 if has_header else None
        original_columns: list[str] | None = None
        sql_columns: list[str] | None = None
        columns: list[dict[str, Any]] = []
        numeric_columns: list[str] = []
        row_count = 0
        if database_path.exists():
            database_path.unlink()

        last_decode_error: Exception | None = None
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                first_batch = True
                for batch in pd.read_csv(
                    path,
                    header=header,
                    chunksize=batch_size,
                    low_memory=False,
                    encoding=encoding,
                ):
                    if first_batch:
                        if not has_header:
                            batch.columns = [
                                f"column_{index}"
                                for index in range(1, len(batch.columns) + 1)
                            ]
                        original_columns = [str(name) for name in batch.columns]
                        sql_columns, _ = normalize_column_names(original_columns)
                        batch.columns = sql_columns
                        columns, numeric_columns = _chunk_profile(
                            batch, original_columns, sql_columns
                        )
                        first_batch = False
                    else:
                        assert sql_columns is not None
                        batch.columns = sql_columns
                        for numeric_name in numeric_columns:
                            batch[numeric_name] = pd.to_numeric(
                                batch[numeric_name], errors="coerce"
                            )

                    _write_batch(
                        batch,
                        database_path,
                        "replace" if row_count == 0 else "append",
                    )
                    row_count += len(batch)

                last_decode_error = None
                break
            except UnicodeDecodeError as error:
                last_decode_error = error
                database_path.unlink(missing_ok=True)
                row_count = 0
                if encoding == "latin-1":
                    raise

        if last_decode_error:
            raise last_decode_error
        if row_count == 0 or original_columns is None:
            raise ValueError("The CSV has no data rows after its heading row.")
        table_columns = columns

    else:
        parquet_file = parquet.ParquetFile(path)
        original_columns = list(parquet_file.schema_arrow.names)
        sql_columns, _ = normalize_column_names(original_columns)
        columns = []
        numeric_columns = []
        row_count = 0
        if database_path.exists():
            database_path.unlink()

        for batch in parquet_file.iter_batches(batch_size=batch_size):
            frame = batch.to_pandas()
            frame.columns = sql_columns
            if row_count == 0:
                columns, numeric_columns = _chunk_profile(
                    frame, original_columns, sql_columns
                )
            _write_batch(
                frame,
                database_path,
                "replace" if row_count == 0 else "append",
            )
            row_count += len(frame)

        if row_count == 0:
            raise ValueError("The Parquet file has no data rows.")
        table_columns = columns

    return {
        "database_path": str(database_path),
        "table_name": UPLOAD_TABLE,
        "filename": path.name,
        "sheet_name": None,
        "header_row_number": header_row_number if suffix == ".csv" and has_header else None,
        "row_count": int(row_count),
        "columns": table_columns,
        "numeric_columns": numeric_columns,
        "file_size_bytes": stat.st_size,
    }

