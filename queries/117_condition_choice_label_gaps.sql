/*
    Diagnose custom-field condition values that query 116 cannot label.

    Result 1 lists each unresolved condition key and any pick-list record that
    uses the same identifier. Result 2 summarizes the reasons. This query is
    read-only and does not scan permit history.
*/
SET NOCOUNT ON;

DROP TABLE IF EXISTS #ConditionChoices;
DROP TABLE IF EXISTS #Gaps;

SELECT DISTINCT
    LTRIM(RTRIM(c.OPERANDVALUE)) AS OperandValue,
    ISNULL(NULLIF(LTRIM(RTRIM(c.CLASSNAME)), ''), '') AS ClassName,
    TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE) AS ComparisonGuid
INTO #ConditionChoices
FROM dbo.CACONDITION c
INNER JOIN dbo.CAEXPRESSION e
    ON c.COMPAREEXPRESSIONID = e.CAEXPRESSIONID
WHERE
    c.OPERANDTYPEID = 3
    AND c.OPERATORID IN (1, 2)
    AND TRY_CONVERT(uniqueidentifier, e.CONSTANTVALUE) IS NOT NULL;

;WITH SafeLabels AS (
    SELECT DISTINCT
        cc.OperandValue,
        cc.ClassName,
        cc.ComparisonGuid
    FROM #ConditionChoices cc
    INNER JOIN dbo.CUSTOMFIELDPICKLISTITEM cfi
        ON TRY_CONVERT(uniqueidentifier, cfi.GCUSTOMFIELDPICKLISTITEM) =
           cc.ComparisonGuid
    INNER JOIN dbo.CUSTOMFIELDPICKLIST cfp
        ON cfi.FKGCUSTOMFIELDPICKLIST = cfp.GCUSTOMFIELDPICKLIST
    INNER JOIN dbo.CUSTOMFIELD cf
        ON cfp.FKGCUSTOMFIELD = cf.GCUSTOMFIELD
    WHERE
        NULLIF(LTRIM(RTRIM(cfi.SVALUE)), '') IS NOT NULL
        AND (
            UPPER(cc.OperandValue) = UPPER(LTRIM(RTRIM(cf.SFIELDNAME)))
            OR
            UPPER(cc.OperandValue) = UPPER(LTRIM(RTRIM(cf.SCUSTOMFIELD)))
        )
), GapDetails AS (
    SELECT
        cc.OperandValue,
        cc.ClassName,
        UPPER(CONVERT(varchar(36), cc.ComparisonGuid)) AS ComparisonValue,
        NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(1000), cfi.SVALUE))), '')
            AS CandidateLabel,
        NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(500), cf.SFIELDNAME))), '')
            AS PickListFieldName,
        NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(500), cf.SCUSTOMFIELD))), '')
            AS PickListCustomField,
        CASE
            WHEN cfi.GCUSTOMFIELDPICKLISTITEM IS NULL
                THEN 'No matching pick-list item'
            WHEN cfp.GCUSTOMFIELDPICKLIST IS NULL OR cf.GCUSTOMFIELD IS NULL
                THEN 'Incomplete pick-list relationship'
            WHEN NULLIF(LTRIM(RTRIM(cfi.SVALUE)), '') IS NULL
                THEN 'Blank configured label'
            ELSE 'Pick-list field does not match condition operand'
        END AS GapReason
    FROM #ConditionChoices cc
    LEFT JOIN dbo.CUSTOMFIELDPICKLISTITEM cfi
        ON TRY_CONVERT(uniqueidentifier, cfi.GCUSTOMFIELDPICKLISTITEM) =
           cc.ComparisonGuid
    LEFT JOIN dbo.CUSTOMFIELDPICKLIST cfp
        ON cfi.FKGCUSTOMFIELDPICKLIST = cfp.GCUSTOMFIELDPICKLIST
    LEFT JOIN dbo.CUSTOMFIELD cf
        ON cfp.FKGCUSTOMFIELD = cf.GCUSTOMFIELD
    WHERE NOT EXISTS (
        SELECT 1
        FROM SafeLabels safe
        WHERE
            safe.OperandValue = cc.OperandValue
            AND safe.ClassName = cc.ClassName
            AND safe.ComparisonGuid = cc.ComparisonGuid
    )
)
SELECT *
INTO #Gaps
FROM GapDetails;

-------------------------------------------------------------------------------
-- Result 1: unresolved custom-field condition keys.
-------------------------------------------------------------------------------
SELECT
    OperandValue,
    ClassName,
    ComparisonValue,
    CandidateLabel,
    PickListFieldName,
    PickListCustomField,
    GapReason
FROM #Gaps
ORDER BY OperandValue, ClassName, ComparisonValue;

-------------------------------------------------------------------------------
-- Result 2: compact reason totals.
-------------------------------------------------------------------------------
SELECT
    GapReason,
    COUNT(*) AS GapRows,
    COUNT(DISTINCT OperandValue) AS DistinctOperandFields
FROM #Gaps
GROUP BY GapReason
ORDER BY GapReason;

DROP TABLE IF EXISTS #Gaps;
DROP TABLE IF EXISTS #ConditionChoices;
