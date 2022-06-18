import pickle
from typing import TYPE_CHECKING, cast

from graia.amnesia.message import MessageChain
from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.element import Forward
from graia.ariadne.message.parser.twilight import (
    PRESERVE,
    ArgResult,
    ArgumentMatch,
    FullMatch,
    ParamMatch,
    RegexResult,
    Twilight,
)
from graia.ariadne.model.relationship import Friend, Member
from graia.saya import Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.saya.channel import Channel
from sqlalchemy.ext.asyncio import session
from sqlmodel.sql.expression import select

if TYPE_CHECKING:
    from . import EnvType

saya = Saya.current()
channel = Channel.current()

Session = saya.access("sqlalchemy.orm.session.sessionmaker")

env: "EnvType" = saya.current_env()
prefix = env.get("prefix", "/record")

__doc__ = Twilight(
    FullMatch(f"{prefix} show").space(PRESERVE),
    "title" @ ParamMatch().help("标题"),
    ArgumentMatch("--help", "-h", action="store_true"),
).get_help(
    f"{prefix} show {{title}}",
    "魔女手账",
    f"{channel.meta['name']}@{channel.meta['author'][0]}",
)


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(f"{prefix} show").space(PRESERVE),
                "title" @ ParamMatch(),
                "help" @ ArgumentMatch("--help", "-h", action="store_true"),
            )
        ],
    )
)
async def show(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    title: RegexResult,
    help: ArgResult,
):
    from . import Record

    if help.result or not title.matched:
        await app.send_message(message, cast(str, __doc__))
        return

    async with Session() as session:
        record = (
            await session.exec(select(Record).where(Record.title == str(title.result)))
        ).first()
        message_chain = pickle.loads(record.message_chain)
    await app.send_message(message, message_chain)
