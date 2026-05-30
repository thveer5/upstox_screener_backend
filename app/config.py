from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    upstox_api_key: str
    upstox_api_secret: str
    upstox_redirect_uri: str = "http://localhost:8000/auth/callback"

    # Browser session cookie copied from tv.upstox.com — required by the
    # internal /jscreener-api endpoint. Refreshes ~hourly.
    upstox_tv_cookie: str = ""

    host: str = "127.0.0.1"
    port: int = 8000

    # Comma-separated allowed origins for CORS.
    # Dev: "http://localhost:5173"
    # Prod: "https://your-app.vercel.app,https://www.yourdomain.com"
    frontend_origin: str = "http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    upstox_auth_url: str = "https://api.upstox.com/v2/login/authorization/dialog"
    upstox_token_url: str = "https://api.upstox.com/v2/login/authorization/token"
    upstox_api_base: str = "https://api.upstox.com/v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
