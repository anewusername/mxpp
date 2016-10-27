import logging

import sleekxmpp
from sleekxmpp.exceptions import IqError, IqTimeout


class ClientXMPP(sleekxmpp.ClientXMPP):
    roster_dict = {}
    jid_nick_map = {}

    def __init__(self, jid, password):
        sleekxmpp.ClientXMPP.__init__(self, jid, password)

        self.add_event_handler('session_start', self.session_start)
        self.add_event_handler('disconnected', self.disconnected)

        self.register_plugin('xep_0030')  # Service Discovery
        self.register_plugin('xep_0004')  # Data Forms
        self.register_plugin('xep_0060')  # PubSub
        self.register_plugin('xep_0199')  # XMPP Ping

        self.auto_authorize = True
        self.auto_subscribe = True

    def session_start(self, _event):
        try:
            try:
                self.send_presence()
            except IqError as err:
                logging.error('There was an error sending presence')
                logging.error(err.iq['error']['condition'])
                self.disconnect()

            try:
                self.get_roster()
            except IqError as err:
                logging.error('There was an error getting the roster')
                logging.error(err.iq['error']['condition'])
                self.disconnect()

        except IqTimeout:
            logging.error('Server is taking too long to respond')
            self.disconnect()

        logging.info('XMPP Logged in!')

    def disconnected(self):
        logging.info('XMPP Disconnected!')
