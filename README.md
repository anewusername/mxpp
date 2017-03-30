# mxpp

**mxpp** is a bot which bridges Matrix and one-to-one XMPP chat.

I wrote this bot to finally get persistent chat history for my 
gchat/hangouts/google talk conversations, and to evaluate Matrix
for future use, so it should probably work for those use cases.


**Functionality**

* The bot creates one Matrix room for each user on your contact list,
then invites a list of Matrix users (of your choosing) to all the rooms.
    - Room name is set to the contact's name.
    - Room topic is set to the contact's JID.
    - Any text sent to the room is sent to the contact's JID.
    * Any text received from the contact's JID is sent as a notice
      to the room.
* A room named "XMPP Control Room" is created
    - Presence info ("available" or "unavailable") is sent to this room
    - Text command ```purge``` makes the bot leave from any rooms which do
      not correspond to a roster entry (excluding the two special rooms),
      and also from any unoccupied rooms (eg. if the user left).
    - Text command ```refresh``` probes the presence of all XMPP contacts
      and requests a roster update from the server.
    - Text commands ```joinmuc room_jid@roomserver.com``` and ```leavemuc room_jid@roomserver.com```
      allow you to join and leave multi-user chats.
* A room named "XMPP All Chat" is created
    - All inbound and outbound chat messages are logged here.
    - The bot complains if you talk in here.
* If the bot is restarted, it recreates its room-JID map based on the
  room topics, and continues as before.
* Currently, the bot automatically accepts anytime anyone asks to add
  you on XMPP, and also automatically adds them to your contact roster.
* Multi-user chats (MUCs) are handled by creating additional rooms
    - Room topic is set to "<groupchat>room_jid@roomserver.com"
    - To join a MUC, send a message saying ```joinmuc room_jid@roomserver.com```
      to the "XMPP Control Room"
    - To leave a MUC, send a message saying ```leavemuc room_jid@roomserver.com```
      to the "XMPP Control Room". Alternatively, leave the MUC room and send the message
      ```purge``` instead.


## Installation
Install the dependencies:
```bash
pip install -r requirements.txt
```

Edit config.yaml to set your usernames, passwords, and servers.

If you're using your own homeserver and you have more than a handful of
 XMPP contacts, you'll probably want to loosen the rate limits on your
 homeserver (see ```homeserver.yaml``` for synapse), or you'll have to
 wait multiple minutes while the bot creates a bunch of new rooms.

You should probably also set your Matrix client to auto-accept new room
 invitations for the first run of the bot, so you don't have to
 manually accept each invitation.

From the same directory as ```config.yaml```, run
```bash
python3 -m mxpp.main
```

**Dependencies:**

* python >=3.5 (written and tested with 3.5)
* [sleekXMPP](https://pypi.python.org/pypi/sleekxmpp/1.3.1)
* [matrix_client](https://github.com/matrix-org/matrix-python-sdk)
  (currently requires git version)
* [pyyaml](https://pypi.python.org/pypi/PyYAML/3.12)
* and their dependencies (dnspython, requests, others?)


## TODO

* Set bot's presence for each room individually
 (impossible with current Matrix m.presence API)
* Set bot's name for each room individually
 (waiting on support from synapse)
* Require higher-than-default power-level to speak in All-chat (i.e.,
only let the bot talk in all-chat)
 (waiting on matrix_client pull request)
