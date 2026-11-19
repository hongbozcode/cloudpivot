from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from connection.schema import account_model_defaults, accounts, personal_models, session_model_overrides


ModelKind = Literal['llm', 'vlm']
ModelSource = Literal['session', 'account', 'platform']


@dataclass(frozen=True)
class PersonalModel:
    base_url: str
    model_id: str
    api_key: str = field(repr=False)
    input_budget: int | None = None
    max_output_tokens: int | None = None
    supports_temperature: bool = False
    temperature: float | None = None


@dataclass(frozen=True)
class RunModels:
    llm: str | PersonalModel | None
    vlm: str | PersonalModel | None

    @property
    def uses_platform_model(self) -> bool:
        return not all(isinstance(model, PersonalModel) for model in (self.llm, self.vlm))


@dataclass(frozen=True)
class ModelChoices:
    llm: UUID | None = None
    vlm: UUID | None = None


@dataclass(frozen=True)
class EffectiveModels:
    defaults: ModelChoices
    overrides: ModelChoices
    platform: RunModels
    profiles: dict[UUID, PersonalModel]

    def select(self, kind: ModelKind) -> tuple[ModelSource, str | PersonalModel | None]:
        override = self.profiles.get(getattr(self.overrides, kind))
        if override is not None:
            return 'session', override
        default = self.profiles.get(getattr(self.defaults, kind))
        if default is not None:
            return 'account', default
        return 'platform', getattr(self.platform, kind)

    @property
    def selected(self) -> RunModels:
        return RunModels(self.select('llm')[1], self.select('vlm')[1])


def owned_profiles(account_id: UUID):
    return select(personal_models).where(personal_models.c.account_id == account_id)


async def lock_account(conn: AsyncConnection, account_id: UUID) -> bool:
    return await conn.scalar(select(accounts.c.id).where(accounts.c.id == account_id).with_for_update()) is not None


async def model_choices(conn: AsyncConnection, account_id: UUID, session_id: UUID | None = None) -> ModelChoices:
    table = account_model_defaults if session_id is None else session_model_overrides
    key = table.c.account_id if session_id is None else table.c.session_id
    row = (await conn.execute(select(table.c.llm, table.c.vlm).where(key == (session_id or account_id)))).one_or_none()
    return ModelChoices(*row) if row is not None else ModelChoices()


async def save_choices(conn: AsyncConnection, account_id: UUID, choices: ModelChoices, session_id: UUID | None) -> bool:
    ids = [model_id for model_id in (choices.llm, choices.vlm) if model_id]
    if ids:
        rows = await conn.execute(owned_profiles(account_id).with_only_columns(
            personal_models.c.kind, personal_models.c.id,
        ).where(personal_models.c.id.in_(ids)))
        if ModelChoices(**dict(rows.tuples().all())) != choices:
            return False
    table = account_model_defaults if session_id is None else session_model_overrides
    key = table.c.account_id if session_id is None else table.c.session_id
    values = {'llm': choices.llm, 'vlm': choices.vlm}
    await conn.execute(insert(table).values({key: session_id or account_id, **values}).on_conflict_do_update(
        index_elements=[key], set_=values,
    ))
    return True


async def resolve_models(conn: AsyncConnection, account_id: UUID, session_id: UUID, platform: RunModels) -> EffectiveModels:
    defaults = await model_choices(conn, account_id)
    overrides = await model_choices(conn, account_id, session_id)
    ids = {defaults.llm, defaults.vlm, overrides.llm, overrides.vlm} - {None}
    profiles = {}
    if ids:
        rows = await conn.execute(owned_profiles(account_id).where(personal_models.c.id.in_(ids)))
        profiles = {
            row.id: PersonalModel(api_key=row.api_key, **row.configuration)
            for row in rows
        }
    return EffectiveModels(defaults, overrides, platform, profiles)


async def save_profile(
    conn: AsyncConnection, account_id: UUID, configuration: dict, api_key: str | None,
    *, kind: ModelKind | None = None, model_id: UUID | None = None,
):
    if model_id is None:
        statement = insert(personal_models).values(
            id=uuid4(), account_id=account_id, kind=kind, configuration=configuration, api_key=api_key,
        )
    else:
        values = {'configuration': configuration}
        if api_key is not None:
            values['api_key'] = api_key
        statement = update(personal_models).where(
            personal_models.c.id == model_id, personal_models.c.account_id == account_id,
        ).values(**values)
    return (await conn.execute(statement.returning(personal_models))).mappings().one()
