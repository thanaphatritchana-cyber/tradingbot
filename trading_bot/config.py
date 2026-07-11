from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    symbols: str = "AAPL,MSFT"
    interval: str = "1h"
    lookback_days: int = 365
    poll_seconds: int = 300
    min_win_probability: float = 0.70
    min_signal_samples: int = 30
    starting_cash: float = 100_000
    risk_per_trade: float = 0.005
    max_position_pct: float = 0.10
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    cooldown_minutes: int = 60
    kill_switch: bool = False
    broker: str = "local"
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    line_channel_access_token: str = ""
    line_target_id: str = ""
    database_url: str = "sqlserver://localhost;database=TradingBot;integratedSecurity=true;trustServerCertificate=true"
    log_level: str = "INFO"

    @property
    def symbol_list(self) -> list[str]:
        return [x.strip().upper() for x in self.symbols.split(",") if x.strip()]
