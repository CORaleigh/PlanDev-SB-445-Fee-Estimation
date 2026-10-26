# Raleigh Development Fee Estimate

This repository contains a static development-fee estimator for the City of
Raleigh and the scripts needed to update its data.

## Purpose

Section 49 of North Carolina Session Law 2026-59 created development-fee
transparency requirements in G.S. 160D-402.1. The law addresses publication of
fee schedules and methodologies, project-specific estimates, revised estimates,
final fee statements, payment timing, and annual reporting.

[Official text](https://www3.ncleg.gov/EnactedLegislation/SessionLaws/HTML/2025-2026/SL2026-59.html)

This application supports the fee-schedule and estimation work that can be
derived from current EnerGov configuration and validated evidence. It does not
by itself manage completed-application dates, statutory deadlines, estimate
records, revisions, final binding statements, or annual reports.

## Fee Presentation

The estimator separates three types of information:

- The estimated total includes fees supported by the selected permit, supplied
  project details, and implemented calculation rules.
- Other fees may apply later depending on the project and its scope. They are
  listed separately and are not included in the estimated total.
- Related permits are informational. Historical occurrence does not establish
  that a related permit is required, and related-permit fees are not added
  automatically.

Unsupported rules require staff review. The application does not guess an
amount.

Static PDFs produced from this catalog are fee-schedule and methodology
documents. They are not project-specific estimates or final fee statements.

## Preview

The estimator includes 199 Permit Type / Work Class options.

Unresolved fees, including the reviewed stadium fee, remain subject to staff
review. This is not an operational tool.

The public site omits source dates. Internal browser reviews and PDF drafts
can include them as a small footnote. Technical version identifiers are not
displayed.

## Repository Contents

- `docs/`: GitHub Pages site and reduced public estimator JSON.
- `queries/`: SQL used to rebuild supporting fee evidence.
- `scripts/`: catalog refresh, validation, release staging, and promotion.
- `tests/`: tests required by the retained update and browser runtime.
- `schema/`: internal catalog and reduced public-data contracts.
- `config/refresh-inputs.example.json`: local input configuration template.
- `config/estimation-review.json`: fees that must remain subject to staff review.
- `UPDATE.md`: source-to-release update instructions.

Source extracts, credentials, generated candidates, and historical evidence
files are not committed.

## Internal and Public Data

The complete catalog is the source for refresh review, rule validation, and
PDF generation. It remains under ignored local storage or an approved internal
release location.

`docs/data/` is generated from that catalog. It contains the labels and rule
information needed by the static estimator, but it does not contain EnerGov
identifiers, source filenames, database class names, exact historical counts,
or assessment evidence. Historical percentages are rounded to whole numbers.
Samples below 10 records are shown only as limited history.

Project-field percentages require the all-option SQL result described in
`UPDATE.md`. The refresh checks coverage for every supported project field;
missing source columns or historical records are not treated as zero usage.

Do not use `docs/data/` as the source for refresh analysis or PDFs. It is a
reduced delivery format, not the complete catalog.

## Requirements

The current release workflow is validated with:

- Python 3.12;
- Node.js 24;
- Git 2.50 on Windows.

`python-dotenv` loads local EnerGov settings. `pandas` and `pyodbc` support the
retained SQL queries.

## Setup

```cmd
python -m venv env
env\Scripts\python.exe -m pip install -r requirements.txt
copy config\refresh-inputs.example.json config\refresh-inputs.json
```

Set the local paths in `config\refresh-inputs.json`. Relative paths are
resolved from the repository root. The file is ignored by Git.

Copy `.env.example` to `.env` and set the EnerGov credentials when running a
live extraction or SQL query. Do not commit `.env`.

See `UPDATE.md` for the source queries and the files they produce.

## Validate the Current Release

```cmd
env\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
node scripts\audit_static_runtime_coverage.mjs --catalog-dir docs\data
env\Scripts\python.exe scripts\audit_public_static_site.py docs
```

## Refresh the Catalog

Check that all local inputs are present before making an API request:

```cmd
env\Scripts\python.exe scripts\refresh_estimator.py --as-of YYYY-MM-DD --check-inputs
```

Then run the refresh:

```cmd
env\Scripts\python.exe scripts\refresh_estimator.py --as-of YYYY-MM-DD
```

The refresh process:

1. extracts current production permit-fee configuration;
2. normalizes the configuration;
3. compares it with the published baseline;
4. stops when a change requires review;
5. reuses or rebuilds approved historical evidence;
6. builds and validates the complete catalog;
7. creates the reduced public-data package;
8. runs the retained tests and browser audit; and
9. creates a release candidate without publishing it.

Use `--refresh-history` only when the configured historical fee-detail and
cohort files are available and a historical rebuild is intended.

## Promote a Reviewed Candidate

```cmd
env\Scripts\python.exe scripts\promote_estimator.py
```

Promotion verifies the candidate digest and source commit, writes the reviewed
reduced package to `docs/data/`, reruns the release checks, and stages only
`docs/data/`. It does not commit, push, enable GitHub Pages, or change repository
visibility.

## Local Site

```cmd
env\Scripts\python.exe -m http.server 8000 --directory docs
```

Open `http://localhost:8000/`.

GitHub Pages should publish the `/docs` directory only after the intended
audience and the downloadable JSON content have been approved.

## Draft Fee-Schedule PDFs

Keep the complete internal catalog selected for PDF generation in
`output-prod/catalog/`. Do not use the reduced `docs/data/` package.
Retain older builds and drafts under `output-prod/archive/`; they are not
active release candidates.

```cmd
env\Scripts\python.exe scripts\render_fee_schedule.py output-prod\catalog --permit-type "DSLC - Zoning Permit" --work-class "Zoning Change of Use" --output-dir output-prod\pdf-draft-zoning --pdf --internal
```

This writes a PDF, an HTML preview, and rendering JSON with source hashes.
PDF output uses installed Chrome; no package is added. Choose a new output
directory for each run. The renderer lists methods and conditions without
calculating a project total. Staff review of fee content is pending.

`--internal` adds the configuration date and past-project period as a small,
italic footnote at the end. Without it, those dates are omitted. Documents
do not display technical version identifiers.

For Power Automate, export all options as rendering JSON in one flat folder:

```cmd
env\Scripts\python.exe scripts\render_fee_schedule.py output-prod\catalog --all --internal --output-dir output-prod\pdf-inputs
```

Each file uses its displayed permit type and work class, for example
`Zoning Permit - Zoning Change of Use.json`. No timestamps or random identifiers
are added. The export checks every option and filename before writing. It
refuses an existing output directory, including one containing older drafts.

Copy only this folder to an internal SharePoint `PDF Inputs` folder. Keep it
separate from source data, refresh outputs, archived drafts, and the public
`docs/data/` package. Point the flow only at `PDF Inputs`, not `output-prod/`.
On later exports, retain the previous inputs outside the active folder before
replacing them. Copying new files over old ones can leave retired options behind.

The three PDF layouts have been approved. Power Automate has rendered one JSON
draft as HTML, converted it through OneDrive for Business, and saved the PDF to
SharePoint. Multi-page and full-batch conversion still need checking. All files
remain drafts pending staff review of the fee content.

## Remaining Work

- Resolve missing form choices and unsupported calculations with staff when
  they are available. Until then, keep affected fees subject to review.
- Configure the flow to process `PDF Inputs` sequentially. Check multi-page
  conversion and about 10 options before running all 199.
- Verify a live data refresh after the saved-data checks. Only the user runs
  API extractions and database queries in this workflow.
- Confirm SQL encryption settings before live organizational use.
- Recheck the hosted site after updates, including
  project-subpath loading, calculations, review notices, and narrow screens.
