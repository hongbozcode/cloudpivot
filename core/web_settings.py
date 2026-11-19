from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlsplit, urlunsplit

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


APP_ROOT = Path(__file__).resolve().parents[1]
CommaSeparated = Annotated[tuple[str, ...], NoDecode]


def _database_url(value: str, username: str, database: str, password: SecretStr | None) -> str:
    value = value.strip()
    if not value:
        return ''
    credentials = {'password': password.get_secret_value()} if password else {}
    if value.startswith(('postgresql://', 'postgres://')) or '=' in value:
        return make_conninfo(value, **credentials)

    address = urlsplit(f'//{value}')
    if not (address.hostname and username and database) or (address.path or address.username):
        raise ValueError('PostgreSQL 地址需要配合 CLOUDPIVOT_DATABASE_USER 和 CLOUDPIVOT_DATABASE_NAME。')
    return make_conninfo(
        host=address.hostname, port=address.port or 5432, user=username,
        dbname=database, **credentials,
    )


def _redis_url(value: str, password: SecretStr | None) -> str:
    value = value.strip()
    if not value:
        return ''
    url = urlsplit(value if '://' in value else f'redis://{value}')
    if password and url.password is None:
        username, _, host = url.netloc.rpartition('@')
        url = url._replace(netloc=f'{username}:{quote(password.get_secret_value(), safe="")}@{host}')
    if url.scheme not in ('redis', 'rediss') or url.path not in ('', '/1') or url.query or url.fragment:
        raise ValueError('CLOUDPIVOT_REDIS_URL 必须使用 Redis DB 1，URL 以 /1 结尾。')
    return urlunsplit(url._replace(path='/1'))


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(APP_ROOT / '.env',),
        env_file_encoding='utf-8',
        extra='ignore',
        validate_by_name=True,
        hide_input_in_errors=True,
    )

    database_url: str = Field('', validation_alias='CLOUDPIVOT_DATABASE_URL', repr=False)
    database_user: str = Field('', validation_alias='CLOUDPIVOT_DATABASE_USER')
    database_name: str = Field('', validation_alias='CLOUDPIVOT_DATABASE_NAME')
    database_password: SecretStr | None = Field(None, validation_alias='CLOUDPIVOT_DATABASE_PASSWORD', repr=False)
    redis_url: str = Field('', validation_alias='CLOUDPIVOT_REDIS_URL', repr=False)
    redis_password: SecretStr | None = Field(None, validation_alias='CLOUDPIVOT_REDIS_PASSWORD', repr=False)
    cors_origins: CommaSeparated = Field((), validation_alias='CLOUDPIVOT_CORS_ORIGINS')
    cookie_secure: bool = Field(True, validation_alias='CLOUDPIVOT_COOKIE_SECURE')
    session_seconds: int = Field(7 * 24 * 60 * 60, gt=0)
    uvicorn_port: int = Field(8000, validation_alias='UVICORN_PORT', ge=0, le=65535)
    base_llm: str = Field('', validation_alias='BASE_LLM')
    base_vlm: str = Field('', validation_alias='BASE_VLM')
    platform_llms: CommaSeparated = Field((), validation_alias='CLOUDPIVOT_PLATFORM_LLMS')
    platform_vlms: CommaSeparated = Field((), validation_alias='CLOUDPIVOT_PLATFORM_VLMS')
    legacy_workspace_root: str = Field('', validation_alias='CLOUDPIVOT_LEGACY_WORKSPACE_ROOT')

    @field_validator('cors_origins', 'platform_llms', 'platform_vlms', mode='before')
    @classmethod
    def parse_comma_separated(cls, value):
        if isinstance(value, str):
            return tuple(item for part in value.split(',') if (item := part.strip()))
        return value

    @field_validator('cookie_secure', mode='before')
    @classmethod
    def parse_cookie_secure(cls, value):
        return value.strip().lower() != 'false' if isinstance(value, str) else value

    @model_validator(mode='after')
    def derive_web_configuration(self) -> 'WebSettings':
        self.database_url = _database_url(
            self.database_url, self.database_user.strip(), self.database_name.strip(), self.database_password,
        )
        self.redis_url = _redis_url(self.redis_url, self.redis_password)
        if '*' in self.cors_origins:
            raise ValueError('CLOUDPIVOT_CORS_ORIGINS 必须列出具体的站点源。')
        self.platform_llms = tuple(dict.fromkeys(filter(None, (self.base_llm.strip(), *self.platform_llms))))
        self.platform_vlms = tuple(dict.fromkeys(filter(None, (self.base_vlm.strip(), *self.platform_vlms))))
        self.legacy_workspace_root = self.legacy_workspace_root.strip()
        return self
