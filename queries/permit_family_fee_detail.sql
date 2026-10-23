/*
    Permit Family Fee Detail

    Purpose:
      Roll direct permit fees and validated child-permit fees up to a
      qualifying parent permit.

    Parent eligibility:
      - Target permit type
      - Applied on/after 2019-07-01
      - Issued or finalized
      - Not void/withdrawn/cancelled

    Child eligibility:
      - Child does NOT need to independently meet the parent reporting scope.
      - Child application date/status/completion are retained as diagnostics.
      - Relationship must belong to a validated workflow-action family.
      - Workflow action family must agree with the child's actual permit type.
      - Children linked to >1 qualifying parent are excluded to avoid
        arbitrary attribution and double counting.

    Validated direct relationship families:
      - Certificate of Occupancy
      - Fire
      - MEP
      - Right of Way
      - Stormwater / Environmental
      - Urban Forestry
      - Utilities / Stubs
      - Zoning / Sign

    Known association-only relationship excluded:
      Administrative Conditions of Approval
      -> Create/Link - Right of Way [Encroachment] Permit

    Grain:
      One active computed fee per:
        ParentPermitID
        + FeeOwnerPermitID
        + ComputedFeeID

    This is a direct parent -> child model only.
    Recursive child-of-child traversal is not included.
*/

SET NOCOUNT ON;

DECLARE @StartDate date = '2019-07-01';


/* ============================================================
   1. TARGET PERMIT TYPES
   ============================================================ */

IF OBJECT_ID('tempdb..#TargetPermitTypes') IS NOT NULL
    DROP TABLE #TargetPermitTypes;

CREATE TABLE #TargetPermitTypes (
    PermitType nvarchar(500) NOT NULL PRIMARY KEY
);

INSERT INTO #TargetPermitTypes (PermitType)
VALUES
('DSINSP - Building (Non-Residential)'),
('DSINSP - Building (Residential)'),
('DSINSP - Certificate of Occupancy'),
('DSINSP - Demolition'),
('DSINSP - Electrical (Non-Residential)'),
('DSINSP - Electrical (Residential)'),
('DSINSP - Mechanical (Non-Residential)'),
('DSINSP - Mechanical (Residential)'),
('DSINSP - Online Only Electrical (Non-Residential)'),
('DSINSP - Online Only Electrical (Residential)'),
('DSINSP - Online Only Mechanical (Non-Residential)'),
('DSINSP - Online Only Mechanical (Residential)'),
('DSINSP - Online Only MENR/ELNR Changeout - Combo'),
('DSINSP - Online Only MER/ELR Changeout - Combo'),
('DSINSP - Online Only PLNR/ELNR Changeout - Combo'),
('DSINSP - Online Only PLNR/MENR Changeout- Combo'),
('DSINSP - Online Only PLR/ELR Changeout - Combo'),
('DSINSP - Online Only PLR/MER Changeout - Combo'),
('DSINSP - Online Only Plumbing (Non-Residential)'),
('DSINSP - Online Only Plumbing (Residential)'),
('DSINSP - Plumbing (Non-Residential)'),
('DSINSP - Plumbing (Residential)'),
('DSINSP - Typical Construction Drawings'),
('DSLC - Zoning Permit'),
('Online Only PLNR/ELNR/MENR Changeout - Combo'),
('Online Only PLR/ELR/MER Changeout - Combo');


/* ============================================================
   2. QUALIFYING PARENT / ANCHOR PERMITS
   ============================================================ */

IF OBJECT_ID('tempdb..#TargetParents') IS NOT NULL
    DROP TABLE #TargetParents;

SELECT
    pm.PMPERMITID AS ParentPermitID,
    pm.PERMITNUMBER AS ParentPermitNumber,
    pmt.NAME AS ParentPermitType,
    pwc.NAME AS ParentWorkClass,
    LTRIM(RTRIM(ps.NAME)) AS ParentPermitStatus,

    pm.APPLYDATE AS ParentAppliedDate,
    pm.ISSUEDATE AS ParentIssuedDate,
    pm.FINALIZEDATE AS ParentFinalizedDate,
    pm.EXPIREDATE AS ParentExpireDate,

    CASE
        WHEN pm.FINALIZEDATE IS NOT NULL THEN 'Finalized'
        ELSE 'Issued'
    END AS ParentEligibilityBasis,

    CONCAT(
        'https://raleighnc-energov.tylerhost.net/apps/managepermit/',
        '#/permit/',
        pm.PMPERMITID,
        '/summary'
    ) AS ParentEPLSummaryURL,

    pm.DESCRIPTION AS ParentDescription,
    pm.VALUE AS ParentDeclaredValue,
    pm.SQUAREFEET AS ParentSquareFeet,

    LTRIM(RTRIM(occupancy.SVALUE)) AS ParentCensusOccupancy,
    LTRIM(RTRIM(d.NAME)) AS ParentDistrict,

    project.PRPROJECTID AS ParentProjectID,
    LTRIM(RTRIM(project.PROJECTNUMBER)) AS ParentProjectNumber,
    LTRIM(RTRIM(project.NAME)) AS ParentProjectName,

    NULLIF(
        RTRIM(
            LTRIM(ISNULL(address.ADDRESSLINE1, '') + ' ')
            + LTRIM(ISNULL(address.PREDIRECTION, '') + ' ')
            + LTRIM(ISNULL(address.ADDRESSLINE2, '') + ' ')
            + LTRIM(ISNULL(address.STREETTYPE, '') + ' ')
            + LTRIM(ISNULL(address.POSTDIRECTION, '') + ' ')
            + LTRIM(ISNULL(address.UNITORSUITE, ''))
        ),
        ''
    ) AS ParentAddressLine1,

    NULLIF(
        RTRIM(
            CASE
                WHEN LTRIM(RTRIM(ISNULL(address.CITY, ''))) = ''
                    THEN ''
                ELSE LTRIM(address.CITY + ', ')
            END
            + LTRIM(ISNULL(address.STATE, '') + ' ')
            + ISNULL(address.POSTALCODE, '')
        ),
        ''
    ) AS ParentAddressLine2,

    LTRIM(RTRIM(address.CITY)) AS ParentCity,
    LTRIM(RTRIM(address.STATE)) AS ParentState,
    LTRIM(RTRIM(address.POSTALCODE)) AS ParentPostalCode,
    LTRIM(RTRIM(address.PARCELNUMBER)) AS ParentParcelNumber,

    pm.ISAPPLIEDONLINE AS ParentIsAppliedOnline

INTO #TargetParents

FROM PMPERMIT pm

INNER JOIN PMPERMITTYPE pmt
    ON pm.PMPERMITTYPEID = pmt.PMPERMITTYPEID

INNER JOIN #TargetPermitTypes targetType
    ON pmt.NAME = targetType.PermitType

INNER JOIN PMPERMITWORKCLASS pwc
    ON pm.PMPERMITWORKCLASSID = pwc.PMPERMITWORKCLASSID

INNER JOIN PMPERMITSTATUS ps
    ON pm.PMPERMITSTATUSID = ps.PMPERMITSTATUSID

LEFT JOIN CUSTOMSAVERPERMITMANAGEMENT custom
    ON pm.PMPERMITID = custom.ID

LEFT JOIN CUSTOMFIELDPICKLISTITEM occupancy
    ON custom.CensusOccupancy =
       occupancy.GCUSTOMFIELDPICKLISTITEM

LEFT JOIN DISTRICT d
    ON pm.DISTRICTID = d.DISTRICTID

OUTER APPLY (
    SELECT TOP (1)
        ma.ADDRESSLINE1,
        ma.PREDIRECTION,
        ma.ADDRESSLINE2,
        ma.STREETTYPE,
        ma.POSTDIRECTION,
        ma.UNITORSUITE,
        ma.CITY,
        ma.STATE,
        ma.POSTALCODE,
        ma.PARCELNUMBER
    FROM PMPERMITADDRESS pma
    INNER JOIN MAILINGADDRESS ma
        ON pma.MAILINGADDRESSID = ma.MAILINGADDRESSID
    WHERE
        pma.PMPERMITID = pm.PMPERMITID
        AND pma.MAIN = 1
    ORDER BY pma.PMPERMITADDRESSID
) address

OUTER APPLY (
    SELECT TOP (1)
        prj.PRPROJECTID,
        prj.PROJECTNUMBER,
        prj.NAME
    FROM PRPROJECTPERMIT link
    INNER JOIN PRPROJECT prj
        ON link.PRPROJECTID = prj.PRPROJECTID
    WHERE
        link.PMPERMITID = pm.PMPERMITID
    ORDER BY
        prj.NAME,
        prj.PRPROJECTID
) project

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
    );


CREATE UNIQUE CLUSTERED INDEX IX_TargetParents
    ON #TargetParents (ParentPermitID);


/* ============================================================
   3. DISCOVER + CLASSIFY WORKFLOW RELATIONSHIPS
   ============================================================ */

IF OBJECT_ID('tempdb..#ValidatedRelationships') IS NOT NULL
    DROP TABLE #ValidatedRelationships;

WITH RawRelationships AS (
    SELECT DISTINCT
        tp.ParentPermitID,
        wf.WF_PERMITID AS ChildPermitID,

        wf.STEP_NAME,
        wf.ACTION_NAME,
        wf.ACTION_STATUS,
        wf.ACTION_START_DATE,
        wf.ACTION_END_DATE,

        childType.NAME AS ChildPermitType

    FROM #TargetParents tp

    INNER JOIN dbo.vw_PERMIT_WORKFLOW wf
        ON tp.ParentPermitID = wf.PMPERMITID

    INNER JOIN PMPERMIT child
        ON wf.WF_PERMITID = child.PMPERMITID

    INNER JOIN PMPERMITTYPE childType
        ON child.PMPERMITTYPEID =
           childType.PMPERMITTYPEID

    WHERE
        wf.WF_PERMITID IS NOT NULL

        AND tp.ParentPermitID <> wf.WF_PERMITID

        /* Known association/reuse relationship */
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

        /* --------------------------------------------
           Family implied by workflow action
           -------------------------------------------- */
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

        /* --------------------------------------------
           Family implied by actual child permit type
           -------------------------------------------- */
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
    SELECT
        *
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
        COUNT(DISTINCT ParentPermitID)
            AS QualifyingParentCount
    FROM RelationshipPairs
    GROUP BY ChildPermitID
)

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

INTO #ValidatedRelationships

FROM RelationshipPairs rp

INNER JOIN ParentCounts pc
    ON rp.ChildPermitID = pc.ChildPermitID;


CREATE INDEX IX_ValidatedRelationships_Parent
    ON #ValidatedRelationships (ParentPermitID);

CREATE INDEX IX_ValidatedRelationships_Child
    ON #ValidatedRelationships (ChildPermitID);


/* ============================================================
   4. FAMILY MEMBERSHIP

   Parent itself = depth 0
   Unambiguous direct child = depth 1
   ============================================================ */

IF OBJECT_ID('tempdb..#FamilyMembers') IS NOT NULL
    DROP TABLE #FamilyMembers;

SELECT
    tp.ParentPermitID AS AnchorParentPermitID,
    tp.ParentPermitID AS FeeOwnerPermitID,

    CAST('Parent' AS varchar(10)) AS FeeOwnerRole,
    CAST(0 AS int) AS FamilyMemberDepth,

    CAST('Parent Permit' AS varchar(100))
        AS RelationshipFamily,

    CAST(NULL AS nvarchar(500))
        AS RelationshipStepName,

    CAST(NULL AS nvarchar(500))
        AS RelationshipActionName,

    CAST(NULL AS datetime)
        AS RelationshipActionStartDate,

    CAST(NULL AS datetime)
        AS RelationshipActionEndDate,

    CAST(NULL AS int)
        AS RelationshipActionRowCount,

    CAST(NULL AS int)
        AS QualifyingParentCount

INTO #FamilyMembers

FROM #TargetParents tp;


INSERT INTO #FamilyMembers (
    AnchorParentPermitID,
    FeeOwnerPermitID,
    FeeOwnerRole,
    FamilyMemberDepth,
    RelationshipFamily,
    RelationshipStepName,
    RelationshipActionName,
    RelationshipActionStartDate,
    RelationshipActionEndDate,
    RelationshipActionRowCount,
    QualifyingParentCount
)
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

FROM #ValidatedRelationships vr

WHERE
    vr.QualifyingParentCount = 1;


CREATE UNIQUE CLUSTERED INDEX IX_FamilyMembers
    ON #FamilyMembers (
        AnchorParentPermitID,
        FeeOwnerPermitID
    );

CREATE INDEX IX_FamilyMembers_FeeOwner
    ON #FamilyMembers (FeeOwnerPermitID);


/* ============================================================
   5. ENRICH FAMILY MEMBERS WITH PERMIT METADATA
   ============================================================ */

IF OBJECT_ID('tempdb..#FamilyCases') IS NOT NULL
    DROP TABLE #FamilyCases;

SELECT
    fm.AnchorParentPermitID,

    CONCAT(
        'Permit|',
        fm.AnchorParentPermitID
    ) AS ParentCaseKey,

    tp.ParentPermitNumber,
    tp.ParentPermitType,
    tp.ParentWorkClass,
    tp.ParentPermitStatus,
    tp.ParentAppliedDate,
    tp.ParentIssuedDate,
    tp.ParentFinalizedDate,
    tp.ParentExpireDate,
    tp.ParentEligibilityBasis,
    tp.ParentEPLSummaryURL,
    tp.ParentDescription,
    tp.ParentDeclaredValue,
    tp.ParentSquareFeet,
    tp.ParentCensusOccupancy,
    tp.ParentDistrict,
    tp.ParentProjectID,
    tp.ParentProjectNumber,
    tp.ParentProjectName,
    tp.ParentAddressLine1,
    tp.ParentAddressLine2,
    tp.ParentCity,
    tp.ParentState,
    tp.ParentPostalCode,
    tp.ParentParcelNumber,
    tp.ParentIsAppliedOnline,

    fm.FeeOwnerPermitID,

    CONCAT(
        'Permit|',
        fm.FeeOwnerPermitID
    ) AS FeeOwnerCaseKey,

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
    LTRIM(RTRIM(ownerStatus.NAME))
        AS FeeOwnerPermitStatus,

    owner.APPLYDATE AS FeeOwnerAppliedDate,
    owner.ISSUEDATE AS FeeOwnerIssuedDate,
    owner.FINALIZEDATE AS FeeOwnerFinalizedDate,
    owner.EXPIREDATE AS FeeOwnerExpireDate,

    CONCAT(
        'https://raleighnc-energov.tylerhost.net/apps/managepermit/',
        '#/permit/',
        owner.PMPERMITID,
        '/summary'
    ) AS FeeOwnerEPLSummaryURL,

    owner.DESCRIPTION AS FeeOwnerDescription,

    CAST(
        CASE
            WHEN targetOwner.PermitType IS NOT NULL
                THEN 1
            ELSE 0
        END
        AS bit
    ) AS FeeOwnerMeetsTargetType,

    CAST(
        CASE
            WHEN UPPER(
                LTRIM(RTRIM(ownerStatus.NAME))
            ) NOT IN (
                'VOID',
                'WITHDRAWN',
                'CANCELLED',
                'CANCELED'
            )
                THEN 1
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
    ) AS FeeOwnerIsIssuedOrFinalized,

    CAST(
        CASE
            WHEN targetOwner.PermitType IS NOT NULL
             AND UPPER(
                    LTRIM(RTRIM(ownerStatus.NAME))
                 ) NOT IN (
                    'VOID',
                    'WITHDRAWN',
                    'CANCELLED',
                    'CANCELED'
                 )
             AND (
                    owner.ISSUEDATE IS NOT NULL
                    OR owner.FINALIZEDATE IS NOT NULL
                 )
                THEN 1
            ELSE 0
        END
        AS bit
    ) AS FeeOwnerMeetsCaseCriteria,

    CAST(
        CASE
            WHEN targetOwner.PermitType IS NOT NULL
             AND UPPER(
                    LTRIM(RTRIM(ownerStatus.NAME))
                 ) NOT IN (
                    'VOID',
                    'WITHDRAWN',
                    'CANCELLED',
                    'CANCELED'
                 )
             AND (
                    owner.ISSUEDATE IS NOT NULL
                    OR owner.FINALIZEDATE IS NOT NULL
                 )
             AND owner.APPLYDATE >= @StartDate
                THEN 1
            ELSE 0
        END
        AS bit
    ) AS FeeOwnerMeetsOriginalScope

INTO #FamilyCases

FROM #FamilyMembers fm

INNER JOIN #TargetParents tp
    ON fm.AnchorParentPermitID =
       tp.ParentPermitID

INNER JOIN PMPERMIT owner
    ON fm.FeeOwnerPermitID =
       owner.PMPERMITID

INNER JOIN PMPERMITTYPE ownerType
    ON owner.PMPERMITTYPEID =
       ownerType.PMPERMITTYPEID

LEFT JOIN PMPERMITWORKCLASS ownerWC
    ON owner.PMPERMITWORKCLASSID =
       ownerWC.PMPERMITWORKCLASSID

LEFT JOIN PMPERMITSTATUS ownerStatus
    ON owner.PMPERMITSTATUSID =
       ownerStatus.PMPERMITSTATUSID

LEFT JOIN #TargetPermitTypes targetOwner
    ON ownerType.NAME =
       targetOwner.PermitType;


CREATE UNIQUE CLUSTERED INDEX IX_FamilyCases
    ON #FamilyCases (
        AnchorParentPermitID,
        FeeOwnerPermitID
    );


/* ============================================================
   6. PERMIT FEE LINKS

   PMPERMITFEE only.
   IMINSPECTIONFEE remains intentionally excluded.
   ============================================================ */

WITH FeeLinkCandidates AS (
    SELECT
        fc.AnchorParentPermitID,
        fc.FeeOwnerPermitID,

        pf.CACOMPUTEDFEEID,

        pf.PMPERMITFEEID AS FeeBridgeID,
        pf.CREATEDON AS FeeBridgeCreatedOn

    FROM #FamilyCases fc

    INNER JOIN PMPERMITFEE pf
        ON fc.FeeOwnerPermitID =
           pf.PMPERMITID
),

FeeLinks AS (
    SELECT
        AnchorParentPermitID,
        FeeOwnerPermitID,
        CACOMPUTEDFEEID,

        MIN(FeeBridgeID)
            AS PrimaryPermitFeeBridgeID,

        MIN(FeeBridgeCreatedOn)
            AS FeeBridgeCreatedOn,

        COUNT(*) AS FeeBridgeRowCount

    FROM FeeLinkCandidates

    GROUP BY
        AnchorParentPermitID,
        FeeOwnerPermitID,
        CACOMPUTEDFEEID
),

ComputedFeeXrefRoles AS (
    SELECT
        FeeID AS CACOMPUTEDFEEID,
        MAX(IsSource)
            AS IsComputedFeeXrefSource,
        MAX(IsReferenced)
            AS IsComputedFeeXrefReferenced

    FROM (
        SELECT
            x.CACOMPUTEDFEEID AS FeeID,
            1 AS IsSource,
            0 AS IsReferenced
        FROM CACOMPUTEDFEEXREF x

        UNION ALL

        SELECT
            x.XREFCACOMPUTEDFEEID AS FeeID,
            0 AS IsSource,
            1 AS IsReferenced
        FROM CACOMPUTEDFEEXREF x
        WHERE
            x.XREFCACOMPUTEDFEEID IS NOT NULL
    ) roles

    GROUP BY FeeID
),

FeeDetail AS (
    SELECT
        fc.*,

        fl.PrimaryPermitFeeBridgeID,
        fl.FeeBridgeCreatedOn,
        fl.FeeBridgeRowCount,

        cf.CACOMPUTEDFEEID AS ComputedFeeID,

        cf.CAFEETEMPLATEFEEID
            AS FeeTemplateFeeID,

        tf.CAFEETEMPLATEID
            AS FeeTemplateID,

        ft.CAFEETEMPLATENAME
            AS FeeTemplateName,

        ce.NAME AS FeeTemplateEntity,

        LTRIM(RTRIM(tf.FEENAME))
            AS TemplateFeeName,

        LTRIM(RTRIM(cf.FEENAME))
            AS FeeName,

        LTRIM(RTRIM(cf.FEEDESCRIPTION))
            AS FeeDescription,

        f.CAFEEID AS StandardFeeID,

        LTRIM(RTRIM(f.NAME))
            AS StandardFeeName,

        ct.NAME AS ComputationType,

        cf.CASTATUSID AS FeeStatusID,

        LTRIM(RTRIM(cs.NAME))
            AS FeeStatusName,

        cf.ISMANUALLYADDED
            AS IsManuallyAdded,

        tf.ISMANUAL
            AS TemplateIsManual,

        cf.ISONLINEADDED
            AS IsOnlineAdded,

        cf.ISPROCESSED
            AS IsProcessed,

        cf.INPUTVALUE
            AS InputValue,

        cf.DISPLAYINPUTVALUE
            AS DisplayInputValue,

        cf.BASEAMOUNT
            AS BaseAmount,

        cf.COMPUTEDAMOUNT
            AS ComputedAmount,

        cf.AMOUNTPAIDTODATE
            AS AmountPaidToDate,

        COALESCE(cf.COMPUTEDAMOUNT, 0)
            - COALESCE(cf.AMOUNTPAIDTODATE, 0)
            AS OutstandingAmount,

        cf.CREDITAMOUNT
            AS CreditAmount,

        cf.CPIAMOUNT
            AS CPIAmount,

        cf.FEEPRORATIONAMOUNT
            AS FeeProrationAmount,

        cf.FEEPRORATIONRATE
            AS FeeProrationRate,

        cf.CREATEDON
            AS FeeCreatedOn,

        cf.LASTCHANGEDON
            AS FeeLastChangedOn,

        cf.WFACTIONID,

        CASE
            WHEN xref.CACOMPUTEDFEEID IS NULL
                THEN 0
            ELSE 1
        END AS HasComputedFeeXref,

        CASE
            WHEN
                xref.IsComputedFeeXrefSource = 1
                AND xref.IsComputedFeeXrefReferenced = 1
                THEN 'Both'

            WHEN xref.IsComputedFeeXrefSource = 1
                THEN 'Source fee'

            WHEN xref.IsComputedFeeXrefReferenced = 1
                THEN 'Referenced fee'

            ELSE 'None'
        END AS ComputedFeeXrefRole,

        CASE
            WHEN ce.NAME = 'Inspection'
                THEN 'Inspection'
            ELSE 'Permit'
        END AS FeeOriginModule,

        CASE
            WHEN cf.WFACTIONID IS NOT NULL
                THEN 'Workflow action'

            WHEN ce.NAME = 'Inspection'
                THEN 'Inspection template only'

            WHEN xref.CACOMPUTEDFEEID IS NOT NULL
                THEN 'Related fee only'

            WHEN COALESCE(
                cf.ISMANUALLYADDED,
                0
            ) = 1
                THEN 'Manual fee'

            ELSE 'Case fee timestamp only'
        END AS TimingProvenanceClass,

        DATEDIFF(
            DAY,
            fc.ParentAppliedDate,
            cf.CREATEDON
        ) AS DaysFromParentApplicationToFee,

        DATEDIFF(
            DAY,
            fc.FeeOwnerAppliedDate,
            cf.CREATEDON
        ) AS DaysFromFeeOwnerApplicationToFee

    FROM #FamilyCases fc

    INNER JOIN FeeLinks fl
        ON fc.AnchorParentPermitID =
           fl.AnchorParentPermitID

        AND fc.FeeOwnerPermitID =
            fl.FeeOwnerPermitID

    INNER JOIN CACOMPUTEDFEE cf
        ON fl.CACOMPUTEDFEEID =
           cf.CACOMPUTEDFEEID

    LEFT JOIN CASTATUS cs
        ON cf.CASTATUSID =
           cs.CASTATUSID

    LEFT JOIN CAFEETEMPLATEFEE tf
        ON cf.CAFEETEMPLATEFEEID =
           tf.CAFEETEMPLATEFEEID

    LEFT JOIN CAFEETEMPLATE ft
        ON tf.CAFEETEMPLATEID =
           ft.CAFEETEMPLATEID

    LEFT JOIN CAENTITY ce
        ON ft.CAENTITYID =
           ce.CAENTITYID

    LEFT JOIN CAFEE f
        ON tf.CAFEEID =
           f.CAFEEID

    LEFT JOIN CACOMPUTATIONTYPE ct
        ON f.CACOMPUTATIONTYPEID =
           ct.CACOMPUTATIONTYPEID

    LEFT JOIN ComputedFeeXrefRoles xref
        ON cf.CACOMPUTEDFEEID =
           xref.CACOMPUTEDFEEID

    WHERE
        cf.CASTATUSID NOT IN (5, 10)
        AND COALESCE(cf.ISDELETED, 0) = 0
)


/* ============================================================
   7. FINAL FACT
   ============================================================ */

SELECT
    CONCAT(
        'Permit|',
        AnchorParentPermitID
    ) AS FamilyKey,

    ParentCaseKey,
    AnchorParentPermitID AS ParentPermitID,
    ParentPermitNumber,
    ParentPermitType,
    ParentWorkClass,
    ParentPermitStatus,

    CAST(ParentAppliedDate AS date)
        AS ParentAppliedDate,

    CAST(ParentIssuedDate AS date)
        AS ParentIssuedDate,

    CAST(ParentFinalizedDate AS date)
        AS ParentFinalizedDate,

    CAST(ParentExpireDate AS date)
        AS ParentExpireDate,

    ParentEligibilityBasis,
    ParentEPLSummaryURL,
    ParentDescription,
    ParentDeclaredValue,
    ParentSquareFeet,
    ParentCensusOccupancy,
    ParentDistrict,
    ParentProjectID,
    ParentProjectNumber,
    ParentProjectName,
    ParentAddressLine1,
    ParentAddressLine2,
    ParentCity,
    ParentState,
    ParentPostalCode,
    ParentParcelNumber,
    ParentIsAppliedOnline,

    FeeOwnerCaseKey,
    FeeOwnerPermitID,
    FeeOwnerRole,
    FamilyMemberDepth,

    RelationshipFamily,
    RelationshipStepName,
    RelationshipActionName,

    CAST(RelationshipActionStartDate AS datetime)
        AS RelationshipActionStartDate,

    CAST(RelationshipActionEndDate AS datetime)
        AS RelationshipActionEndDate,

    RelationshipActionRowCount,
    QualifyingParentCount,

    FeeOwnerPermitNumber,
    FeeOwnerPermitType,
    FeeOwnerWorkClass,
    FeeOwnerPermitStatus,

    CAST(FeeOwnerAppliedDate AS date)
        AS FeeOwnerAppliedDate,

    CAST(FeeOwnerIssuedDate AS date)
        AS FeeOwnerIssuedDate,

    CAST(FeeOwnerFinalizedDate AS date)
        AS FeeOwnerFinalizedDate,

    CAST(FeeOwnerExpireDate AS date)
        AS FeeOwnerExpireDate,

    FeeOwnerEPLSummaryURL,
    FeeOwnerDescription,

    FeeOwnerMeetsTargetType,
    FeeOwnerHasValidStatus,
    FeeOwnerIsIssuedOrFinalized,
    FeeOwnerMeetsCaseCriteria,
    FeeOwnerMeetsOriginalScope,

    PrimaryPermitFeeBridgeID,

    CAST(FeeBridgeCreatedOn AS datetime)
        AS FeeBridgeCreatedOn,

    FeeBridgeRowCount,
    FeeBridgeRowCount - 1
        AS DuplicateFeeBridgeRows,

    ComputedFeeID,
    FeeTemplateFeeID,
    FeeTemplateID,
    FeeTemplateName,
    FeeTemplateEntity,
    TemplateFeeName,
    FeeName,
    FeeDescription,
    StandardFeeID,
    StandardFeeName,
    ComputationType,
    FeeStatusID,
    FeeStatusName,
    IsManuallyAdded,
    TemplateIsManual,
    IsOnlineAdded,
    IsProcessed,
    InputValue,
    DisplayInputValue,
    BaseAmount,
    ComputedAmount,
    AmountPaidToDate,
    OutstandingAmount,
    CreditAmount,
    CPIAmount,
    FeeProrationAmount,
    FeeProrationRate,

    CAST(FeeCreatedOn AS datetime)
        AS FeeCreatedOn,

    CAST(FeeLastChangedOn AS datetime)
        AS FeeLastChangedOn,

    WFACTIONID,
    HasComputedFeeXref,
    ComputedFeeXrefRole,
    FeeOriginModule,
    TimingProvenanceClass,

    DaysFromParentApplicationToFee,
    DaysFromFeeOwnerApplicationToFee,

    ROW_NUMBER() OVER (
        PARTITION BY
            AnchorParentPermitID,
            FeeOwnerPermitID,
            FeeName
        ORDER BY
            FeeCreatedOn,
            ComputedFeeID
    ) AS SameFeeInstanceSequenceOnFeeOwner,

    COUNT(*) OVER (
        PARTITION BY
            AnchorParentPermitID,
            FeeOwnerPermitID,
            FeeName
    ) AS SameFeeInstancesOnFeeOwner,

    ROW_NUMBER() OVER (
        PARTITION BY
            AnchorParentPermitID,
            FeeName
        ORDER BY
            FeeCreatedOn,
            ComputedFeeID,
            FeeOwnerPermitID
    ) AS SameFeeInstanceSequenceInFamily,

    COUNT(*) OVER (
        PARTITION BY
            AnchorParentPermitID,
            FeeName
    ) AS SameFeeInstancesInFamily,

    /*
        Useful QA field.

        >1 does not automatically mean an error because a qualifying permit
        can be its own anchor and also be a child of another qualifying permit.
    */
    COUNT(*) OVER (
        PARTITION BY ComputedFeeID
    ) AS ComputedFeeRowsInFamilyFact

FROM FeeDetail

ORDER BY
    ParentPermitType,
    ParentWorkClass,
    ParentPermitNumber,
    FamilyMemberDepth,
    FeeOwnerPermitType,
    FeeOwnerWorkClass,
    FeeOwnerPermitNumber,
    FeeCreatedOn,
    ComputedFeeID;