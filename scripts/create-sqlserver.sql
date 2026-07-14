IF DB_ID(N'TradingBot') IS NULL CREATE DATABASE TradingBot;
GO
USE TradingBot;
GO
IF OBJECT_ID('dbo.trades', 'U') IS NULL
CREATE TABLE dbo.trades(
  id BIGINT IDENTITY(1,1) PRIMARY KEY,
  ts DATETIMEOFFSET NOT NULL,
  symbol NVARCHAR(32) NOT NULL,
  side VARCHAR(8) NOT NULL CHECK(side IN ('buy','sell')),
  qty DECIMAL(20,6) NOT NULL CHECK(qty > 0),
  price DECIMAL(20,6) NOT NULL CHECK(price > 0),
  probability DECIMAL(9,8) NOT NULL,
  status VARCHAR(32) NOT NULL,
  broker_exec_id NVARCHAR(128) NULL,
  commission DECIMAL(20,6) NOT NULL CONSTRAINT DF_trades_commission DEFAULT 0,
  realized_pnl DECIMAL(20,6) NULL,
  gross_profit DECIMAL(20,6) NULL,
  allocated_commission DECIMAL(20,6) NULL,
  exchange_fee DECIMAL(20,6) NULL,
  fx_cost DECIMAL(20,6) NULL,
  estimated_tax DECIMAL(20,6) NULL,
  net_profit DECIMAL(20,6) NULL,
  purpose NVARCHAR(32) NOT NULL CONSTRAINT DF_trades_purpose DEFAULT 'strategy'
);
GO
IF COL_LENGTH('dbo.trades', 'broker_exec_id') IS NULL
  ALTER TABLE dbo.trades ADD broker_exec_id NVARCHAR(128) NULL;
GO
IF COL_LENGTH('dbo.trades', 'commission') IS NULL
  ALTER TABLE dbo.trades ADD commission DECIMAL(20,6) NOT NULL
  CONSTRAINT DF_trades_commission DEFAULT 0 WITH VALUES;
GO
IF COL_LENGTH('dbo.trades', 'realized_pnl') IS NULL
  ALTER TABLE dbo.trades ADD realized_pnl DECIMAL(20,6) NULL;
GO
IF COL_LENGTH('dbo.trades', 'gross_profit') IS NULL
  ALTER TABLE dbo.trades ADD gross_profit DECIMAL(20,6) NULL;
IF COL_LENGTH('dbo.trades', 'allocated_commission') IS NULL
  ALTER TABLE dbo.trades ADD allocated_commission DECIMAL(20,6) NULL;
IF COL_LENGTH('dbo.trades', 'exchange_fee') IS NULL
  ALTER TABLE dbo.trades ADD exchange_fee DECIMAL(20,6) NULL;
IF COL_LENGTH('dbo.trades', 'fx_cost') IS NULL
  ALTER TABLE dbo.trades ADD fx_cost DECIMAL(20,6) NULL;
IF COL_LENGTH('dbo.trades', 'estimated_tax') IS NULL
  ALTER TABLE dbo.trades ADD estimated_tax DECIMAL(20,6) NULL;
IF COL_LENGTH('dbo.trades', 'net_profit') IS NULL
  ALTER TABLE dbo.trades ADD net_profit DECIMAL(20,6) NULL;
IF COL_LENGTH('dbo.trades', 'purpose') IS NULL
  ALTER TABLE dbo.trades ADD purpose NVARCHAR(32) NOT NULL
  CONSTRAINT DF_trades_purpose DEFAULT 'strategy' WITH VALUES;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_trades_broker_exec_id')
  CREATE UNIQUE INDEX UX_trades_broker_exec_id ON dbo.trades(broker_exec_id)
  WHERE broker_exec_id IS NOT NULL;
GO
IF OBJECT_ID('dbo.positions', 'U') IS NULL
CREATE TABLE dbo.positions(
  symbol NVARCHAR(32) PRIMARY KEY,
  qty DECIMAL(20,6) NOT NULL,
  avg_price DECIMAL(20,6) NOT NULL,
  updated_at DATETIMEOFFSET NOT NULL
);
GO
CREATE INDEX IX_trades_symbol_ts ON dbo.trades(symbol, ts DESC);
GO
IF OBJECT_ID('dbo.bot_state', 'U') IS NULL
CREATE TABLE dbo.bot_state(
  state_key NVARCHAR(100) PRIMARY KEY,
  state_value NVARCHAR(1000) NOT NULL,
  updated_at DATETIMEOFFSET NOT NULL
);
GO
IF OBJECT_ID('dbo.notification_outbox', 'U') IS NULL
CREATE TABLE dbo.notification_outbox(
  id BIGINT IDENTITY(1,1) PRIMARY KEY,
  message NVARCHAR(MAX) NOT NULL,
  attempts INT NOT NULL DEFAULT 0,
  last_error NVARCHAR(1000) NULL,
  created_at DATETIMEOFFSET NOT NULL,
  sent_at DATETIMEOFFSET NULL
);
GO
IF OBJECT_ID('dbo.line_webhook_events', 'U') IS NULL
CREATE TABLE dbo.line_webhook_events(
  event_id NVARCHAR(128) PRIMARY KEY,
  received_at DATETIMEOFFSET NOT NULL
);
GO
