import json
from typing import TypedDict

from graia.ariadne.entry import Ariadne
from graia.ariadne.entry import config as ariadne_config
from graia.saya import Saya
from graia.saya.builtins.broadcast import BroadcastBehaviour
from typing_extensions import NotRequired, Required

app = Ariadne(ariadne_config(**json.load(open(".secret.json"))))
bcc = app.broadcast
saya = Saya(bcc)

saya.install_behaviours(BroadcastBehaviour(bcc))


class ConfigType(TypedDict):
    database_url: NotRequired[str]
    debug: NotRequired[bool]
    modules: NotRequired[dict[str, dict | None]]
    modules_path: NotRequired[str]
    sudoer: Required[int]


config: ConfigType = json.load(open("config.json"))
saya.mount("__main__.config", config)

with saya.module_context():
    modules_path = config.get("modules_path", "modules")
    saya.require(modules_path)

    for module, env in config["modules"].items():
        saya.require(f"{modules_path}.{module}", env or {})


app.launch_blocking()
