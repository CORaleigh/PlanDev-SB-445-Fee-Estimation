# Updating the Fee Data

This repository contains the reusable API, SQL, build, test, and release
tools. Credentials, query results, source extracts, and generated candidates
stay under ignored local directories.

A live refresh retrieves permit-fee configuration from the EnerGov API.
Resuming a reviewed refresh uses its saved extract and makes no API requests.
Both reuse supporting evidence unless it needs to be rebuilt.

The estimator does not calculate statutory business-day deadlines. It does
not use a holiday table. Deadline tracking requires a separate process that
records application dates, completeness decisions, revisions, and final fee
statements.

The refresh builds a complete internal catalog under `output-prod/`, then
creates a reduced public package for the static site. Use the complete catalog
for evidence queries, review, and PDFs. Do not use `docs/data/` for those tasks.

`config/estimation-review.json` lists fees that must remain subject to staff
review. The build preserves their source rates and rules, but neither they
nor calculations that need their amounts can enter a total until resolved.
The review configuration is checked before a live refresh makes API requests.

## Local Setup

Create the local configuration:

```cmd
copy config\refresh-inputs.example.json config\refresh-inputs.json
copy .env.example .env
```

Set these values in `.env`:

- `ENERGOVWEBAPI_USERNAME`
- `ENERGOVWEBAPI_PASSWORD`
- `ENERGOV_SQL_SERVER`
- `ENERGOV_SQL_DATABASE`

The SQL connection uses Windows trusted authentication. Do not commit `.env`
or files under `local-data/`.

The SQL helper currently sets `Encrypt=no` and `TrustServerCertificate=yes`.
Confirm the required encryption and certificate settings with IT before
organizational use. They were not changed or tested against a live server.

## Required Local Inputs

`config\refresh-inputs.json` identifies every local input. Before the first
refresh in a new checkout, place the last approved normalized permit-fee
snapshot at:

```text
local-data/permit_fee_templates_clean.json
```

Keep that snapshot with the release record. It is the baseline used to detect
configuration changes. It is not committed because it contains more source
configuration than the public site requires.

Run the input check before any live extraction:

```cmd
env\Scripts\python.exe scripts\refresh_estimator.py --as-of YYYY-MM-DD --check-inputs
```

The command reports missing inputs and makes no EnerGov API requests.

## Historical Fee and Related-Permit Evidence

Run both queries from the same database snapshot:

```cmd
env\Scripts\python.exe scripts\run_query.py queries\permit_family_fee_detail.sql --output local-data\permit_family_fee_detail.csv --overwrite --preview-rows 0
```

```cmd
env\Scripts\python.exe scripts\run_query.py queries\15_permit_family_cohort.sql --output local-data\permit_family_cohort.csv --overwrite --preview-rows 0
```

Build the percentages used for fee occurrence and related permits:

```cmd
env\Scripts\python.exe scripts\build_fee_usage_patterns.py local-data\permit_family_fee_detail.csv --cohort-csv local-data\permit_family_cohort.csv --templates-json local-data\permit_fee_templates_clean.json --output local-data\permit_fee_usage_patterns.json --period-grain both --compact
```

The main refresh performs this last build step when `--refresh-history` is
used. It still requires the two current CSV files.

## Inspection Fees

Extract and normalize the current inspection fee configuration:

```cmd
env\Scripts\python.exe scripts\inspection_fee_extract.py --environment prod --output-dir local-data
```

```cmd
env\Scripts\python.exe scripts\clean_inspection_fee_templates.py local-data\inspection_fee_templates.json --output local-data\inspection_fee_templates_clean.json --as-of YYYY-MM-DD --compact
```

Refresh the inspection-status reference and validate the rules:

```cmd
env\Scripts\python.exe scripts\run_query.py queries\12_inspection_fee_condition_statuses.sql --output local-data\inspection_status_reference.csv --overwrite --preview-rows 0
```

```cmd
env\Scripts\python.exe scripts\validate_inspection_fee_rules.py local-data\inspection_fee_templates_clean.json --status-reference local-data\inspection_status_reference.csv --output local-data\inspection_fee_rule_validation.json --technology-csv local-data\inspection_technology_fee_examples.csv
```

Query 13 is retained for direct review of technology-surcharge assessment
behavior when that rule changes.

## Choice Labels

Available choices come from the selected form in the API extract. SQL labels
alone do not establish which choices that form permits. Unlabeled choices are
omitted only when no fee condition references them. Missing fields, ambiguous
labels, and referenced choices without labels remain subject to staff review.
Changes to the form or its choices require refresh review.

```cmd
env\Scripts\python.exe scripts\run_query.py queries\116_authoritative_condition_choice_labels.sql --all-results --output-dir local-data\choice-labels --overwrite --preview-rows 0
```

`result-01.csv` contains the approved labels. Confirm that the conflict result
is empty before using the file.

If a catalog choice still lacks a label, run the gap query:

```cmd
env\Scripts\python.exe scripts\run_query.py queries\117_condition_choice_label_gaps.sql --all-results --output-dir local-data\choice-label-gaps --overwrite --preview-rows 0
```

Do not infer labels for missing or blank pick-list items. The affected fee must
remain subject to staff review until its configured choice is confirmed.

## Project-Detail Percentages

Queries 122 and 123 reproduce the two earlier broad option studies:

```cmd
env\Scripts\python.exe scripts\run_query.py queries\122_building_accessory_input_prevalence.sql --all-results --output-dir local-data\building-accessory-prevalence --overwrite --preview-rows 0
```

```cmd
env\Scripts\python.exe scripts\normalize_input_prevalence_results.py local-data\building-accessory-prevalence\result-03.csv --catalog-dir PATH\TO\INTERNAL-CATALOG --permit-type "DSINSP - Building (Non-Residential)" --work-class "Accessory Structure" --output local-data\building_accessory_input_prevalence.csv
```

```cmd
env\Scripts\python.exe scripts\run_query.py queries\123_row_occupancy_scenario_prevalence.sql --all-results --output-dir local-data\row-occupancy-prevalence --overwrite --preview-rows 0
```

```cmd
env\Scripts\python.exe scripts\normalize_input_prevalence_results.py local-data\row-occupancy-prevalence\result-03.csv --catalog-dir PATH\TO\INTERNAL-CATALOG --permit-type "DSENG - Right of Way" --work-class "Right-of-Way Occupancy" --output local-data\row_occupancy_input_prevalence.csv
```

Generate the electrical query from the released controls instead of keeping a
permit-specific generated SQL file in the repository:

```cmd
env\Scripts\python.exe scripts\build_option_input_prevalence_query.py PATH\TO\INTERNAL-CATALOG --permit-type "DSINSP - Electrical (Non-Residential)" --work-class "Alteration/Repairs" --output local-data\electrical_nonres_alteration_input_prevalence.sql
```

```cmd
env\Scripts\python.exe scripts\run_query.py local-data\electrical_nonres_alteration_input_prevalence.sql --all-results --output-dir local-data\electrical-prevalence --overwrite --preview-rows 0
```

## Fee-Assessment Evidence

Generate the query from the complete fee calculation relationships under
review, then run it. Use the retained internal catalog for the current release.

```cmd
env\Scripts\python.exe scripts\build_fee_assessment_evidence_query.py PATH\TO\INTERNAL-CATALOG --output local-data\catalog_fee_assessment_evidence.sql
```

```cmd
env\Scripts\python.exe scripts\run_query.py local-data\catalog_fee_assessment_evidence.sql --all-results --output-dir local-data\fee-assessment --overwrite --preview-rows 0
```

`result-02.csv` is the catalog input.

## Fixed-Fee Behavior

Rebuild fixed-fee evidence when fixed assignments or amounts change. Use the
catalog directory being reviewed. A temporary review catalog may use the prior
evidence, but it must be rebuilt with the new summary before promotion.

```cmd
env\Scripts\python.exe scripts\analyze_fixed_fee_behavior.py PATH\TO\CATALOG local-data\permit_family_fee_detail.csv --validation-start YYYY-MM-DD --validation-end YYYY-MM-DD --output-dir local-data\fixed-fee --overwrite
```

The date range must cover the period being reviewed. Review the summary before
using it in a release.

## Refresh and Release Candidate

After the input check passes:

```cmd
env\Scripts\python.exe scripts\refresh_estimator.py --as-of YYYY-MM-DD
```

The refresh stops when the current configuration differs materially from the
approved baseline. Review the report with:

```cmd
env\Scripts\python.exe scripts\summarize_refresh_changes.py output-prod\refresh-YYYY-MM-DD\permit_template_change_report.json --assignment-details
```

Do not promote a changed catalog until the report and affected evidence have
been reviewed. Promotion does not push, publish, or enable GitHub Pages.

Build and staging commands require new output directories. Promotion refuses
untracked or ignored files under `docs/data/` and an existing `docs/data.tmp/`.
Move any local files elsewhere before replacing the reviewed site data.

After approval, record the accepted changes, intended use, and any remaining
restrictions in a local text file. Resume from the saved summary with a new
version:

```cmd
env\Scripts\python.exe scripts\refresh_estimator.py --resume output-prod\refresh-YYYY-MM-DD\estimator-refresh-summary.json --review-note local-data\refresh-approval.txt --catalog-version YYYY-MM-DD-reviewed-r1
```

Resume makes no API requests. It normalizes the saved extract with the current
code, retains the original configuration date, and reruns the comparison,
build, tests, and release audits. Review any normalization or runtime changes
as well as the source report before resuming. The original files remain
unchanged; new outputs use the version above. The new summary records the
approval note and source hashes without clearing the report's review flags.

The staged candidate contains only the reduced public package. The complete
catalog remains the source for later PDF generation.
