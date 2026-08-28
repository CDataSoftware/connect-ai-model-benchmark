# Synthetic dataset (frozen)

The frozen, fully synthetic dataset the benchmark runs against. Generated with a fixed seed
(20260716) so the numbers are reproducible. ~1,200 accounts modeled across three systems with
**divergent join keys**, so the task requires real cross-source reconciliation rather than a
single lookup.

| File | System | Rows | Notes |
|---|---|--:|---|
| `crm_accounts.csv` | CRM | 1,200 | Account master |
| `crm_contacts.csv` | CRM | 2,376 | Contacts per account |
| `warehouse_dim_account.csv` | Cloud warehouse | 1,200 | Account dimension |
| `warehouse_telemetry_events.csv` | Cloud warehouse | 21,003 | Product usage / telemetry |
| `itsm_companies.csv` | ITSM | 1,200 | Company records |
| `itsm_incidents.csv` | ITSM | 6,865 | Support incidents |
| `golden_result.csv` | — | 50 | Frozen expected answer (the scoring target) |

The golden set derives from a health-score formula (`100 + sla_bonus - usage_pen -
renewal_pen - urgent_pen`; bands: <40 CRITICAL, <60 AT_RISK, <80 MONITOR, >=80 HEALTHY) and
includes the not-eligible edge cases used to test correctness on tricky accounts.

All data is synthetic — no real customer information.
