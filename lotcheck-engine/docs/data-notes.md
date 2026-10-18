# Data notes

What the real data looks like, checked against the openFDA API on October 17, 2026 (FDA's data last updated 2026-10-07). Re-check these numbers when you run the pipeline; your profile is the source of truth.

## Size

- **Device adverse event reports in openFDA: about 26.56 million.** By event type: Malfunction 16,607,966; Injury 9,556,216; Death 231,597; Other 104,507; blank 56,494; "No answer provided" 995.
- Reference datasets in the download manifest: device classification 7,095 records (1 file, 3.1 MB); device enforcement 40,249 records (1 file, 90.5 MB); device UDI 5,182,695 records (52 files, 1.9 GB). The UDI data maps box barcodes to devices, useful later.

## CGM product codes

From FDA's classification database:

| code | device | device entries in reports |
| --- | --- | --- |
| QBJ | Integrated continuous glucose monitoring system, factory calibrated (iCGM) | 2,352,086 |
| PQF | Sensor, glucose, invasive, non-adjunctive | 327,316 |
| QLG | Integrated CGM, factory calibrated, not for use with automated insulin delivery | 260,427 |
| MDS | Sensor, glucose, invasive | 135,022 |
| PZE | Sensor, glucose, invasive, non-adjunctive, factory-calibrated, user-initiated | 134,193 |

Not included by default: OYC (insulin pump used with a glucose sensor, 467,539 entries) and NBW (over-the-counter blood glucose meters, 427,056).

Count aggregations count device entries; a report can list more than one device. A search for QBJ returns 2,268,325 reports.

## Lot numbers are messy

For QBJ device entries, the most common `lot_number` values are:

- blank: 548,156
- `NI` (no information): 203,242
- `NA`: 5,083
- `ASKU` (asked, unknown): 1,069
- `N/A` and others further down

That is about **1 in 3 entries with no usable lot**. Real lots come in different shapes by manufacturer: `1725204004` (10 digits), `T60002615` and `P13465867` (a letter plus 8 digits), `17276923` (8 digits).

## Narratives are templated and partly redacted

A real 2020 QBJ record (Dexcom G6, lot 5268240) has:

- `product_problems`: "Premature End-of-Life Indicator", listed twice (the pipeline dedupes)
- Description of Event or Problem: "IT WAS REPORTED THAT EARLY SENSOR EXPIRATION OCCURRED. NO PRODUCT OR DATA WAS PROVIDED FOR EVALUATION. CONFIRMATION OF THE..."
- Additional Manufacturer Narrative: "(B)(4)." (redacted under FOIA exemption 4)

So: FDA already codes problems, many texts repeat word for word, and some texts carry no information. The AI step (week 3) should add what the codes miss (day of wear, high vs low readings, a lot number written in the text) and only process unique texts.

## Recalls

openFDA lists 23 recall records for product code QBJ. `code_info` varies a lot:

- a UDI and software build: "Lot Code: UDI: SW11678 - 00386270000811"
- "Lot/Serial Number: All Lots;"
- software versions and a GTIN, with no lot numbers

Matching recalls to lots needs parsing; `code_tokens` is a first pass.

## Formats and gotchas

- Dates: `YYYYMMDD` in events, `YYYY-MM-DD` in recalls. `parse_date` handles both.
- Some fields are sometimes a string and sometimes a list (`type_of_report`). `_text` joins lists.
- Open question for week 1: how do follow-up (supplemental) reports appear, and do they share `report_number` with the initial report? Decide your dedupe rule and write it here.
- Not rates: MAUDE is passive surveillance. Reports are unverified, under-reported, sometimes duplicated, and have no denominators.
