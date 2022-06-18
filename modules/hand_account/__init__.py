from time import time_ns
from typing import TYPE_CHECKING, TypedDict

from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.parser.twilight import (
    PRESERVE,
    ArgResult,
    ArgumentMatch,
    FullMatch,
    ParamMatch,
    RegexMatch,
    RegexResult,
    Twilight,
)
from graia.ariadne.model.relationship import Friend, Member
from graia.broadcast.exceptions import PropagationCancelled
from graia.saya import Channel, Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from sqlmodel import Field, SQLModel
from typing_extensions import NotRequired

if TYPE_CHECKING:
    from ...main import ConfigType

saya = Saya.current()
channel = Channel.current()

channel.name(__name__.split(".")[-1])
channel.author("ProgramRipper")

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


class Record(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    timestamp: int = Field(default_factory=time_ns)
    owner: int
    message_chain: bytes


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(FullMatch(prefix).space(PRESERVE), RegexMatch(".*", True))
        ],
        priority=0,
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
        priority=32,
    )
)
async def help(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    cmd: RegexResult,
    help: ArgResult,
):
    description = channel.meta["description"]
    if not cmd.matched or str(cmd.result) == "help" or help.result:
        await app.send_message(message, description)
    else:
        await app.send_message(
            message, f"Unknown command: {str(cmd.result)}\n{description}"
        )


from .clear import *
from .list import *
from .show import *
from .start import *
from .stop import *
