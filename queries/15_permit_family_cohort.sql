/*
    Permit Family Cohort

    Purpose:
      Supply the complete historical denominator for fee-occurrence analysis.
      The result retains every qualifying parent permit, including parents
      with no active fees, and every validated direct related permit,
      including related permits with no active fees.

    Parent eligibility and relationship validation intentionally match
    permit_family_fee_detail.sql.

    Grain:
      One row per ParentPermitID + FeeOwnerPermitID.

    Notes:
      - Fee counts are diagnostics only and never filter a family member.
      - Children linked to more than one qualifying parent are excluded.
      - This is a direct parent -> child model; it is not recursive.
*/

SET NOCOUNT ON;

DECLARE @StartDate date = '2019-07-01';

WITH TargetPermitTypes AS (
    SELECT PermitType
    FROM (VALUES
        (CAST(N'DSINSP - Building (Non-Residential)' AS nvarchar(500))),
        (N'DSINSP - Building (Residential)'),
        (N'DSINSP - Certificate of Occupancy'),
        (N'DSINSP - Demolition'),
        (N'DSINSP - Electrical (Non-Residential)'),
        (N'DSINSP - Electrical (Residential)'),
        (N'DSINSP - Mechanical (Non-Residential)'),
        (N'DSINSP - Mechanical (Residential)'),
        (N'DSINSP - Online Only Electrical (Non-Residential)'),
        (N'DSINSP - Online Only Electrical (Residential)'),
        (N'DSINSP - Online Only Mechanical (Non-Residential)'),
        (N'DSINSP - Online Only Mechanical (Residential)'),
        (N'DSINSP - Online Only MENR/ELNR Changeout - Combo'),
        (N'DSINSP - Online Only MER/ELR Changeout - Combo'),
        (N'DSINSP - Online Only PLNR/ELNR Changeout - Combo'),
        (N'DSINSP - Online Only PLNR/MENR Changeout- Combo'),
        (N'DSINSP - Online Only PLR/ELR Changeout - Combo'),
        (N'DSINSP - Online Only PLR/MER Changeout - Combo'),
        (N'DSINSP - Online Only Plumbing (Non-Residential)'),
        (N'DSINSP - Online Only Plumbing (Residential)'),
        (N'DSINSP - Plumbing (Non-Residential)'),
        (N'DSINSP - Plumbing (Residential)'),
        (N'DSINSP - Typical Construction Drawings'),
        (N'DSLC - Zoning Permit'),
        (N'Online Only PLNR/ELNR/MENR Changeout - Combo'),
        (N'Online Only PLR/ELR/MER Changeout - Combo')
    ) valueset(PermitType)
),
TargetParents AS (
    SELECT
        pm.PMPERMITID AS ParentPermitID,
        pm.PERMITNUMBER AS ParentPermitNumber,
        pmt.NAME AS ParentPermitType,
        pwc.NAME AS ParentWorkClass,
        LTRIM(RTRIM(ps.NAME)) AS ParentPermitStatus,
        pm.APPLYDATE AS ParentAppliedDate,
        pm.ISSUEDATE AS ParentIssuedDate,
        pm.FINALIZEDATE AS ParentFinalizedDate
    FROM PMPERMIT pm
    INNER JOIN PMPERMITTYPE pmt
        ON pm.PMPERMITTYPEID = pmt.PMPERMITTYPEID
    INNER JOIN TargetPermitTypes targetType
        ON pmt.NAME = targetType.PermitType
    INNER JOIN PMPERMITWORKCLASS pwc
        ON pm.PMPERMITWORKCLASSID = pwc.PMPERMITWORKCLASSID
    INNER JOIN PMPERMITSTATUS ps
        ON pm.PMPERMITSTATUSID = ps.PMPERMITSTATUSID
    WHERE
        pm.APPLYDATE >= @StartDate
        AND (
            pm.ISSUEDATE IS NOT NULL
            OR pm.FINALIZEDATE IS NOT NULL
        )
        AND UPPER(LTRIM(RTRIM(ps.NAME))) NOT IN (
            'VOID',
            'WITHDRAWN',
            'CANCELLED',
            'CANCELED'
        )
),
RawRelationships AS (
    SELECT DISTINCT
        tp.ParentPermitID,
        wf.WF_PERMITID AS ChildPermitID,
        wf.STEP_NAME,
        wf.ACTION_NAME,
        wf.ACTION_START_DATE,
        wf.ACTION_END_DATE,
        childType.NAME AS ChildPermitType
    FROM TargetParents tp
    INNER JOIN dbo.vw_PERMIT_WORKFLOW wf
        ON tp.ParentPermitID = wf.PMPERMITID
    INNER JOIN PMPERMIT child
        ON wf.WF_PERMITID = child.PMPERMITID
    INNER JOIN PMPERMITTYPE childType
        ON child.PMPERMITTYPEID = childType.PMPERMITTYPEID
    WHERE
        wf.WF_PERMITID IS NOT NULL
        AND tp.ParentPermitID <> wf.WF_PERMITID
        AND NOT (
            wf.STEP_NAME =
                'Administrative Conditions of Approval'
            AND wf.ACTION_NAME =
                'Create/Link - Right of Way [Encroachment] Permit'
        )
),
Classified AS (
    SELECT
        r.*,
        CASE
            WHEN
                   UPPER(r.ACTION_NAME)
                       LIKE '%CERTIFICATE OF OCCUPANCY%'
                OR UPPER(r.ACTION_NAME)
                       LIKE '%TEMPORARY CERTIFICATE%'
                OR UPPER(r.ACTION_NAME)
                       LIKE '%PARTIAL CERTIFICATE%'
                THEN 'Certificate of Occupancy'
            WHEN UPPER(r.ACTION_NAME) LIKE '%FIRE%'
                THEN 'Fire'
            WHEN
                   UPPER(r.ACTION_NAME) LIKE '%ELECTRICAL%'
                OR UPPER(r.ACTION_NAME) LIKE '%MECHANICAL%'
                OR UPPER(r.ACTION_NAME) LIKE '%PLUMBING%'
                THEN 'MEP'
            WHEN
                   UPPER(r.ACTION_NAME) = 'RIGHT OF WAY'
                OR UPPER(r.ACTION_NAME) LIKE '%RIGHT OF WAY%'
                OR UPPER(r.ACTION_NAME) LIKE '%RIGHT-OF-WAY%'
                OR UPPER(r.ACTION_NAME) = 'STREET CUT'
                THEN 'Right of Way'
            WHEN
                   UPPER(r.ACTION_NAME) LIKE '%STORMWATER%'
                OR UPPER(r.ACTION_NAME) = 'LAND DISTURBING'
                OR UPPER(r.ACTION_NAME) LIKE '%LAND DISTURBANCE%'
                OR UPPER(r.ACTION_NAME) = 'FLOOD PLAIN'
                OR UPPER(r.ACTION_NAME) LIKE '%FLOODPLAIN%'
                OR UPPER(r.ACTION_NAME) LIKE '%WATERCOURSE BUFFER%'
                OR UPPER(r.ACTION_NAME)
                       LIKE '%WATER SUPPLY WATERSHED%'
                THEN 'Stormwater / Environmental'
            WHEN
                   UPPER(r.ACTION_NAME) = 'UTILITY CONNECTION'
                OR UPPER(r.ACTION_NAME) = 'STUB'
                OR UPPER(r.ACTION_NAME) LIKE '%PUBLIC UTILITIES%'
                OR UPPER(r.ACTION_NAME) LIKE '%UC / STUBS%'
                OR UPPER(r.ACTION_NAME) LIKE '%UTILITY CONNECTION%'
                THEN 'Utilities / Stubs'
            WHEN UPPER(r.ACTION_NAME) LIKE '%URBAN FORESTRY%'
                THEN 'Urban Forestry'
            WHEN
                   UPPER(r.ACTION_NAME) = 'ZONING'
                OR UPPER(r.ACTION_NAME) LIKE '%ZONING PERMIT%'
                OR UPPER(r.ACTION_NAME) = 'SIGN'
                THEN 'Zoning / Sign'
            ELSE NULL
        END AS ActionFamily,
        CASE
            WHEN r.ChildPermitType =
                'DSINSP - Certificate of Occupancy'
                THEN 'Certificate of Occupancy'
            WHEN r.ChildPermitType =
                'FIRE - Fire Protection'
                THEN 'Fire'
            WHEN r.ChildPermitType IN (
                'DSINSP - Electrical (Residential)',
                'DSINSP - Electrical (Non-Residential)',
                'DSINSP - Mechanical (Residential)',
                'DSINSP - Mechanical (Non-Residential)',
                'DSINSP - Plumbing (Residential)',
                'DSINSP - Plumbing (Non-Residential)',
                'DSINSP - Online Only Electrical (Residential)',
                'DSINSP - Online Only Electrical (Non-Residential)',
                'DSINSP - Online Only Mechanical (Residential)',
                'DSINSP - Online Only Mechanical (Non-Residential)',
                'DSINSP - Online Only Plumbing (Residential)',
                'DSINSP - Online Only Plumbing (Non-Residential)',
                'DSINSP - Online Only MENR/ELNR Changeout - Combo',
                'DSINSP - Online Only MER/ELR Changeout - Combo',
                'DSINSP - Online Only PLNR/ELNR Changeout - Combo',
                'DSINSP - Online Only PLNR/MENR Changeout- Combo',
                'DSINSP - Online Only PLR/ELR Changeout - Combo',
                'DSINSP - Online Only PLR/MER Changeout - Combo',
                'Online Only PLNR/ELNR/MENR Changeout - Combo',
                'Online Only PLR/ELR/MER Changeout - Combo'
            )
                THEN 'MEP'
            WHEN r.ChildPermitType IN (
                'DSENG - Right of Way',
                'DSENG - Right of Way Encroachment'
            )
                THEN 'Right of Way'
            WHEN r.ChildPermitType IN (
                'ENG - Stormwater Control',
                'ENG - Floodplain',
                'ENG - Land Disturbance (Grading)',
                'ENG - Watercourse Buffer',
                'ENG - Water Supply Watershed Permit'
            )
                THEN 'Stormwater / Environmental'
            WHEN r.ChildPermitType =
                'PRCR - Urban Forestry'
                THEN 'Urban Forestry'
            WHEN r.ChildPermitType =
                'PU - Utility Water/Sewer Permit (CoR ETJ)'
                THEN 'Utilities / Stubs'
            WHEN r.ChildPermitType =
                'DSLC - Zoning Permit'
                THEN 'Zoning / Sign'
            ELSE NULL
        END AS ChildTypeFamily
    FROM RawRelationships r
),
ValidatedActionRows AS (
    SELECT *
    FROM Classified
    WHERE
        ActionFamily IS NOT NULL
        AND ChildTypeFamily IS NOT NULL
        AND ActionFamily = ChildTypeFamily
),
RelationshipPairs AS (
    SELECT
        ParentPermitID,
        ChildPermitID,
        MIN(ChildTypeFamily) AS RelationshipFamily,
        MIN(STEP_NAME) AS RelationshipStepName,
        MIN(ACTION_NAME) AS RelationshipActionName,
        MIN(ACTION_START_DATE) AS RelationshipActionStartDate,
        MAX(ACTION_END_DATE) AS RelationshipActionEndDate,
        COUNT(*) AS RelationshipActionRowCount
    FROM ValidatedActionRows
    GROUP BY
        ParentPermitID,
        ChildPermitID
),
ParentCounts AS (
    SELECT
        ChildPermitID,
        COUNT(DISTINCT ParentPermitID) AS QualifyingParentCount
    FROM RelationshipPairs
    GROUP BY ChildPermitID
),
ValidatedRelationships AS (
    SELECT
        rp.ParentPermitID,
        rp.ChildPermitID,
        rp.RelationshipFamily,
        rp.RelationshipStepName,
        rp.RelationshipActionName,
        rp.RelationshipActionStartDate,
        rp.RelationshipActionEndDate,
        rp.RelationshipActionRowCount,
        pc.QualifyingParentCount
    FROM RelationshipPairs rp
    INNER JOIN ParentCounts pc
        ON rp.ChildPermitID = pc.ChildPermitID
),
FamilyMembers AS (
    SELECT
        tp.ParentPermitID AS AnchorParentPermitID,
        tp.ParentPermitID AS FeeOwnerPermitID,
        CAST('Parent' AS varchar(10)) AS FeeOwnerRole,
        CAST(0 AS int) AS FamilyMemberDepth,
        CAST('Parent Permit' AS varchar(100)) AS RelationshipFamily,
        CAST(NULL AS nvarchar(500)) AS RelationshipStepName,
        CAST(NULL AS nvarchar(500)) AS RelationshipActionName,
        CAST(NULL AS datetime) AS RelationshipActionStartDate,
        CAST(NULL AS datetime) AS RelationshipActionEndDate,
        CAST(NULL AS int) AS RelationshipActionRowCount,
        CAST(NULL AS int) AS QualifyingParentCount
    FROM TargetParents tp

    UNION ALL

    SELECT
        vr.ParentPermitID,
        vr.ChildPermitID,
        'Child',
        1,
        vr.RelationshipFamily,
        vr.RelationshipStepName,
        vr.RelationshipActionName,
        vr.RelationshipActionStartDate,
        vr.RelationshipActionEndDate,
        vr.RelationshipActionRowCount,
        vr.QualifyingParentCount
    FROM ValidatedRelationships vr
    WHERE vr.QualifyingParentCount = 1
),
FamilyCases AS (
    SELECT
        fm.AnchorParentPermitID,
        tp.ParentPermitNumber,
        tp.ParentPermitType,
        tp.ParentWorkClass,
        tp.ParentPermitStatus,
        tp.ParentAppliedDate,
        tp.ParentIssuedDate,
        tp.ParentFinalizedDate,
        fm.FeeOwnerPermitID,
        fm.FeeOwnerRole,
        fm.FamilyMemberDepth,
        fm.RelationshipFamily,
        fm.RelationshipStepName,
        fm.RelationshipActionName,
        fm.RelationshipActionStartDate,
        fm.RelationshipActionEndDate,
        fm.RelationshipActionRowCount,
        fm.QualifyingParentCount,
        owner.PERMITNUMBER AS FeeOwnerPermitNumber,
        ownerType.NAME AS FeeOwnerPermitType,
        ownerWC.NAME AS FeeOwnerWorkClass,
        LTRIM(RTRIM(ownerStatus.NAME)) AS FeeOwnerPermitStatus,
        owner.APPLYDATE AS FeeOwnerAppliedDate,
        owner.ISSUEDATE AS FeeOwnerIssuedDate,
        owner.FINALIZEDATE AS FeeOwnerFinalizedDate,
        CAST(
            CASE
                WHEN targetOwner.PermitType IS NOT NULL THEN 1
                ELSE 0
            END
            AS bit
        ) AS FeeOwnerMeetsTargetType,
        CAST(
            CASE
                WHEN UPPER(LTRIM(RTRIM(ownerStatus.NAME))) NOT IN (
                    'VOID',
                    'WITHDRAWN',
                    'CANCELLED',
                    'CANCELED'
                ) THEN 1
                ELSE 0
            END
            AS bit
        ) AS FeeOwnerHasValidStatus,
        CAST(
            CASE
                WHEN
                    owner.ISSUEDATE IS NOT NULL
                    OR owner.FINALIZEDATE IS NOT NULL
                THEN 1
                ELSE 0
            END
            AS bit
        ) AS FeeOwnerIsIssuedOrFinalized
    FROM FamilyMembers fm
    INNER JOIN TargetParents tp
        ON fm.AnchorParentPermitID = tp.ParentPermitID
    INNER JOIN PMPERMIT owner
        ON fm.FeeOwnerPermitID = owner.PMPERMITID
    INNER JOIN PMPERMITTYPE ownerType
        ON owner.PMPERMITTYPEID = ownerType.PMPERMITTYPEID
    LEFT JOIN PMPERMITWORKCLASS ownerWC
        ON owner.PMPERMITWORKCLASSID = ownerWC.PMPERMITWORKCLASSID
    LEFT JOIN PMPERMITSTATUS ownerStatus
        ON owner.PMPERMITSTATUSID = ownerStatus.PMPERMITSTATUSID
    LEFT JOIN TargetPermitTypes targetOwner
        ON ownerType.NAME = targetOwner.PermitType
),
ActiveFeeLinks AS (
    SELECT DISTINCT
        pf.PMPERMITID AS FeeOwnerPermitID,
        cf.CACOMPUTEDFEEID AS ComputedFeeID,
        cf.COMPUTEDAMOUNT AS ComputedAmount
    FROM PMPERMITFEE pf
    INNER JOIN (
        SELECT DISTINCT FeeOwnerPermitID
        FROM FamilyMembers
    ) members
        ON pf.PMPERMITID = members.FeeOwnerPermitID
    INNER JOIN CACOMPUTEDFEE cf
        ON pf.CACOMPUTEDFEEID = cf.CACOMPUTEDFEEID
    WHERE
        cf.CASTATUSID NOT IN (5, 10)
        AND COALESCE(cf.ISDELETED, 0) = 0
),
ActiveFeeRollup AS (
    SELECT
        FeeOwnerPermitID,
        COUNT(*) AS ActiveFeeInstances,
        SUM(COALESCE(ComputedAmount, 0)) AS ActiveComputedAmount
    FROM ActiveFeeLinks
    GROUP BY FeeOwnerPermitID
),
Result AS (
    SELECT
        fc.AnchorParentPermitID AS ParentPermitID,
        CONCAT('Permit|', fc.AnchorParentPermitID) AS ParentCaseKey,
        fc.ParentPermitNumber,
        fc.ParentPermitType,
        fc.ParentWorkClass,
        fc.ParentPermitStatus,
        CAST(fc.ParentAppliedDate AS date) AS ParentAppliedDate,
        CAST(fc.ParentIssuedDate AS date) AS ParentIssuedDate,
        CAST(fc.ParentFinalizedDate AS date) AS ParentFinalizedDate,
        fc.FeeOwnerPermitID,
        CONCAT('Permit|', fc.FeeOwnerPermitID) AS FeeOwnerCaseKey,
        fc.FeeOwnerRole,
        fc.FamilyMemberDepth,
        fc.RelationshipFamily,
        fc.RelationshipStepName,
        fc.RelationshipActionName,
        fc.RelationshipActionStartDate,
        fc.RelationshipActionEndDate,
        fc.RelationshipActionRowCount,
        fc.QualifyingParentCount,
        fc.FeeOwnerPermitNumber,
        fc.FeeOwnerPermitType,
        fc.FeeOwnerWorkClass,
        fc.FeeOwnerPermitStatus,
        CAST(fc.FeeOwnerAppliedDate AS date) AS FeeOwnerAppliedDate,
        CAST(fc.FeeOwnerIssuedDate AS date) AS FeeOwnerIssuedDate,
        CAST(fc.FeeOwnerFinalizedDate AS date) AS FeeOwnerFinalizedDate,
        fc.FeeOwnerMeetsTargetType,
        fc.FeeOwnerHasValidStatus,
        fc.FeeOwnerIsIssuedOrFinalized,
        CAST(
            CASE
                WHEN COALESCE(af.ActiveFeeInstances, 0) > 0 THEN 1
                ELSE 0
            END
            AS bit
        ) AS FeeOwnerHasActiveFee,
        COALESCE(af.ActiveFeeInstances, 0) AS ActiveFeeInstances,
        COALESCE(af.ActiveComputedAmount, 0) AS ActiveComputedAmount,
        COUNT(*) OVER (
            PARTITION BY fc.AnchorParentPermitID
        ) AS FamilyMemberCount,
        SUM(
            CASE WHEN fc.FeeOwnerRole = 'Child' THEN 1 ELSE 0 END
        ) OVER (
            PARTITION BY fc.AnchorParentPermitID
        ) AS RelatedPermitCount,
        SUM(COALESCE(af.ActiveFeeInstances, 0)) OVER (
            PARTITION BY fc.AnchorParentPermitID
        ) AS FamilyActiveFeeInstances
    FROM FamilyCases fc
    LEFT JOIN ActiveFeeRollup af
        ON fc.FeeOwnerPermitID = af.FeeOwnerPermitID
)
SELECT
    *,
    CAST(
        CASE WHEN FamilyActiveFeeInstances > 0 THEN 1 ELSE 0 END
        AS bit
    ) AS FamilyHasActiveFee
FROM Result
ORDER BY
    ParentPermitID,
    FamilyMemberDepth,
    FeeOwnerPermitID;
