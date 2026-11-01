from pathlib import Path
from zipfile import ZipFile
import shutil

import requests


DOWNLOAD_URL = (
    "https://archive.ics.uci.edu/static/public/502/"
    "online%2Bretail%2Bii.zip"
)
ARCHIVE = Path("online_retail_II.zip")
OUTPUT = Path("online_retail_II.xlsx")

if OUTPUT.exists():
    print(f"Dataset workbook already exists: {OUTPUT}")
else:
    print("Downloading Online Retail II from the UCI Machine Learning Repository...")
    with requests.get(
        DOWNLOAD_URL,
        stream=True,
        timeout=(20, 180),
    ) as response:
        response.raise_for_status()
        with ARCHIVE.open("wb") as output_file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output_file.write(chunk)

    try:
        with ZipFile(ARCHIVE) as archive:
            workbook_names = [
                name for name in archive.namelist()
                if name.lower().endswith(".xlsx")
            ]
            if not workbook_names:
                raise FileNotFoundError(
                    "The UCI download did not contain an Excel workbook."
                )

            with archive.open(workbook_names[0]) as workbook:
                with OUTPUT.open("wb") as output_file:
                    shutil.copyfileobj(workbook, output_file)
    finally:
        ARCHIVE.unlink(missing_ok=True)

    print(f"Saved dataset workbook to: {OUTPUT}")

print("Source: UCI Online Retail II, DOI 10.24432/C5CG6D, CC BY 4.0.")

