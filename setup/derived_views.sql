-- Connect AI Derived View definitions.
-- Create these in the Connect AI UI under Derived Views.
-- These are cross-catalog views spanning the CRM, cloud warehouse, and ITSM sources;
-- they require connections to all three systems to be set up first.
--
-- Reference date note: '2026-07-16' is the frozen benchmark reference date and
-- must match the synthetic dataset. The 90-day windows in account_usage_trend
-- ('2026-04-17' and '2026-01-17') are derived from this date. Update all three
-- if adapting to a live dataset with a different reference date.


-- ── account_health_score ────────────────────────────────────────────────────
-- Cross-catalog join: CRM accounts + warehouse usage/dimension data +
-- ITSM urgent-ticket counts. Filtered to High-priority accounts only
-- (the benchmark population: 450 rows in the synthetic dataset).
-- Computes health score, status band, review eligibility, and eligibility reason.

SELECT
  A.AccountId,
  A.AccountName,
  A.SLA_Level,
  A.Priority,
  A.Monthly_Jobs,
  A.Contract_End_Date,
  A.Annual_Contract_Value,
  A.Days_To_Renewal,
  A.Urgent_Open_Tickets,
  A.Health_Score_100,
  CASE
    WHEN A.Health_Score_100 < 40 THEN 'CRITICAL'
    WHEN A.Health_Score_100 < 60 THEN 'AT_RISK'
    WHEN A.Health_Score_100 < 80 THEN 'MONITOR'
    ELSE 'HEALTHY'
  END AS Health_Status,
  CASE
    WHEN (A.Days_To_Renewal BETWEEN 0 AND 90)
      OR (A.Annual_Contract_Value >= 250000)
      OR (A.Urgent_Open_Tickets >= 5)
    THEN 'ELIGIBLE'
    ELSE 'NOT ELIGIBLE'
  END AS Review_Eligible,
  CASE
    WHEN A.Days_To_Renewal BETWEEN 0 AND 90 THEN 'Renewal within 90 days'
    WHEN A.Annual_Contract_Value >= 250000   THEN 'ACV above review threshold'
    WHEN A.Urgent_Open_Tickets >= 5         THEN 'High urgent ticket volume'
    ELSE 'No escalation trigger met'
  END AS Eligibility_Reason
FROM (
  SELECT
    a.Id                          AS AccountId,
    a.Name                        AS AccountName,
    a.SLA__c                      AS SLA_Level,
    a.CustomerPriority__c         AS Priority,
    a.AnnualContractValue__c      AS Annual_Contract_Value,
    d.MONTHLY_JOBS_L90            AS Monthly_Jobs,
    a.ContractEndDate__c          AS Contract_End_Date,
    COALESCE(i.uot, 0)            AS Urgent_Open_Tickets,
    DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) AS Days_To_Renewal,
    (
      100
      + CASE a.SLA__c
          WHEN 'Platinum' THEN 10
          WHEN 'Gold'     THEN 5
          WHEN 'Bronze'   THEN -5
          ELSE 0
        END
      - CASE
          WHEN d.MONTHLY_JOBS_L90 < 41  THEN 25
          WHEN d.MONTHLY_JOBS_L90 <= 150 THEN 10
          ELSE 0
        END
      - CASE
          WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 30 THEN 25
          WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 90 THEN 10
          ELSE 0
        END
      - CASE
          WHEN COALESCE(i.uot, 0) >= 5 THEN 25
          ELSE COALESCE(i.uot, 0) * 5
        END
    ) AS Health_Score_100
  FROM CRM_System.Salesforce.Account a
  LEFT JOIN Warehouse_System.WH_DATA.DIM_ACCOUNT d
    ON a.Ext_Account_Id__c = d.SFDC_ACCOUNT_ID
  LEFT JOIN (
    SELECT u_company, COUNT(*) AS uot
    FROM ITSM_System.ServiceNow.u_bm_incident
    WHERE u_priority IN ('Critical', 'High') AND u_state = 'Open'
    GROUP BY u_company
  ) i ON a.Name = i.u_company
  WHERE a.CustomerPriority__c = 'High'
    AND d.MONTHLY_JOBS_L90 IS NOT NULL
) A


-- ── account_usage_trend ─────────────────────────────────────────────────────
-- Covers all 1,200 accounts (no priority filter). The R2 population (poor-health
-- + declining usage) is derived by joining with account_health_score at query time.
-- Health score is recomputed inline rather than referencing account_health_score
-- to avoid a cross-view dependency in Connect AI's federation SQL.
-- Date windows: cur90 = 2026-04-17 to 2026-07-16, prev90 = 2026-01-17 to 2026-04-16.

SELECT
  a.Name                   AS AccountName,
  a.CustomerPriority__c    AS Priority,
  a.SLA__c                 AS SLA_Level,
  (
    100
    + CASE a.SLA__c WHEN 'Platinum' THEN 10 WHEN 'Gold' THEN 5 WHEN 'Bronze' THEN -5 ELSE 0 END
    - CASE WHEN d.MONTHLY_JOBS_L90 < 41 THEN 25 WHEN d.MONTHLY_JOBS_L90 <= 150 THEN 10 ELSE 0 END
    - CASE
        WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 30 THEN 25
        WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 90 THEN 10
        ELSE 0
      END
    - CASE WHEN COALESCE(i.uot, 0) >= 5 THEN 25 ELSE COALESCE(i.uot, 0) * 5 END
  ) AS Health_Score,
  CASE
    WHEN (100
      + CASE a.SLA__c WHEN 'Platinum' THEN 10 WHEN 'Gold' THEN 5 WHEN 'Bronze' THEN -5 ELSE 0 END
      - CASE WHEN d.MONTHLY_JOBS_L90 < 41 THEN 25 WHEN d.MONTHLY_JOBS_L90 <= 150 THEN 10 ELSE 0 END
      - CASE WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 30 THEN 25
             WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 90 THEN 10 ELSE 0 END
      - CASE WHEN COALESCE(i.uot, 0) >= 5 THEN 25 ELSE COALESCE(i.uot, 0) * 5 END
    ) < 40 THEN 'CRITICAL'
    WHEN (100
      + CASE a.SLA__c WHEN 'Platinum' THEN 10 WHEN 'Gold' THEN 5 WHEN 'Bronze' THEN -5 ELSE 0 END
      - CASE WHEN d.MONTHLY_JOBS_L90 < 41 THEN 25 WHEN d.MONTHLY_JOBS_L90 <= 150 THEN 10 ELSE 0 END
      - CASE WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 30 THEN 25
             WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 90 THEN 10 ELSE 0 END
      - CASE WHEN COALESCE(i.uot, 0) >= 5 THEN 25 ELSE COALESCE(i.uot, 0) * 5 END
    ) < 60 THEN 'AT_RISK'
    WHEN (100
      + CASE a.SLA__c WHEN 'Platinum' THEN 10 WHEN 'Gold' THEN 5 WHEN 'Bronze' THEN -5 ELSE 0 END
      - CASE WHEN d.MONTHLY_JOBS_L90 < 41 THEN 25 WHEN d.MONTHLY_JOBS_L90 <= 150 THEN 10 ELSE 0 END
      - CASE WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 30 THEN 25
             WHEN DATEDIFF(day, '2026-07-16', a.ContractEndDate__c) <= 90 THEN 10 ELSE 0 END
      - CASE WHEN COALESCE(i.uot, 0) >= 5 THEN 25 ELSE COALESCE(i.uot, 0) * 5 END
    ) < 80 THEN 'MONITOR'
    ELSE 'HEALTHY'
  END AS Health_Status,
  s.Sessions_Cur90,
  s.Sessions_Prev90,
  CASE
    WHEN s.Sessions_Cur90 < 0.8 * s.Sessions_Prev90 THEN 'DECLINING'
    WHEN s.Sessions_Cur90 > 1.2 * s.Sessions_Prev90 THEN 'GROWING'
    ELSE 'STABLE'
  END AS Usage_Trend
FROM (
  SELECT
    t.ACCOUNT_SK,
    SUM(CASE WHEN t.EVENT_TIMESTAMP >= '2026-04-17'                                THEN t.SESSION_COUNT ELSE 0 END) AS Sessions_Cur90,
    SUM(CASE WHEN t.EVENT_TIMESTAMP >= '2026-01-17' AND t.EVENT_TIMESTAMP < '2026-04-17' THEN t.SESSION_COUNT ELSE 0 END) AS Sessions_Prev90
  FROM Warehouse_System.WH_DATA.TELEMETRY_EVENTS t
  GROUP BY t.ACCOUNT_SK
) s
JOIN Warehouse_System.WH_DATA.DIM_ACCOUNT d ON s.ACCOUNT_SK = d.ACCOUNT_SK
JOIN CRM_System.Salesforce.Account a ON a.Ext_Account_Id__c = d.SFDC_ACCOUNT_ID
LEFT JOIN (
  SELECT u_company, COUNT(*) AS uot
  FROM ITSM_System.ServiceNow.u_bm_incident
  WHERE u_priority IN ('Critical', 'High') AND u_state = 'Open'
  GROUP BY u_company
) i ON a.Name = i.u_company
