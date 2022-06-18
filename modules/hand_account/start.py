from datetime import datetime
import pickle

from typing import TYPE_CHECKING, cast
from graia.amnesia.message import MessageChain

from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.parser.twilight import (
    PRESERVE,
    ArgResult,
    ArgumentMatch,
    FullMatch,
    ParamMatch,
    RegexResult,
    Twilight,
)
from graia.saya import Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.saya.channel import Channel
from loguru import logger
from sqlalchemy.engine import result
from sqlalchemy.engine.result import ScalarResult
from sqlmodel import select

if TYPE_CHECKING:
    from . import EnvType

saya = Saya.current()
channel = Channel.current()

Session = saya.access("sqlalchemy.orm.session.sessionmaker")

env: "EnvType" = saya.current_env()
prefix = env.get("prefix", "/record")

__doc__ = Twilight(
    FullMatch(f"{prefix} start").space(PRESERVE),
    "title" @ ParamMatch().help("标题"),
    ArgumentMatch("--help", "-h", action="store_true"),
).get_help(
    f"{prefix} start {{title}}",
    "魔女手账",
    f"{channel.meta['name']}@{channel.meta['author'][0]}",
)


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(f"{prefix} start").space(PRESERVE),
                "t" @ ParamMatch(True),
                "help" @ ArgumentMatch("--help", "-h", action="store_true"),
            )
        ],
    )
)
async def start(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    message_chain: MessageChain,
    t: RegexResult,
    help: ArgResult,
):
    from . import Record

    if help.result:
        await app.send_message(message, cast(str, __doc__))
        return
    if t.matched:
        title = str(t.result)
    else:
        title = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    await app.send_message(message, f"Start recording {title}")

    async with Session() as session:
        record = Record(
            title=title,
            owner=message.sender.id,
            message_chain=pickle.dumps(message_chain),
        )
        session.add(record)
        await session.commit()
    async with Session() as session:
        result = (
            await session.exec(select(Record).where(Record.title == title))
        ).one_or_none()
        await app.send_message(message, pickle.loads(result.message_chain))
