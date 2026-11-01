from pathlib import Path
import pandas as pd

file_path = Path("online_retail_II.xlsx")

if not file_path.exists():
    raise FileNotFoundError(
        f"Can't find {file_path}. Make sure the workbook is in the project folder."
    )

workbook = pd.ExcelFile(file_path)

print("Workbook sheets:")
for sheet in workbook.sheet_names:
    print(f"- {sheet}")

    # Read only a few rows so we can inspect the columns without loading
    # the entire workbook yet.
    preview = pd.read_excel(file_path, sheet_name=sheet, nrows=5)
    print("  Columns:", list(preview.columns))
    print(preview.head(2).to_string(index=False))
