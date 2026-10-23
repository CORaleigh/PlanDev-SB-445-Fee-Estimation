/*
    Historical use of project fields for Building Accessory Structure permits.

    Query 121 established 100% same-ID linkage for the recent target cohort:
      CUSTOMSAVERPERMITMANAGEMENT.ID = PMPERMIT.PMPERMITID

    This query measures the exact 29 controls currently compiled by the r7
    estimator for:

      DSINSP - Building (Non-Residential) / Accessory Structure

    It is a prevalence/UX evidence query, not a calculation-validation query.

    The query deliberately reports three lifecycle denominators instead of
    choosing one silently:

      ALL_RECENT
        Every target permit in the date window.

      NON_TERMINAL
        Excludes Void / Withdrawn / Cancelled / Canceled.

      MATURE_ISSUED_OR_FINALIZED
        NON_TERMINAL plus IssueDate or FinalizeDate present. This mirrors the
        mature-case pattern used in prior family-validation queries and reduces
        bias from very early applications whose project fields may not yet have
        been completed.

    Per-field semantics:
      numeric
        populated = source value is not NULL
        active    = numeric value <> 0

      boolean
        populated = source value is not NULL
        active    = value is true / 1

      choice
        populated = non-NULL, nonblank string
        active    = same as populated

    "Active" is only an empirical usage flag. Low prevalence is NOT permission
    to delete a valid estimator input. It can support grouping/progressive
    disclosure decisions after the underlying rule remains available.

    ValuationValue is not read from the custom-saver table. The validated
    repository path is PMPERMIT.VALUE (see Queries 39-40).

    Results:
      1. Cohort/denominator counts.
      2. Physical-source audit for all 29 compiled controls.
      3. Field prevalence for all three denominators.
      4. Mature-denominator ranking by active prevalence.
      5. Compact observed choice/boolean values for the mature denominator.
*/
SET NOCOUNT ON;

DECLARE @StartDate date = '2024-07-01';
DECLARE @EndDate date = CAST(GETDATE() AS date);

DROP TABLE IF EXISTS #Fields;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Expanded;
DROP TABLE IF EXISTS #Prevalence;
DROP TABLE IF EXISTS #ObservedCategorical;

CREATE TABLE #Fields (
    FieldOrder int NOT NULL PRIMARY KEY,
    InputKey sysname NOT NULL UNIQUE,
    InputLabel nvarchar(250) NOT NULL,
    SourceKind varchar(30) NOT NULL,
    SourceColumn sysname NOT NULL,
    ValueKind varchar(20) NOT NULL
);

INSERT INTO #Fields (
    FieldOrder,
    InputKey,
    InputLabel,
    SourceKind,
    SourceColumn,
    ValueKind
)
VALUES
    (1,  'OpenSpaceUnits', 'Open Space Units', 'custom-saver', 'OpenSpaceUnits', 'numeric'),
    (2,  'Cemetery', 'Cemetery', 'custom-saver', 'Cemetery', 'numeric'),
    (3,  'ChurchesSynagogues', 'Churches/Synagogues', 'custom-saver', 'ChurchesSynagogues', 'numeric'),
    (4,  'CollegeUniversity', 'College/University', 'custom-saver', 'CollegeUniversity', 'numeric'),
    (5,  'DaycareFacilities', 'Daycare Facilities', 'custom-saver', 'DaycareFacilities', 'numeric'),
    (6,  'ElementaryMiddleAndHighSchools', 'Elementary, Middle and High Schools', 'custom-saver', 'ElementaryMiddleAndHighSchools', 'numeric'),
    (7,  'EmergencyServiceFacility', 'Emergency Service Facility', 'custom-saver', 'EmergencyServiceFacility', 'numeric'),
    (8,  'GeneralRecreationAllOther', 'General Recreation/All Other', 'custom-saver', 'GeneralRecreationAllOther', 'numeric'),
    (9,  'GolfCourse', 'Golf Course', 'custom-saver', 'GolfCourse', 'numeric'),
    (10, 'HospitalsMedicalCareFacilities', 'Hospitals/Medical Care', 'custom-saver', 'HospitalsMedicalCareFacilities', 'numeric'),
    (11, 'HotelMotel', 'Hotel/Motel', 'custom-saver', 'HotelMotel', 'numeric'),
    (12, 'IndustrialManufacturingAgricultural', 'Industrial/Manufacturing/Agricultural', 'custom-saver', 'IndustrialManufacturingAgricultural', 'numeric'),
    (13, 'MiniWarehousing', 'Mini Warehousing', 'custom-saver', 'MiniWarehousing', 'numeric'),
    (14, 'MultiFamilydwellings', 'Multi-Family Dwellings', 'custom-saver', 'MultiFamilydwellings', 'numeric'),
    (15, 'NursingHomeGroupQuarters', 'Nursing Home/Group Quarters', 'custom-saver', 'NursingHomeGroupQuarters', 'numeric'),
    (16, 'Office', 'Office', 'custom-saver', 'Office', 'numeric'),
    (17, 'PassengerTransportationFacility', 'Passenger Transportation Facility', 'custom-saver', 'PassengerTransportationFacility', 'numeric'),
    (18, 'PublicParks', 'Public Parks', 'custom-saver', 'PublicParks', 'numeric'),
    (19, 'RetailPerFuelingStations', 'Retail (Per Vehicle Fueling Stations)', 'custom-saver', 'RetailPerFuelingStations', 'numeric'),
    (20, 'RetailCommercial', 'Retail/Commercial', 'custom-saver', 'RetailCommercial', 'numeric'),
    (21, 'RetirementCommunity', 'Retirement Community', 'custom-saver', 'RetirementCommunity', 'numeric'),
    (22, 'StadiumsColiseumsRaceTracks', 'Stadiums/Coliseums/Race Tracks', 'custom-saver', 'StadiumsColiseumsRaceTracks', 'numeric'),
    (23, 'Warehouse', 'Warehouse', 'custom-saver', 'Warehouse', 'numeric'),
    (24, 'ValuationValue', 'Permit Value', 'permit', 'VALUE', 'numeric'),
    (25, 'OpenSpaceZoneCombobox', 'Open Space Zone', 'custom-saver', 'OpenSpaceZoneCombobox', 'choice'),
    (26, 'OpenSpaceTypeCombobox', 'Open Space Type', 'custom-saver', 'OpenSpaceTypeCombobox', 'choice'),
    (27, 'ThoroughfareZoneCombobox', 'Thoroughfare Zone', 'custom-saver', 'ThoroughfareZoneCombobox', 'choice'),
    (28, 'WorkingwithoutaPermit', 'Working without a Permit', 'custom-saver', 'WorkingwithoutaPermit', 'boolean'),
    (29, 'InfillNotificationRequired', 'Infill Notification Required', 'custom-saver', 'InfillNotificationRequired', 'choice');

-------------------------------------------------------------------------------
-- Build the exact permit/work-class cohort once.
-------------------------------------------------------------------------------
SELECT
    pm.PMPERMITID,
    pm.PERMITNUMBER AS PermitNumber,
    pm.APPLYDATE AS AppliedDate,
    pm.ISSUEDATE AS IssueDate,
    pm.FINALIZEDATE AS FinalizeDate,
    LTRIM(RTRIM(ps.NAME)) AS PermitStatus,
    TRY_CONVERT(decimal(19,6), pm.VALUE) AS PermitValue,
    CAST(
        CASE
            WHEN UPPER(LTRIM(RTRIM(ps.NAME))) IN (
                'VOID',
                'WITHDRAWN',
                'CANCELLED',
                'CANCELED'
            )
                THEN 0
            ELSE 1
        END
        AS bit
    ) AS IsNonTerminal,
    CAST(
        CASE
            WHEN UPPER(LTRIM(RTRIM(ps.NAME))) NOT IN (
                'VOID',
                'WITHDRAWN',
                'CANCELLED',
                'CANCELED'
            )
             AND (
                    pm.ISSUEDATE IS NOT NULL
                 OR pm.FINALIZEDATE IS NOT NULL
             )
                THEN 1
            ELSE 0
        END
        AS bit
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
    pmt.NAME = 'DSINSP - Building (Non-Residential)'
    AND wc.NAME = 'Accessory Structure'
    AND pm.APPLYDATE >= @StartDate
    AND pm.APPLYDATE < DATEADD(DAY, 1, @EndDate);

CREATE UNIQUE CLUSTERED INDEX IX_122_Cohort_Permit
    ON #Cohort (PMPERMITID);

-------------------------------------------------------------------------------
-- Expand each permit into the explicit denominator populations.
-------------------------------------------------------------------------------
SELECT
    d.DenominatorOrder,
    d.DenominatorName,
    c.*
INTO #Expanded
FROM #Cohort c
CROSS APPLY (
    VALUES
        (1, 'ALL_RECENT',
            CAST(1 AS bit)),
        (2, 'NON_TERMINAL',
            c.IsNonTerminal),
        (3, 'MATURE_ISSUED_OR_FINALIZED',
            c.IsMatureIssuedOrFinalized)
) d (
    DenominatorOrder,
    DenominatorName,
    IncludeRow
)
WHERE d.IncludeRow = 1;

CREATE INDEX IX_122_Expanded_DenomPermit
    ON #Expanded (DenominatorOrder, PMPERMITID);

-------------------------------------------------------------------------------
-- Result 1: denominator counts and lifecycle context.
-------------------------------------------------------------------------------
SELECT
    e.DenominatorOrder,
    e.DenominatorName,
    COUNT_BIG(*) AS PermitRows,
    COUNT(DISTINCT e.PermitStatus) AS DistinctStatuses,
    MIN(e.AppliedDate) AS FirstAppliedDate,
    MAX(e.AppliedDate) AS LastAppliedDate,
    SUM(CASE WHEN e.IssueDate IS NOT NULL THEN 1 ELSE 0 END) AS IssuedRows,
    SUM(CASE WHEN e.FinalizeDate IS NOT NULL THEN 1 ELSE 0 END) AS FinalizedRows
FROM #Expanded e
GROUP BY
    e.DenominatorOrder,
    e.DenominatorName
ORDER BY
    e.DenominatorOrder;

-------------------------------------------------------------------------------
-- Result 2: physical-source audit for all 29 controls.
--
-- Custom-saver fields must exist on dbo.CUSTOMSAVERPERMITMANAGEMENT.
-- ValuationValue is intentionally mapped to PMPERMIT.VALUE.
-------------------------------------------------------------------------------
SELECT
    f.FieldOrder,
    f.InputKey,
    f.InputLabel,
    f.SourceKind,
    f.SourceColumn,
    f.ValueKind,
    CASE
        WHEN f.SourceKind = 'permit'
         AND EXISTS (
                SELECT 1
                FROM sys.columns c
                WHERE c.object_id = OBJECT_ID('dbo.PMPERMIT')
                  AND UPPER(c.name) = UPPER(f.SourceColumn)
            )
            THEN 1
        WHEN f.SourceKind = 'custom-saver'
         AND EXISTS (
                SELECT 1
                FROM sys.columns c
                WHERE c.object_id =
                    OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT')
                  AND UPPER(c.name) = UPPER(f.SourceColumn)
            )
            THEN 1
        ELSE 0
    END AS PhysicalColumnFound,
    CASE
        WHEN f.SourceKind = 'permit'
            THEN 'dbo.PMPERMIT'
        WHEN f.SourceKind = 'custom-saver'
            THEN 'dbo.CUSTOMSAVERPERMITMANAGEMENT'
        ELSE NULL
    END AS PhysicalTable
FROM #Fields f
ORDER BY
    f.FieldOrder;

-------------------------------------------------------------------------------
-- Prevalence result table.
-------------------------------------------------------------------------------
CREATE TABLE #Prevalence (
    DenominatorOrder int NOT NULL,
    DenominatorName varchar(40) NOT NULL,
    PermitRows bigint NOT NULL,
    FieldOrder int NOT NULL,
    InputKey sysname NOT NULL,
    InputLabel nvarchar(250) NOT NULL,
    SourceKind varchar(30) NOT NULL,
    SourceColumn sysname NOT NULL,
    ValueKind varchar(20) NOT NULL,
    PhysicalColumnFound bit NOT NULL,
    PopulatedRows bigint NULL,
    PopulatedPct decimal(9,4) NULL,
    ActiveRows bigint NULL,
    ActivePct decimal(9,4) NULL,
    ActiveDefinition nvarchar(120) NOT NULL,
    NumericMinNonzero decimal(19,6) NULL,
    NumericMaxNonzero decimal(19,6) NULL
);

CREATE TABLE #ObservedCategorical (
    FieldOrder int NOT NULL,
    InputKey sysname NOT NULL,
    InputLabel nvarchar(250) NOT NULL,
    ValueKind varchar(20) NOT NULL,
    ObservedValue nvarchar(250) NOT NULL,
    PermitRows bigint NOT NULL
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
    FieldOrder,
    InputKey,
    InputLabel,
    SourceKind,
    SourceColumn,
    ValueKind
FROM #Fields
ORDER BY FieldOrder;

OPEN field_cursor;

FETCH NEXT FROM field_cursor
INTO
    @FieldOrder,
    @InputKey,
    @InputLabel,
    @SourceKind,
    @SourceColumn,
    @ValueKind;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @ColumnFound =
        CASE
            WHEN @SourceKind = 'permit'
             AND EXISTS (
                    SELECT 1
                    FROM sys.columns c
                    WHERE c.object_id = OBJECT_ID('dbo.PMPERMIT')
                      AND UPPER(c.name) = UPPER(@SourceColumn)
                )
                THEN 1
            WHEN @SourceKind = 'custom-saver'
             AND EXISTS (
                    SELECT 1
                    FROM sys.columns c
                    WHERE c.object_id =
                        OBJECT_ID('dbo.CUSTOMSAVERPERMITMANAGEMENT')
                      AND UPPER(c.name) = UPPER(@SourceColumn)
                )
                THEN 1
            ELSE 0
        END;

    IF @ColumnFound = 0
    BEGIN
        INSERT INTO #Prevalence (
            DenominatorOrder,
            DenominatorName,
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
            ActiveDefinition,
            NumericMinNonzero,
            NumericMaxNonzero
        )
        SELECT
            e.DenominatorOrder,
            e.DenominatorName,
            COUNT_BIG(*),
            @FieldOrder,
            @InputKey,
            @InputLabel,
            @SourceKind,
            @SourceColumn,
            @ValueKind,
            0,
            NULL,
            NULL,
            NULL,
            NULL,
            CASE
                WHEN @ValueKind = 'numeric' THEN 'numeric <> 0'
                WHEN @ValueKind = 'boolean' THEN 'boolean = true'
                WHEN @ValueKind = 'choice' THEN 'nonblank choice'
                ELSE 'unknown'
            END,
            NULL,
            NULL
        FROM #Expanded e
        GROUP BY
            e.DenominatorOrder,
            e.DenominatorName;
    END
    ELSE IF @SourceKind = 'permit'
    BEGIN
        -- The only permit-source field in this query is
        -- ValuationValue -> PMPERMIT.VALUE, already normalized into #Expanded.
        INSERT INTO #Prevalence (
            DenominatorOrder,
            DenominatorName,
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
            ActiveDefinition,
            NumericMinNonzero,
            NumericMaxNonzero
        )
        SELECT
            e.DenominatorOrder,
            e.DenominatorName,
            COUNT_BIG(*) AS PermitRows,
            @FieldOrder,
            @InputKey,
            @InputLabel,
            @SourceKind,
            @SourceColumn,
            @ValueKind,
            1,
            SUM(CASE WHEN e.PermitValue IS NOT NULL THEN 1 ELSE 0 END),
            CAST(
                100.0 * SUM(CASE WHEN e.PermitValue IS NOT NULL THEN 1 ELSE 0 END)
                / NULLIF(COUNT_BIG(*), 0)
                AS decimal(9,4)
            ),
            SUM(CASE
                WHEN e.PermitValue IS NOT NULL
                 AND ABS(e.PermitValue) > 0.0000005
                    THEN 1 ELSE 0
            END),
            CAST(
                100.0 * SUM(CASE
                    WHEN e.PermitValue IS NOT NULL
                     AND ABS(e.PermitValue) > 0.0000005
                        THEN 1 ELSE 0
                END)
                / NULLIF(COUNT_BIG(*), 0)
                AS decimal(9,4)
            ),
            'numeric <> 0',
            MIN(CASE
                WHEN e.PermitValue IS NOT NULL
                 AND ABS(e.PermitValue) > 0.0000005
                    THEN e.PermitValue
            END),
            MAX(CASE
                WHEN e.PermitValue IS NOT NULL
                 AND ABS(e.PermitValue) > 0.0000005
                    THEN e.PermitValue
            END)
        FROM #Expanded e
        GROUP BY
            e.DenominatorOrder,
            e.DenominatorName;
    END
    ELSE
    BEGIN
        -- Custom-saver source. Dynamic SQL is used so a missing/misnamed field
        -- does not cause a parse error for the entire query.
        IF @ValueKind = 'numeric'
        BEGIN
            SET @Sql = N'
                INSERT INTO #Prevalence (
                    DenominatorOrder,
                    DenominatorName,
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
                    ActiveDefinition,
                    NumericMinNonzero,
                    NumericMaxNonzero
                )
                SELECT
                    e.DenominatorOrder,
                    e.DenominatorName,
                    COUNT_BIG(*),
                    @FieldOrder,
                    @InputKey,
                    @InputLabel,
                    @SourceKind,
                    @SourceColumn,
                    @ValueKind,
                    1,
                    SUM(CASE WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                                      IS NOT NULL
                             THEN 1 ELSE 0 END),
                    CAST(
                        100.0 * SUM(CASE
                            WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                                 IS NOT NULL
                                THEN 1 ELSE 0
                        END)
                        / NULLIF(COUNT_BIG(*), 0)
                        AS decimal(9,4)
                    ),
                    SUM(CASE
                        WHEN TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                         AND ABS(TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                            THEN 1 ELSE 0
                    END),
                    CAST(
                        100.0 * SUM(CASE
                            WHEN TRY_CONVERT(decimal(19,6),
                                 cs.' + QUOTENAME(@SourceColumn) + N')
                                 IS NOT NULL
                             AND ABS(TRY_CONVERT(decimal(19,6),
                                 cs.' + QUOTENAME(@SourceColumn) + N'))
                                 > 0.0000005
                                THEN 1 ELSE 0
                        END)
                        / NULLIF(COUNT_BIG(*), 0)
                        AS decimal(9,4)
                    ),
                    N''numeric <> 0'',
                    MIN(CASE
                        WHEN TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                         AND ABS(TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                            THEN TRY_CONVERT(decimal(19,6),
                                 cs.' + QUOTENAME(@SourceColumn) + N')
                    END),
                    MAX(CASE
                        WHEN TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N') IS NOT NULL
                         AND ABS(TRY_CONVERT(decimal(19,6),
                             cs.' + QUOTENAME(@SourceColumn) + N')) > 0.0000005
                            THEN TRY_CONVERT(decimal(19,6),
                                 cs.' + QUOTENAME(@SourceColumn) + N')
                    END)
                FROM #Expanded e
                INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                    ON cs.ID = e.PMPERMITID
                GROUP BY
                    e.DenominatorOrder,
                    e.DenominatorName;';

            EXEC sys.sp_executesql
                @Sql,
                N'@FieldOrder int,
                  @InputKey sysname,
                  @InputLabel nvarchar(250),
                  @SourceKind varchar(30),
                  @SourceColumn sysname,
                  @ValueKind varchar(20)',
                @FieldOrder,
                @InputKey,
                @InputLabel,
                @SourceKind,
                @SourceColumn,
                @ValueKind;
        END
        ELSE IF @ValueKind = 'boolean'
        BEGIN
            SET @Sql = N'
                INSERT INTO #Prevalence (
                    DenominatorOrder,
                    DenominatorName,
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
                    ActiveDefinition,
                    NumericMinNonzero,
                    NumericMaxNonzero
                )
                SELECT
                    e.DenominatorOrder,
                    e.DenominatorName,
                    COUNT_BIG(*),
                    @FieldOrder,
                    @InputKey,
                    @InputLabel,
                    @SourceKind,
                    @SourceColumn,
                    @ValueKind,
                    1,
                    SUM(CASE WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                                      IS NOT NULL
                             THEN 1 ELSE 0 END),
                    CAST(
                        100.0 * SUM(CASE
                            WHEN cs.' + QUOTENAME(@SourceColumn) + N'
                                 IS NOT NULL
                                THEN 1 ELSE 0
                        END)
                        / NULLIF(COUNT_BIG(*), 0)
                        AS decimal(9,4)
                    ),
                    SUM(CASE
                        WHEN TRY_CONVERT(bit,
                             cs.' + QUOTENAME(@SourceColumn) + N') = 1
                            THEN 1 ELSE 0
                    END),
                    CAST(
                        100.0 * SUM(CASE
                            WHEN TRY_CONVERT(bit,
                                 cs.' + QUOTENAME(@SourceColumn) + N') = 1
                                THEN 1 ELSE 0
                        END)
                        / NULLIF(COUNT_BIG(*), 0)
                        AS decimal(9,4)
                    ),
                    N''boolean = true'',
                    NULL,
                    NULL
                FROM #Expanded e
                INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                    ON cs.ID = e.PMPERMITID
                GROUP BY
                    e.DenominatorOrder,
                    e.DenominatorName;';

            EXEC sys.sp_executesql
                @Sql,
                N'@FieldOrder int,
                  @InputKey sysname,
                  @InputLabel nvarchar(250),
                  @SourceKind varchar(30),
                  @SourceColumn sysname,
                  @ValueKind varchar(20)',
                @FieldOrder,
                @InputKey,
                @InputLabel,
                @SourceKind,
                @SourceColumn,
                @ValueKind;
        END
        ELSE IF @ValueKind = 'choice'
        BEGIN
            SET @Sql = N'
                INSERT INTO #Prevalence (
                    DenominatorOrder,
                    DenominatorName,
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
                    ActiveDefinition,
                    NumericMinNonzero,
                    NumericMaxNonzero
                )
                SELECT
                    e.DenominatorOrder,
                    e.DenominatorName,
                    COUNT_BIG(*),
                    @FieldOrder,
                    @InputKey,
                    @InputLabel,
                    @SourceKind,
                    @SourceColumn,
                    @ValueKind,
                    1,
                    SUM(CASE
                        WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                             cs.' + QUOTENAME(@SourceColumn) + N'))), N'''')
                             IS NOT NULL
                            THEN 1 ELSE 0
                    END),
                    CAST(
                        100.0 * SUM(CASE
                            WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                                 cs.' + QUOTENAME(@SourceColumn) + N'))), N'''')
                                 IS NOT NULL
                                THEN 1 ELSE 0
                        END)
                        / NULLIF(COUNT_BIG(*), 0)
                        AS decimal(9,4)
                    ),
                    SUM(CASE
                        WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                             cs.' + QUOTENAME(@SourceColumn) + N'))), N'''')
                             IS NOT NULL
                            THEN 1 ELSE 0
                    END),
                    CAST(
                        100.0 * SUM(CASE
                            WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                                 cs.' + QUOTENAME(@SourceColumn) + N'))), N'''')
                                 IS NOT NULL
                                THEN 1 ELSE 0
                        END)
                        / NULLIF(COUNT_BIG(*), 0)
                        AS decimal(9,4)
                    ),
                    N''nonblank choice'',
                    NULL,
                    NULL
                FROM #Expanded e
                INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                    ON cs.ID = e.PMPERMITID
                GROUP BY
                    e.DenominatorOrder,
                    e.DenominatorName;

                INSERT INTO #ObservedCategorical (
                    FieldOrder,
                    InputKey,
                    InputLabel,
                    ValueKind,
                    ObservedValue,
                    PermitRows
                )
                SELECT
                    @FieldOrder,
                    @InputKey,
                    @InputLabel,
                    @ValueKind,
                    LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@SourceColumn) + N'))),
                    COUNT_BIG(*)
                FROM #Expanded e
                INNER JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT cs
                    ON cs.ID = e.PMPERMITID
                WHERE
                    e.DenominatorName =
                        ''MATURE_ISSUED_OR_FINALIZED''
                    AND NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@SourceColumn) + N'))), N'''')
                        IS NOT NULL
                GROUP BY
                    LTRIM(RTRIM(CONVERT(nvarchar(250),
                        cs.' + QUOTENAME(@SourceColumn) + N')));';

            EXEC sys.sp_executesql
                @Sql,
                N'@FieldOrder int,
                  @InputKey sysname,
                  @InputLabel nvarchar(250),
                  @SourceKind varchar(30),
                  @SourceColumn sysname,
                  @ValueKind varchar(20)',
                @FieldOrder,
                @InputKey,
                @InputLabel,
                @SourceKind,
                @SourceColumn,
                @ValueKind;
        END
    END;

    FETCH NEXT FROM field_cursor
    INTO
        @FieldOrder,
        @InputKey,
        @InputLabel,
        @SourceKind,
        @SourceColumn,
        @ValueKind;
END;

CLOSE field_cursor;
DEALLOCATE field_cursor;

-------------------------------------------------------------------------------
-- Result 3: complete prevalence table.
-------------------------------------------------------------------------------
SELECT
    DenominatorOrder,
    DenominatorName,
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
    ActiveDefinition,
    NumericMinNonzero,
    NumericMaxNonzero
FROM #Prevalence
ORDER BY
    DenominatorOrder,
    FieldOrder;

-------------------------------------------------------------------------------
-- Result 4: mature-denominator prevalence ranking.
--
-- This is the main UX evidence view. Keep FieldOrder so a zero-prevalence field
-- can still be traced back to the current compiled form.
-------------------------------------------------------------------------------
SELECT
    FieldOrder,
    InputKey,
    InputLabel,
    SourceKind,
    SourceColumn,
    ValueKind,
    PhysicalColumnFound,
    PermitRows,
    PopulatedRows,
    PopulatedPct,
    ActiveRows,
    ActivePct,
    ActiveDefinition,
    NumericMinNonzero,
    NumericMaxNonzero
FROM #Prevalence
WHERE DenominatorName = 'MATURE_ISSUED_OR_FINALIZED'
ORDER BY
    COALESCE(ActivePct, -1) DESC,
    COALESCE(PopulatedPct, -1) DESC,
    FieldOrder;

-------------------------------------------------------------------------------
-- Result 5: compact categorical values in the mature denominator.
--
-- These are raw stored values for evidence only. They are not automatically
-- staff-facing labels.
-------------------------------------------------------------------------------
SELECT
    FieldOrder,
    InputKey,
    InputLabel,
    ValueKind,
    ObservedValue,
    PermitRows
FROM #ObservedCategorical
ORDER BY
    FieldOrder,
    PermitRows DESC,
    ObservedValue;

DROP TABLE IF EXISTS #ObservedCategorical;
DROP TABLE IF EXISTS #Prevalence;
DROP TABLE IF EXISTS #Expanded;
DROP TABLE IF EXISTS #Cohort;
DROP TABLE IF EXISTS #Fields;
