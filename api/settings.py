from dataclasses import dataclass, field
from os import getenv
from urllib.parse import quote, urlsplit, urlunsplit

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo


def _database_url() -> str:
    value = getenv('CLOUDPIVOT_DATABASE_URL', '').strip()
    password = getenv('CLOUDPIVOT_DATABASE_PASSWORD')
    if not value:
        return ''
    if value.startswith(('postgresql://', 'postgres://')) or '=' in value:
        return make_conninfo(value, **({'password': password} if password else {}))

    address = urlsplit(f'//{value}')
    username = getenv('CLOUDPIVOT_DATABASE_USER', '').strip()
    database = getenv('CLOUDPIVOT_DATABASE_NAME', '').strip()
    if not (address.hostname and username and database) or (address.path or address.username):
        raise ValueError('PostgreSQL 地址需要配合 CLOUDPIVOT_DATABASE_USER 和 CLOUDPIVOT_DATABASE_NAME。')
    return make_conninfo(
        host=address.hostname, port=address.port or 5432, user=username,
        dbname=database, **({'password': password} if password else {}),
    )


def _redis_url() -> str:
    value = getenv('CLOUDPIVOT_REDIS_URL', '').strip()
    if not value:
        return ''
    url = urlsplit(value if '://' in value else f'redis://{value}')
    password = getenv('CLOUDPIVOT_REDIS_PASSWORD')
    if password and url.password is None:
        username, _, host = url.netloc.rpartition('@')
        url = url._replace(netloc=f'{username}:{quote(password, safe="")}@{host}')
    return urlunsplit(url._replace(path=url.path or '/1'))


def _model_choices(default: str, available: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for item in (getenv(default, ''), *getenv(available, '').split(','))
                               if (value := item.strip())))


@dataclass(frozen=True)
class WebSettings:
    database_url: str = field(default='', repr=False)
    redis_url: str = field(default='', repr=False)
    cors_origins: tuple[str, ...] = ()
    cookie_secure: bool = True
    session_seconds: int = 7 * 24 * 60 * 60
    platform_llms: tuple[str, ...] = ()
    platform_vlms: tuple[str, ...] = ()
    legacy_workspace_root: str = ''

    def __post_init__(self) -> None:
        if self.redis_url:
            url = urlsplit(self.redis_url)
            if url.scheme not in ('redis', 'rediss') or url.path != '/1' or url.query or url.fragment:
                raise ValueError('CLOUDPIVOT_REDIS_URL 必须使用 Redis DB 1，URL 以 /1 结尾。')
        if '*' in self.cors_origins:
            raise ValueError('CLOUDPIVOT_CORS_ORIGINS 必须列出具体的站点源。')
        if self.session_seconds <= 0:
            raise ValueError('登录有效期必须为正数。')

    @classmethod
    def from_env(cls) -> 'WebSettings':
        load_dotenv()
        return cls(
            database_url=_database_url(),
            redis_url=_redis_url(),
            cors_origins=tuple(
                origin for value in getenv('CLOUDPIVOT_CORS_ORIGINS', '').split(',')
                if (origin := value.strip())
            ),
            cookie_secure=getenv('CLOUDPIVOT_COOKIE_SECURE', 'true').strip().lower() != 'false',
            platform_llms=_model_choices('BASE_LLM', 'CLOUDPIVOT_PLATFORM_LLMS'),
            platform_vlms=_model_choices('BASE_VLM', 'CLOUDPIVOT_PLATFORM_VLMS'),
            legacy_workspace_root=getenv('CLOUDPIVOT_LEGACY_WORKSPACE_ROOT', '').strip(),
        )
