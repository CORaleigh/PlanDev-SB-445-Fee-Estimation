/*
    Right-of-Way Occupancy historical input/scenario prevalence.

    Query 121 established complete same-ID linkage for the recent ROW cohort:
      CUSTOMSAVERPERMITMANAGEMENT.ID = PMPERMIT.PMPERMITID
      3,404 / 3,404 recent permits linked.

    The r7 UI audit shows 72 compiled controls. They form an exact 18-scenario
    matrix. Each scenario has four controls:
      - primary quantity
      - secondary quantity
      - duration
      - Review applicability choice

    This query measures both field-level and scenario-level activation so the
    progressive-disclosure design can be based on observed usage rather than
    field-name heuristics.

    Denominators:
      ALL_RECENT
      NON_TERMINAL
      MATURE_ISSUED_OR_FINALIZED

    Field semantics:
      numeric populated = value is not NULL
      numeric active    = numeric value <> 0
      choice populated  = non-NULL, nonblank string
      choice active     = same as populated

    Scenario semantics:
      populated = any of its four controls is populated
      active    = any numeric control is nonzero OR Review is nonblank

    Results:
      1. denominator counts
      2. physical-source audit for all 72 controls
      3. mature field-level prevalence ranking
      4. scenario-level prevalence across all denominators
      5. mature scenario ranking
      6. mature scenario co-occurrence count per permit
      7. raw observed Review values in the mature denominator

    Prevalence remains descriptive UX evidence. It must not delete or disable a
    valid rule input by itself.
*/
SET NOCOUNT ON;

DECLARE @StartDate date = '2024-07-01';
DECLARE @EndDate date = CAST(GETDATE() AS date);

DROP TABLE IF EXISTS #Fields;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Expanded;
DROP TABLE IF EXISTS #FieldPrevalence;
DROP TABLE IF EXISTS #ScenarioActivation;
DROP TABLE IF EXISTS #ObservedReview;

CREATE TABLE #Fields (
    FieldOrder int NOT NULL PRIMARY KEY,
    ScenarioOrder int NOT NULL,
    ScenarioName nvarchar(200) NOT NULL,
    FieldRole varchar(20) NOT NULL,
    InputKey sysname NOT NULL UNIQUE,
    SourceColumn sysname NOT NULL,
    ValueKind varchar(20) NOT NULL
);

INSERT INTO #Fields (
    FieldOrder,
    ScenarioOrder,
    ScenarioName,
    FieldRole,
    InputKey,
    SourceColumn,
    ValueKind
)
VALUES
    (1, 1, 'Full Streets - Major Closures', 'primary', 'TotalMajorLF', 'TotalMajorLF', 'numeric'),
    (2, 1, 'Full Streets - Major Closures', 'secondary', 'TotalMajorFSLFSecondary', 'TotalMajorFSLFSecondary', 'numeric'),
    (3, 1, 'Full Streets - Major Closures', 'duration', 'TotalDuration', 'TotalDuration', 'numeric'),
    (4, 1, 'Full Streets - Major Closures', 'review', 'ReviewFeeFullStreetsMajorClosures', 'ReviewFeeFullStreetsMajorClosures', 'choice'),
    (5, 2, 'Full Streets - Minor Closures', 'primary', 'TotalMinorLFROW', 'TotalMinorLFROW', 'numeric'),
    (6, 2, 'Full Streets - Minor Closures', 'secondary', 'TotalMinorFSLFSecondary', 'TotalMinorFSLFSecondary', 'numeric'),
    (7, 2, 'Full Streets - Minor Closures', 'duration', 'TotalDurationMinor', 'TotalDurationMinor', 'numeric'),
    (8, 2, 'Full Streets - Minor Closures', 'review', 'ReviewFeeFullStreetsMinorClosures', 'ReviewFeeFullStreetsMinorClosures', 'choice'),
    (9, 3, 'Misc Major - Dumpster/POD', 'primary', 'TotalMISCMajorDumpLF', 'TotalMISCMajorDumpLF', 'numeric'),
    (10, 3, 'Misc Major - Dumpster/POD', 'secondary', 'TotalMMDumpsterSecondary', 'TotalMMDumpsterSecondary', 'numeric'),
    (11, 3, 'Misc Major - Dumpster/POD', 'duration', 'TotalMISCMajorDuDuration', 'TotalMISCMajorDuDuration', 'numeric'),
    (12, 3, 'Misc Major - Dumpster/POD', 'review', 'ReviewFeeMiscMajorDumpster', 'ReviewFeeMiscMajorDumpster', 'choice'),
    (13, 4, 'Misc Major - Non-Specific', 'primary', 'TotalMISCMajorNSLF', 'TotalMISCMajorNSLF', 'numeric'),
    (14, 4, 'Misc Major - Non-Specific', 'secondary', 'TotalMMNonSpecSecondary', 'TotalMMNonSpecSecondary', 'numeric'),
    (15, 4, 'Misc Major - Non-Specific', 'duration', 'TotalMISCMajorDuration', 'TotalMISCMajorDuration', 'numeric'),
    (16, 4, 'Misc Major - Non-Specific', 'review', 'ReviewFeeMiscMajorNonSpecific', 'ReviewFeeMiscMajorNonSpecific', 'choice'),
    (17, 5, 'Misc Minor - Dumpster/POD', 'primary', 'TotalMISCMinorDumpLF', 'TotalMISCMinorDumpLF', 'numeric'),
    (18, 5, 'Misc Minor - Dumpster/POD', 'secondary', 'TotalMMiDumpsterSecondary', 'TotalMMiDumpsterSecondary', 'numeric'),
    (19, 5, 'Misc Minor - Dumpster/POD', 'duration', 'TotalMISCMinorDuDuration', 'TotalMISCMinorDuDuration', 'numeric'),
    (20, 5, 'Misc Minor - Dumpster/POD', 'review', 'ReviewFeeMiscMinorDumpster', 'ReviewFeeMiscMinorDumpster', 'choice'),
    (21, 6, 'Misc Minor - Non-Specific', 'primary', 'TotalMISCMinorNSLF', 'TotalMISCMinorNSLF', 'numeric'),
    (22, 6, 'Misc Minor - Non-Specific', 'secondary', 'TotalMMiNonSpecSecondary', 'TotalMMiNonSpecSecondary', 'numeric'),
    (23, 6, 'Misc Minor - Non-Specific', 'duration', 'TotalMISCMinorDuration', 'TotalMISCMinorDuration', 'numeric'),
    (24, 6, 'Misc Minor - Non-Specific', 'review', 'ReviewFeeMiscMinorNonSpecific', 'ReviewFeeMiscMinorNonSpecific', 'choice'),
    (25, 7, 'Parking Lane - Major Closures', 'primary', 'TotalMajorLFParkLane', 'TotalMajorLFParkLane', 'numeric'),
    (26, 7, 'Parking Lane - Major Closures', 'secondary', 'TotalMajorPLLFSecondary', 'TotalMajorPLLFSecondary', 'numeric'),
    (27, 7, 'Parking Lane - Major Closures', 'duration', 'TotalDurationMajorParkLN', 'TotalDurationMajorParkLN', 'numeric'),
    (28, 7, 'Parking Lane - Major Closures', 'review', 'ReviewFeeParkingLaneMajor', 'ReviewFeeParkingLaneMajor', 'choice'),
    (29, 8, 'Parking Lane - Minor Closures', 'primary', 'TotalMinorLFParkLane', 'TotalMinorLFParkLane', 'numeric'),
    (30, 8, 'Parking Lane - Minor Closures', 'secondary', 'TotalMinorPLLFSecondary', 'TotalMinorPLLFSecondary', 'numeric'),
    (31, 8, 'Parking Lane - Minor Closures', 'duration', 'TotalDurationMinorParkLN', 'TotalDurationMinorParkLN', 'numeric'),
    (32, 8, 'Parking Lane - Minor Closures', 'review', 'ReviewFeeParkingLaneMinor', 'ReviewFeeParkingLaneMinor', 'choice'),
    (33, 9, 'Sidewalk Major - Aux Closures', 'primary', 'TotalMajorSWAUXLF', 'TotalMajorSWAUXLF', 'numeric'),
    (34, 9, 'Sidewalk Major - Aux Closures', 'secondary', 'TotalMajAUXSWLFSecondary', 'TotalMajAUXSWLFSecondary', 'numeric'),
    (35, 9, 'Sidewalk Major - Aux Closures', 'duration', 'TotalDurationSWMajorAUX', 'TotalDurationSWMajorAUX', 'numeric'),
    (36, 9, 'Sidewalk Major - Aux Closures', 'review', 'ReviewFeeSidewalkMajorAuxClosures', 'ReviewFeeSidewalkMajorAuxClosures', 'choice'),
    (37, 10, 'Sidewalk Major - Full Closures', 'primary', 'TotalMajorSWFullLF', 'TotalMajorSWFullLF', 'numeric'),
    (38, 10, 'Sidewalk Major - Full Closures', 'secondary', 'TotalMajorSWLFSecondary', 'TotalMajorSWLFSecondary', 'numeric'),
    (39, 10, 'Sidewalk Major - Full Closures', 'duration', 'TotalDurationSWMajorFull', 'TotalDurationSWMajorFull', 'numeric'),
    (40, 10, 'Sidewalk Major - Full Closures', 'review', 'ReviewFeeSidewalkMajorFullClosures', 'ReviewFeeSidewalkMajorFullClosures', 'choice'),
    (41, 11, 'Sidewalk Major - Maintenance Closures', 'primary', 'TotalMajorSWMainLF', 'TotalMajorSWMainLF', 'numeric'),
    (42, 11, 'Sidewalk Major - Maintenance Closures', 'secondary', 'TotalMajMainSWLFSecondary', 'TotalMajMainSWLFSecondary', 'numeric'),
    (43, 11, 'Sidewalk Major - Maintenance Closures', 'duration', 'TotalDurationSWMajorMain', 'TotalDurationSWMajorMain', 'numeric'),
    (44, 11, 'Sidewalk Major - Maintenance Closures', 'review', 'ReviewFeeSidewalkMajorMaintenanceClosures', 'ReviewFeeSidewalkMajorMaintenanceClosures', 'choice'),
    (45, 12, 'Sidewalk Major - Partial Closures', 'primary', 'TotalMajorSWPartialLF', 'TotalMajorSWPartialLF', 'numeric'),
    (46, 12, 'Sidewalk Major - Partial Closures', 'secondary', 'TotalMajPartSWLFSecondary', 'TotalMajPartSWLFSecondary', 'numeric'),
    (47, 12, 'Sidewalk Major - Partial Closures', 'duration', 'TotalDurationSWMajorPart', 'TotalDurationSWMajorPart', 'numeric'),
    (48, 12, 'Sidewalk Major - Partial Closures', 'review', 'ReviewFeeSidewalkMajorPartialClosures', 'ReviewFeeSidewalkMajorPartialClosures', 'choice'),
    (49, 13, 'Sidewalk Minor - Aux Closures', 'primary', 'TotalMinorSWAUXLF', 'TotalMinorSWAUXLF', 'numeric'),
    (50, 13, 'Sidewalk Minor - Aux Closures', 'secondary', 'TotalMinorAUXLFSecondary', 'TotalMinorAUXLFSecondary', 'numeric'),
    (51, 13, 'Sidewalk Minor - Aux Closures', 'duration', 'TotalMinorSWAUXDuration', 'TotalMinorSWAUXDuration', 'numeric'),
    (52, 13, 'Sidewalk Minor - Aux Closures', 'review', 'ReviewFeeSidewalkMinorAUXClosures', 'ReviewFeeSidewalkMinorAUXClosures', 'choice'),
    (53, 14, 'Sidewalk Minor - Full Closures', 'primary', 'TotalMinorSWLF', 'TotalMinorSWLF', 'numeric'),
    (54, 14, 'Sidewalk Minor - Full Closures', 'secondary', 'TotalMinorSWLFSecondary', 'TotalMinorSWLFSecondary', 'numeric'),
    (55, 14, 'Sidewalk Minor - Full Closures', 'duration', 'TotalMinorSWDuration', 'TotalMinorSWDuration', 'numeric'),
    (56, 14, 'Sidewalk Minor - Full Closures', 'review', 'ReviewFeeSidewalkMinorFullClosures', 'ReviewFeeSidewalkMinorFullClosures', 'choice'),
    (57, 15, 'Sidewalk Minor - Maintenance Closures', 'primary', 'TotalMinorSWMainLF', 'TotalMinorSWMainLF', 'numeric'),
    (58, 15, 'Sidewalk Minor - Maintenance Closures', 'secondary', 'TotalMinorMAINLFSecondary', 'TotalMinorMAINLFSecondary', 'numeric'),
    (59, 15, 'Sidewalk Minor - Maintenance Closures', 'duration', 'TotalMinorSWMainDuration', 'TotalMinorSWMainDuration', 'numeric'),
    (60, 15, 'Sidewalk Minor - Maintenance Closures', 'review', 'ReviewFeeSidewalkMinorMaintenanceClosures', 'ReviewFeeSidewalkMinorMaintenanceClosures', 'choice'),
    (61, 16, 'Sidewalk Minor - Partial Closures', 'primary', 'TotalMinorSWPartialLF', 'TotalMinorSWPartialLF', 'numeric'),
    (62, 16, 'Sidewalk Minor - Partial Closures', 'secondary', 'TotalMinorPARTLFSecondary', 'TotalMinorPARTLFSecondary', 'numeric'),
    (63, 16, 'Sidewalk Minor - Partial Closures', 'duration', 'TotalMinorSWPartDuration', 'TotalMinorSWPartDuration', 'numeric'),
    (64, 16, 'Sidewalk Minor - Partial Closures', 'review', 'ReviewFeeSidewalkMinorPartialClosures', 'ReviewFeeSidewalkMinorPartialClosures', 'choice'),
    (65, 17, 'Street Lane - Major Closures', 'primary', 'TotalMajorLFStrLN', 'TotalMajorLFStrLN', 'numeric'),
    (66, 17, 'Street Lane - Major Closures', 'secondary', 'TotalMajorSLLFSecondary', 'TotalMajorSLLFSecondary', 'numeric'),
    (67, 17, 'Street Lane - Major Closures', 'duration', 'TotalMajorSLDuration', 'TotalMajorSLDuration', 'numeric'),
    (68, 17, 'Street Lane - Major Closures', 'review', 'ReviewFeeStreetLaneMajorClosures', 'ReviewFeeStreetLaneMajorClosures', 'choice'),
    (69, 18, 'Street Lane - Minor Closures', 'primary', 'TotalMinorStreetLaneLF', 'TotalMinorStreetLaneLF', 'numeric'),
    (70, 18, 'Street Lane - Minor Closures', 'secondary', 'TotalMinorSLLFSecondary', 'TotalMinorSLLFSecondary', 'numeric'),
    (71, 18, 'Street Lane - Minor Closures', 'duration', 'TotalDurationMinorStrLN', 'TotalDurationMinorStrLN', 'numeric'),
    (72, 18, 'Street Lane - Minor Closures', 'review', 'ReviewFeeStreetLaneMinorClosures', 'ReviewFeeStreetLaneMinorClosures', 'choice');

-------------------------------------------------------------------------------
-- Exact ROW Occupancy cohort.
-------------------------------------------------------------------------------
SELECT
    pm.PMPERMITID,
    pm.PERMITNUMBER AS PermitNumber,
    pm.APPLYDATE AS AppliedDate,
    pm.ISSUEDATE AS IssueDate,
    pm.FINALIZEDATE AS FinalizeDate,
    LTRIM(RTRIM(ps.NAME)) AS PermitStatus,
    CAST(
        CASE
            WHEN UPPER(LTRIM(RTRIM(ps.NAME))) IN (
                'VOID','WITHDRAWN','CANCELLED','CANCELED'
            )
                THEN 0
            ELSE 1
        END AS bit
    ) AS IsNonTerminal,
    CAST(
        CASE
            WHEN UPPER(LTRIM(RTRIM(ps.NAME))) NOT IN (
                'VOID','WITHDRAWN','CANCELLED','CANCELED'
            )
             AND (pm.ISSUEDATE IS NOT NULL OR pm.FINALIZEDATE IS NOT NULL)
                THEN 1
            ELSE 0
        END AS bit
    ) AS IsMatureIssuedOrFinalized
INTO #Cohort
FROM dbo.PMPERMIT pm
INNER JOIN dbo.PMPERMITTYPE pmt
    ON pm.PMPERMITTYPEID = pmt.PMPERMITTYPEID
INNER JOIN dbo.PMPERMITWORKCLASS wc
    ON pm.PMPERMITWORKCLASSID = wc.PMPERMITWORKCLASSID
INNER JOIN dbo.PMPERMITSTATUS ps
    ON pm.PMPERMITSTATUSID = ps.PMPERMITSTATUSID
WHERE
    pmt.NAME = 'DSENG - Right of Way'
    AND wc.NAME = 'Right-of-Way Occupancy'
    AND pm.APPLYDATE >= @StartDate
    AND pm.APPLYDATE < DATEADD(DAY, 1, @EndDate);

CREATE UNIQUE CLUSTERED INDEX IX_123_Cohort_Permit
    ON #Cohort (PMPERMITID);

SELECT
    d.DenominatorOrder,
    d.DenominatorName,
    c.*
INTO #Expanded
FROM #Cohort c
CROSS APPLY (
    VALUES
        (1, 'ALL_RECENT', CAST(1 AS bit)),
        (2, 'NON_TERMINAL', c.IsNonTerminal),
        (3, 'MATURE_ISSUED_OR_FINALIZED', c.IsMatureIssuedOrFinalized)
) d (DenominatorOrder, DenominatorName, IncludeRow)
WHERE d.IncludeRow = 1;

CREATE INDEX IX_123_Expanded_DenomPermit
    ON #Expanded (DenominatorOrder, PMPERMITID);

-------------------------------------------------------------------------------
-- Result 1: denominator counts.
-------------------------------------------------------------------------------
SELECT
    DenominatorOrder,
    DenominatorName,
    COUNT_BIG(*) AS PermitRows,
    COUNT(DISTINCT PermitStatus) AS DistinctStatuses,
    MIN(AppliedDate) AS FirstAppliedDate,
    MAX(AppliedDate) AS LastAppliedDate,
    SUM(CASE WHEN IssueDate IS NOT NULL THEN 1 ELSE 0 END) AS IssuedRows,
    SUM(CASE WHEN FinalizeDate IS NOT NULL THEN 1 ELSE 0 END) AS FinalizedRows
FROM #Expanded
GROUP BY
    DenominatorOrder,
    DenominatorName
ORDER BY DenominatorOrder;

-------------------------------------------------------------------------------
-- Result 2: physical-source audit.
-------------------------------------------------------------------------------
SELECT
    f.FieldOrder,
    f.ScenarioOrder,
    f.ScenarioName,
    f.FieldRole,
    f.InputKey,
    f.SourceColumn,
    f.ValueKind,
    CASE WHEN EXISTS (
        SELECT 1
        FROM sys.columns c
        WHERE c.object_id = OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT')
          AND UPPER(c.name) = UPPER(f.SourceColumn)
    ) THEN 1 ELSE 0 END AS PhysicalColumnFound
FROM #Fields f
ORDER BY f.FieldOrder;

CREATE TABLE #FieldPrevalence (
    DenominatorOrder int NOT NULL,
    DenominatorName varchar(40) NOT NULL,
    PermitRows bigint NOT NULL,
    FieldOrder int NOT NULL,
    ScenarioOrder int NOT NULL,
    ScenarioName nvarchar(200) NOT NULL,
    FieldRole varchar(20) NOT NULL,
    InputKey sysname NOT NULL,
    ValueKind varchar(20) NOT NULL,
    PhysicalColumnFound bit NOT NULL,
    PopulatedRows bigint NULL,
    PopulatedPct decimal(9,4) NULL,
    ActiveRows bigint NULL,
    ActivePct decimal(9,4) NULL,
    NumericMinNonzero decimal(19,6) NULL,
    NumericMaxNonzero decimal(19,6) NULL
);

CREATE TABLE #ScenarioActivation (
    DenominatorOrder int NOT NULL,
    DenominatorName varchar(40) NOT NULL,
    PMPERMITID char(36) NOT NULL,
    ScenarioOrder int NOT NULL,
    ScenarioName nvarchar(200) NOT NULL,
    AnyPopulated bit NOT NULL,
    AnyActive bit NOT NULL,
    NumericActiveCount int NOT NULL,
    ReviewPopulated bit NOT NULL
);

CREATE TABLE #ObservedReview (
    ScenarioOrder int NOT NULL,
    ScenarioName nvarchar(200) NOT NULL,
    InputKey sysname NOT NULL,
    ObservedValue nvarchar(250) NOT NULL,
    PermitRows bigint NOT NULL
);

DECLARE
    @FieldOrder int,
    @ScenarioOrder int,
    @ScenarioName nvarchar(200),
    @FieldRole varchar(20),
    @InputKey sysname,
    @SourceColumn sysname,
    @ValueKind varchar(20),
    @ColumnFound bit,
    @Sql nvarchar(max);

DECLARE field_cursor CURSOR LOCAL FAST_FORWARD FOR
SELECT
    FieldOrder,
    ScenarioOrder,
    ScenarioName,
    FieldRole,
    InputKey,
    SourceColumn,
    ValueKind
FROM #Fields
ORDER BY FieldOrder;

OPEN field_cursor;
FETCH NEXT FROM field_cursor
INTO @FieldOrder,@ScenarioOrder,@ScenarioName,@FieldRole,
     @InputKey,@SourceColumn,@ValueKind;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @ColumnFound = CASE WHEN EXISTS (
        SELECT 1
        FROM sys.columns c
        WHERE c.object_id = OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT')
          AND UPPER(c.name) = UPPER(@SourceColumn)
    ) THEN 1 ELSE 0 END;

    IF @ColumnFound = 0
    BEGIN
        INSERT INTO #FieldPrevalence
        SELECT
            e.DenominatorOrder,e.DenominatorName,COUNT_BIG(*),
            @FieldOrder,@ScenarioOrder,@ScenarioName,@FieldRole,
            @InputKey,@ValueKind,0,
            NULL,NULL,NULL,NULL,NULL,NULL
        FROM #Expanded e
        GROUP BY e.DenominatorOrder,e.DenominatorName;
    END
    ELSE IF @ValueKind = 'numeric'
    BEGIN
        SET @Sql = N'
            INSERT INTO #FieldPrevalence
            SELECT
                e.DenominatorOrder,
                e.DenominatorName,
                COUNT_BIG(*),
                @FieldOrder,
                @ScenarioOrder,
                @ScenarioName,
                @FieldRole,
                @InputKey,
                @ValueKind,
                1,
                SUM(CASE WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                              IS NOT NULL THEN 1 ELSE 0 END),
                CAST(100.0 * SUM(CASE
                    WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                         IS NOT NULL THEN 1 ELSE 0 END)
                    / NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                SUM(CASE
                    WHEN TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                     AND ABS(TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                        THEN 1 ELSE 0 END),
                CAST(100.0 * SUM(CASE
                    WHEN TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                     AND ABS(TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                        THEN 1 ELSE 0 END)
                    / NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                MIN(CASE
                    WHEN TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                     AND ABS(TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                        THEN TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N') END),
                MAX(CASE
                    WHEN TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                     AND ABS(TRY_CONVERT(decimal(19,6),
                         cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                        THEN TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N') END)
            FROM #Expanded e
            INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID = e.PMPERMITID
            GROUP BY e.DenominatorOrder,e.DenominatorName;';

        EXEC sys.sp_executesql @Sql,
            N'@FieldOrder int,@ScenarioOrder int,@ScenarioName nvarchar(200),
              @FieldRole varchar(20),@InputKey sysname,@ValueKind varchar(20)',
            @FieldOrder,@ScenarioOrder,@ScenarioName,@FieldRole,
            @InputKey,@ValueKind;
    END
    ELSE
    BEGIN
        SET @Sql = N'
            INSERT INTO #FieldPrevalence
            SELECT
                e.DenominatorOrder,
                e.DenominatorName,
                COUNT_BIG(*),
                @FieldOrder,
                @ScenarioOrder,
                @ScenarioName,
                @FieldRole,
                @InputKey,
                @ValueKind,
                1,
                SUM(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                    cs.' + QUOTENAME(@SourceColumn) + N'))),N'''')
                    IS NOT NULL THEN 1 ELSE 0 END),
                CAST(100.0 * SUM(CASE
                    WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@SourceColumn) + N'))),N'''')
                        IS NOT NULL THEN 1 ELSE 0 END)
                    / NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                SUM(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                    cs.' + QUOTENAME(@SourceColumn) + N'))),N'''')
                    IS NOT NULL THEN 1 ELSE 0 END),
                CAST(100.0 * SUM(CASE
                    WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@SourceColumn) + N'))),N'''')
                        IS NOT NULL THEN 1 ELSE 0 END)
                    / NULLIF(COUNT_BIG(*),0) AS decimal(9,4)),
                NULL,NULL
            FROM #Expanded e
            INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID = e.PMPERMITID
            GROUP BY e.DenominatorOrder,e.DenominatorName;

            INSERT INTO #ObservedReview
            SELECT
                @ScenarioOrder,@ScenarioName,@InputKey,
                LTRIM(RTRIM(CONVERT(nvarchar(250),
                    cs.' + QUOTENAME(@SourceColumn) + N'))),
                COUNT_BIG(*)
            FROM #Expanded e
            INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID = e.PMPERMITID
            WHERE e.DenominatorName = ''MATURE_ISSUED_OR_FINALIZED''
              AND NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                    cs.' + QUOTENAME(@SourceColumn) + N'))),N'''')
                  IS NOT NULL
            GROUP BY LTRIM(RTRIM(CONVERT(nvarchar(250),
                cs.' + QUOTENAME(@SourceColumn) + N')));';

        EXEC sys.sp_executesql @Sql,
            N'@FieldOrder int,@ScenarioOrder int,@ScenarioName nvarchar(200),
              @FieldRole varchar(20),@InputKey sysname,@ValueKind varchar(20)',
            @FieldOrder,@ScenarioOrder,@ScenarioName,@FieldRole,
            @InputKey,@ValueKind;
    END;

    FETCH NEXT FROM field_cursor
    INTO @FieldOrder,@ScenarioOrder,@ScenarioName,@FieldRole,
         @InputKey,@SourceColumn,@ValueKind;
END;

CLOSE field_cursor;
DEALLOCATE field_cursor;

-------------------------------------------------------------------------------
-- Build scenario activation from exact four-field metadata.
-------------------------------------------------------------------------------
DECLARE
    @GOrder int,
    @GName nvarchar(200),
    @Primary sysname,
    @Secondary sysname,
    @Duration sysname,
    @Review sysname;

DECLARE scenario_cursor CURSOR LOCAL FAST_FORWARD FOR
SELECT
    ScenarioOrder,
    MAX(ScenarioName),
    MAX(CASE WHEN FieldRole='primary' THEN SourceColumn END),
    MAX(CASE WHEN FieldRole='secondary' THEN SourceColumn END),
    MAX(CASE WHEN FieldRole='duration' THEN SourceColumn END),
    MAX(CASE WHEN FieldRole='review' THEN SourceColumn END)
FROM #Fields
GROUP BY ScenarioOrder
ORDER BY ScenarioOrder;

OPEN scenario_cursor;
FETCH NEXT FROM scenario_cursor
INTO @GOrder,@GName,@Primary,@Secondary,@Duration,@Review;

WHILE @@FETCH_STATUS = 0
BEGIN
    IF @Primary IS NOT NULL AND @Secondary IS NOT NULL
       AND @Duration IS NOT NULL AND @Review IS NOT NULL
       AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT') AND UPPER(name)=UPPER(@Primary))
       AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT') AND UPPER(name)=UPPER(@Secondary))
       AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT') AND UPPER(name)=UPPER(@Duration))
       AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT') AND UPPER(name)=UPPER(@Review))
    BEGIN
        SET @Sql = N'
            INSERT INTO #ScenarioActivation
            SELECT
                e.DenominatorOrder,
                e.DenominatorName,
                e.PMPERMITID,
                @GOrder,
                @GName,
                CAST(CASE WHEN
                    cs.' + QUOTENAME(@Primary) + N' IS NOT NULL
                    OR cs.' + QUOTENAME(@Secondary) + N' IS NOT NULL
                    OR cs.' + QUOTENAME(@Duration) + N' IS NOT NULL
                    OR NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@Review) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END AS bit),
                CAST(CASE WHEN
                    COALESCE(ABS(TRY_CONVERT(decimal(19,6),
                        cs.' + QUOTENAME(@Primary) + N')),0) > 0.0000005
                    OR COALESCE(ABS(TRY_CONVERT(decimal(19,6),
                        cs.' + QUOTENAME(@Secondary) + N')),0) > 0.0000005
                    OR COALESCE(ABS(TRY_CONVERT(decimal(19,6),
                        cs.' + QUOTENAME(@Duration) + N')),0) > 0.0000005
                    OR NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@Review) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END AS bit),
                (CASE WHEN COALESCE(ABS(TRY_CONVERT(decimal(19,6),
                    cs.' + QUOTENAME(@Primary) + N')),0) > 0.0000005
                    THEN 1 ELSE 0 END)
                + (CASE WHEN COALESCE(ABS(TRY_CONVERT(decimal(19,6),
                    cs.' + QUOTENAME(@Secondary) + N')),0) > 0.0000005
                    THEN 1 ELSE 0 END)
                + (CASE WHEN COALESCE(ABS(TRY_CONVERT(decimal(19,6),
                    cs.' + QUOTENAME(@Duration) + N')),0) > 0.0000005
                    THEN 1 ELSE 0 END),
                CAST(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                    cs.' + QUOTENAME(@Review) + N'))),N'''') IS NOT NULL
                    THEN 1 ELSE 0 END AS bit)
            FROM #Expanded e
            INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                ON cs.ID=e.PMPERMITID;';

        EXEC sys.sp_executesql @Sql,
            N'@GOrder int,@GName nvarchar(200)',
            @GOrder,@GName;
    END;

    FETCH NEXT FROM scenario_cursor
    INTO @GOrder,@GName,@Primary,@Secondary,@Duration,@Review;
END;

CLOSE scenario_cursor;
DEALLOCATE scenario_cursor;

-------------------------------------------------------------------------------
-- Result 3: mature field prevalence ranking.
-------------------------------------------------------------------------------
SELECT
    FieldOrder,ScenarioOrder,ScenarioName,FieldRole,InputKey,ValueKind,
    PhysicalColumnFound,PermitRows,PopulatedRows,PopulatedPct,
    ActiveRows,ActivePct,NumericMinNonzero,NumericMaxNonzero
FROM #FieldPrevalence
WHERE DenominatorName='MATURE_ISSUED_OR_FINALIZED'
ORDER BY
    COALESCE(ActivePct,-1) DESC,
    ScenarioOrder,
    FieldOrder;

-------------------------------------------------------------------------------
-- Result 4: scenario prevalence across all denominators.
-------------------------------------------------------------------------------
SELECT
    DenominatorOrder,
    DenominatorName,
    ScenarioOrder,
    ScenarioName,
    COUNT_BIG(*) AS PermitRows,
    SUM(CASE WHEN AnyPopulated=1 THEN 1 ELSE 0 END) AS PopulatedScenarioRows,
    CAST(100.0*SUM(CASE WHEN AnyPopulated=1 THEN 1 ELSE 0 END)
         /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)) AS PopulatedScenarioPct,
    SUM(CASE WHEN AnyActive=1 THEN 1 ELSE 0 END) AS ActiveScenarioRows,
    CAST(100.0*SUM(CASE WHEN AnyActive=1 THEN 1 ELSE 0 END)
         /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)) AS ActiveScenarioPct,
    SUM(CASE WHEN NumericActiveCount>0 THEN 1 ELSE 0 END) AS NumericActiveRows,
    SUM(CASE WHEN ReviewPopulated=1 THEN 1 ELSE 0 END) AS ReviewPopulatedRows
FROM #ScenarioActivation
GROUP BY
    DenominatorOrder,DenominatorName,ScenarioOrder,ScenarioName
ORDER BY
    DenominatorOrder,ScenarioOrder;

-------------------------------------------------------------------------------
-- Result 5: mature scenario ranking.
-------------------------------------------------------------------------------
SELECT
    ScenarioOrder,
    ScenarioName,
    COUNT_BIG(*) AS PermitRows,
    SUM(CASE WHEN AnyPopulated=1 THEN 1 ELSE 0 END) AS PopulatedScenarioRows,
    CAST(100.0*SUM(CASE WHEN AnyPopulated=1 THEN 1 ELSE 0 END)
         /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)) AS PopulatedScenarioPct,
    SUM(CASE WHEN AnyActive=1 THEN 1 ELSE 0 END) AS ActiveScenarioRows,
    CAST(100.0*SUM(CASE WHEN AnyActive=1 THEN 1 ELSE 0 END)
         /NULLIF(COUNT_BIG(*),0) AS decimal(9,4)) AS ActiveScenarioPct,
    SUM(CASE WHEN NumericActiveCount>0 THEN 1 ELSE 0 END) AS NumericActiveRows,
    SUM(CASE WHEN ReviewPopulated=1 THEN 1 ELSE 0 END) AS ReviewPopulatedRows
FROM #ScenarioActivation
WHERE DenominatorName='MATURE_ISSUED_OR_FINALIZED'
GROUP BY ScenarioOrder,ScenarioName
ORDER BY ActiveScenarioPct DESC,ScenarioOrder;

-------------------------------------------------------------------------------
-- Result 6: how many scenarios are active on each mature permit.
-------------------------------------------------------------------------------
WITH PerPermit AS (
    SELECT
        PMPERMITID,
        SUM(CASE WHEN AnyActive=1 THEN 1 ELSE 0 END) AS ActiveScenarioCount
    FROM #ScenarioActivation
    WHERE DenominatorName='MATURE_ISSUED_OR_FINALIZED'
    GROUP BY PMPERMITID
)
SELECT
    ActiveScenarioCount,
    COUNT_BIG(*) AS PermitRows,
    CAST(100.0*COUNT_BIG(*)/NULLIF(SUM(COUNT_BIG(*)) OVER (),0)
         AS decimal(9,4)) AS PermitPct
FROM PerPermit
GROUP BY ActiveScenarioCount
ORDER BY ActiveScenarioCount;

-------------------------------------------------------------------------------
-- Result 7: raw Review values in mature permits.
-------------------------------------------------------------------------------
SELECT
    ScenarioOrder,
    ScenarioName,
    InputKey,
    ObservedValue,
    PermitRows
FROM #ObservedReview
ORDER BY
    ScenarioOrder,
    PermitRows DESC,
    ObservedValue;

DROP TABLE IF EXISTS #ObservedReview;
DROP TABLE IF EXISTS #ScenarioActivation;
DROP TABLE IF EXISTS #FieldPrevalence;
DROP TABLE IF EXISTS #Expanded;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Fields;
