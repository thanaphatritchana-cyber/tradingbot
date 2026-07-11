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
  status VARCHAR(32) NOT NULL
);
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
