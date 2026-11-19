import argparse
import asyncio
from getpass import getpass

from api.accounts import ACCOUNT_PATTERN
from api.bootstrap import event_loop
from api.settings import WebSettings
from connection.accounts import AccountStore, UsernameTaken
from connection.postgres import open_database
from core.security import hash_password


async def create_operator(database_url: str, username: str, password_hash: str) -> None:
    async with open_database(database_url) as database:
        await AccountStore(database).create_operator(username, password_hash)


def main() -> None:
    parser = argparse.ArgumentParser(description='管理 CloudPivot 运营账号。')
    parser.add_argument('command', choices=['create-operator'])
    parser.add_argument('username')
    arguments = parser.parse_args()
    username = arguments.username.strip().lower()
    if not ACCOUNT_PATTERN.fullmatch(username):
        parser.error('账号为 3–64 个小写字母、数字、下划线、点或短横线，且以字母、数字或下划线开头。')
    settings = WebSettings.from_env()
    if not settings.database_url:
        parser.error('需要配置 CLOUDPIVOT_DATABASE_URL。')
    password = getpass('密码：')
    if not 10 <= len(password) <= 256 or password != getpass('确认密码：'):
        parser.error('密码必须为 10–256 个字符，并与确认密码一致。')
    try:
        with asyncio.Runner(loop_factory=event_loop) as runner:
            runner.run(create_operator(settings.database_url, username, hash_password(password)))
    except UsernameTaken:
        parser.error('这个账号已存在。')
    print('运营账号已创建。')


if __name__ == '__main__':
    main()
