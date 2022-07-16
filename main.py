import json
from typing import Any, TypedDict

import creart  # before Ariadne 0.7.17
from graia.ariadne.app import Ariadne
from graia.ariadne.connection.config import (
    HttpClientConfig,
    WebsocketServerConfig,
    WebsocketClientConfig,
)
from graia.ariadne.connection.config import config as ariadne_config
from graia.ariadne.message.commander import Commander
from graia.ariadne.message.commander.saya import CommanderBehaviour
from graia.broadcast.entities.decorator import Decorator
from graia.broadcast.exceptions import PropagationCancelled
from graia.broadcast.interfaces.decorator import DecoratorInterface
from graia.broadcast.interrupt import InterruptControl
from graia.saya import Saya
from graia.saya.builtins.broadcast import BroadcastBehaviour
from loguru import logger
from typing_extensions import NotRequired, Required

secret = json.load(open(".secret.json"))
app = Ariadne(
    ariadne_config(
        secret["account"],
        secret["verify_key"],
        HttpClientConfig("http://localhost:8080"),
        # WebsocketClientConfig("ws://localhost:8080"),
        WebsocketServerConfig("/mirai", {"verify_key": secret["verify_key"]}),
    )
)
bcc = app.broadcast
saya = Saya(bcc)

saya.mount("graia.broadcast.interrupt.InterruptControl", InterruptControl(bcc))
saya.install_behaviours(BroadcastBehaviour(bcc))
saya.install_behaviours(CommanderBehaviour(Commander(bcc)))


class ConfigType(TypedDict):
    database_url: NotRequired[str]
    debug: NotRequired[bool]
    modules: NotRequired[dict[str, Any]]
    sudoer: Required[int]
    whitelist: NotRequired[list[int]]


config: ConfigType = json.load(open("config.json"))
saya.mount("__main__.config", config)


class PermissionDecorator(Decorator):
    pre = True
    whitelist = set(config.get("whitelist", []))

    def target(self, interface: DecoratorInterface) -> None:
        if self.whitelist and interface.event.sender.id not in self.whitelist:
            raise PropagationCancelled("Permission denied")


saya.mount("__main__.PermissionDecorator", PermissionDecorator())

with saya.module_context():
    saya.require("modules")

    modules = config.get("modules", {})
    for module, env in modules.items():
        try:
            saya.require(f"modules.{module}", env)
        except ModuleNotFoundError as e:
            logger.error(e)

app.launch_blocking()
