import asyncio
import pickle
import re
from datetime import datetime
from time import time_ns
from typing import TYPE_CHECKING, TypedDict, cast

from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.chain import MessageChain, MessageContainer
from graia.ariadne.message.element import At, Forward, ForwardNode, MultimediaElement
from graia.ariadne.message.parser.twilight import (
    PRESERVE,
    ArgResult,
    ArgumentMatch,
    FullMatch,
    ParamMatch,
    RegexResult,
    Twilight,
    WildcardMatch,
)
from graia.ariadne.model.relationship import Friend, Member
from graia.broadcast.exceptions import PropagationCancelled
from graia.saya import Channel, Saya
from graia.saya.builtins.broadcast.schema import ListenerSchema
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm.session import sessionmaker
from sqlmodel import Field, SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from typing_extensions import NotRequired

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ...main import ConfigType

saya = Saya.current()
channel = Channel.current()

channel.name(__name__.split(".")[-1])
channel.author("ProgramRipper")

_background_task: set[asyncio.Task] = saya.access("__main__._background_task")
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

helps: dict[str, str] = {
    "start": Twilight(
        FullMatch(f"{prefix} start").space(PRESERVE),
        "title" @ ParamMatch().help("标题"),
        ArgumentMatch("--help", "-h", action="store_true"),
    ).get_help(
        f"{prefix} start {{title}}",
        "魔女手账",
        f"{channel.meta['name']}@{channel.meta['author'][0]}",
    ),
    "stop": Twilight(
        FullMatch(f"{prefix} stop").space(PRESERVE),
        ArgumentMatch("--help", "-h", action="store_true"),
    ).get_help(
        f"{prefix} stop",
        "魔女手账",
        f"{channel.meta['name']}@{channel.meta['author'][0]}",
    ),
    "show": Twilight(
        FullMatch(f"{prefix} show").space(PRESERVE),
        "title" @ ParamMatch().help("标题"),
        ArgumentMatch("--help", "-h", action="store_true"),
    ).get_help(
        f"{prefix} show {{title}}",
        "魔女手账",
        f"{channel.meta['name']}@{channel.meta['author'][0]}",
    ),
}


class Recording(SQLModel):
    title: str
    owner: int
    message_chain: list[ForwardNode]


class Record(Recording, table=True):
    id: int | None = Field(default=None, primary_key=True)
    timestamp: int = Field(default_factory=time_ns)
    message_chain: bytes


recordings: dict[int, tuple[asyncio.Task, Recording]] = {}


async def callback(
    sender: int,
    title: str,
    owner: int,
):
    await asyncio.sleep(5 * 60)
    app = Ariadne.current()
    event = cast(FriendMessage | GroupMessage, app.broadcast.event_ctx.get())
    await app.send_message(event, f"WARN: Timeout, auto stop recording {title}")
    _background_task.add(asyncio.create_task(stop(sender, owner)))


@channel.use(ListenerSchema([FriendMessage, GroupMessage], priority=32))
async def record(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    message_chain: MessageChain,
    sender: Friend | Member,
):
    owner = sender.id

    if not (recording := recordings.get(owner)):
        return

    time = datetime.now()

    cb, recording = recording
    cb.cancel()

    if len(recording.message_chain) >= 100:
        title = recording.title
        result = re.search(r"\((\d*?)\)$", title)
        new_title = (
            f"{title[:result.start()]}({int(result[1])+1})" if result else f"{title}(1)"
        )

        await app.send_message(
            message,
            f"WARN: Max message length reached, auto stop recording {title} and start new recording {new_title}",
        )

        await stop(owner)
        await start(owner, new_title)

        cb, recording = recordings[owner]
        cb.cancel()

    for element in message_chain:
        if isinstance(element, MultimediaElement):
            await element.get_bytes()

    recording.message_chain.append(
        ForwardNode(
            sender,
            time,
            message_chain,
            cast(
                str,
                getattr(sender, "nickname", None) or getattr(sender, "name", None),
            ),
        )
    )

    cb = asyncio.create_task(callback(owner, recording.title, owner))
    _background_task.add(cb)
    cb.add_done_callback(_background_task.discard)

    recordings[owner] = (cb, recording)


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                "cmd" @ ParamMatch(True).space(PRESERVE),
                "title" @ ParamMatch(True).space(PRESERVE),
                "target" @ ArgumentMatch("--traget", "-t"),  # 本来想着这里是ElementMatch(At)
                "page" @ ArgumentMatch("--page", "-p"),
                "help" @ ArgumentMatch("--help", "-h", action="store_true"),
                WildcardMatch(True, True),
            )
        ],
    )
)
async def main(
    app: Ariadne,
    message: FriendMessage | GroupMessage,
    sender: Friend | Member,
    cmd: RegexResult,
    title: RegexResult,
    target: ArgResult,
    page: ArgResult,
    help: ArgResult,
):
    # TODO: inherit from Generic to support ElementResult[At]
    target = cast(ArgResult[MessageChain], target)  # TODO: support TypeVar in Twilight
    page = cast(ArgResult[MessageChain], page)
    help = cast(ArgResult[bool], help)

    if sender.id not in whitelist:
        await app.send_message(message, "FATAL: Permission denied")
        raise PropagationCancelled

    command = str(cmd.result) if cmd.matched else "help"

    if help.result and command in helps:
        await app.send_message(message, helps[command])
        raise PropagationCancelled

    match command:
        case "start":
            msg = await start(
                sender.id,
                str(title.result) if title.matched else None,
                target.result[0].target if target.matched else None,  # type: ignore
            )
        case "stop":
            msg = await stop(
                sender.id,
                target.result[0].target if target.matched else None,  # type: ignore
            )
        case "show":
            msg = await show(str(title.result) if title.matched else None)
        case "list":
            msg = await list_(int(str(page.result)) if page.matched else 0)
        case "help":
            msg = cast(str, __doc__)
        case unknown_command:
            msg = f"Unknown command: {unknown_command}\n{__doc__}"

    await app.send_message(message, msg)
    raise PropagationCancelled


async def start(
    sender: int, title: str | None = None, target: int | None = None
) -> MessageContainer:
    owner = target or sender

    if owner in recordings:
        return [
            "ERROR: ",
            At(target) if target else "You",
            " are already being recorded",
        ]

    title = title or datetime.now().strftime("%Y-%m-%d_%H:%M:%S")

    async with Session() as session:
        if (
            await session.exec(
                select(func.count(Record.id)).where(Record.title == title)  # type: ignore
            )
        ).one():
            return f"ERROR: Record {title} already exists"

    recording = Recording(title=title, owner=owner, message_chain=[])

    cb = asyncio.create_task(callback(sender, title, owner))
    _background_task.add(cb)
    cb.add_done_callback(_background_task.discard)

    recordings[owner] = (cb, recording)

    return f"INFO: Start recording {title}"


async def stop(sender: int, target: int | None = None) -> MessageContainer:
    if not (recording := recordings.get(target or sender)):
        return (
            ["ERROR: ", At(target) if target else "You", " are not being recorded"],
        )

    target = target or sender
    del recordings[target]
    cb, recording = recording
    cb.cancel()

    message_chain = MessageChain([Forward(recording.message_chain)])

    async with Session() as session:
        record = Record(
            title=recording.title,
            owner=recording.owner,
            message_chain=pickle.dumps(message_chain),
        )
        session.add(record)
        await session.commit()

    return f"INFO: Stop recording {recording.title}"


async def show(title: str | None = None) -> MessageContainer:
    if title is None:
        return helps["show"]

    try:
        async with Session() as session:
            record = (
                await session.exec(
                    select(Record).where(Record.title == title)  # type: ignore # I don't know why...
                )
            ).one()  # type: Record
            msg = pickle.loads(record.message_chain)  # type: str
    except NoResultFound:
        msg = f"ERROR: No matching record found for {title}"

    return msg


async def list_(page: int = 0) -> MessageContainer:
    async with Session() as session:
        records = await session.exec(
            select(Record).order_by(Record.timestamp).offset(page * 10).limit(10)  # type: ignore
        )  # type: Iterable[Record]

        return (
            "\n".join(
                f"{record.title}@{record.owner}\n{datetime.fromtimestamp(record.timestamp/10**9)}"
                for record in records
            )
            or "No record found"
        )
