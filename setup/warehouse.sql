-- Cloud data warehouse objects required to run the benchmark.
-- Creates a dedicated database and schema, then the three tables the
-- Warehouse_System connection exposes.
--
-- DIM_ACCOUNT and TELEMETRY_EVENTS are loaded from the synthetic-data/ CSVs.
-- REVIEW_QUEUE is the A1 write target, populated at run time by the
-- queue_account_review tool (guarded or unguarded).
--
-- Schema note: the schema is WH_DATA, matching the fully-qualified paths used
-- everywhere else in the benchmark (derived_views.sql, core/verifier.py,
-- .env.example all reference Warehouse_System.WH_DATA.*).
-- Point the Warehouse_System connection's Database at BENCHMARK_WH and its Schema
-- at WH_DATA. If you rename either, update those references to match.

-- ── Benchmark warehouse: database + schema ──────────────────────────────
CREATE DATABASE IF NOT EXISTS BENCHMARK_WH;
CREATE SCHEMA   IF NOT EXISTS BENCHMARK_WH.WH_DATA;

USE DATABASE BENCHMARK_WH;
USE SCHEMA   BENCHMARK_WH.WH_DATA;

-- ── DIM_ACCOUNT ─────────────────────────────────────────────────────────
-- Loaded from synthetic-data/warehouse_dim_account.csv (explicit ACCOUNT_SK).
CREATE TABLE IF NOT EXISTS DIM_ACCOUNT (
    ACCOUNT_SK       NUMBER        NOT NULL PRIMARY KEY,
    SFDC_ACCOUNT_ID  VARCHAR(18),
    ACCOUNT_NAME     VARCHAR(255),
    ACTIVE_SEATS     NUMBER,
    PLAN_CODE        VARCHAR(50),
    MONTHLY_JOBS_L90 NUMBER,
    REGION_CODE      VARCHAR(50)
);

-- ── TELEMETRY_EVENTS ────────────────────────────────────────────────────
-- Loaded from synthetic-data/warehouse_telemetry_events.csv (explicit EVENT_SK).
CREATE TABLE IF NOT EXISTS TELEMETRY_EVENTS (
    EVENT_SK        NUMBER        NOT NULL PRIMARY KEY,
    ACCOUNT_SK      NUMBER        NOT NULL,
    EVENT_TYPE      VARCHAR(100),
    SESSION_COUNT   NUMBER,
    FEATURE_FLAG    VARCHAR(50),
    EVENT_TIMESTAMP TIMESTAMP_NTZ,
    INGESTED_AT     TIMESTAMP_NTZ,
    APP_VERSION     VARCHAR(50),
    REGION_CODE     VARCHAR(50)
);

-- ── REVIEW_QUEUE (A1 write target) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS REVIEW_QUEUE (
    ID           NUMBER        NOT NULL AUTOINCREMENT PRIMARY KEY,
    ACCOUNT_NAME VARCHAR(255)  NOT NULL,
    REASON       VARCHAR(2000) NOT NULL,
    QUEUED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
