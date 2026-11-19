from os import environ
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_ROOT = Path(__file__).resolve().parents[1]
_TRACING_ENV_NAMES = ('LANGSMITH_TRACING_V2', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING', 'LANGCHAIN_TRACING')


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(APP_ROOT / '.env',),
        env_file_encoding='utf-8',
        extra='ignore',
        validate_by_name=True,
        hide_input_in_errors=True,
        frozen=True,
    )

    workspace_root: Path = Field(Path('/agent_files'), validation_alias='AGENT_WORKSPACE_ROOT')
    openai_api_key: SecretStr | None = Field(None, validation_alias='OPENAI_API_KEY')
    base_llm: str | None = Field(None, validation_alias='BASE_LLM')
    small_llm: str | None = Field(None, validation_alias='SMALL_LLM')
    base_vlm: str | None = Field(None, validation_alias='BASE_VLM')
    model_api_base_url: str | None = Field(None, validation_alias='MODEL_API_BASE_URL')
    image_model: str | None = Field(None, validation_alias='IMAGE_MODEL')
    edit_image_model: str | None = Field(None, validation_alias='EDIT_IMAGE_MODEL')
    minio_host: str | None = Field(None, validation_alias='HOST_IP')
    minio_port: str | None = Field(None, validation_alias='MINIO_PORT')
    minio_access_key: SecretStr | None = Field(None, validation_alias='MINIO_ACCESS_KEY')
    minio_secret_key: SecretStr | None = Field(None, validation_alias='MINIO_SECRET_KEY')
    minio_bucket_name: str | None = Field(None, validation_alias='MINIO_BUCKET_NAME')
    minio_console_port: str | None = Field(None, validation_alias='MINIO_CONSOLE_PORT')
    tavily_search_key: SecretStr = Field(SecretStr(''), validation_alias='TAVILY_SEARCH_KEY')
    use_excel: bool = Field(False, validation_alias='USE_EXCEL')
    langsmith_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices('LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY'),
    )
    langsmith_project: str | None = Field(
        None, validation_alias=AliasChoices('LANGSMITH_PROJECT', 'LANGCHAIN_PROJECT'),
    )
    langsmith_tracing: str | None = Field(
        None, validation_alias=AliasChoices(*_TRACING_ENV_NAMES),
    )
    langsmith_endpoint: str | None = Field(
        None, validation_alias=AliasChoices('LANGSMITH_ENDPOINT', 'LANGCHAIN_ENDPOINT'),
    )

    @field_validator('workspace_root', mode='before')
    @classmethod
    def default_workspace_root(cls, value):
        return value or Path('/agent_files')

    @field_validator('use_excel', mode='before')
    @classmethod
    def parse_use_excel(cls, value):
        return value.strip().lower() == 'true' if isinstance(value, str) else value


def require_values(**values: str | SecretStr | None) -> tuple[str, ...]:
    resolved = {
        name: (value.get_secret_value() if isinstance(value, SecretStr) else value or '').strip()
        for name, value in values.items()
    }
    if missing := [name for name, value in resolved.items() if not value]:
        raise RuntimeError('缺少环境变量：' + ', '.join(missing) + '。请检查 .env.example。')
    return tuple(resolved.values())


def configure_langsmith() -> None:
    # The tracing SDK reads configuration from the process environment.
    tracing = settings.langsmith_tracing
    if tracing and not any(environ.get(name) for name in _TRACING_ENV_NAMES):
        environ['LANGSMITH_TRACING_V2'] = tracing
    for name, value in (
        ('API_KEY', settings.langsmith_api_key.get_secret_value() if settings.langsmith_api_key else None),
        ('PROJECT', settings.langsmith_project),
        ('ENDPOINT', settings.langsmith_endpoint),
    ):
        if value and not (environ.get(f'LANGSMITH_{name}') or environ.get(f'LANGCHAIN_{name}')):
            environ[f'LANGSMITH_{name}'] = value


settings = AgentSettings()

# These values are read by the existing Agent and workspace modules.
ROOT_PATH_AGENT = settings.workspace_root
USE_EXCEL = settings.use_excel
TAVILY_SEARCH_KEY = settings.tavily_search_key.get_secret_value().strip()

LOG_DIR = Path(__file__).resolve().parent
NEED_CONSOLE_LOG = True
USER_UPLOAD_DIR = 'user_upload'
CONVERSATION_HISTORY_PATH = 'conversation_history'
SKILL_DIR_PATH = 'skills'
GENERATE_IMAGE_PATH = 'generate_images'
WAIT_RATE_LIMIT_SEC = 90
WAIT_RATE_LIMIT_RETRY = 3
