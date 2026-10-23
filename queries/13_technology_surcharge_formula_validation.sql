/*
    Validate technology-surcharge cross-reference direction and arithmetic.

    Scope is active MISC - Technology Surcharge fees created during the
    current configuration era. Results are aggregate diagnostics only.
*/
SET NOCOUNT ON;

DECLARE @StartDate date = '2022-07-01';

WITH CurrentInspectionTechnologyAssignment AS (
    SELECT CAST(v.FeeTemplateFeeID AS char(36)) AS FeeTemplateFeeID
    FROM (VALUES
        ('c3542e9c-0375-4bc2-8265-930ec7a41ded'),
        ('403bb6c4-669d-4377-96ef-a5942be84ee2'),
        ('507b594f-7621-466d-8954-660a0bff6097'),
        ('dc8b93d6-397a-4bac-80e0-4d88e7b5587a'),
        ('aeca9d25-5428-409c-8b62-11fce3a97e54'),
        ('75b6a2ea-d3a6-44a0-a08a-227920983908'),
        ('d60dcf5d-1e15-4316-85eb-04aedd60e666'),
        ('860bab91-a74a-42c8-bde3-21c880a49784'),
        ('673ea50f-d4e4-4a69-b647-f87d7d48163a'),
        ('63ca9133-5652-4983-a6c6-5a6f60e3bdfe'),
        ('17ab37b5-f16b-46b6-8d26-5ea49c89014a'),
        ('d477b555-4bc5-46a0-9f86-3cf87c88a33f'),
        ('05a3f0a2-1468-4a96-9e40-492fd34a3fd6'),
        ('03d74690-0fa1-495e-abfe-9344fb2eb22c'),
        ('58968657-caf4-47c4-9471-7b974ab2dbdc'),
        ('cff64d22-d9ad-43b2-a537-a6efbf2b3cb6'),
        ('7475ff70-262a-4d3d-b5d1-4867248c0df0'),
        ('ade8cc60-5fcb-4ae7-bdc9-0dd9507c6f27'),
        ('3a62503b-5e7f-48c9-9171-17394473f9b1'),
        ('1181113d-adba-43e9-9885-d7f1c2408a32'),
        ('23632749-4c15-41b5-80ef-77b8fb17f112'),
        ('34a7694f-cc8d-41a4-bc1d-137d35e964cc'),
        ('0a29cd01-b9a1-4508-8632-10586f9d2a67'),
        ('b952aede-23d4-47f9-b1d7-fe7b4a9a204e'),
        ('9bef55ff-d0d5-4e84-91f7-9edd3e827c3c'),
        ('793ba9e6-5227-4db6-a517-0f8a10a7f97e'),
        ('c507f5f5-572a-4318-92a4-5c3d84f8b712'),
        ('5c3209ff-ee5e-45cc-b21d-ebe6a843f680')
    ) v (FeeTemplateFeeID)
),
TechnologyFee AS (
    SELECT
        cf.CACOMPUTEDFEEID AS TechnologyComputedFeeID,
        cf.CAFEETEMPLATEFEEID AS TechnologyFeeTemplateFeeID,
        cf.COMPUTEDAMOUNT AS TechnologyAmount,
        cf.INPUTVALUE AS TechnologyInputValue,
        cf.ISPROCESSED AS TechnologyIsProcessed,
        cf.CREATEDON AS TechnologyCreatedOn
    FROM CACOMPUTEDFEE cf
    INNER JOIN CurrentInspectionTechnologyAssignment current_assignment
        ON cf.CAFEETEMPLATEFEEID = current_assignment.FeeTemplateFeeID
    WHERE
        cf.CREATEDON >= @StartDate
        AND UPPER(LTRIM(RTRIM(cf.FEENAME))) =
            'MISC - TECHNOLOGY SURCHARGE'
        AND cf.CASTATUSID NOT IN (5, 10)
        AND COALESCE(cf.ISDELETED, 0) = 0
),
LinkCandidate AS (
    SELECT
        t.TechnologyComputedFeeID,
        t.TechnologyAmount,
        t.TechnologyInputValue,
        t.TechnologyIsProcessed,
        YEAR(t.TechnologyCreatedOn) AS TechnologyCreatedYear,
        'Technology fee is CACOMPUTEDFEEID' AS LinkDirection,
        x.CACOMPUTEDFEEXREFID,
        x.XREFAMOUNT,
        referenced.COMPUTEDAMOUNT AS ReferencedComputedAmount
    FROM TechnologyFee t
    INNER JOIN CACOMPUTEDFEEXREF x
        ON t.TechnologyComputedFeeID = x.CACOMPUTEDFEEID
    LEFT JOIN CACOMPUTEDFEE referenced
        ON x.XREFCACOMPUTEDFEEID = referenced.CACOMPUTEDFEEID

    UNION ALL

    SELECT
        t.TechnologyComputedFeeID,
        t.TechnologyAmount,
        t.TechnologyInputValue,
        t.TechnologyIsProcessed,
        YEAR(t.TechnologyCreatedOn),
        'Technology fee is XREFCACOMPUTEDFEEID',
        x.CACOMPUTEDFEEXREFID,
        x.XREFAMOUNT,
        source_fee.COMPUTEDAMOUNT
    FROM TechnologyFee t
    INNER JOIN CACOMPUTEDFEEXREF x
        ON t.TechnologyComputedFeeID = x.XREFCACOMPUTEDFEEID
    LEFT JOIN CACOMPUTEDFEE source_fee
        ON x.CACOMPUTEDFEEID = source_fee.CACOMPUTEDFEEID
),
LinkRollup AS (
    SELECT
        TechnologyComputedFeeID,
        TechnologyAmount,
        TechnologyInputValue,
        TechnologyIsProcessed,
        TechnologyCreatedYear,
        LinkDirection,
        COUNT(*) AS XrefRows,
        SUM(COALESCE(XREFAMOUNT, 0)) AS XrefAmountTotal,
        SUM(COALESCE(ReferencedComputedAmount, 0))
            AS ReferencedComputedAmountTotal,
        SUM(ROUND(COALESCE(XREFAMOUNT, 0) * 0.04, 0))
            AS SumRoundedXrefComponents,
        SUM(ROUND(COALESCE(ReferencedComputedAmount, 0) * 0.04, 0))
            AS SumRoundedComputedComponents
    FROM LinkCandidate
    GROUP BY
        TechnologyComputedFeeID,
        TechnologyAmount,
        TechnologyInputValue,
        TechnologyIsProcessed,
        TechnologyCreatedYear,
        LinkDirection
),
Evaluation AS (
    SELECT
        *,
        ROUND(XrefAmountTotal * 0.04, 0) AS RoundedXrefTotal,
        ROUND(ReferencedComputedAmountTotal * 0.04, 0)
            AS RoundedComputedTotal
    FROM LinkRollup
)
SELECT
    e.LinkDirection,
    e.TechnologyCreatedYear,
    (SELECT COUNT(*) FROM TechnologyFee) AS TechnologyFeesInScope,
    COUNT(*) AS TechnologyFeesWithXref,
    SUM(e.XrefRows) AS XrefRows,
    SUM(CASE
        WHEN ABS(e.TechnologyAmount - e.RoundedXrefTotal) < 0.005
            THEN 1 ELSE 0
    END) AS MatchesRoundedXrefTotal,
    SUM(CASE
        WHEN ABS(e.TechnologyAmount - e.SumRoundedXrefComponents) < 0.005
            THEN 1 ELSE 0
    END) AS MatchesSumRoundedXrefComponents,
    SUM(CASE
        WHEN ABS(e.TechnologyAmount - e.RoundedComputedTotal) < 0.005
            THEN 1 ELSE 0
    END) AS MatchesRoundedComputedTotal,
    SUM(CASE
        WHEN ABS(e.TechnologyAmount - e.SumRoundedComputedComponents) < 0.005
            THEN 1 ELSE 0
    END) AS MatchesSumRoundedComputedComponents,
    SUM(CASE
        WHEN ABS(e.TechnologyAmount - e.XrefAmountTotal) < 0.005
            THEN 1 ELSE 0
    END) AS MatchesXrefAmountDirectly,
    SUM(CASE
        WHEN ABS(
            e.TechnologyAmount
            - ROUND(COALESCE(e.TechnologyInputValue, 0) * 0.04, 0)
        ) < 0.005 THEN 1 ELSE 0
    END) AS MatchesRoundedTechnologyInput,
    SUM(CASE
        WHEN ABS(
            e.TechnologyAmount
            - CEILING(COALESCE(e.TechnologyInputValue, 0) * 0.04)
        ) < 0.005 THEN 1 ELSE 0
    END) AS MatchesCeilingTechnologyInput,
    SUM(CASE WHEN e.TechnologyIsProcessed = 1 THEN 1 ELSE 0 END)
        AS ProcessedTechnologyFees,
    SUM(CASE
        WHEN e.TechnologyIsProcessed = 1
            AND ABS(
                e.TechnologyAmount
                - CEILING(COALESCE(e.TechnologyInputValue, 0) * 0.04)
            ) < 0.005 THEN 1 ELSE 0
    END) AS ProcessedMatchesCeilingTechnologyInput,
    SUM(CASE WHEN e.TechnologyAmount = 0 THEN 1 ELSE 0 END)
        AS ZeroTechnologyAmounts,
    SUM(CASE WHEN e.XrefAmountTotal = 0 THEN 1 ELSE 0 END)
        AS ZeroXrefAmountTotals,
    SUM(CASE WHEN e.ReferencedComputedAmountTotal = 0 THEN 1 ELSE 0 END)
        AS ZeroReferencedComputedTotals,
    SUM(CASE WHEN e.XrefRows > 1 THEN 1 ELSE 0 END)
        AS MultiInputTechnologyFees,
    SUM(CASE
        WHEN e.XrefRows > 1
            AND ABS(e.RoundedXrefTotal - e.SumRoundedXrefComponents) >= 0.005
            THEN 1 ELSE 0
    END) AS MultiInputRowsThatDistinguishRounding,
    SUM(CASE
        WHEN e.XrefRows > 1
            AND ABS(e.TechnologyAmount - e.RoundedXrefTotal) < 0.005
            THEN 1 ELSE 0
    END) AS MultiInputMatchesRoundAfterSum,
    SUM(CASE
        WHEN e.XrefRows > 1
            AND ABS(e.TechnologyAmount - e.SumRoundedXrefComponents) < 0.005
            THEN 1 ELSE 0
    END) AS MultiInputMatchesSumAfterRounding
FROM Evaluation e
GROUP BY
    e.LinkDirection,
    e.TechnologyCreatedYear
ORDER BY
    e.LinkDirection,
    e.TechnologyCreatedYear;
