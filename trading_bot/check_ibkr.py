"""Read-only connectivity check for TWS/IB Gateway."""

from .config import Settings


def run() -> None:
    from ib_async import IB

    cfg = Settings()
    ib = IB()
    try:
        ib.connect(
            cfg.ibkr_host,
            cfg.ibkr_port,
            clientId=cfg.ibkr_client_id,
            account=cfg.ibkr_account,
            readonly=True,
            timeout=10,
        )
        accounts = ib.managedAccounts()
        if cfg.ibkr_account and cfg.ibkr_account not in accounts:
            raise RuntimeError(f"Configured account {cfg.ibkr_account!r} is not available")
        account = cfg.ibkr_account or (accounts[0] if len(accounts) == 1 else "")
        if not account:
            raise RuntimeError(f"Set IBKR_ACCOUNT; available accounts: {', '.join(accounts) or 'none'}")

        print(f"Connected to IBKR at {cfg.ibkr_host}:{cfg.ibkr_port}")
        print(f"Account: {account}")
        positions = ib.positions(account)
        if not positions:
            print("Positions: none")
        for item in positions:
            print(
                f"Position: {item.contract.symbol} "
                f"qty={float(item.position):g} avg_cost={float(item.avgCost):.4f}"
            )
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    run()
