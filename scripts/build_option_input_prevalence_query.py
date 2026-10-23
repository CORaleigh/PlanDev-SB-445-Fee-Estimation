from __future__ import annotations

import argparse
import json
from pathlib import Path

from option_ui_schema import build_option_ui_schema


DEFAULT_CATALOG = "output-prod/catalog-2026-09-15-r7"
DEFAULT_START_DATE = "2024-07-01"


def sql_literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def load_option(catalog_dir, permit_type, work_class):
    manifest = json.loads(
        (catalog_dir / "manifest.json").read_text(encoding="utf-8")
    )
    matches = [
        item
        for item in manifest.get("options") or []
        if str(item.get("permitType") or "") == permit_type
        and str(item.get("workClass") or "") == work_class
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one catalog option for {permit_type!r} / "
            f"{work_class!r}; found {len(matches)}."
        )
    option_path = catalog_dir / str(matches[0]["path"])
    return json.loads(option_path.read_text(encoding="utf-8"))


def field_rows(document):
    ui = build_option_ui_schema(document)
    controls = [
        control
        for control in ui.get("controls") or []
        if control.get("displayReady")
    ]
    rows = []
    for order, control in enumerate(controls, start=1):
        key = str(control.get("key") or "").strip()
        label = str(
            control.get("displayLabel")
            or control.get("label")
            or key
        ).strip()
        control_type = str(control.get("controlType") or "")
        value_type = str(control.get("valueType") or "")

        if control_type == "number":
            value_kind = "numeric"
        elif control_type == "boolean":
            value_kind = "boolean"
        elif control_type == "select":
            value_kind = "choice"
        else:
            continue

        if key == "ValuationValue":
            source_kind = "permit"
            source_column = "VALUE"
        else:
            source_kind = "custom-saver"
            source_column = key

        rows.append(
            {
                "order": order,
                "key": key,
                "label": label,
                "source_kind": source_kind,
                "source_column": source_column,
                "value_kind": value_kind,
                "value_type": value_type,
            }
        )
    if not rows:
        raise ValueError("Selected option has no display-ready controls.")
    return rows


def render_sql(document, rows, start_date):
    option = document.get("option") or {}
    permit_type = str(option.get("permitType") or "")
    work_class = str(option.get("workClass") or "")
    values = ",\n".join(
        "    ({order}, {key}, {label}, {source_kind}, {source_column}, "
        "{value_kind})".format(
            order=row["order"],
            key=sql_literal(row["key"]),
            label=sql_literal(row["label"]),
            source_kind=sql_literal(row["source_kind"]),
            source_column=sql_literal(row["source_column"]),
            value_kind=sql_literal(row["value_kind"]),
        )
        for row in rows
    )

    return f"""/*
    Direct input-prevalence query generated from the current estimator catalog.

    Option:
      {permit_type} / {work_class}

    Source contract:
      - exact permit/work-class cohort;
      - mature, non-terminal permits only;
      - CUSTOMSAVERPERMITMANAGEMENT.ID = PMPERMIT.PMPERMITID;
      - ValuationValue is PMPERMIT.VALUE;
      - all other controls are treated as candidate custom-saver columns and
        are checked against sys.columns before use.

    Active semantics:
      numeric  = nonzero
      boolean  = true / 1
      choice   = nonblank

    Result 2 is import-ready for --input-prevalence. A percentage must not be
    shown in the staff UI unless it comes from this direct-field evidence.
*/
SET NOCOUNT ON;

DECLARE @StartDate date = '{start_date}';
DECLARE @EndDate date = CAST(GETDATE() AS date);
DECLARE @PermitType nvarchar(250) = {sql_literal(permit_type)};
DECLARE @WorkClass nvarchar(250) = {sql_literal(work_class)};

DROP TABLE IF EXISTS #Fields;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Prevalence;

CREATE TABLE #Fields (
    FieldOrder int NOT NULL PRIMARY KEY,
    InputKey sysname NOT NULL UNIQUE,
    InputLabel nvarchar(250) NOT NULL,
    SourceKind varchar(30) NOT NULL,
    SourceColumn sysname NOT NULL,
    ValueKind varchar(20) NOT NULL
);

INSERT INTO #Fields (
    FieldOrder, InputKey, InputLabel, SourceKind, SourceColumn, ValueKind
)
VALUES
{values};

SELECT
    pm.PMPERMITID,
    pm.APPLYDATE AS AppliedDate,
    pm.ISSUEDATE AS IssueDate,
    pm.FINALIZEDATE AS FinalizeDate,
    TRY_CONVERT(decimal(19,6), pm.VALUE) AS PermitValue
INTO #Cohort
FROM dbo.PMPERMIT pm
INNER JOIN dbo.PMPERMITTYPE pmt
    ON pm.PMPERMITTYPEID = pmt.PMPERMITTYPEID
INNER JOIN dbo.PMPERMITWORKCLASS wc
    ON pm.PMPERMITWORKCLASSID = wc.PMPERMITWORKCLASSID
INNER JOIN dbo.PMPERMITSTATUS ps
    ON pm.PMPERMITSTATUSID = ps.PMPERMITSTATUSID
WHERE
    pmt.NAME = @PermitType
    AND wc.NAME = @WorkClass
    AND pm.APPLYDATE >= @StartDate
    AND pm.APPLYDATE < DATEADD(DAY, 1, @EndDate)
    AND UPPER(LTRIM(RTRIM(ps.NAME))) NOT IN (
        'VOID','WITHDRAWN','CANCELLED','CANCELED'
    )
    AND (pm.ISSUEDATE IS NOT NULL OR pm.FINALIZEDATE IS NOT NULL);

CREATE UNIQUE CLUSTERED INDEX IX_124_Cohort_Permit
    ON #Cohort (PMPERMITID);

-------------------------------------------------------------------------------
-- Result 1: denominator/linkage context.
-------------------------------------------------------------------------------
SELECT
    @PermitType AS PermitType,
    @WorkClass AS WorkClass,
    'MATURE_ISSUED_OR_FINALIZED' AS DenominatorName,
    COUNT_BIG(*) AS PermitRows,
    SUM(CASE WHEN cs.ID IS NOT NULL THEN 1 ELSE 0 END) AS LinkedCustomSaverRows,
    MIN(c.AppliedDate) AS FirstAppliedDate,
    MAX(c.AppliedDate) AS LastAppliedDate
FROM #Cohort c
LEFT JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
    ON cs.ID = c.PMPERMITID;

CREATE TABLE #Prevalence (
    FieldOrder int NOT NULL,
    InputKey sysname NOT NULL,
    InputLabel nvarchar(250) NOT NULL,
    SourceKind varchar(30) NOT NULL,
    SourceColumn sysname NOT NULL,
    ValueKind varchar(20) NOT NULL,
    PhysicalColumnFound bit NOT NULL,
    PermitRows bigint NOT NULL,
    PopulatedRows bigint NULL,
    PopulatedPct decimal(9,4) NULL,
    ActiveRows bigint NULL,
    ActivePct decimal(9,4) NULL,
    ActiveDefinition nvarchar(120) NOT NULL
);

DECLARE
    @FieldOrder int,
    @InputKey sysname,
    @InputLabel nvarchar(250),
    @SourceKind varchar(30),
    @SourceColumn sysname,
    @ValueKind varchar(20),
    @ColumnFound bit,
    @Sql nvarchar(max);

DECLARE field_cursor CURSOR LOCAL FAST_FORWARD FOR
SELECT
    FieldOrder, InputKey, InputLabel, SourceKind, SourceColumn, ValueKind
FROM #Fields
ORDER BY FieldOrder;

OPEN field_cursor;
FETCH NEXT FROM field_cursor
INTO @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,@ValueKind;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @ColumnFound = CASE
        WHEN @SourceKind = 'permit'
         AND EXISTS (
            SELECT 1 FROM sys.columns
            WHERE object_id = OBJECT_ID('dbo.PMPERMIT')
              AND UPPER(name) = UPPER(@SourceColumn)
         ) THEN 1
        WHEN @SourceKind = 'custom-saver'
         AND EXISTS (
            SELECT 1 FROM sys.columns
            WHERE object_id = OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT')
              AND UPPER(name) = UPPER(@SourceColumn)
         ) THEN 1
        ELSE 0
    END;

    IF @ColumnFound = 0
    BEGIN
        INSERT INTO #Prevalence
        SELECT
            @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,
            @ValueKind,0,COUNT_BIG(*),NULL,NULL,NULL,NULL,
            CASE
                WHEN @ValueKind='numeric' THEN 'numeric <> 0'
                WHEN @ValueKind='boolean' THEN 'boolean = true'
                WHEN @ValueKind='choice' THEN 'nonblank choice'
                ELSE 'unknown'
            END
        FROM #Cohort;
    END
    ELSE IF @SourceKind = 'permit'
    BEGIN
        INSERT INTO #Prevalence
        SELECT
            @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,
            @ValueKind,1,COUNT_BIG(*),
            SUM(CASE WHEN PermitValue IS NOT NULL THEN 1 ELSE 0 END),
            CAST(
                100.0*SUM(CASE WHEN PermitValue IS NOT NULL THEN 1 ELSE 0 END)
                /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)
            ),
            SUM(CASE
                WHEN PermitValue IS NOT NULL
                 AND ABS(PermitValue)>0.0000005 THEN 1 ELSE 0 END),
            CAST(
                100.0*SUM(CASE
                    WHEN PermitValue IS NOT NULL
                     AND ABS(PermitValue)>0.0000005 THEN 1 ELSE 0 END)
                /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)
            ),
            'numeric <> 0'
        FROM #Cohort;
    END
    ELSE IF @ValueKind = 'numeric'
    BEGIN
        SET @Sql = N'
            INSERT INTO #Prevalence
            SELECT
                @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,
                @ValueKind,1,COUNT_BIG(*),
                SUM(CASE WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                    IS NOT NULL THEN 1 ELSE 0 END),
                CAST(100.0*SUM(CASE WHEN cs.'
                    + QUOTENAME(@SourceColumn) + N'
                    IS NOT NULL THEN 1 ELSE 0 END)
                    /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                SUM(CASE
                    WHEN TRY_CONVERT(decimal(19,6),cs.'
                    + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                     AND ABS(TRY_CONVERT(decimal(19,6),cs.'
                    + QUOTENAME(@SourceColumn) + N'))>0.0000005
                    THEN 1 ELSE 0 END),
                CAST(100.0*SUM(CASE
                    WHEN TRY_CONVERT(decimal(19,6),cs.'
                    + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                     AND ABS(TRY_CONVERT(decimal(19,6),cs.'
                    + QUOTENAME(@SourceColumn) + N'))>0.0000005
                    THEN 1 ELSE 0 END)
                    /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                N''numeric <> 0''
            FROM #Cohort c
            LEFT JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID=c.PMPERMITID;';

        EXEC sys.sp_executesql @Sql,
            N'@FieldOrder int,@InputKey sysname,@InputLabel nvarchar(250),
              @SourceKind varchar(30),@SourceColumn sysname,@ValueKind varchar(20)',
            @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,@ValueKind;
    END
    ELSE IF @ValueKind = 'boolean'
    BEGIN
        SET @Sql = N'
            INSERT INTO #Prevalence
            SELECT
                @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,
                @ValueKind,1,COUNT_BIG(*),
                SUM(CASE WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                    IS NOT NULL THEN 1 ELSE 0 END),
                CAST(100.0*SUM(CASE WHEN cs.'
                    + QUOTENAME(@SourceColumn) + N'
                    IS NOT NULL THEN 1 ELSE 0 END)
                    /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                SUM(CASE WHEN TRY_CONVERT(bit,cs.'
                    + QUOTENAME(@SourceColumn) + N')=1 THEN 1 ELSE 0 END),
                CAST(100.0*SUM(CASE WHEN TRY_CONVERT(bit,cs.'
                    + QUOTENAME(@SourceColumn) + N')=1 THEN 1 ELSE 0 END)
                    /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                N''boolean = true''
            FROM #Cohort c
            LEFT JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID=c.PMPERMITID;';

        EXEC sys.sp_executesql @Sql,
            N'@FieldOrder int,@InputKey sysname,@InputLabel nvarchar(250),
              @SourceKind varchar(30),@SourceColumn sysname,@ValueKind varchar(20)',
            @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,@ValueKind;
    END
    ELSE
    BEGIN
        SET @Sql = N'
            INSERT INTO #Prevalence
            SELECT
                @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,
                @ValueKind,1,COUNT_BIG(*),
                SUM(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),cs.'
                    + QUOTENAME(@SourceColumn) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END),
                CAST(100.0*SUM(CASE
                    WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),cs.'
                    + QUOTENAME(@SourceColumn) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END)
                    /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                SUM(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),cs.'
                    + QUOTENAME(@SourceColumn) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END),
                CAST(100.0*SUM(CASE
                    WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),cs.'
                    + QUOTENAME(@SourceColumn) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END)
                    /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                N''nonblank choice''
            FROM #Cohort c
            LEFT JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID=c.PMPERMITID;';

        EXEC sys.sp_executesql @Sql,
            N'@FieldOrder int,@InputKey sysname,@InputLabel nvarchar(250),
              @SourceKind varchar(30),@SourceColumn sysname,@ValueKind varchar(20)',
            @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,@ValueKind;
    END;

    FETCH NEXT FROM field_cursor
    INTO @FieldOrder,@InputKey,@InputLabel,@SourceKind,@SourceColumn,@ValueKind;
END;

CLOSE field_cursor;
DEALLOCATE field_cursor;

-------------------------------------------------------------------------------
-- Result 2: import-ready direct input prevalence.
-------------------------------------------------------------------------------
SELECT
    @PermitType AS PermitType,
    @WorkClass AS WorkClass,
    'MATURE_ISSUED_OR_FINALIZED' AS DenominatorName,
    PermitRows,
    FieldOrder,
    InputKey,
    InputLabel,
    SourceKind,
    SourceColumn,
    ValueKind,
    PhysicalColumnFound,
    PopulatedRows,
    PopulatedPct,
    ActiveRows,
    ActivePct,
    ActiveDefinition
FROM #Prevalence
ORDER BY FieldOrder;

DROP TABLE IF EXISTS #Prevalence;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Fields;
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a direct EnerGov input-prevalence SQL query for one "
            "current catalog permit/work-class option."
        )
    )
    parser.add_argument("catalog_dir", nargs="?", default=DEFAULT_CATALOG)
    parser.add_argument("--permit-type", required=True)
    parser.add_argument("--work-class", required=True)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    catalog_dir = Path(args.catalog_dir)
    document = load_option(
        catalog_dir,
        args.permit_type,
        args.work_class,
    )
    rows = field_rows(document)
    sql = render_sql(document, rows, args.start_date)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(sql, encoding="utf-8")
    print(f"Option:       {args.permit_type} / {args.work_class}")
    print(f"Fields:       {len(rows)}")
    print(f"SQL output:   {output}")


if __name__ == "__main__":
    main()
