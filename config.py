"""
Configuration module for Stock Scanner
Uses Pydantic for type-safe configuration management
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class ScannerConfig(BaseSettings):
    """Type-safe configuration using Pydantic"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== Capital & Risk ====================
    CAPITAL: int = Field(
        default=1_000_000,
        description="Total trading capital (₹)"
    )

    RISK_PCT: float = Field(
        default=0.01,
        description="Max % of capital to risk per trade",
        ge=0.001,
        le=0.05
    )

    MIN_RR: float = Field(
        default=2.5,
        description="Minimum risk/reward ratio",
        ge=1.5
    )

    PORTFOLIO_HEAT_LIMIT: float = Field(
        default=0.05,
        description="Max total portfolio risk (5%)",
        ge=0.01,
        le=0.20
    )

    MAX_CONCURRENT_POSITIONS: int = Field(
        default=10,
        description="Max open positions",
        ge=1,
        le=20
    )

    # ==================== Fundamental Filters ====================
    MIN_MARKET_CAP_CR: int = Field(
        default=500,
        description="Min market cap (₹ Crores)",
        ge=100
    )

    MIN_REVENUE_GROWTH: float = Field(
        default=0.0,
        description="Min YoY revenue growth %"
    )

    MAX_DEBT_TO_EQUITY: float = Field(
        default=1.0,
        description="Max debt-to-equity ratio",
        ge=0.0
    )

    MIN_PROMOTER_HOLDING: float = Field(
        default=0.40,
        description="Min promoter holding %",
        ge=0.0,
        le=1.0
    )

    # ==================== Technical Parameters ====================
    VOLUME_LOOKBACK_DAYS: int = Field(
        default=100,
        description="Days of OHLCV for volume profile",
        ge=50,
        le=200
    )

    CONSOLIDATION_DAYS: int = Field(
        default=20,
        description="Lookback for consolidation check",
        ge=10,
        le=50
    )

    CONSOLIDATION_RANGE_PCT: float = Field(
        default=0.08,
        description="Max % range for consolidation (8%)",
        ge=0.03,
        le=0.15
    )

    VOLUME_SPIKE_MULTIPLIER: float = Field(
        default=1.5,
        description="Min volume vs 20-day avg for confirmation",
        ge=1.0
    )

    UPTREND_MIN_GAIN_PCT: float = Field(
        default=0.20,
        description="Min prior uptrend gain % (20%)",
        ge=0.10
    )

    UPTREND_LOOKBACK_DAYS: int = Field(
        default=60,
        description="Lookback period for uptrend check",
        ge=30,
        le=120
    )

    # ==================== Volume Profile ====================
    HVN_THRESHOLD: float = Field(
        default=1.5,
        description="Volume multiple for HVN (High Volume Node)",
        ge=1.2
    )

    LVN_THRESHOLD: float = Field(
        default=0.5,
        description="Volume fraction for LVN (Low Volume Node)",
        le=0.7
    )

    VOLUME_PROFILE_BINS: int = Field(
        default=20,
        description="Number of bins for volume profile",
        ge=10,
        le=50
    )

    # ==================== Market Regime ====================
    NIFTY_SMA_PERIOD: int = Field(
        default=200,
        description="Nifty 50 SMA period for trend filter",
        ge=50
    )

    BULL_MARKET_MIN_RR: float = Field(
        default=2.5,
        description="Min R:R in bull market",
        ge=2.0
    )

    BEAR_MARKET_MIN_RR: float = Field(
        default=3.5,
        description="Min R:R in bear market",
        ge=2.5
    )

    # ==================== Sector Diversification ====================
    SECTOR_CORRELATION_LIMIT: int = Field(
        default=2,
        description="Max stocks from same sector in final shortlist",
        ge=1,
        le=5
    )

    # ==================== Agent Configuration ====================
    SCANNER_AGENT_MODEL: str = Field(
        default="claude-haiku-4-5",
        description="Model for Scanner Agent (fast, rule-based)"
    )

    RESEARCH_AGENT_MODEL: str = Field(
        default="claude-sonnet-4-5",
        description="Model for Research Agent (reasoning)"
    )

    RISK_AGENT_MODEL: str = Field(
        default="claude-sonnet-4-5",
        description="Model for Risk Agent (critical analysis)"
    )

    EXECUTION_AGENT_MODEL: str = Field(
        default="claude-haiku-4-5",
        description="Model for Execution Agent (simple monitoring)"
    )

    LEARNING_AGENT_MODEL: str = Field(
        default="claude-opus-4-8",
        description="Model for Learning Agent (deep analysis)"
    )

    # ==================== API Keys ====================
    ANTHROPIC_API_KEY: Optional[str] = Field(
        default=None,
        description="Anthropic API key for Claude models; required only for agent workflows"
    )

    TELEGRAM_BOT_TOKEN: Optional[str] = Field(
        default=None,
        description="Telegram bot token for alerts"
    )

    TELEGRAM_CHAT_ID: Optional[str] = Field(
        default=None,
        description="Telegram chat ID for alerts"
    )

    # ==================== Database ====================
    DATABASE_URL: str = Field(
        default="sqlite:///./data/stonx.db",
        description="Database connection URL"
    )

    # ==================== Data Sources ====================
    DATA_CACHE_DIR: str = Field(
        default="./data/cache",
        description="Directory for cached OHLCV data"
    )

    FUNDAMENTALS_DIR: str = Field(
        default="./data/fundamentals",
        description="Directory for fundamental data CSVs"
    )

    NSE_UNIVERSE_INDEX: Optional[str] = Field(
        default=None,
        description=(
            "If set (e.g. 'nifty50', 'nifty200', 'nifty500'), fetch a live NSE "
            "index constituent list instead of the static universe CSV. Leave "
            "unset to use data/universe/nse_universe.csv (offline-friendly)."
        )
    )

    NSE_UNIVERSE_CACHE_TTL_HOURS: float = Field(
        default=24.0,
        description="Hours to cache a fetched live NSE universe list before refreshing",
        ge=1.0
    )

    # ==================== Logging ====================
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )

    LOG_DIR: str = Field(
        default="./logs",
        description="Directory for log files"
    )

    # ==================== Scheduling ====================
    MARKET_CLOSE_TIME: str = Field(
        default="15:30",
        description="Market close time IST (HH:MM format)"
    )

    SCANNER_RUN_TIME: str = Field(
        default="16:00",
        description="Time to run scanner after market close (HH:MM)"
    )


# Singleton instance
_config: Optional[ScannerConfig] = None


def get_config() -> ScannerConfig:
    """Get singleton config instance"""
    global _config
    if _config is None:
        _config = ScannerConfig()
    return _config


def reload_config() -> ScannerConfig:
    """Reload configuration (useful for testing)"""
    global _config
    _config = ScannerConfig()
    return _config


if __name__ == "__main__":
    # Test configuration loading
    config = get_config()
    print("Configuration loaded successfully!")
    print(f"Capital: ₹{config.CAPITAL:,}")
    print(f"Risk per trade: {config.RISK_PCT * 100}%")
    print(f"Min R:R ratio: {config.MIN_RR}x")
    print(f"Scanner Agent Model: {config.SCANNER_AGENT_MODEL}")
    print(f"Database: {config.DATABASE_URL}")
