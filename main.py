import json

from graia.ariadne.entry import Ariadne, Friend, config
from graia.ariadne.event.message import FriendMessage

app = Ariadne(config(**json.load(open(".secret.json"))))


@app.broadcast.receiver(FriendMessage)
async def friend_message_listener(app: Ariadne, friend: Friend):
    await app.send_message(friend, "Hello, World!")


Ariadne.launch_blocking()
