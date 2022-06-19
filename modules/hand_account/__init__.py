import pickle
from collections import defaultdict
from datetime import datetime
from time import time_ns
from typing import TYPE_CHECKING, TypedDict, cast

from graia.ariadne.message.chain import MessageChain
from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.element import Forward, ForwardNode
from graia.ariadne.message.parser.twilight import (
    PRESERVE,
    ArgResult,
    ArgumentMatch,
    FullMatch,
    ParamMatch,
    RegexMatch,
    RegexResult,
    Twilight,
    UnionMatch,
)
from graia.ariadne.model.relationship import Friend, Member
from graia.broadcast.exceptions import PropagationCancelled
from graia.saya import Channel, Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from sqlmodel import Field, SQLModel
from typing_extensions import NotRequired

from sqlmodel.sql.expression import select

select
if TYPE_CHECKING:
    from sqlalchemy.orm.session import sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    from ...main import ConfigType


saya = Saya.current()
channel = Channel.current()

channel.name(__name__.split(".")[-1])
channel.author("ProgramRipper")

Session: "sessionmaker[AsyncSession]" = saya.access(  # type: ignore
    "sqlalchemy.orm.session.sessionmaker"
)
config: "ConfigType" = saya.access("__main__.config")
sudoer = config["sudoer"]


class EnvType(TypedDict):
    prefix: NotRequired[str]
    whitelist: NotRequired[list[int]]


env: EnvType = saya.current_env()

prefix = env.get("prefix", "/record")
whitelist = set(env.get("whitelist", [sudoer]))

__doc__ = Twilight(
    "start" @ ParamMatch().help("开始记录"),
    "stop" @ ParamMatch().help("停止记录"),
    "clear" @ ParamMatch().help("清空记录"),
    "list" @ ParamMatch().help("列出记录"),
    "show" @ ParamMatch().help("显示记录"),
    "help" @ ParamMatch().help("显示帮助"),
    ArgumentMatch("--help", "-h", action="store_true").help("显示帮助"),
).get_help(
    f"{prefix} {{command}}",
    "魔女手账",
    f"{channel.meta['name']}@{channel.meta['author'][0]}",
)
channel.description(__doc__)

helps: dict[str, str] = {}


class Record(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    timestamp: int = Field(default_factory=time_ns)
    owner: int
    message_chain: bytes


class Recording(TypedDict):
    title: str
    owner: int
    message_chain: list[ForwardNode]


recording: dict[int, Recording] = {}


# root


@channel.use(ListenerSchema([FriendMessage, GroupMessage], priority=28))
async def recorder(
    message_chain: MessageChain,
    sender: Friend | Member,
):
    if record := recording.get(sender.id):
        record["message_chain"].append(
            ForwardNode(
                sender,
                datetime.now(),
                message_chain,
                cast(
                    str,
                    getattr(sender, "nickname", None) or getattr(sender, "name", None),
                ),
            )
        )


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(FullMatch(prefix).space(PRESERVE), RegexMatch(".*", True))
        ],
        priority=12,
    )
)
async def permission(
    app: Ariadne, message: FriendMessage | GroupMessage, sender: Friend | Member
):
    if sender.id not in whitelist:
        await app.send_message(message, "Permisson denied")
        raise PropagationCancelled


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                "cmd" @ ParamMatch(True),
                "help" @ ArgumentMatch("--help", "-h", action="store_true"),
            )
        ],
        priority=20,
    )
)
async def help(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    cmd: RegexResult,
):
    description = channel.meta["description"]
    if not cmd.matched or (command := str(cmd.result)) == "help":
        await app.send_message(message, description)
    elif help := helps.get(command):
        await app.send_message(message, help)
    else:
        await app.send_message(
            message, f"Unknown command: {str(cmd.result)}\n{description}"
        )
    raise PropagationCancelled


# start


helps["start"] = Twilight(
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
            )
        ],
    )
)
async def start(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    sender: Friend | Member,
    title: RegexResult,
):
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


# stop


helps["stop"] = Twilight(
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


# show

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
            )
        ],
    )
)
async def show(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    title: RegexResult,
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
