import asyncio
import re
from collections import ChainMap
from datetime import datetime
from typing import TYPE_CHECKING

from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.commander.saya import CommandSchema
from graia.ariadne.message.element import (
    At,
    AtAll,
    Forward,
    ForwardNode,
    MultimediaElement,
    Source,
)
from graia.ariadne.model.relationship import Friend, Member
from graia.ariadne.util.interrupt import EventWaiter
from graia.ariadne.util.validator import CertainFriend, CertainGroup, CertainMember
from graia.broadcast.exceptions import PropagationCancelled
from graia.saya import Channel, Saya
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlmodel import JSON, Column, Field, SQLModel, select

if TYPE_CHECKING:
    from graia.broadcast.interrupt import InterruptControl
    from sqlalchemy.orm.session import sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    from ..main import PermissionDecorator

saya = Saya.current()
channel = Channel.current()

channel.name(__name__.split(".")[-1])
channel.author("ProgramRipper")
channel.description(__doc__ or "")

prefix = f"/{channel._name}"
inc: "InterruptControl" = saya.access("graia.broadcast.interrupt.InterruptControl")
permission: "PermissionDecorator" = saya.access("__main__.PermissionDecorator")
session_maker: "sessionmaker[AsyncSession]" = saya.access(  # type: ignore
    "sqlalchemy.orm.session.sessionmaker"
)
recording: dict[str, asyncio.Future] = {}
_background_task: set[asyncio.Task] = set()


class Record(SQLModel, table=True):
    title: str = Field(primary_key=True)
    time: datetime = Field(default_factory=datetime.now)
    owner: int
    message: Forward = Field(sa_column=Column(JSON))

    class Config:
        arbitrary_types_allowed = True


@channel.use(
    CommandSchema(f"{prefix} new {{title: str = ''}}", decorators=[permission])
)
async def new(
    app: Ariadne,
    event: FriendMessage | GroupMessage,
    sender: Friend | Member,
    title: str,
):
    try:
        record = Record(title=title, owner=sender.id, message=Forward([]))

        async with session_maker() as session:
            session.add(record)
            await session.commit()
    except IntegrityError:
        msg = f"ERROR: Record {title} already exists"
    else:
        msg = f"INFO: Record {title} created"

    await app.send_message(event, msg)
    raise PropagationCancelled


@channel.use(
    CommandSchema(
        f"{prefix} start {{targets: AtAll}}", decorators=[permission], priority=15
    )
)
@channel.use(
    CommandSchema(
        f"{prefix} start {{...targets: At}}", decorators=[permission], priority=15
    )
)
def start(app: Ariadne, event: FriendMessage | GroupMessage, targets: list[At] | AtAll):
    _background_task.add(
        task := asyncio.create_task(
            start2(app, event, datetime.now().strftime("%Y-%m-%d_%H:%M:%S"), targets)
        )
    )
    task.add_done_callback(_background_task.discard)
    raise PropagationCancelled


@channel.use(
    CommandSchema(
        f"{prefix} start {{title: str}} {{targets: AtAll}}", decorators=[permission]
    )
)
@channel.use(
    CommandSchema(
        f"{prefix} start {{title: str}} {{...targets: At}}", decorators=[permission]
    )
)
async def start2(
    app: Ariadne,
    event: FriendMessage | GroupMessage,
    title: str,
    targets: list[At] | AtAll,
):
    if isinstance(event, FriendMessage):
        waiter = EventWaiter(
            [FriendMessage],
            decorators=[CertainFriend([event.sender])],
            block_propagation=True,  # maybe it cant work
        )
    else:
        waiter = EventWaiter(
            [GroupMessage],
            decorators=[
                CertainGroup(event.sender.group)
                if isinstance(targets, AtAll)
                else CertainMember([at.target for at in targets] or [event.sender])
            ],
        )

    waiter.block_propagation = True
    coro = _start(title, event.sender.id, waiter)

    try:
        await anext(coro)
        await app.send_message(event, f"INFO: Start recording {title}")

        async for new_title in coro:
            await app.send_message(
                event,
                (
                    f"WARN: Record {title} length has reached the maximum value\n"
                    f"auto stop recording and start recording {new_title}"
                ),
            )
            title = new_title
    except ValueError:
        await app.send_message(event, f"ERROR: Record {title} already exists")
    else:
        await app.send_message(event, f"INFO: Stop recording {title}")

    raise PropagationCancelled


async def _start(title: str, owner: int, waiter: EventWaiter):
    async with session_maker() as session:
        while True:
            if (
                title in recording
                or (
                    await session.exec(select(Record).where(Record.title == title))  # type: ignore
                ).first()
            ):
                raise ValueError(f"Record {title} already exists")

            session.add(record := Record(title=title, owner=owner, message=Forward([])))
            future = recording[title] = asyncio.get_running_loop().create_future()
            yield title

            while len(record.message.node_list) < 100:
                task: asyncio.Task[FriendMessage | GroupMessage] = asyncio.create_task(
                    inc.wait(waiter, 15, timeout=5 * 60)
                )
                done, _ = await asyncio.wait(
                    {future, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if (
                    future in done  # cancelled
                    or task in done  # timeout
                    and isinstance(task.exception(), asyncio.TimeoutError)
                ):
                    future.cancel()
                    task.cancel()
                    await session.commit()
                    return

                event = task.result()
                message = event.message_chain
                time = message.get_first(Source).time
                message = message.as_sendable()

                if (
                    msg := str(message).strip()
                ) == f"{prefix} stop" and event.sender.id in permission.whitelist:
                    await session.commit()
                    return
                elif msg.startswith("/") or not message.content:
                    continue

                asyncio.gather(
                    e.get_bytes() for e in message if isinstance(e, MultimediaElement)
                )

                record.message.node_list.append(
                    ForwardNode(
                        event.sender,
                        time,
                        message,
                    )
                )

            await session.commit()
            future.cancel()
            del recording[title]

            result = re.search(r"\((\d*?)\)$", title)
            title = (
                f"{title[:result.start()]}({int(result[1])+1})"
                if result
                else f"{title}(1)"
            )


@channel.use(CommandSchema(f"{prefix} stop {{title: str}}", decorators=[permission]))
async def stop(app: Ariadne, event: FriendMessage | GroupMessage, title: str):
    try:
        recording[title].set_result(None)
    except KeyError:
        await app.send_message(event, f"ERROR: Record {title} does not exist")

    raise PropagationCancelled


@channel.use(CommandSchema(f"{prefix} stop", decorators=[permission]))
async def stop2(app: Ariadne, event: FriendMessage | GroupMessage):
    await app.send_message(event, "ERROR: You are not being recorded")
    raise PropagationCancelled


@channel.use(CommandSchema(f"{prefix} show {{title: str}}", decorators=[permission]))
async def show(app: Ariadne, event: FriendMessage | GroupMessage, title: str):
    try:
        async with session_maker() as session:
            record: Record = (await session.exec(select(Record).where(Record.title == title))).one()  # type: ignore
    except NoResultFound:
        await app.send_message(event, f"ERROR: Record {title} does not exist")
    else:
        await app.send_message(event, record.message)

    raise PropagationCancelled
