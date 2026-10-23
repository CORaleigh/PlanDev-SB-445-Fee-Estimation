/*
    Publish the authoritative condition-choice labels that are safe to carry
    into the static estimator catalog.

    Evidence boundary:
      - custom-field values: CUSTOMFIELDPICKLISTITEM.SVALUE, only when the
        pick-list's CUSTOMFIELD agrees with CACONDITION.OPERANDVALUE;
      - entity-property values: explicit current native configuration tables
        and domain-specific staff-facing label columns;
      - no search/history/custom-saver fallback;
      - unresolved GUIDs are intentionally omitted.

    Result 1 is the build artifact consumed by build_estimator_catalog.py.
    Result 2 reports any conflicting labels for the same condition key.
    Result 3 gives compact coverage totals.

    Build key:
      OperandValue + ClassName + ComparisonValue
    with a safe field/GUID fallback allowed only when all emitted rows agree.

    No permit/history scans are performed.
*/
SET NOCOUNT ON;

DROP TABLE IF EXISTS #Candidates;

CREATE TABLE #Candidates (
    OperandValue nvarchar(200) NOT NULL,
    ClassName nvarchar(500) NULL,
    ComparisonValue varchar(36) NOT NULL,
    FriendlyLabel nvarchar(1000) NOT NULL,
    LabelSource varchar(100) NOT NULL
);

-------------------------------------------------------------------------------
-- Custom-field pick lists.
-------------------------------------------------------------------------------
INSERT INTO #Candidates
    (OperandValue, ClassName, ComparisonValue, FriendlyLabel, LabelSource)
SELECT DISTINCT
    LTRIM(RTRIM(c.OPERANDVALUE)),
    NULLIF(LTRIM(RTRIM(c.CLASSNAME)), ''),
    UPPER(CONVERT(varchar(36), TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE))),
    CONVERT(nvarchar(1000), cfi.SVALUE),
    'CUSTOMFIELDPICKLISTITEM.SVALUE'
FROM dbo.CACONDITION c
INNER JOIN dbo.CAEXPRESSION e
    ON c.COMPAREEXPRESSIONID = e.CAEXPRESSIONID
INNER JOIN dbo.CUSTOMFIELDPICKLISTITEM cfi
    ON TRY_CONVERT(uniqueidentifier, cfi.GCUSTOMFIELDPICKLISTITEM) =
       TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE)
INNER JOIN dbo.CUSTOMFIELDPICKLIST cfp
    ON cfi.FKGCUSTOMFIELDPICKLIST = cfp.GCUSTOMFIELDPICKLIST
INNER JOIN dbo.CUSTOMFIELD cf
    ON cfp.FKGCUSTOMFIELD = cf.GCUSTOMFIELD
WHERE
    c.OPERANDTYPEID = 3
    AND c.OPERATORID IN (1, 2)
    AND TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE) IS NOT NULL
    AND NULLIF(LTRIM(RTRIM(cfi.SVALUE)), '') IS NOT NULL
    AND (
        UPPER(LTRIM(RTRIM(c.OPERANDVALUE))) =
        UPPER(LTRIM(RTRIM(cf.SFIELDNAME)))
        OR
        UPPER(LTRIM(RTRIM(c.OPERANDVALUE))) =
        UPPER(LTRIM(RTRIM(cf.SCUSTOMFIELD)))
    );

-------------------------------------------------------------------------------
-- Entity-property domains with explicit current native tables and explicit
-- staff-facing label columns.
-------------------------------------------------------------------------------
;WITH EntityConditions AS (
    SELECT DISTINCT
        LTRIM(RTRIM(c.OPERANDVALUE)) AS OperandValue,
        NULLIF(LTRIM(RTRIM(c.CLASSNAME)), '') AS ClassName,
        TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE) AS ComparisonGuid
    FROM dbo.CACONDITION c
    INNER JOIN dbo.CAEXPRESSION e
        ON c.COMPAREEXPRESSIONID = e.CAEXPRESSIONID
    WHERE
        c.OPERANDTYPEID = 2
        AND c.OPERATORID IN (1, 2)
        AND TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE) IS NOT NULL
)
INSERT INTO #Candidates
    (OperandValue, ClassName, ComparisonValue, FriendlyLabel, LabelSource)
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'DISTRICT.NAME'
FROM EntityConditions ec
INNER JOIN dbo.DISTRICT t
    ON TRY_CONVERT(uniqueidentifier, t.DISTRICTID) = ec.ComparisonGuid
WHERE ec.OperandValue = 'DistrictID'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'ZONE.NAME'
FROM EntityConditions ec
INNER JOIN dbo.ZONE t
    ON TRY_CONVERT(uniqueidentifier, t.ZONEID) = ec.ComparisonGuid
WHERE ec.OperandValue = 'ZoneID'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'ILLICENSECLASSIFICATION.NAME'
FROM EntityConditions ec
INNER JOIN dbo.ILLICENSECLASSIFICATION t
    ON TRY_CONVERT(uniqueidentifier, t.ILLICENSECLASSIFICATIONID) =
       ec.ComparisonGuid
WHERE ec.OperandValue = 'LicenseClassificationID'
  AND ec.ClassName = 'EnerGovBusiness.IndividualLicense.License'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'BLLICENSECLASS.NAME'
FROM EntityConditions ec
INNER JOIN dbo.BLLICENSECLASS t
    ON TRY_CONVERT(uniqueidentifier, t.BLLICENSECLASSID) = ec.ComparisonGuid
WHERE ec.OperandValue = 'LicenseClassID'
  AND ec.ClassName = 'EnerGovBusiness.BusinessLicense.BusinessLicense'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'CMCASETYPE.NAME'
FROM EntityConditions ec
INNER JOIN dbo.CMCASETYPE t
    ON TRY_CONVERT(uniqueidentifier, t.CMCASETYPEID) = ec.ComparisonGuid
WHERE ec.OperandValue = 'CodeCaseTypeID'
  AND ec.ClassName = 'EnerGovBusiness.CodeEnforcement.CodeCase'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.STATUSNAME), 'IMINSPECTIONSTATUS.STATUSNAME'
FROM EntityConditions ec
INNER JOIN dbo.IMINSPECTIONSTATUS t
    ON TRY_CONVERT(uniqueidentifier, t.IMINSPECTIONSTATUSID) =
       ec.ComparisonGuid
WHERE ec.OperandValue = 'InspectionStatusID'
  AND ec.ClassName IN (
      'EnerGovBusiness.Inspections.Inspection',
      'EnerGovBusiness.Inspections.AttachedInspection'
  )
  AND NULLIF(LTRIM(RTRIM(t.STATUSNAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'IMINSPECTIONTYPE.NAME'
FROM EntityConditions ec
INNER JOIN dbo.IMINSPECTIONTYPE t
    ON TRY_CONVERT(uniqueidentifier, t.IMINSPECTIONTYPEID) =
       ec.ComparisonGuid
WHERE ec.OperandValue = 'InspectionTypeID'
  AND ec.ClassName = 'EnerGovBusiness.Inspections.AttachedInspection'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'PMPERMITTYPE.NAME'
FROM EntityConditions ec
INNER JOIN dbo.PMPERMITTYPE t
    ON TRY_CONVERT(uniqueidentifier, t.PMPERMITTYPEID) = ec.ComparisonGuid
WHERE ec.OperandValue = 'PermitTypeID'
  AND ec.ClassName = 'EnerGovBusiness.PermitManagement.Permit'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'PMPERMITWORKCLASS.NAME'
FROM EntityConditions ec
INNER JOIN dbo.PMPERMITWORKCLASS t
    ON TRY_CONVERT(uniqueidentifier, t.PMPERMITWORKCLASSID) =
       ec.ComparisonGuid
WHERE ec.OperandValue = 'PermitWorkClassID'
  AND ec.ClassName = 'EnerGovBusiness.PermitManagement.Permit'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.PLANNAME), 'PLPLANTYPE.PLANNAME'
FROM EntityConditions ec
INNER JOIN dbo.PLPLANTYPE t
    ON TRY_CONVERT(uniqueidentifier, t.PLPLANTYPEID) = ec.ComparisonGuid
WHERE ec.OperandValue = 'PlanTypeID'
  AND ec.ClassName = 'EnerGovBusiness.PlanManagement.Plan'
  AND NULLIF(LTRIM(RTRIM(t.PLANNAME)), '') IS NOT NULL

UNION ALL
SELECT ec.OperandValue, ec.ClassName,
       UPPER(CONVERT(varchar(36), ec.ComparisonGuid)),
       CONVERT(nvarchar(1000), t.NAME), 'PLPLANWORKCLASS.NAME'
FROM EntityConditions ec
INNER JOIN dbo.PLPLANWORKCLASS t
    ON TRY_CONVERT(uniqueidentifier, t.PLPLANWORKCLASSID) =
       ec.ComparisonGuid
WHERE ec.OperandValue = 'PlanWorkClassID'
  AND ec.ClassName = 'EnerGovBusiness.PlanManagement.Plan'
  AND NULLIF(LTRIM(RTRIM(t.NAME)), '') IS NOT NULL;

-------------------------------------------------------------------------------
-- Result 1: safe build artifact. Conflicting keys are excluded.
-------------------------------------------------------------------------------
SELECT
    OperandValue,
    ISNULL(ClassName, '') AS ClassName,
    ComparisonValue,
    MIN(FriendlyLabel) AS FriendlyLabel,
    MIN(LabelSource) AS LabelSource
FROM #Candidates
GROUP BY
    OperandValue,
    ISNULL(ClassName, ''),
    ComparisonValue
HAVING COUNT(DISTINCT FriendlyLabel) = 1
ORDER BY
    OperandValue,
    ClassName,
    FriendlyLabel,
    ComparisonValue;

-------------------------------------------------------------------------------
-- Result 2: conflicts, expected to be empty. Any row here must be reviewed
-- before the artifact is used.
-------------------------------------------------------------------------------
SELECT
    OperandValue,
    ISNULL(ClassName, '') AS ClassName,
    ComparisonValue,
    COUNT(DISTINCT FriendlyLabel) AS DistinctLabels,
    STRING_AGG(CONVERT(nvarchar(max), FriendlyLabel), ' | ')
        AS CandidateLabels
FROM #Candidates
GROUP BY
    OperandValue,
    ISNULL(ClassName, ''),
    ComparisonValue
HAVING COUNT(DISTINCT FriendlyLabel) > 1
ORDER BY
    OperandValue,
    ClassName,
    ComparisonValue;

-------------------------------------------------------------------------------
-- Result 3: compact artifact summary.
-------------------------------------------------------------------------------
SELECT
    COUNT(*) AS CandidateRows,
    COUNT(DISTINCT CONCAT(
        UPPER(OperandValue), '|',
        UPPER(ISNULL(ClassName, '')), '|',
        UPPER(ComparisonValue)
    )) AS DistinctConditionKeys,
    COUNT(DISTINCT ComparisonValue) AS DistinctGuidValues,
    COUNT(DISTINCT OperandValue) AS DistinctOperandFields
FROM #Candidates;

DROP TABLE IF EXISTS #Candidates;
