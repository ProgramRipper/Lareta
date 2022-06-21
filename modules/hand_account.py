import asyncio
import json
import pickle
import re
from contextlib import suppress
from datetime import datetime
from itertools import repeat
from time import time_ns
from typing import TYPE_CHECKING, TypedDict, cast
from weakref import WeakValueDictionary

from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.chain import MessageChain, MessageContainer
from graia.ariadne.message.element import At, Forward, ForwardNode, MultimediaElement
from graia.ariadne.message.parser.twilight import (
    PRESERVE,
    ArgumentMatch,
    FullMatch,
    ParamMatch,
    RegexResult,
    Twilight,
    WildcardMatch,
)
from graia.ariadne.model.relationship import Friend, Group, Member
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

    from ..main import ConfigType

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

helps: dict[str, str] = {
    "start": Twilight(
        "title" @ ParamMatch().help("标题（可选，默认为当前时间 %Y-%m-%d_%H:%M:%S）"),
        "target"
        @ WildcardMatch().help(
            "目标（可选，默认为仅发送者。可指定多个目标。注意：如果指定了目标，默认不包含发送者，可以@自己以显示指定记录目标）"
        ),
    ).get_help(f"{prefix} start [title [*target]]", "开始记录"),
    "stop": Twilight(
        "title" @ ParamMatch().help("标题（可选，默认为发送者的当前的记录）"),
    ).get_help(f"{prefix} stop [title]", "停止记录"),
    "show": Twilight(
        "title" @ ParamMatch().help("标题"),
    ).get_help(f"{prefix} show {{title}}", "显示记录"),
    "list": Twilight(
        "page" @ ParamMatch().help("页数（可选，默认为 0）"),
        "num" @ ParamMatch().help("每页项数（可选，默认为 10）"),
    ).get_help(f"{prefix} list [page [num]]", "列出记录"),
}


class Recording(SQLModel):
    message_chain: list[ForwardNode]
    owner: Friend | Member
    targets: list[int]
    timestamp: int = Field(default_factory=time_ns)
    title: str = Field(index=True)


class Record(Recording, table=True):
    id: int | None = Field(default=None, primary_key=True)
    message_chain: bytes
    owner: int
    targets: str  # list[int] in json format


recorded: WeakValueDictionary[int, Recording] = WeakValueDictionary()  # id to recording
recordings: dict[str, tuple[asyncio.Task, Recording]] = {}  # title to recording


@channel.use(ListenerSchema([FriendMessage, GroupMessage], priority=32))
async def record(
    app: Ariadne,
    message_chain: MessageChain,
    sender: Friend | Member,
):
    target = sender.id

    if not (recording := recorded.get(target)):
        return

    time = datetime.now()

    if len(recording.message_chain) >= 100:
        title = recording.title
        result = re.search(r"\((\d*?)\)$", title)
        new_title = (
            f"{title[:result.start()]}({int(result[1])+1})" if result else f"{title}(1)"
        )
        owner = recording.owner

        await app.send_message(
            owner.group if isinstance(owner, Member) else owner,
            f"WARN: Max message length reached, auto stop recording {title} and start recording {new_title}",
        )

        await _stop(title=title)
        await _start(app, owner, new_title, recording.targets)

        recording = recorded[target]

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


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                "cmd" @ ParamMatch(True).space(PRESERVE),
                WildcardMatch(True, True),
            )
        ],
    )
)
async def main(
    app: Ariadne,
    sender: Friend | Member,
    cmd: RegexResult,
):
    # TODO: inherit from Generic to support ElementResult[At]
    # TODO: support TypeVar in Twilight
    target = sender.group if isinstance(sender, Member) else sender
    if sender.id not in whitelist:
        await app.send_message(target, "FATAL: Permission denied")
        raise PropagationCancelled

    command = str(cmd.result) if cmd.matched else "help"

    if command in helps:
        await app.send_message(target, helps[command])
        raise PropagationCancelled

    msg = f"Unknown command: {command}\n{__doc__}"

    await app.send_message(target, msg)
    raise PropagationCancelled


async def _daemon(
    app: Ariadne,
    owner: Friend | Member,
    title: str,
):
    while True:
        if not (recording := recordings.get(title)):
            return
        if (delay := recording[1].timestamp - time_ns()) / 10**9 <= 0:
            break
        await asyncio.sleep(delay)

    await asyncio.gather(
        app.send_message(
            owner.group if isinstance(owner, Member) else owner,
            f"WARN: Timeout, auto stop recording {title}",
        ),
        _stop(title=title),
    )


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                FullMatch("start").space(PRESERVE),
                "title" @ ParamMatch(True).space(PRESERVE),
                "target" @ WildcardMatch(optional=True),
            )
        ],
    )
)
async def start(
    app: Ariadne,
    sender: Friend | Member,
    title: RegexResult,
    target: RegexResult,
):
    targets = (
        [e.target for e in cast(MessageChain, target.result) if isinstance(e, At)]
        if target.matched
        else []
    )
    if not title.matched:
        title_ = None
    elif at := cast(MessageChain, title.result).get(At):
        targets.append(at[0].target)
        title_ = None
    else:
        title_ = str(title.result)

    await app.send_message(
        sender.group if isinstance(sender, Member) else sender,
        await _start(
            app,
            sender,
            title_,
            targets,
        ),
    )
    raise PropagationCancelled


async def _start(
    app: Ariadne,
    owner: Friend | Member,
    title: str | None = None,
    targets: list[int] | None = None,
) -> MessageContainer:
    title = title or datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    if title in recordings:
        return f"ERROR: {title} is already being recorded"
    async with Session() as session:
        if (
            await session.exec(
                select(func.count(Record.id)).where(Record.title == title)  # type: ignore
            )
        ).one():
            return f"ERROR: Record {title} already exists"

    targets = targets or [owner.id]

    recording = Recording(message_chain=[], owner=owner, targets=targets, title=title)
    daemon = asyncio.create_task(_daemon(app, owner, title))
    recordings[title] = (daemon, recording)
    map(recorded.__setitem__, targets, repeat(recording))

    return f"INFO: Start recording {title}"


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                FullMatch("stop").space(PRESERVE),
                "title" @ ParamMatch(True),
            )
        ],
    )
)
async def stop(
    app: Ariadne,
    sender: Friend | Member,
    title: RegexResult,
):
    await app.send_message(
        sender.group if isinstance(sender, Member) else sender,
        await _stop(sender.id, str(title.result) if title.matched else None),
    )
    raise PropagationCancelled


async def _stop(sender: int = 0, title: str | None = None) -> MessageContainer:
    if not title:
        if not (recording := recorded.get(sender)):
            return "ERROR: You are not being recorded"
        title = recording.title
    if not (recording := recordings.get(title)):
        return f"ERROR: {title} is not recording"

    daemon, recording = recording
    daemon.cancel()
    with suppress(asyncio.CancelledError):
        await daemon
    del recordings[title]

    message_chain = MessageChain([Forward(recording.message_chain)])

    async with Session() as session:
        record = Record(
            message_chain=pickle.dumps(message_chain),
            owner=recording.owner.id,
            targets=json.dumps(recording.targets),
            timestamp=recording.timestamp,
            title=title,
        )
        session.add(record)
        await session.commit()

    return f"INFO: Stop recording {title}"


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                FullMatch("show").space(PRESERVE),
                "title" @ ParamMatch(),
            )
        ],
    )
)
async def show(app: Ariadne, message: FriendMessage | GroupMessage, title: RegexResult):
    await app.send_message(message, await _show(str(title.result)))
    raise PropagationCancelled


async def _show(title: str | None = None) -> MessageContainer:
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


@channel.use(
    ListenerSchema(
        [FriendMessage, GroupMessage],
        inline_dispatchers=[
            Twilight(
                FullMatch(prefix).space(PRESERVE),
                FullMatch("list").space(PRESERVE),
                "page" @ ParamMatch(optional=True),
                "num" @ ParamMatch(optional=True),
            )
        ],
    )
)
async def list_(
    app: Ariadne,
    sender: Friend | Member,
    page: RegexResult,
    num: RegexResult,
):
    await app.send_message(
        sender.group if isinstance(sender, Member) else sender,
        await _list(
            int(str(page.result)) if page.matched else 0,
            int(str(num.result)) if page.matched else 10,
        ),
    )
    raise PropagationCancelled


async def _list(page: int = 0, num: int = 10) -> MessageContainer:
    async with Session() as session:
        records = await session.exec(
            select(Record).order_by(Record.timestamp).offset(page * num).limit(num)  # type: ignore
        )  # type: Iterable[Record]

        return (
            "\n".join(
                f"{record.title}@{record.owner}\n{datetime.fromtimestamp(record.timestamp/10**9)}"
                for record in records
            )
            or "No record found"
        )