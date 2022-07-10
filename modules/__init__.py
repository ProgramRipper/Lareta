import json
from datetime import datetime
from functools import partial
from traceback import format_exception_only, print_exc, walk_tb
from typing import TYPE_CHECKING, Any

from graia.ariadne.app import Ariadne
from graia.ariadne.event import MiraiEvent
from graia.ariadne.event.lifecycle import ApplicationLaunched
from graia.ariadne.event.message import FriendMessage, GroupMessage, MessageEvent
from graia.ariadne.message.parser.base import MatchContent
from graia.broadcast.builtin.event import ExceptionThrowed
from graia.broadcast.exceptions import PropagationCancelled
from graia.saya import Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.saya.channel import Channel
from loguru import logger
from pydantic.main import BaseModel
from sqlalchemy.ext.asyncio.engine import create_async_engine
from sqlalchemy.orm.session import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from ..main import ConfigType, PermissionDecorator

saya = Saya.current()
channel = Channel.current()

channel.name(__name__.split(".")[-1])
channel.author("ProgramRipper")
channel.description(__doc__ or "")

config: "ConfigType" = saya.access("__main__.config")
permission: "PermissionDecorator" = saya.access("__main__.PermissionDecorator")


def default(o: Any):
    if isinstance(o, BaseModel):
        return o.dict()
    elif isinstance(o, datetime):
        return int(o.timestamp())


engine = create_async_engine(
    config.get("database_url", "sqlite+aiosqlite:///:memory:"),
    echo=config.get("debug", False),
    json_deserializer=json.loads,  # may use other json lib
    json_serializer=partial(json.dumps, default=default),
)
session_maker: "sessionmaker[AsyncSession]" = sessionmaker(  # type: ignore
    engine,
    AsyncSession,  # type: ignore
    expire_on_commit=False,
)
saya.mount("sqlalchemy.orm.session.sessionmaker", session_maker)


@channel.use(ListenerSchema([ExceptionThrowed]))
async def exception_handler(
    app: Ariadne,
    event: MiraiEvent,
    exception: Exception,
):
    try:
        msg = [
            f"ERROR: During handling of {event!r}, an exception occurred:\n",
            *format_exception_only(None, exception),
            "Please report this exception to the developer",
        ]

        frame, _ = next(
            filter(
                lambda f: not f[0].f_globals["__name__"].startswith("graia.broadcast"),
                walk_tb(exception.__traceback__),
            )
        )
        if channel := saya.channels.get(frame.f_globals["__name__"]):
            msg[-1] += f" @{channel._author[0]}"

        if isinstance(event, MessageEvent) and event.sender.id != config["sudoer"]:
            await app.send_message(event, msg)
        await app.send_friend_message(config["sudoer"], msg)
    except Exception:
        logger.critical(
            "During handling of the above exception, another exception occurred:"
        )
        print_exc()


@channel.use(ListenerSchema([ApplicationLaunched]))
async def on_app_launched() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        decorators=[MatchContent("ping"), permission],
    )
)
async def ping(app: Ariadne, message: FriendMessage | GroupMessage):
    await app.send_message(message, "pong!")
    raise PropagationCancelled
