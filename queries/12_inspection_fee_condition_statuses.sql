/*
    Resolve the inspection-status GUIDs referenced by fee-template conditions.

    This is a small validation query for the production inspection-template
    snapshot. It does not identify assessed fees or change report grain.
*/
SET NOCOUNT ON;

WITH ReferencedStatus AS (
    SELECT CAST('1d154ce9-426b-4df0-994e-568060d8d46b' AS char(36))
        AS IMINSPECTIONSTATUSID
    UNION ALL
    SELECT CAST('d25b1b2b-0696-45b5-a059-d0586fe5982d' AS char(36))
    UNION ALL
    SELECT CAST('6dbdbea6-207b-4d24-b942-0a7c4aca2a07' AS char(36))
    UNION ALL
    SELECT CAST('033d8cdb-5d98-43b3-a184-1d15815e54e8' AS char(36))
    UNION ALL
    SELECT CAST('0b2cd89a-2f30-40d2-a468-a42cc2a205c3' AS char(36))
    UNION ALL
    SELECT CAST('5bab43e8-b55f-47fd-9611-4ea089787eaf' AS char(36))
    UNION ALL
    SELECT CAST('09bc0f2e-6943-4056-9116-6ade1c26661d' AS char(36))
    UNION ALL
    SELECT CAST('98fd370d-60e1-4b87-b0fd-5341209f0d40' AS char(36))
    UNION ALL
    SELECT CAST('3175bb64-8446-4047-873d-f7f39893214c' AS char(36))
    UNION ALL
    SELECT CAST('584efeb7-6cf4-4405-910d-45a5b06cf5f9' AS char(36))
    UNION ALL
    SELECT CAST('43da5fee-a741-486e-a0b1-3998ff5ea88f' AS char(36))
)
SELECT
    r.IMINSPECTIONSTATUSID AS ReferencedInspectionStatusID,
    s.STATUSNAME AS InspectionStatus,
    s.INDICATESSUCCESS AS IndicatesSuccess,
    s.REINSPECTIONFEEFLAG AS ReinspectionFeeFlag,
    CASE
        WHEN s.IMINSPECTIONSTATUSID IS NULL THEN 0
        ELSE 1
    END AS Resolved
FROM ReferencedStatus r
LEFT JOIN IMINSPECTIONSTATUS s
    ON r.IMINSPECTIONSTATUSID = s.IMINSPECTIONSTATUSID
ORDER BY
    s.STATUSNAME,
    r.IMINSPECTIONSTATUSID;
