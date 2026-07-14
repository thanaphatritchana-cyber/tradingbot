from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    symbols: str = "AAPL,MSFT"
    interval: str = "1h"
    lookback_days: int = 365
    poll_seconds: int = 300
    min_win_probability: float = Field(default=0.70, ge=0.70, le=1)
    min_signal_samples: int = 30
    signal_horizon_bars: int = Field(default=20, ge=1, le=500)
    estimated_round_trip_commission: float = Field(default=2.01, ge=0)
    what_if_commission_buffer_pct: float = Field(default=0.10, ge=0, le=1)
    estimated_exchange_fee_rate: float = Field(default=0, ge=0, le=0.10)
    estimated_fx_cost_rate: float = Field(default=0, ge=0, le=0.10)
    tax_rate: float = Field(default=0, ge=0, le=0.20)
    min_net_profit_cost_multiple: float = Field(default=3, ge=0, le=100)
    min_volume_ratio: float = Field(default=1.0, gt=0, le=10)
    min_atr_pct: float = Field(default=0.005, gt=0, le=0.20)
    max_atr_pct: float = Field(default=0.04, gt=0, le=0.50)
    paper_test_symbol: str = "T"
    starting_cash: float = 200
    max_order_notional: float = Field(default=50, gt=0)
    max_total_exposure: float = Field(default=150, gt=0)
    max_daily_loss: float = Field(default=20, gt=0)
    max_daily_loss_pct: float = Field(default=0.02, gt=0, le=0.10)
    max_orders_per_day: int = Field(default=10, ge=1, le=100)
    max_concurrent_positions: int = Field(default=3, ge=1, le=100)
    max_consecutive_losses: int = Field(default=3, ge=1, le=20)
    max_consecutive_cycle_errors: int = Field(default=3, ge=1, le=20)
    min_paper_closed_trades_for_live: int = Field(default=30, ge=10, le=1000)
    min_paper_win_rate_for_live: float = Field(default=0.50, ge=0, le=1)
    min_paper_profit_factor_for_live: float = Field(default=1.20, ge=1, le=10)
    min_paper_trading_days_for_live: int = Field(default=20, ge=5, le=365)
    max_paper_drawdown_pct_for_live: float = Field(default=0.10, gt=0, le=0.50)
    stop_loss_pct: float = 0.02
    trailing_stop_pct: float = Field(default=0.015, gt=0, le=0.10)
    take_profit_pct: float = Field(default=0.10, ge=0.03, le=0.10)
    take_profit_max_pct: float = Field(default=0.10, ge=0.03, le=0.10)
    take_profit_atr_multiplier: float = Field(default=3.0, gt=0, le=10)
    cooldown_minutes: int = 60
    kill_switch: bool = True
    broker: str = "local"
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = Field(default=7497, ge=1, le=65535)
    ibkr_client_id: int = Field(default=7, ge=0)
    ibkr_account: str = ""
    ibkr_exchange: str = "SMART"
    ibkr_currency: str = "USD"
    ibkr_primary_exchange: str = ""
    ibkr_paper: bool = True
    ibkr_read_only: bool = True
    ibkr_order_timeout_seconds: int = Field(default=30, gt=0)
    ibkr_market_data_type: int = Field(default=3, ge=1, le=4)
    max_market_data_age_seconds: int = Field(default=120, ge=5, le=3600)
    watchdog_stale_seconds: int = Field(default=900, ge=60, le=86400)
    live_preflight_valid_hours: int = Field(default=24, ge=1, le=168)
    ibkr_native_bracket: bool = True
    ibkr_entry_limit_offset_pct: float = Field(default=0.002, ge=0, le=0.01)
    live_trading_confirm: str = ""
    live_tax_confirm: str = ""
    live_cost_model_confirm: str = ""
    allowed_os_user: str = ""
    line_channel_access_token: str = ""
    line_target_id: str = ""
    line_channel_secret: str = ""
    line_control_user_id: str = ""
    line_controller_host: str = "127.0.0.1"
    line_controller_port: int = Field(default=8080, ge=1, le=65535)
    ngrok_authtoken: str = ""
    ngrok_path: str = "ngrok"
    ngrok_upstream: str = "http://127.0.0.1:8080"
    daily_report_time: str = "23:59"
    daily_report_timezone: str = "Asia/Bangkok"
    daily_report_currency: str = "USD"
    daily_profit_target: float = 0
    database_url: str = "sqlserver://localhost;database=TradingBot;integratedSecurity=true;trustServerCertificate=true"
    log_level: str = "INFO"

    @property
    def symbol_list(self) -> list[str]:
        return [x.strip().upper() for x in self.symbols.split(",") if x.strip()]

    @property
    def normalized_paper_test_symbol(self) -> str:
        return self.paper_test_symbol.strip().upper()

    @field_validator("tax_rate")
    @classmethod
    def validate_tax_rate_menu(cls, value: float) -> float:
        allowed = (0.0, 0.10, 0.15, 0.20)
        if not any(abs(value - option) < 1e-9 for option in allowed):
            raise ValueError("TAX_RATE must be one of: 0, 0.10, 0.15, 0.20")
        return value
