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
from graia.ariadne.model.relationship import Friend, Member
from graia.saya import Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.saya.channel import Channel
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
                "title" @ ParamMatch(True),
                "help" @ ArgumentMatch("--help", "-h", action="store_true"),
            )
        ],
    )
)
async def start(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    sender: Friend | Member,
    title: RegexResult,
    help: ArgResult,
):
    from . import recording, Recording

    if help.result:
        await app.send_message(message, cast(str, __doc__))
        return
    if sender.id in recording:
        await app.send_message(message, "你已经在被记录中了")
        return
    record = Recording(
        title=str(title.result)
        if title.matched
        else datetime.now().strftime("%Y-%m-%d_%H:%M:%S"),
        owner=sender.id,
        message_chain=[],
    )
    recording[sender.id] = record
    await app.send_message(message, f"Start recording {record['title']}")
