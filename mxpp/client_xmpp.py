import logging
from typing import Dict
from queue import Queue

import sleekxmpp
from sleekxmpp.exceptions import IqError, IqTimeout

logger = logging.getLogger(__name__)


class ClientXMPP(sleekxmpp.ClientXMPP):
    roster_dict = {}            # type: Dict[str, str]
    jid_nick_map = {}           # type: Dict[str, str]
    inbound_queue = None          # type: Queue

    def __init__(self,
                 inbound_queue: Queue,
                 jid: str,
                 password: str,
                 auto_authorize: bool=True,
                 auto_subscribe: bool=True):
        self.inbound_queue = inbound_queue

        sleekxmpp.ClientXMPP.__init__(self, jid, password)

        self.add_event_handler('session_start', self.handle_session_start)
        self.add_event_handler('disconnected', self.handle_disconnected)
        self.add_event_handler('roster_update', self.handle_roster_update)
        self.add_event_handler('presence_available', self.handle_presence_available)
        self.add_event_handler('presence_unavailable', self.handle_presence_unavailable)
        self.add_event_handler('message', self.handle_message)
        self.add_event_handler('groupchat_message', self.handle_groupchat_message)

        self.register_plugin('xep_0030')  # Service Discovery
        self.register_plugin('xep_0004')  # Data Forms
        self.register_plugin('xep_0060')  # PubSub
        self.register_plugin('xep_0199')  # XMPP Ping
        self.register_plugin('xep_0045')  # Multi-User Chats (MUC)

        self.auto_authorize = auto_authorize
        self.auto_subscribe = auto_subscribe

    def handle_session_start(self, _event):
        try:
            try:
                self.send_presence()
            except IqError as err:
                logger.error('There was an error sending presence')
                logger.error(err.iq['error']['condition'])
                self.disconnect()

            try:
                self.get_roster(block=True)
            except IqError as err:
                logger.error('There was an error getting the roster')
                logger.error(err.iq['error']['condition'])
                self.disconnect()

        except IqTimeout:
            logger.error('Server is taking too long to respond')
            self.disconnect()

        logger.info('XMPP Logged in!')

    def handle_disconnected(self, _event):
        logger.info('XMPP Disconnected!')

    def handle_roster_update(self, roster):
        logger.info('XMPP Roster update')
        self.inbound_queue.put(roster)

    def handle_presence_available(self, presence):
        logger.debug('XMPP Received presence_available: {}'.format(presence))
        self.inbound_queue.put(presence)

    def handle_presence_unavailable(self, presence):
        logger.debug('XMPP Received presence_unavailable: {}'.format(presence))
        self.inbound_queue.put(presence)

    def handle_message(self, message):
        logger.debug('XMPP Received message: {}'.format(message))
        self.inbound_queue.put(message)

    def handle_groupchat_message(self, message):
        logger.debug('XMPP Received groupchat_message: {}'.format(message))
        self.inbound_queue.put(message)
