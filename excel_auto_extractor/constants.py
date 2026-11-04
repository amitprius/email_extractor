"""EDIT THIS FILE to change what the tool produces. No other file needs to be touched.

Input  : one .msg file (attachments and nested emails are opened automatically)
Output : one Excel file whose columns are exactly TARGET_COLUMNS, built only from the Excel
         attachments found in that .msg.
"""

# --------------------------------------------------------------------------- output columns
# The output Excel has exactly these columns, in this order.
TARGET_COLUMNS = [
    "du_code",
    "du_name",
    "language_cluster",
    "country",
    "action",
    "cuc_code",
    "predecessor_du",
    "successor_du",
]

# Optional: known alternative header names for a target column (matched like an exact match).
# Add names you have seen in real files. Spaces, case and punctuation are ignored when comparing.
ALIASES = {
    "du_code": [],
    "du_name": [],
    "language_cluster": [],
    "country": ["Country Variant Name"],   # seen in your sample file
    "action": [],
    "cuc_code": ["CU code"],               # seen in your sample file
    "predecessor_du": ["Predecessor DU_Code"],  # seen in your sample file
    "successor_du": [],
}

# --------------------------------------------------------------------------- matching
USE_FUZZY = True          # step 2: fuzzy name matching when no exact match exists
FUZZY_THRESHOLD = 0.80    # 0-1, higher = stricter

USE_AI = True             # step 3: ask Gemini ONLY for target columns still unmatched after steps 1-2
AI_MIN_CONFIDENCE = 0.50  # ignore AI answers below this confidence
AI_SAMPLE_VALUES = 5      # sample cell values per column shown to the AI (helps it judge a column)
AI_SHEETS_PER_CALL = 8    # sheets sent together in one AI call
AI_RETRIES = 3            # attempts per model before moving to the next model

# A sheet contributes rows to the output if at least this many target columns were matched in it.
MIN_MATCHED_TARGETS_PER_SHEET = 1

# --------------------------------------------------------------------------- output file
INCLUDE_MAPPING_SHEET = True   # adds a 2nd sheet "Mapping" listing which source column fed each output column
OUTPUT_SHEET_NAME = "Data"

# --------------------------------------------------------------------------- the FIXED prompt
FIXED_PROMPT = """You are a data-integration engine. You map the columns of spreadsheet sheets onto a FIXED target schema.

You receive JSON with:
  - "target_columns": the full target schema (for context)
  - "sheets": a list; each sheet has "sheet_id", "unresolved_targets" (the targets you must try to
    map for that sheet) and "available_columns" (each with its exact "name" and a few "samples").

For every sheet, and for every target in its "unresolved_targets", decide which ONE available column
holds that information. Judge by the column name AND its sample values (names may be abbreviated,
misspelled, in another word order, or in another language).

Rules:
1. "source" MUST be copied exactly, character for character, from that sheet's available column names.
2. If no available column clearly holds that information, set "source" to null. Do not guess.
3. Never use the same source column for two different targets in one sheet.
4. "confidence" is between 0 and 1. Be conservative: use below 0.5 when unsure.
5. "reason" is at most 15 words.
6. Return one entry for every sheet and every unresolved target. Return JSON only."""
