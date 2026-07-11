from datetime import datetime, timezone
from urllib.parse import unquote
import pyodbc


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
        )
        """)
        cursor.execute("""
        IF OBJECT_ID('dbo.positions', 'U') IS NULL
        CREATE TABLE dbo.positions(
          symbol NVARCHAR(32) PRIMARY KEY, qty DECIMAL(20,6) NOT NULL,
          avg_price DECIMAL(20,6) NOT NULL, updated_at DATETIMEOFFSET NOT NULL
        )
        """)
        self.db.commit()

    def position(self, symbol: str) -> tuple[float, float]:
        row = self.db.cursor().execute("SELECT qty, avg_price FROM dbo.positions WHERE symbol=?", symbol).fetchone()
        return (float(row[0]), float(row[1])) if row else (0.0, 0.0)

    def record(self, symbol: str, side: str, qty: float, price: float, probability: float, status="filled"):
        now = datetime.now(timezone.utc)
        cursor = self.db.cursor()
        old_qty, old_avg = self.position(symbol)
        new_qty = old_qty + qty if side == "buy" else max(0, old_qty - qty)
        avg = ((old_qty * old_avg + qty * price) / new_qty) if side == "buy" and new_qty else 0
        try:
            cursor.execute("INSERT INTO dbo.trades(ts,symbol,side,qty,price,probability,status) VALUES(?,?,?,?,?,?,?)", now,symbol,side,qty,price,probability,status)
            cursor.execute("""
              MERGE dbo.positions AS target USING (SELECT ? AS symbol) AS source ON target.symbol=source.symbol
              WHEN MATCHED THEN UPDATE SET qty=?, avg_price=?, updated_at=?
              WHEN NOT MATCHED THEN INSERT(symbol,qty,avg_price,updated_at) VALUES(?,?,?,?);
            """, symbol,new_qty,avg,now,symbol,new_qty,avg,now)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def last_trade(self, symbol: str):
        row = self.db.cursor().execute("SELECT TOP 1 ts FROM dbo.trades WHERE symbol=? ORDER BY id DESC", symbol).fetchone()
        return (row[0].isoformat(),) if row else None
