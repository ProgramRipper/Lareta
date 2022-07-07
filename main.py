import json
from typing import TypedDict

from graia.ariadne.entry import Ariadne
from graia.ariadne.entry import config as ariadne_config
from graia.ariadne.message.commander import Commander
from graia.ariadne.message.commander.saya import CommanderBehaviour
from graia.broadcast.entities.decorator import Decorator
from graia.broadcast.exceptions import PropagationCancelled
from graia.broadcast.interfaces.decorator import DecoratorInterface
from graia.broadcast.interrupt import InterruptControl
from graia.saya import Saya
from graia.saya.builtins.broadcast import BroadcastBehaviour
from typing_extensions import NotRequired, Required

secret = json.load(open(".secret.json"))
app = Ariadne(
    ariadne_config(
        secret["account"],
        secret["verify_key"],
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
    modules: NotRequired[dict[str, dict | None]]
    modules_path: NotRequired[str]
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
    modules_path = config.get("modules_path", "modules")
    saya.require(modules_path)

    for module, env in config["modules"].items():
        saya.require(f"{modules_path}.{module}", env or {})

app.launch_blocking()
