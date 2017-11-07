import logging
from typing import Dict, Tuple, List
import sys
from queue import Queue

if sys.version_info[0] != 3 or sys.version_info[1] < 5:
    raise Exception('mxpp requires python >= 3.5')

import sleekxmpp
from sleekxmpp import stanza
import requests
import yaml

from matrix_client.client import MatrixClient
from matrix_client.errors import MatrixError
from matrix_client.room import Room as MatrixRoom
from mxpp.client_xmpp import ClientXMPP

CONFIG_FILE = 'config.yaml'

logging.basicConfig(level=logging.INFO,
                    format='%(levelname)-8s %(message)s')
logging.getLogger(sleekxmpp.__name__).setLevel(logging.ERROR)
logging.getLogger(requests.__name__).setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class BridgeBot:
    xmpp = None                # type: ClientXMPP
    matrix = None              # type: MatrixClient
    topic_room_id_map = None   # type: Dict[str, str]
    special_rooms = None       # type: Dict[str, MatrixRoom]
    special_room_names = None  # type: Dict[str, str]
    groupchat_flag = None      # type: str
    groupchat_jids = None      # type: List[str]

    users_to_invite = None      # type: List[str]
    matrix_room_topics = None   # type: Dict[str, str]
    matrix_server = None        # type: Dict[str, str]
    matrix_login = None         # type: Dict[str, str]
    xmpp_server = None          # type: Tuple[str, int]
    xmpp_login = None           # type: Dict[str, str]
    xmpp_roster_options = None  # type: Dict[str, bool]
    xmpp_groupchat_nick = None  # type: str

    send_messages_to_all_chat = True    # type: bool
    send_presences_to_control = True    # type: bool
    groupchat_mute_own_nick = True      # type: bool

    inbound_xmpp = None         # type: Queue

    exception = None            # type: Exception or None

    @property
    def bot_id(self) -> str:
        return self.matrix_login['username']

    def __init__(self, config_file: str=CONFIG_FILE):
        self.groupchat_jids = []
        self.topic_room_id_map = {}
        self.special_rooms = {
                'control': None,
                'all_chat': None,
                }
        self.special_room_names = {
                'control': 'XMPP Control Room',
                'all_chat': 'XMPP All Chat',
                }
        self.xmpp_roster_options = {}
        self.inbound_xmpp = Queue()

        self.load_config(config_file)

        self.matrix = MatrixClient(**self.matrix_server)
        self.xmpp = ClientXMPP(self.inbound_xmpp,
                               **self.xmpp_login,
                               **self.xmpp_roster_options)

        self.matrix.login_with_password(**self.matrix_login)

        # Recover existing matrix rooms
        for room in self.matrix.get_rooms().values():
            room.update_room_topic()
            topic = room.topic

            if topic in self.special_rooms.keys():
                logger.debug('Recovering special room: ' + topic)
                self.special_rooms[topic] = room

            elif topic.startswith(self.groupchat_flag):
                room_jid = topic[len(self.groupchat_flag):]
                self.groupchat_jids.append(room_jid)

        # Prepare matrix special rooms and their listeners
        for topic, room in self.special_rooms.items():
            if room is None:
                room = self.matrix.create_room()
            self.setup_special_room(room, topic)

        self.special_rooms['control'].add_listener(self.matrix_control_message, 'm.room.message')
        self.special_rooms['all_chat'].add_listener(self.matrix_all_chat_message, 'm.room.message')

        # Invite users to special rooms
        for room in self.special_rooms.values():
            for user_id in self.users_to_invite:
                room.invite_user(user_id)

        # Connect to XMPP and start processing XMPP events
        self.xmpp.connect(self.xmpp_server)
        self.xmpp.process(block=False)

        # Rejoin group chats
        logger.debug('Rejoining group chats')
        for room_jid in self.groupchat_jids:
            self.xmpp.plugin['xep_0045'].joinMUC(room_jid, self.xmpp_groupchat_nick)

        # Listen for Matrix events
        def exception_handler(e: Exception):
            self.exception = e

        self.matrix.start_listener_thread(exception_handler=exception_handler)

        logger.debug('Done with bot init')

    def shutdown(self):
        self.matrix.stop_listener_thread()
        self.xmpp.disconnect()

    def handle_inbound_xmpp(self):
        while self.exception is None:
            event = self.inbound_xmpp.get()

            if isinstance(event, sleekxmpp.Presence):
                handler = {
                        'available': self.xmpp_presence_available,
                        'unavailable': self.xmpp_presence_unavailable,
                }.get(event.get_type(), self.xmpp_unrecognized_event)

            elif isinstance(event, sleekxmpp.Message):
                handler = {
                        'normal': self.xmpp_message,
                        'chat': self.xmpp_message,
                        'groupchat': self.xmpp_groupchat_message,
                }.get(event.get_type(), self.xmpp_unrecognized_event)

            elif isinstance(event, sleekxmpp.Iq) and event.get_query() == 'jabber:iq:roster':
                handler = self.xmpp_roster_update

            else:
                handler = self.xmpp_unrecognized_event

            handler(event)
        raise self.exception

    def load_config(self, path: str):
        with open(path, 'r') as conf_file:
            config = yaml.load(conf_file)

        self.users_to_invite = config['matrix']['users_to_invite']
        self.matrix_room_topics = config['matrix']['room_topics']
        self.groupchat_flag = config['matrix']['groupchat_flag']

        self.matrix_server = config['matrix']['server']
        self.matrix_login = config['matrix']['login']
        self.xmpp_server = (config['xmpp']['server']['host'],
                            config['xmpp']['server']['port'])
        self.xmpp_login = config['xmpp']['login']
        self.xmpp_groupchat_nick = config['xmpp']['groupchat_nick']

        self.send_presences_to_control = config['send_presences_to_control']
        self.send_messages_to_all_chat = config['send_messages_to_all_chat']
        self.groupchat_mute_own_nick = config['groupchat_mute_own_nick']

        self.xmpp_roster_options = config['xmpp']['roster_options']

    def get_room_for_topic(self, jid: str) -> MatrixRoom:
        """
        Return the room corresponding to the given XMPP JID
        :param jid: bare XMPP JID, should not include the resource
        :return: Matrix room object for chatting with that JID
        """
        room_id = self.topic_room_id_map[jid]
        return self.matrix.get_rooms()[room_id]

    def get_unmapped_rooms(self) -> List[MatrixRoom]:
        """
        Returns a list of all Matrix rooms which are not a special room (e.g., the control room) and
        do not have a corresponding entry in the topic -> room map.
        :return: List of unmapped, non-special Matrix room objects.
        """
        special_room_ids = [r.room_id for r in self.special_rooms.values()]
        valid_room_ids = [v for v in self.topic_room_id_map.values()] + special_room_ids
        unmapped_rooms = [room for room_id, room in self.matrix.get_rooms().items()
                          if room_id not in valid_room_ids]
        return unmapped_rooms

    def get_empty_rooms(self) -> List[MatrixRoom]:
        """
        Returns a list of all Matrix rooms which are occupied by only one user
        (the bot itself).
        :return: List of Matrix rooms occupied by only the bot.
        """
        empty_rooms = [room for room in self.matrix.get_rooms().values()
                       if len(room.get_joined_members()) < 2]
        return empty_rooms

    def setup_special_room(self, room, topic: str):
        """
        Sets up a Matrix room with the requested topic and adds it to the self.special_rooms map.

        If a special room with that topic already exists, it is replaced in the special_rooms
         map by the new room.
        :param room: Room to set up
        :param topic: Topic for the room
        """
        room.set_room_topic(topic)
        room.set_room_name(self.special_room_names[topic])
        self.special_rooms[topic] = room

        logger.debug('Set up special room with topic {} and id'.format(
            str(room.topic), room.room_id))

    def create_mapped_room(self, topic: str, name: str=None) -> MatrixRoom or None:
        """
        Create a new room and add it to self.topic_room_id_map.

        :param topic: Topic for the new room
        :param name: (Optional) Name for the new room
        :return: Room which was created
        """
        if topic in self.groupchat_jids:
            logger.debug('Topic {} is a groupchat without its flag, ignoring'.format(topic))
            return None
        elif topic in self.topic_room_id_map.keys():
            room_id = self.topic_room_id_map[topic]
            room = self.matrix.get_rooms()[room_id]
            logger.debug('Room with topic {} already exists!'.format(topic))
        else:
            room = self.matrix.create_room()
            room.set_room_topic(topic)
            self.topic_room_id_map[topic] = room.room_id
            logger.info('Created mapped room with topic {} and id {}'.format(topic, str(room.room_id)))
            room.add_listener(self.matrix_message, 'm.room.message')

        if room.name != name:
            if name != "":
                room.set_room_name(name)
                room.set_user_profile(displayname=name)
            else:
                room.set_room_name(topic.split('@')[0])

        return room

    def leave_mapped_room(self, topic: str) -> bool:
        """
        Leave an existing, mapped room and remove it from self.topic_room_id_map.

        :param topic: Topic for room to leave
        :retrun: True if the room was left, False if the room was not found.
        """
        if topic in self.groupchat_jids:
            logger.debug('Topic {} is a groupchat without its flag, ignoring'.format(topic))
            return False

        if topic not in self.topic_room_id_map.keys():
            err_msg = 'Room with topic {} isn\'t mapped or doesn\'t exist'.format(topic)
            logger.warning(err_msg)
            return False

        if topic.startswith(self.groupchat_flag):
            # Leave the groupchat
            room_jid = topic[len(self.groupchat_flag):]
            if room_jid in self.groupchat_jids:
                self.groupchat_jids.remove(room_jid)
            logger.info('XMPP MUC leave: {}'.format(room_jid))
            self.xmpp.plugin['xep_0045'].leaveMUC(room_jid, self.xmpp_groupchat_nick)

        room = self.get_room_for_topic(topic)
        del self.topic_room_id_map[topic]
        room.leave()
        logger.info('Left mapped room with topic {}'.format(topic))
        return True

    def map_rooms_by_topic(self):
        """
        Add unmapped rooms to self.topic_room_id_map, and listen to messages from those rooms.

        Rooms whose topics are empty or do not contain an '@' symbol are assumed to be special
         rooms, and will not be mapped.
        """
        unmapped_rooms = self.get_unmapped_rooms()

        for room in unmapped_rooms:
            room.update_room_topic()

            logger.debug('Unmapped room {} ({}) [{}]'.format(room.room_id, room.name, room.topic))

            if room.topic is None or '@' not in room.topic:
                logger.debug('Leaving it as-is (special room, topic does not contain @)')
            else:
                self.topic_room_id_map[room.topic] = room.room_id
                room.add_listener(self.matrix_message, 'm.room.message')

    def matrix_control_message(self, room: MatrixRoom, event: Dict):
        """
        Handle a message sent to the control room.

        Does nothing unless a valid command is received:
          refresh  Probes the presence of all XMPP contacts, and updates the roster.
          purge    Leaves any ((un-mapped and non-special) or empty) Matrix rooms.
          joinmuc some@muc.com   Joins a muc
          leavemuc some@muc.com  Leaves a muc

        :param room: Matrix room object representing the control room
        :param event: The Matrix event that was received. Assumed to be an m.room.message .
        """
        # Always ignore our own messages
        if event['sender'] == self.bot_id:
            return

        logger.debug('matrix_control_message: {}  {}'.format(room.room_id, str(event)))

        if event['content']['msgtype'] == 'm.text':
            message_body = event['content']['body']
            logger.info('Matrix received control message: ' + message_body)

            message_parts = message_body.split()
            if len(message_parts) < 1:
                logger.warning('Received empty control message, ignoring')
                return

            if message_parts[0] == 'refresh':
                for jid in self.topic_room_id_map.keys():
                    self.xmpp.send_presence(pto=jid, ptype='probe')
                self.xmpp.send_presence()
                self.xmpp.get_roster()

            elif message_parts[0] == 'purge':
                self.special_rooms['control'].send_text('Purging unused rooms')

                # Leave from unwanted rooms
                for room in self.get_unmapped_rooms() + self.get_empty_rooms():
                    logger.info('Leaving room {r.room_id} ({r.name}) [{r.topic}]'.format(r=room))

                    if room.topic in self.topic_room_id_map.keys():
                        self.leave_mapped_room(room.topic)
                    else:
                        room.leave()

            elif message_parts[0] == 'joinmuc':
                if len(message_parts) < 2:
                    logger.warning('joinmuc command didn\'t specify a room, ignoring')
                    return

                room_jid = message_parts[1]
                logger.info('XMPP MUC join: {}'.format(room_jid))
                self.create_groupchat_room(room_jid)
                self.xmpp.plugin['xep_0045'].joinMUC(room_jid, self.xmpp_groupchat_nick)

            elif message_parts[0] == 'leavemuc':
                if len(message_parts) < 2:
                    logger.warning('leavemuc command didn\'t specify a room, ignoring')
                    return

                room_jid = message_parts[1]
                room_topic = self.groupchat_flag + room_jid

                success = self.leave_mapped_room(room_topic)
                if not success:
                    msg = 'Groupchat {} isn\'t mapped or doesn\'t exist'.format(room_jid)
                else:
                    msg = 'Left groupchat {}'.format(room_jid)
                self.special_rooms['control'].send_notice(msg)

    def matrix_all_chat_message(self, room: MatrixRoom, event: Dict):
        """
        Handle a message sent to Matrix all-chat room.

        Currently just sends a warning that nobody will hear your message.

        :param room: Matrix room object representing the all-chat room
        :param event: The Matrix event that was received. Assumed to be an m.room.message .
        """
        # Always ignore our own messages
        if event['sender'] == self.bot_id:
            return

        logger.debug('matrix_all_chat_message: {}  {}'.format(room.room_id, str(event)))

        room.send_notice('Don\'t talk in here! Nobody gets your messages.')

    def matrix_message(self, room: MatrixRoom, event: Dict):
        """
        Handle a message sent to a mapped Matrix room.

        Sends the message to the xmpp handle specified by the room's topic.

        :param room: Matrix room object representing the room in which the message was received.
        :param event: The Matrix event that was received. Assumed to be an m.room.message .
        """
        if event['sender'] == self.bot_id:
            return

        if room.topic in self.special_rooms.keys():
            logger.error('matrix_message called on special channel')

        logger.debug('matrix_message: {}  {}'.format(room.room_id, event))

        if event['content']['msgtype'] == 'm.text':
            message_body = event['content']['body']

            if room.topic.startswith(self.groupchat_flag):
                jid = room.topic[len(self.groupchat_flag):]
                message_type = 'groupchat'
            else:
                jid = room.topic
                message_type = 'chat'

            logger.info('Matrix received message to {} : {}'.format(jid, message_body))
            self.xmpp.send_message(mto=jid, mbody=message_body, mtype=message_type)

            # Possible that we're in a room that wasn't mapped
            if jid not in self.xmpp.jid_nick_map:
                logger.error('Received message in matrix room with topic {},'.format(jid) +
                              'which wasn\'t in the jid_nick_map')
            name = self.xmpp.jid_nick_map.get(jid, jid)

            if self.send_messages_to_all_chat:
                self.special_rooms['all_chat'].send_notice('To {} : {}'.format(name, message_body))

    def xmpp_message(self, message: Dict):
        """
        Handle a message received by the XMPP client.

        Sends the message to the relevant mapped Matrix room, as well as the Matrix all-chat room.

        :param message: The message that was received.
        :return:
        """
        logger.info('XMPP received {} : {}'.format(message['from'].full, message['body']))

        if message['type'] in ('normal', 'chat'):
            from_jid = message['from'].bare

            if from_jid not in self.xmpp.jid_nick_map.keys():
                logger.error('xmpp_message: JID {} NOT IN ROSTER!?'.format(from_jid))
                self.xmpp.get_roster(block=True)

            if from_jid in self.groupchat_jids:
                logger.warning('Normal chat message from a groupchat, ignoring...')
                return

            from_name = self.xmpp.jid_nick_map.get(from_jid, from_jid)

            room = self.get_room_for_topic(from_jid)
            room.send_text(message['body'])
            if self.send_messages_to_all_chat:
                self.special_rooms['all_chat'].send_text('From {}: {}'.format(from_name, message['body']))

    def xmpp_groupchat_message(self, message: Dict):
        """
        Handle a groupchat message received by the XMPP client.

        Sends the message to the relevant mapped Matrix room, as well as the Matrix all-chat room.

        :param message: The message that was received.
        :return:
        """
        logger.info('XMPP MUC received {} : {}'.format(message['from'].full, message['body']))

        if message['type'] == 'groupchat':
            from_jid = message['from'].bare
            from_name = message['mucnick']

            if self.groupchat_mute_own_nick and from_name == self.xmpp_groupchat_nick:
                return

            room = self.get_room_for_topic(self.groupchat_flag + from_jid)
            room.send_text(from_name + ': ' + message['body'])
            if self.send_messages_to_all_chat:
                self.special_rooms['all_chat'].send_text(
                    'Room {}, from {}: {}'.format(from_jid, from_name, message['body']))

    def create_groupchat_room(self, room_jid: str):
        room = self.create_mapped_room(topic=self.groupchat_flag + room_jid)
        if room_jid not in self.groupchat_jids:
            self.groupchat_jids.append(room_jid)
        for user_id in self.users_to_invite:
            room.invite_user(user_id)

    def xmpp_presence_available(self, presence: Dict):
        """
        Handle a presence of type "available".

        Sends a notice to the control channel.

        :param presence: The presence that was received.
        """
        logger.debug('XMPP received {} : (available)'.format(presence['from'].full))

        jid = presence['from'].bare
        if jid not in self.xmpp.jid_nick_map.keys():
            logger.error('xmpp_presence_available: JID {} NOT IN ROSTER!?'.format(jid))
            self.xmpp.get_roster(block=True)

        if self.send_presences_to_control:
            name = self.xmpp.jid_nick_map.get(jid, jid)
            self.special_rooms['control'].send_notice('{} available ({})'.format(name, jid))

    def xmpp_presence_unavailable(self, presence):
        """
        Handle a presence of type "unavailable".

        Sends a notice to the control channel.

        :param presence: The presence that was received.
        """
        logger.debug('XMPP received {} : (unavailable)'.format(presence['from'].full))

        jid = presence['from'].bare
        if jid not in self.xmpp.jid_nick_map.keys():
            logger.error('xmpp_presence_unavailable: JID {} NOT IN ROSTER!?'.format(jid))
            self.xmpp.get_roster(block=True)

        if self.send_presences_to_control:
            name = self.xmpp.jid_nick_map.get(jid, jid)
            self.special_rooms['control'].send_notice('{} unavailable ({})'.format(name, jid))

    def xmpp_roster_update(self, _event):
        """
        Handle an XMPP roster update.

        Maps all existing Matrix rooms, creates a new mapped room for each JID in the roster
        which doesn't have one yet, and invites the users specified in the config in to all the rooms.

        :param _event: The received roster update event (unused).
        """
        logger.debug('######### ROSTER UPDATE ###########')

        rjids = [jid for jid in self.xmpp.roster]
        if len(rjids) > 1:
            raise Exception('Not sure what to do with more than one roster...')

        roster0 = self.xmpp.roster[rjids[0]]
        self.xmpp.roster_dict = {jid: roster0[jid] for jid in roster0}
        roster = self.xmpp.roster_dict

        self.map_rooms_by_topic()

        # Create new rooms where none exist
        for jid, info in roster.items():
            if '@' not in jid:
                logger.warning('Skipping fake jid in roster: ' + jid)
                continue
            name = info['name']
            self.xmpp.jid_nick_map[jid] = name
            self.create_mapped_room(topic=jid, name=name)

        logger.debug('Sending invitations..')
        # Invite to all rooms
        for room in self.matrix.get_rooms().values():
            users_in_room = room.get_joined_members()
            for user_id in self.users_to_invite:
                if user_id not in users_in_room:
                    room.invite_user(user_id)

        logger.debug('######## Done with roster update #######')

    def xmpp_unrecognized_event(self, event):
        logger.error('Unrecognized event: {} || {}'.format(type(event), event))


def main():
    while True:
        try:
            bot = BridgeBot()
            bot.handle_inbound_xmpp()
        except Exception as e:
            bot.shutdown()
            logger.error('Fatal Exception: {}'.format(e))
            pass


if __name__ == "__main__":
    main()
