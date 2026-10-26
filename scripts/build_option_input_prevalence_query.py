from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from uuid import UUID

from option_ui_schema import build_option_ui_schema


DEFAULT_CATALOG = "output-prod/catalog"
DEFAULT_START_DATE = "2024-07-01"


def sql_literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def load_options(catalog_dir):
    catalog_dir = Path(catalog_dir).resolve()
    manifest = json.loads(
        (catalog_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schemaVersion") != "1.0":
        raise ValueError("Use the complete internal catalog, not docs/data.")
    documents = []
    identities = set()
    for entry in manifest.get("options") or []:
        source = (catalog_dir / entry["path"]).resolve()
        if not source.is_relative_to(catalog_dir / "options"):
            raise ValueError("Option path must remain inside the catalog's options directory.")
        document = json.loads(source.read_text(encoding="utf-8"))
        option = document.get("option") or {}
        if any(option.get(key) != entry.get(key) for key in ("optionKey", "permitType", "workClass")):
            raise ValueError("Option identity does not match its manifest entry.")
        if document.get("schemaVersion") != "1.0" or document.get("catalogVersion") != manifest.get("catalogVersion"):
            raise ValueError("Option and manifest versions do not match.")
        if document.get("configuration", {}).get("asOfDate") != manifest.get("configurationAsOfDate"):
            raise ValueError("Option and manifest configuration dates do not match.")
        identity = option.get("optionKey")
        if not identity or identity in identities:
            raise ValueError("Catalog option identities must be nonempty and unique.")
        identities.add(identity)
        documents.append(document)
    if not documents:
        raise ValueError("The catalog contains no options.")
    return documents


def load_option(catalog_dir, permit_type, work_class):
    matches = [
        document for document in load_options(catalog_dir)
        if document["option"]["permitType"] == permit_type
        and document["option"]["workClass"] == work_class
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one catalog option for {permit_type!r} / "
            f"{work_class!r}; found {len(matches)}."
        )
    return matches[0]


def field_rows(document, *, allow_empty=False):
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

        if not key or len(key) > 128 or len(label) > 250:
            raise ValueError("Field keys and labels must fit the SQL source contract.")

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
    if not rows and not allow_empty:
        raise ValueError("Selected option has no display-ready controls.")
    return rows


def render_sql(document, rows, start_date, end_date=None, *, batch=False):
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
    date.fromisoformat(start_date)
    if end_date:
        date.fromisoformat(end_date)
        if start_date > end_date:
            raise ValueError("Start date must not follow end date.")
    declarations = "" if batch else (
        f"DECLARE @StartDate date = '{start_date}';\n"
        + (f"DECLARE @EndDate date = '{end_date}';\n" if end_date
           else "DECLARE @EndDate date = CAST(GETDATE() AS date);\n")
        + f"DECLARE @PermitType nvarchar(250) = {sql_literal(permit_type)};\n"
        + f"DECLARE @WorkClass nvarchar(250) = {sql_literal(work_class)};"
    )
    field_source = (
        "SELECT FieldOrder, InputKey, InputLabel, SourceKind, SourceColumn, ValueKind\n"
        "FROM #BatchFields WHERE OptionKey = @OptionKey;"
        if batch else f"VALUES\n{values};"
    )
    cohort_filter = (
        "pm.PMPERMITTYPEID = @PermitTypeId\n    AND pm.PMPERMITWORKCLASSID = @WorkClassId"
        if batch else "pmt.NAME = @PermitType\n    AND wc.NAME = @WorkClass"
    )
    context_target = "INSERT INTO #BatchContext" if batch else ""
    prevalence_target = "INSERT INTO #BatchPrevalence" if batch else ""

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

{declarations}

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
{field_source}

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
    {cohort_filter}
    AND pm.APPLYDATE >= @StartDate
    AND pm.APPLYDATE < DATEADD(DAY, 1, @EndDate)
    AND UPPER(LTRIM(RTRIM(ps.NAME))) NOT IN (
        'VOID','WITHDRAWN','CANCELLED','CANCELED'
    )
    AND (pm.ISSUEDATE IS NOT NULL OR pm.FINALIZEDATE IS NOT NULL);

CREATE UNIQUE CLUSTERED INDEX IX_124_Cohort_Permit
    ON #Cohort (PMPERMITID);

IF EXISTS (
    SELECT cs.ID
    FROM dbo.CUSTOMSAVERPERMITMANAGEMENT cs
    INNER JOIN #Cohort c ON c.PMPERMITID = cs.ID
    GROUP BY cs.ID HAVING COUNT_BIG(*) > 1
)
    THROW 50001, 'Duplicate custom-saver rows would inflate field percentages.', 1;

-------------------------------------------------------------------------------
-- Result 1: denominator/linkage context.
-------------------------------------------------------------------------------
{context_target}
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
{prevalence_target}
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
{"" if batch else "ORDER BY FieldOrder"};

DROP TABLE IF EXISTS #Prevalence;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Fields;
"""


def insert_rows(table, columns, rows):
    statements = []
    for offset in range(0, len(rows), 1000):
        values = ["(" + ", ".join(str(value) if isinstance(value, int) else sql_literal(value)
                                  for value in row) + ")" for row in rows[offset:offset + 1000]]
        statements.append(f"INSERT INTO {table} ({columns}) VALUES\n" + ",\n".join(values) + ";")
    return "\n".join(statements)


def query_period(documents, start=None, end=None):
    periods = {tuple((doc.get("configuration", {}).get("historicalPeriod") or {}).get(key)
                     for key in ("startDate", "endDate")) for doc in documents}
    if len(periods) > 1 and not (start and end):
        raise ValueError("Catalog history periods differ; supply --start-date and --end-date.")
    period = next(iter(periods)) if len(periods) == 1 else (None, None)
    start, end = start or period[0] or DEFAULT_START_DATE, end or period[1] or date.today().isoformat()
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError("Start date must not follow end date.")
    return start, end


def render_batch_sql(selections, start_date, end_date):
    options, fields, identities = [], [], set()
    for document, rows in selections:
        option = document["option"]
        permit_id, work_id = str(UUID(option["permitTypeId"])), str(UUID(option["workClassId"]))
        key = permit_id + "|" + work_id
        if str(option["optionKey"]).lower() != key or key in identities:
            raise ValueError("Batch options must have unique, matching source identities.")
        identities.add(key)
        options.append((key, permit_id, work_id, option["permitType"], option["workClass"]))
        fields.extend((key, row["order"], row["key"], row["label"], row["source_kind"],
                       row["source_column"], row["value_kind"]) for row in rows)
    if not options or not fields:
        raise ValueError("No options with supported project fields were selected.")
    option_sql = insert_rows("#BatchOptions", "OptionKey, PermitTypeId, WorkClassId, PermitType, WorkClass", options)
    field_sql = insert_rows("#BatchFields", "OptionKey, FieldOrder, InputKey, InputLabel, SourceKind, SourceColumn, ValueKind", fields)
    template = render_sql({"option": {"permitType": "Batch", "workClass": "Batch"}}, [],
                          start_date, end_date, batch=True)
    return f"""-- Direct field history for all selected permit/work-class options.
-- Reads EnerGov tables and writes only session-local temporary tables.
-- Result 1: cohort context. Result 2: catalog-ready field history.
SET NOCOUNT ON;
DECLARE @StartDate date = '{start_date}', @EndDate date = '{end_date}';
DROP TABLE IF EXISTS #BatchOptions;
DROP TABLE IF EXISTS #BatchFields;
DROP TABLE IF EXISTS #BatchContext;
DROP TABLE IF EXISTS #BatchPrevalence;
CREATE TABLE #BatchOptions (
    OptionKey nvarchar(73) NOT NULL PRIMARY KEY,
    PermitTypeId uniqueidentifier NOT NULL, WorkClassId uniqueidentifier NOT NULL,
    PermitType nvarchar(250) NOT NULL, WorkClass nvarchar(250) NOT NULL
);
CREATE TABLE #BatchFields (
    OptionKey nvarchar(73) NOT NULL, FieldOrder int NOT NULL, InputKey sysname NOT NULL,
    InputLabel nvarchar(250) NOT NULL, SourceKind varchar(30) NOT NULL,
    SourceColumn sysname NOT NULL, ValueKind varchar(20) NOT NULL,
    PRIMARY KEY (OptionKey, InputKey)
);
CREATE TABLE #BatchContext (
    PermitType nvarchar(250), WorkClass nvarchar(250), DenominatorName varchar(40),
    PermitRows bigint, LinkedCustomSaverRows bigint,
    FirstAppliedDate datetime2, LastAppliedDate datetime2
);
CREATE TABLE #BatchPrevalence (
    PermitType nvarchar(250), WorkClass nvarchar(250), DenominatorName varchar(40),
    PermitRows bigint, FieldOrder int, InputKey sysname, InputLabel nvarchar(250),
    SourceKind varchar(30), SourceColumn sysname, ValueKind varchar(20),
    PhysicalColumnFound bit, PopulatedRows bigint, PopulatedPct decimal(9,4),
    ActiveRows bigint, ActivePct decimal(9,4), ActiveDefinition nvarchar(120)
);
{option_sql}
{field_sql}
DECLARE @OptionKey nvarchar(73), @PermitTypeId uniqueidentifier, @WorkClassId uniqueidentifier,
        @PermitType nvarchar(250), @WorkClass nvarchar(250),
        @ReadOptionSql nvarchar(max) = {sql_literal(template)};
DECLARE option_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT OptionKey, PermitTypeId, WorkClassId, PermitType, WorkClass
    FROM #BatchOptions ORDER BY OptionKey;
OPEN option_cursor;
FETCH NEXT FROM option_cursor INTO @OptionKey,@PermitTypeId,@WorkClassId,@PermitType,@WorkClass;
WHILE @@FETCH_STATUS = 0
BEGIN
    EXEC sys.sp_executesql @ReadOptionSql,
        N'@OptionKey nvarchar(73),@PermitTypeId uniqueidentifier,@WorkClassId uniqueidentifier,
          @PermitType nvarchar(250),@WorkClass nvarchar(250),@StartDate date,@EndDate date',
        @OptionKey,@PermitTypeId,@WorkClassId,@PermitType,@WorkClass,@StartDate,@EndDate;
    FETCH NEXT FROM option_cursor INTO @OptionKey,@PermitTypeId,@WorkClassId,@PermitType,@WorkClass;
END;
CLOSE option_cursor;
DEALLOCATE option_cursor;
SELECT *, @StartDate AS PeriodStartDate, @EndDate AS PeriodEndDate
FROM #BatchContext ORDER BY PermitType, WorkClass;
SELECT * FROM #BatchPrevalence ORDER BY PermitType, WorkClass, FieldOrder;
DROP TABLE IF EXISTS #BatchPrevalence;
DROP TABLE IF EXISTS #BatchContext;
DROP TABLE IF EXISTS #BatchFields;
DROP TABLE IF EXISTS #BatchOptions;
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Generate field-history SQL for one option or all options in the internal catalog."
        )
    )
    parser.add_argument("catalog_dir", nargs="?", default=DEFAULT_CATALOG)
    parser.add_argument("--permit-type")
    parser.add_argument("--work-class")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--start-date", help="Defaults to the catalog's historical period.")
    parser.add_argument("--end-date", help="Inclusive; defaults to the catalog's historical period.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.all and (args.permit_type or args.work_class):
        parser.error("--all cannot be combined with --permit-type or --work-class.")
    if not args.all and not (args.permit_type and args.work_class):
        parser.error("Supply both --permit-type and --work-class, or use --all.")
    return args


def main():
    args = parse_args()
    catalog_dir = Path(args.catalog_dir)
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError("SQL output already exists; use --overwrite to replace it.")
    if args.all:
        documents = load_options(catalog_dir)
        selections = [(document, field_rows(document, allow_empty=True)) for document in documents]
        selections = [(document, rows) for document, rows in selections if rows]
        start, end = query_period([document for document, _ in selections], args.start_date, args.end_date)
        sql = render_batch_sql(selections, start, end)
        print(f"Options:      {len(selections)} with project fields; {len(documents) - len(selections)} without")
        print(f"Fields:       {sum(len(rows) for _, rows in selections)}")
    else:
        document = load_option(catalog_dir, args.permit_type, args.work_class)
        rows = field_rows(document)
        start, end = query_period([document], args.start_date, args.end_date)
        sql = render_sql(document, rows, start, end)
        print(f"Option:       {args.permit_type} / {args.work_class}")
        print(f"Fields:       {len(rows)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w" if args.overwrite else "x", encoding="utf-8") as handle:
        handle.write(sql)
    print(f"Period:       {start} to {end}")
    print(f"SQL output:   {output}")


if __name__ == "__main__":
    main()
