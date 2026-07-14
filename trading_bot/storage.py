from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import unquote
import pyodbc


@dataclass(frozen=True)
class TradingSummary:
    daily_profit: float
    daily_trades: int
    daily_wins: int
    daily_losses: int
    total_profit: float
    total_trades: int
    total_wins: int
    total_losses: int
    daily_fees: float = 0.0
    total_fees: float = 0.0
    daily_buys: int = 0
    daily_consecutive_losses: int = 0


def summarize_trades(rows, report_date: date, timezone_name: str) -> TradingSummary:
    """Calculate realized P/L with a weighted-average cost basis."""
    report_tz = ZoneInfo(timezone_name)
    positions: dict[str, tuple[float, float]] = {}
    daily_profit = total_profit = 0.0
    daily_fees = total_fees = 0.0
    daily_trades = daily_wins = daily_losses = 0
    daily_buys = daily_consecutive_losses = 0
    total_trades = total_wins = total_losses = 0

    for row in rows:
        ts, symbol, side, qty, price = row[:5]
        commission = float(row[5] or 0) if len(row) > 5 else 0.0
        qty, price = float(qty), float(price)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        trade_date = ts.astimezone(report_tz).date()
        if trade_date > report_date:
            continue
        total_fees += commission
        if trade_date == report_date:
            daily_fees += commission
        position_qty, average_price = positions.get(str(symbol), (0.0, 0.0))
        if side.lower() == "buy":
            if trade_date == report_date:
                daily_buys += 1
            new_qty = position_qty + qty
            average_price = (
                (position_qty * average_price) + (qty * price) + commission
            ) / new_qty
            positions[str(symbol)] = (new_qty, average_price)
            continue
        if side.lower() != "sell" or position_qty <= 0:
            continue

        matched_qty = min(qty, position_qty)
        sell_commission = commission * (matched_qty / qty) if qty else 0.0
        profit = ((price - average_price) * matched_qty) - sell_commission
        total_profit += profit
        total_trades += 1
        total_wins += profit > 0
        total_losses += profit < 0
        if trade_date == report_date:
            daily_profit += profit
            daily_trades += 1
            daily_wins += profit > 0
            daily_losses += profit < 0
            daily_consecutive_losses = daily_consecutive_losses + 1 if profit < 0 else 0
        position_qty -= matched_qty
        if position_qty <= 1e-9:
            position_qty = average_price = 0.0
        positions[str(symbol)] = (position_qty, average_price)

    return TradingSummary(
        daily_profit, daily_trades, daily_wins, daily_losses,
        total_profit, total_trades, total_wins, total_losses,
        daily_fees, total_fees, daily_buys, daily_consecutive_losses,
    )


def _odbc_connection_string(url: str) -> str:
    """Convert the Prisma-style SQL Server URL used by Invoice to pyodbc."""
    if url.lower().startswith("driver={"):
        return url
    if not url.lower().startswith("sqlserver://"):
        raise ValueError("DATABASE_URL must start with sqlserver:// or DRIVER={")
    parts = unquote(url[len("sqlserver://"):]).split(";")
    server, options = parts[0], {}
    for item in parts[1:]:
        if "=" in item:
            key, value = item.split("=", 1)
            options[key.strip().lower()] = value.strip()
    values = ["DRIVER={ODBC Driver 18 for SQL Server}", f"SERVER={server}"]
    if db := options.get("database"):
        values.append(f"DATABASE={db}")
    integrated = options.get("integratedsecurity", "false").lower() == "true"
    if integrated:
        values.append("Trusted_Connection=yes")
    else:
        values.extend([f"UID={options.get('user', '')}", f"PWD={options.get('password', '')}"])
    trust = "yes" if options.get("trustservercertificate", "false").lower() == "true" else "no"
    values.extend(["Encrypt=yes", f"TrustServerCertificate={trust}"])
    return ";".join(values)


class Store:
    def __init__(self, database_url: str):
        self.db = pyodbc.connect(_odbc_connection_string(database_url), autocommit=False)
        cursor = self.db.cursor()
        cursor.execute("""
        IF OBJECT_ID('dbo.trades', 'U') IS NULL
        CREATE TABLE dbo.trades(
          id BIGINT IDENTITY(1,1) PRIMARY KEY, ts DATETIMEOFFSET NOT NULL,
          symbol NVARCHAR(32) NOT NULL, side VARCHAR(8) NOT NULL,
          qty DECIMAL(20,6) NOT NULL, price DECIMAL(20,6) NOT NULL,
          probability DECIMAL(9,8) NOT NULL, status VARCHAR(32) NOT NULL
          ,broker_exec_id NVARCHAR(128) NULL,
          commission DECIMAL(20,6) NOT NULL DEFAULT 0,
          realized_pnl DECIMAL(20,6) NULL
        )
        """)
        cursor.execute("""
        IF COL_LENGTH('dbo.trades', 'broker_exec_id') IS NULL
          ALTER TABLE dbo.trades ADD broker_exec_id NVARCHAR(128) NULL
        """)
        cursor.execute("""
        IF COL_LENGTH('dbo.trades', 'commission') IS NULL
          ALTER TABLE dbo.trades ADD commission DECIMAL(20,6) NOT NULL
          CONSTRAINT DF_trades_commission DEFAULT 0 WITH VALUES
        """)
        cursor.execute("""
        IF COL_LENGTH('dbo.trades', 'realized_pnl') IS NULL
          ALTER TABLE dbo.trades ADD realized_pnl DECIMAL(20,6) NULL
        """)
        cursor.execute("""
        IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_trades_broker_exec_id')
          CREATE UNIQUE INDEX UX_trades_broker_exec_id ON dbo.trades(broker_exec_id)
          WHERE broker_exec_id IS NOT NULL
        """)
        cursor.execute("""
        IF OBJECT_ID('dbo.positions', 'U') IS NULL
        CREATE TABLE dbo.positions(
          symbol NVARCHAR(32) PRIMARY KEY, qty DECIMAL(20,6) NOT NULL,
          avg_price DECIMAL(20,6) NOT NULL, updated_at DATETIMEOFFSET NOT NULL
        )
        """)
        cursor.execute("""
        IF OBJECT_ID('dbo.bot_state', 'U') IS NULL
        CREATE TABLE dbo.bot_state(
          state_key NVARCHAR(100) PRIMARY KEY, state_value NVARCHAR(1000) NOT NULL,
          updated_at DATETIMEOFFSET NOT NULL
        )
        """)
        cursor.execute("""
        IF OBJECT_ID('dbo.notification_outbox', 'U') IS NULL
        CREATE TABLE dbo.notification_outbox(
          id BIGINT IDENTITY(1,1) PRIMARY KEY,
          message NVARCHAR(MAX) NOT NULL,
          attempts INT NOT NULL DEFAULT 0,
          last_error NVARCHAR(1000) NULL,
          created_at DATETIMEOFFSET NOT NULL,
          sent_at DATETIMEOFFSET NULL
        )
        """)
        cursor.execute("""
        IF OBJECT_ID('dbo.line_webhook_events', 'U') IS NULL
        CREATE TABLE dbo.line_webhook_events(
          event_id NVARCHAR(128) PRIMARY KEY,
          received_at DATETIMEOFFSET NOT NULL
        )
        """)
        self.db.commit()

    def position(self, symbol: str) -> tuple[float, float]:
        row = self.db.cursor().execute("SELECT qty, avg_price FROM dbo.positions WHERE symbol=?", symbol).fetchone()
        return (float(row[0]), float(row[1])) if row else (0.0, 0.0)

    def record(
        self, symbol: str, side: str, qty: float, price: float, probability: float,
        status="filled", broker_exec_id: str | None = None,
        commission: float = 0, realized_pnl: float | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc)
        cursor = self.db.cursor()
        if broker_exec_id and cursor.execute(
            "SELECT 1 FROM dbo.trades WHERE broker_exec_id=?", broker_exec_id
        ).fetchone():
            return False
        old_qty, old_avg = self.position(symbol)
        new_qty = old_qty + qty if side == "buy" else max(0, old_qty - qty)
        if side == "buy" and new_qty:
            avg = (old_qty * old_avg + qty * price) / new_qty
        else:
            avg = old_avg if new_qty else 0
        try:
            cursor.execute(
                "INSERT INTO dbo.trades(ts,symbol,side,qty,price,probability,status,broker_exec_id,commission,realized_pnl) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                now, symbol, side, qty, price, probability, status, broker_exec_id,
                commission, realized_pnl,
            )
            cursor.execute("""
              MERGE dbo.positions AS target USING (SELECT ? AS symbol) AS source ON target.symbol=source.symbol
              WHEN MATCHED THEN UPDATE SET qty=?, avg_price=?, updated_at=?
              WHEN NOT MATCHED THEN INSERT(symbol,qty,avg_price,updated_at) VALUES(?,?,?,?);
            """, symbol,new_qty,avg,now,symbol,new_qty,avg,now)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def update_execution_costs(
        self, broker_exec_id: str, commission: float | None = None,
        realized_pnl: float | None = None,
    ) -> None:
        if not broker_exec_id:
            return
        cursor = self.db.cursor()
        try:
            cursor.execute(
                "UPDATE dbo.trades SET commission=COALESCE(?, commission), "
                "realized_pnl=COALESCE(?, realized_pnl) WHERE broker_exec_id=?",
                commission, realized_pnl, broker_exec_id,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def last_trade(self, symbol: str):
        row = self.db.cursor().execute(
            "SELECT TOP 1 CONVERT(datetime2, SWITCHOFFSET(ts, '+00:00')) "
            "FROM dbo.trades WHERE symbol=? ORDER BY id DESC",
            symbol,
        ).fetchone()
        return (row[0].replace(tzinfo=timezone.utc).isoformat(),) if row else None

    def trading_summary(self, report_date: date, timezone_name: str) -> TradingSummary:
        rows = self.db.cursor().execute(
            "SELECT CONVERT(datetime2, SWITCHOFFSET(ts, '+00:00')), "
            "symbol, side, qty, price, commission FROM dbo.trades ORDER BY id"
        ).fetchall()
        return summarize_trades(rows, report_date, timezone_name)

    def get_state(self, key: str) -> str | None:
        row = self.db.cursor().execute(
            "SELECT state_value FROM dbo.bot_state WHERE state_key=?", key
        ).fetchone()
        return str(row[0]) if row else None

    def set_state(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc)
        cursor = self.db.cursor()
        try:
            cursor.execute("""
              MERGE dbo.bot_state AS target
              USING (SELECT ? AS state_key) AS source ON target.state_key=source.state_key
              WHEN MATCHED THEN UPDATE SET state_value=?, updated_at=?
              WHEN NOT MATCHED THEN INSERT(state_key,state_value,updated_at) VALUES(?,?,?);
            """, key, value, now, key, value, now)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def claim_webhook_event(self, event_id: str) -> bool:
        if not event_id:
            return True
        cursor = self.db.cursor()
        try:
            if cursor.execute(
                "SELECT 1 FROM dbo.line_webhook_events WHERE event_id=?", event_id
            ).fetchone():
                return False
            cursor.execute(
                "INSERT INTO dbo.line_webhook_events(event_id,received_at) VALUES(?,?)",
                event_id, datetime.now(timezone.utc),
            )
            cursor.execute(
                "DELETE FROM dbo.line_webhook_events "
                "WHERE received_at < DATEADD(day,-7,SYSDATETIMEOFFSET())"
            )
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def enqueue_notification(self, message: str) -> int:
        cursor = self.db.cursor()
        try:
            cursor.execute(
                "INSERT INTO dbo.notification_outbox(message,created_at) OUTPUT INSERTED.id VALUES(?,?)",
                message,
                datetime.now(timezone.utc),
            )
            notification_id = int(cursor.fetchone()[0])
            self.db.commit()
            return notification_id
        except Exception:
            self.db.rollback()
            raise

    def pending_notifications(self, limit: int = 20):
        limit = max(1, min(int(limit), 100))
        return self.db.cursor().execute(
            f"SELECT TOP {limit} id, message FROM dbo.notification_outbox "
            "WHERE sent_at IS NULL ORDER BY id"
        ).fetchall()

    def mark_notification_sent(self, notification_id: int) -> None:
        cursor = self.db.cursor()
        try:
            cursor.execute(
                "UPDATE dbo.notification_outbox SET sent_at=?, last_error=NULL WHERE id=?",
                datetime.now(timezone.utc), notification_id,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def mark_notification_failed(self, notification_id: int, error: str) -> None:
        cursor = self.db.cursor()
        try:
            cursor.execute(
                "UPDATE dbo.notification_outbox "
                "SET attempts=attempts+1, last_error=? WHERE id=?",
                error[:1000], notification_id,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
