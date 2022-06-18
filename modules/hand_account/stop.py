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
    Twilight,
)
from graia.ariadne.model.relationship import Friend, Member
from graia.saya import Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.saya.channel import Channel

if TYPE_CHECKING:
    from . import EnvType

saya = Saya.current()
channel = Channel.current()

Session = saya.access("sqlalchemy.orm.session.sessionmaker")

env: "EnvType" = saya.current_env()
prefix = env.get("prefix", "/record")

__doc__ = Twilight(
    FullMatch(f"{prefix} stop").space(PRESERVE),
    ArgumentMatch("--help", "-h", action="store_true"),
).get_help(
    f"{prefix} stop",
    "魔女手账",
    f"{channel.meta['name']}@{channel.meta['author'][0]}",
)


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(f"{prefix} stop").space(PRESERVE),
                "help" @ ArgumentMatch("--help", "-h", action="store_true"),
            )
        ],
    )
)
async def stop(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    sender: Friend | Member,
    help: ArgResult,
):
    from . import Record, recording

    if help.result:
        await app.send_message(message, cast(str, __doc__))
        return
    if not (record := recording.get(sender.id)):
        await app.send_message(message, "你没有在被记录中")
        return
    del recording[sender.id]

    title = record["title"]
    owner = record["owner"]
    message_chain = MessageChain([Forward(record["message_chain"])])

    async with Session() as session:
        record = Record(
            title=title,
            owner=owner,
            message_chain=pickle.dumps(message_chain),
        )
        session.add(record)
        await session.commit()

    await app.send_message(message, f"Stop recording {title}")
