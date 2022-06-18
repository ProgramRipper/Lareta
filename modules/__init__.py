from typing import TYPE_CHECKING

from graia.ariadne.app import Ariadne
from graia.ariadne.event.lifecycle import ApplicationLaunched
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.parser.twilight import FullMatch, Twilight
from graia.saya import Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.saya.channel import Channel, ChannelMeta
from graia.saya.event import (
    SayaModuleInstalled,
    SayaModuleUninstall,
    SayaModuleUninstalled,
)
from loguru import logger
from sqlalchemy.ext.asyncio.engine import create_async_engine
from sqlalchemy.orm.session import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from ..main import ConfigType

saya = Saya.current()
channel = Channel.current()

channel.name(__name__.split(".")[-1])
channel.author("ProgramRipper")

config: "ConfigType" = saya.access("__main__.config")

engine = create_async_engine(
    config.get("database_url", "sqlite+aiosqlite:///:memory:"),
    echo=config.get("debug", False),
)
Session = sessionmaker(
    engine,
    AsyncSession,  # type: ignore[arg-type]
)
saya.mount("sqlalchemy.orm.session.sessionmaker", Session)


@channel.use(ListenerSchema([ApplicationLaunched]))
async def on_app_launched() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@channel.use(ListenerSchema([SayaModuleInstalled, SayaModuleUninstall]))
async def module_event_listener(
    app: Ariadne, event: SayaModuleInstalled | SayaModuleUninstalled, channel: Channel
):
    meta = channel.meta  # type: ChannelMeta
    authors = meta["author"]  # type: list[str]
    announcement = (
        f"{meta['name'] or event.module}"
        f"@{authors[0] if authors else 'Unknown'}"
        f" {'installed' if isinstance(event, SayaModuleInstalled) else 'uninstalled'}"
    )
    logger.success(announcement)
    await app.send_friend_message(config["sudoer"], announcement)


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[Twilight(FullMatch("ping"))],
    )
)
async def ping(app: Ariadne, message: FriendMessage | GroupMessage):
    await app.send_message(message, "pong!")
