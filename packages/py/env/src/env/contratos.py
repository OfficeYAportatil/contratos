from typing import Annotated

from pydantic_settings import SettingsConfigDict

from env._base import BaseEnv
from env._mistral_secrets import WorkspaceSecret


class Env(BaseEnv):
    model_config = SettingsConfigDict(env_prefix="CONTRATOS_")

    spreadsheet_id: str | None = None
    carpeta_contratos_id: str | None = None
    gerencia_email: str | None = None
    app_url: str | None = None
    review_passcode: Annotated[str | None, WorkspaceSecret()] = None


env = Env()
