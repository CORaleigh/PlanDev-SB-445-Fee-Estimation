from __future__ import annotations

import argparse
import json
from pathlib import Path

from fee_assessment_roles import (
    EXPLICIT_HIDDEN_RE,
    dependency_reference_index,
)


DEFAULT_CATALOG = "output-prod/catalog"
DEFAULT_OUTPUT = (
    "output-prod/generated/126_catalog_fee_assessment_evidence.sql"
)
DEFAULT_START_DATE = "2024-07-01"


def sql_literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dependency_rows(catalog_dir):
    manifest = load_json(catalog_dir / "manifest.json")
    rows = []

    for option_item in manifest.get("options") or []:
        document = load_json(catalog_dir / str(option_item["path"]))
        option = document.get("option") or {}
        fees = list(document.get("fees") or [])
        by_id = {
            str(fee.get("feeTemplateFeeId") or ""): fee
            for fee in fees
            if fee.get("feeTemplateFeeId")
        }
        references = dependency_reference_index(fees)

        for fee_id, referenced_by in references.items():
            fee = by_id.get(fee_id)
            if fee is None:
                continue
            name = str(fee.get("name") or "")
            rows.append(
                {
                    "permitType": str(option.get("permitType") or ""),
                    "workClass": str(option.get("workClass") or ""),
                    "feeTemplateFeeId": fee_id,
                    "expectedName": name,
                    "explicitHidden": bool(
                        EXPLICIT_HIDDEN_RE.search(name)
                    ),
                    "referencedByCount": len(referenced_by),
                }
            )

    rows.sort(
        key=lambda item: (
            item["permitType"].casefold(),
            item["workClass"].casefold(),
            item["expectedName"].casefold(),
            item["feeTemplateFeeId"],
        )
    )
    for index, row in enumerate(rows, start=1):
        row["candidateOrder"] = index
    return manifest, rows


def render_candidate_inserts(rows, batch_size=900):
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    statements = []
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset: offset + batch_size]
        values = ",\n".join(
            "    ({order}, {permit}, {work}, {fee_id}, {name}, {hidden}, {refs})".format(
                order=row["candidateOrder"],
                permit=sql_literal(row["permitType"]),
                work=sql_literal(row["workClass"]),
                fee_id=sql_literal(row["feeTemplateFeeId"]),
                name=sql_literal(row["expectedName"]),
                hidden=1 if row["explicitHidden"] else 0,
                refs=int(row["referencedByCount"]),
            )
            for row in batch
        )
        statements.append(
            """INSERT INTO #Candidates (
    CandidateOrder,
    PermitType,
    WorkClass,
    FeeTemplateFeeID,
    ExpectedName,
    ExplicitHidden,
    ReferencedByCount
)
VALUES
""" + values + ";"
        )
    return "\n\n".join(statements)


def render_sql(rows, start_date):
    if not rows:
        raise ValueError("Catalog contains no fee calculation dependencies.")

    candidate_inserts = render_candidate_inserts(rows)
    return f"""/*
    Catalog-wide direct fee-assessment evidence query.

    Generated from the estimator calculation graph, not from permit-specific
    fee IDs. One candidate row is emitted for every direct catalog fee
    assignment referenced by another fee calculation.

    Cohort:
      applied on/after {start_date}
      mature issued/finalized
      non-terminal

    Result 1:
      compact coverage summary.

    Result 2:
      import-ready exact-option / exact-assignment evidence for
      build_estimator_catalog.py --assessment-evidence.

    Interpretation boundary:
      A zero direct-assessment count does not by itself make an assignment
      dependency-only. Catalog classification still requires the structural
      dependency plus an explicit Hidden marker (or separate validated
      evidence). Positive direct-assessment evidence always keeps a dependency
      in the assessed subtotal.
*/
SET NOCOUNT ON;

DECLARE @StartDate date = '{start_date}';
DECLARE @EndDate date = CAST(GETDATE() AS date);

DROP TABLE IF EXISTS #Candidates;
DROP TABLE IF EXISTS #Cohort;

CREATE TABLE #Candidates (
    CandidateOrder int NOT NULL PRIMARY KEY,
    PermitType nvarchar(250) NOT NULL,
    WorkClass nvarchar(250) NOT NULL,
    FeeTemplateFeeID nvarchar(36) NOT NULL,
    ExpectedName nvarchar(500) NOT NULL,
    ExplicitHidden bit NOT NULL,
    ReferencedByCount int NOT NULL,
    UNIQUE (PermitType, WorkClass, FeeTemplateFeeID)
);

{candidate_inserts}

SELECT
    options.PermitType,
    options.WorkClass,
    pm.PMPERMITID,
    pm.APPLYDATE
INTO #Cohort
FROM (
    SELECT DISTINCT PermitType, WorkClass
    FROM #Candidates
) options
INNER JOIN dbo.PMPERMITTYPE pmt
    ON pmt.NAME = options.PermitType
INNER JOIN dbo.PMPERMITWORKCLASS wc
    ON wc.NAME = options.WorkClass
INNER JOIN dbo.PMPERMIT pm
    ON pm.PMPERMITTYPEID = pmt.PMPERMITTYPEID
   AND pm.PMPERMITWORKCLASSID = wc.PMPERMITWORKCLASSID
INNER JOIN dbo.PMPERMITSTATUS ps
    ON pm.PMPERMITSTATUSID = ps.PMPERMITSTATUSID
WHERE
    pm.APPLYDATE >= @StartDate
    AND pm.APPLYDATE < DATEADD(DAY, 1, @EndDate)
    AND UPPER(LTRIM(RTRIM(ps.NAME))) NOT IN (
        'VOID','WITHDRAWN','CANCELLED','CANCELED'
    )
    AND (pm.ISSUEDATE IS NOT NULL OR pm.FINALIZEDATE IS NOT NULL);

CREATE UNIQUE CLUSTERED INDEX IX_126_Cohort
    ON #Cohort (PermitType, WorkClass, PMPERMITID);

;WITH OptionStats AS (
    SELECT
        PermitType,
        WorkClass,
        COUNT_BIG(*) AS MaturePermitRows
    FROM #Cohort
    GROUP BY PermitType, WorkClass
),
Observed AS (
    SELECT
        cohort.PermitType,
        cohort.WorkClass,
        candidate.FeeTemplateFeeID,
        COUNT(DISTINCT cohort.PMPERMITID) AS CasesDirectlyCharged,
        COUNT_BIG(*) AS ActiveDirectFeeRows,
        SUM(CASE WHEN cf.COMPUTEDAMOUNT > 0 THEN 1 ELSE 0 END)
            AS PositiveAmountRows,
        SUM(COALESCE(cf.COMPUTEDAMOUNT, 0)) AS TotalComputedAmount
    FROM #Cohort cohort
    INNER JOIN dbo.PMPERMITFEE pf
        ON cohort.PMPERMITID = pf.PMPERMITID
    INNER JOIN dbo.CACOMPUTEDFEE cf
        ON pf.CACOMPUTEDFEEID = cf.CACOMPUTEDFEEID
    INNER JOIN #Candidates candidate
        ON candidate.PermitType = cohort.PermitType
       AND candidate.WorkClass = cohort.WorkClass
       AND candidate.FeeTemplateFeeID = cf.CAFEETEMPLATEFEEID
    WHERE
        cf.CASTATUSID NOT IN (5, 10)
        AND COALESCE(cf.ISDELETED, 0) = 0
    GROUP BY
        cohort.PermitType,
        cohort.WorkClass,
        candidate.FeeTemplateFeeID
),
Evidence AS (
    SELECT
        candidate.CandidateOrder,
        candidate.PermitType,
        candidate.WorkClass,
        candidate.FeeTemplateFeeID,
        candidate.ExpectedName,
        candidate.ExplicitHidden,
        candidate.ReferencedByCount,
        COALESCE(stats.MaturePermitRows, 0) AS MaturePermitRows,
        COALESCE(observed.CasesDirectlyCharged, 0)
            AS CasesDirectlyCharged,
        COALESCE(observed.ActiveDirectFeeRows, 0)
            AS ActiveDirectFeeRows,
        COALESCE(observed.PositiveAmountRows, 0)
            AS PositiveAmountRows,
        COALESCE(observed.TotalComputedAmount, 0)
            AS TotalComputedAmount
    FROM #Candidates candidate
    LEFT JOIN OptionStats stats
        ON stats.PermitType = candidate.PermitType
       AND stats.WorkClass = candidate.WorkClass
    LEFT JOIN Observed observed
        ON observed.PermitType = candidate.PermitType
       AND observed.WorkClass = candidate.WorkClass
       AND observed.FeeTemplateFeeID = candidate.FeeTemplateFeeID
)
SELECT
    COUNT_BIG(*) AS CandidateAssignments,
    COUNT(DISTINCT PermitType + N'|' + WorkClass) AS CandidateOptions,
    SUM(CASE WHEN MaturePermitRows > 0 THEN 1 ELSE 0 END)
        AS AssignmentsWithMatureCoverage,
    SUM(CASE WHEN ActiveDirectFeeRows > 0 THEN 1 ELSE 0 END)
        AS AssignmentsDirectlyAssessed,
    SUM(CASE
        WHEN ExplicitHidden = 1
         AND MaturePermitRows > 0
         AND ActiveDirectFeeRows = 0
        THEN 1 ELSE 0 END) AS HiddenUnassessedCandidates
FROM Evidence;

;WITH OptionStats AS (
    SELECT
        PermitType,
        WorkClass,
        COUNT_BIG(*) AS MaturePermitRows
    FROM #Cohort
    GROUP BY PermitType, WorkClass
),
Observed AS (
    SELECT
        cohort.PermitType,
        cohort.WorkClass,
        candidate.FeeTemplateFeeID,
        COUNT(DISTINCT cohort.PMPERMITID) AS CasesDirectlyCharged,
        COUNT_BIG(*) AS ActiveDirectFeeRows,
        SUM(CASE WHEN cf.COMPUTEDAMOUNT > 0 THEN 1 ELSE 0 END)
            AS PositiveAmountRows,
        SUM(COALESCE(cf.COMPUTEDAMOUNT, 0)) AS TotalComputedAmount
    FROM #Cohort cohort
    INNER JOIN dbo.PMPERMITFEE pf
        ON cohort.PMPERMITID = pf.PMPERMITID
    INNER JOIN dbo.CACOMPUTEDFEE cf
        ON pf.CACOMPUTEDFEEID = cf.CACOMPUTEDFEEID
    INNER JOIN #Candidates candidate
        ON candidate.PermitType = cohort.PermitType
       AND candidate.WorkClass = cohort.WorkClass
       AND candidate.FeeTemplateFeeID = cf.CAFEETEMPLATEFEEID
    WHERE
        cf.CASTATUSID NOT IN (5, 10)
        AND COALESCE(cf.ISDELETED, 0) = 0
    GROUP BY
        cohort.PermitType,
        cohort.WorkClass,
        candidate.FeeTemplateFeeID
)
SELECT
    candidate.CandidateOrder,
    candidate.PermitType,
    candidate.WorkClass,
    'MATURE_ISSUED_OR_FINALIZED' AS DenominatorName,
    COALESCE(stats.MaturePermitRows, 0) AS MaturePermitRows,
    candidate.FeeTemplateFeeID,
    candidate.ExpectedName,
    candidate.ExplicitHidden,
    candidate.ReferencedByCount,
    COALESCE(observed.CasesDirectlyCharged, 0)
        AS CasesDirectlyCharged,
    COALESCE(observed.ActiveDirectFeeRows, 0)
        AS ActiveDirectFeeRows,
    COALESCE(observed.PositiveAmountRows, 0)
        AS PositiveAmountRows,
    COALESCE(observed.TotalComputedAmount, 0)
        AS TotalComputedAmount
FROM #Candidates candidate
LEFT JOIN OptionStats stats
    ON stats.PermitType = candidate.PermitType
   AND stats.WorkClass = candidate.WorkClass
LEFT JOIN Observed observed
    ON observed.PermitType = candidate.PermitType
   AND observed.WorkClass = candidate.WorkClass
   AND observed.FeeTemplateFeeID = candidate.FeeTemplateFeeID
ORDER BY candidate.CandidateOrder;

DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Candidates;
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Generate one catalog-wide SQL query that measures direct "
            "assessment of every fee calculation dependency."
        )
    )
    parser.add_argument(
        "catalog_dir",
        nargs="?",
        default=DEFAULT_CATALOG,
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE,
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
    )
    return parser.parse_args()


def main():
    args = parse_args()
    catalog_dir = Path(args.catalog_dir)
    manifest, rows = dependency_rows(catalog_dir)
    sql = render_sql(rows, args.start_date)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(sql, encoding="utf-8")

    options = {
        (row["permitType"], row["workClass"])
        for row in rows
    }
    hidden = sum(row["explicitHidden"] for row in rows)
    print(
        "Catalog:      "
        f"{manifest.get('catalogVersion') or '(unknown)'}"
    )
    print(f"Options:      {len(options):,}")
    print(f"Dependencies: {len(rows):,}")
    print(f"Hidden:       {hidden:,}")
    print(f"SQL output:   {output}")


if __name__ == "__main__":
    main()
