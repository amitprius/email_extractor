# Excel Column Extractor

**Input:** one `.msg` file (attachments and emails nested inside emails are opened automatically)
**Output:** one Excel file with exactly these columns, built only from the Excel attachments of that email:
`du_code, du_name, language_cluster, country, action, cuc_code, predecessor_du, successor_du`

No chat, no questions: upload → progress + live log → download.

## Run
```
pip install -r requirements.txt
copy env.example .env          (Linux/Mac: cp env.example .env)   then put GEMINI_API_KEY in .env
streamlit run app.py
```
Without the UI (scheduling / automation): `python run_cli.py path\to\email.msg`

## What happens (all logged live, also saved to `run.log`)
1. **Read the email** – every nested email level is opened; **only Excel files are saved** (`.xlsx .xlsm .xls`, also Excel files with a wrong/missing extension). Everything else is listed as ignored. Identical Excel files that repeat in a forwarded chain are saved once.
2. **Save column names** of every sheet of every Excel → `excel_headers.json`. The header row is found even when titles sit above it.
3. **Match** each output column to a source column: exact name → alias → fuzzy name.
4. **Gemini** (fixed prompt in `constants.py`) is asked only about output columns that are still unmatched. Its answers are validated (invented columns are rejected). If Gemini is unavailable or out of quota, the next model is tried; if all fail, the run continues with what was matched.
5. **Output Excel** is written (sheet `Data` = the 8 columns; sheet `Mapping` = which source column fed each column), and the log says **✅ OUTPUT FILE GENERATED**. The last log lines show **TOTAL TOKEN USAGE**.

## Change the behaviour: edit `constants.py` only
`TARGET_COLUMNS` (the output list), `ALIASES` (known alternative header names), fuzzy threshold, AI on/off, the fixed prompt, whether to add the `Mapping` sheet.

## Where are the files? (shown in the app sidebar too)
```
workspace/outputs/<email>_output_<run id>.xlsx     the result
workspace/runs/<run id>/
    input/            the uploaded .msg
    downloads/        the Excel attachments, in folders L1_, L2_ ... for nested emails
    metadata.json     email tree: which email carried which Excel
    excel_headers.json  column names of every Excel / sheet
    run.log  run_report.json   full log, token usage, mapping
```

## Notes
- Gemini receives only column names and 5 sample values per column of sheets that still have unmatched columns. Check this is allowed for the client's data.
- Several output rows can come from several sheets: every sheet with at least one matched column is stacked (`MIN_MATCHED_TARGETS_PER_SHEET` in `constants.py`).
- Free-tier models differ per account; `GEMINI_MODELS` in `.env` lists the models to try in order.
- Streamlit has no login: run it on your own machine or behind your company sign-in.
