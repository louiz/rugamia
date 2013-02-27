#!/usr/bin/env python3
#
# Author: Florent Le Coz <louiz@poezio.eu>
#
# This file is part of Ruĝamia.
#
# Ruĝamia is free software: you can redistribute it and/or modify
# it under the terms of the zlib license. See the COPYING file.

"""
A simple XMPP bot joining one or more MUC rooms. It can automatically
respond to user requests and provide various informations from a redmine
service.

Using the redmine_rugamia plugin and IPC sockets, it delivers realtime
notifications when an issue has been edited on the bug tracker.
"""

import urllib.request
import urllib.error
import sleekxmpp
import argparse
import getpass
import urllib
import signal
import xml
import zmq
import sys
import re

class RedmineApi(object):
    def __init__(self, url):
        self.url = url

    def get_bug_information(self, number):
        uri = "%s/%s" % (self.url, "issues/%s.xml" % (number))
        response = urllib.request.urlopen(uri)
        if response.getcode() != 200:
            print("Response code: %s" % response.getcode())
            return None
        body = response.read()
        issue = xml.etree.ElementTree.fromstring(body)
        res = {}
        res['status'] = issue.find('status').attrib.get('name')
        res['tracker'] = issue.find('tracker').attrib.get('name')
        res['author'] = issue.find('author').attrib.get('name')
        res['subject'] = issue.find('subject').text
        res['created_on'] = issue.find('created_on').text
        res['updated_on'] = issue.find('updated_on').text
        res['id'] = number
        res['url'] = "%s/issues/%s" % (self.url, number)
        return res


class Bot(sleekxmpp.ClientXMPP):
    def __init__(self, args, api):
        self.redmine_api = api
        print(self.redmine_api)
        jid, password, self.host, self.port = args.jid, getpass.getpass(), args.host, args.port
        sleekxmpp.ClientXMPP.__init__(self, jid, password)
        self.register_plugin('xep_0045')
        self.nick = args.nick or self.boundjid.node
        # List of room names, associated with a bool telling if the room is joined or not {roomname: bool}
        self.rooms = {name: False for name in args.rooms}
        # {room_names: [message, message]}
        self.messages_to_send_on_join = {}

        print("Connecting to %s" % (jid))

        self.add_event_handler('connected', self.on_connected)
        self.add_event_handler('disconnected', self.on_disconnected)
        self.add_event_handler('no_auth', self.on_failed_auth)
        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("groupchat_presence", self.on_groupchat_presence)
        self.add_event_handler("groupchat_message", self.on_groupchat_message)

    def join_rooms(self):
        for room in self.rooms.keys():
            self.join_room(room)

    def join_room(self, room):
        self.plugin['xep_0045'].joinMUC(room, self.nick)

    def is_room_joined(self, room):
        return self.rooms.get(room, False)

    def start(self):
        self.connect((self.host or self.boundjid.host, self.port))
        self.process(block=False)

    def exit(self, sig, frame):
        self.disconnect()

    def send_message_to_room(self, room, message):
        if not self.is_room_joined(room):
            self.join_room(room)
            self.set_delayed_message(room, message)
        else:
            print("Actually sending [%s] to %s" % (message, room))
            self.send_message(mto=room, mbody=message, mtype='groupchat')

    def set_delayed_message(self, room, message):
        print("Room %s is not joined, delaying message: [%s]" % (room, message))
        if room not in self.messages_to_send_on_join:
            self.messages_to_send_on_join[room] = [message]
        else:
            self.messages_to_send_on_join[room].append(message)

    def on_connected(self, event):
        print("Connected: %s" % event)

    def on_disconnected(self, event):
        print("Disconnecting")

    def on_failed_auth(self, event):
        print("Authentication failed: %s" % event)

    def on_session_start(self, event):
        print("Session started: %s" % event)
        print("The full JID is %s" % self.boundjid.full)
        self.join_rooms()

    def on_groupchat_presence(self, presence):
        if presence['from'].resource == self.nick:
            room = presence['from'].bare
            if presence['type'] == 'unavailable':
                self.on_groupchat_leave(room)
            else:
                print('Room %s joined.' % room)
                if room in self.rooms:
                    self.rooms[room] = True
                if room in self.messages_to_send_on_join:
                    delayed_messages = self.messages_to_send_on_join[room]
                    del self.messages_to_send_on_join[room]
                    for message in delayed_messages:
                        self.send_message_to_room(room, message)

    def on_groupchat_message(self, message):
        if message['from'].resource == self.nick:
            return
        room = message['from'].bare
        for bug_number in re.findall('#(\d+)', message['body']):
            print(bug_number)
            try:
                info = self.redmine_api.get_bug_information(int(bug_number))
            except urllib.error.HTTPError:
                self.send_message_to_room(room, "Bug %s not found." % bug_number)
            else:
                print(info)
                if info:
                    response = "Bug %(url)s - %(subject)s\n%(status)s - %(author)s - Created on: %(created_on)s" % info
                    self.send_message_to_room(room, response)
        print(message['body'])

    def on_groupchat_leave(self, room):
        print("Left room %s" % room)


def parse_arguments():
    parser = argparse.ArgumentParser(description='XMPP bot')

    parser.add_argument('jid', help='The JID used to authenticate the bot on the XMPP network')
    parser.add_argument('--host', help='The custom host to connect to.')
    parser.add_argument('--forge', help='The url of the forge’s main page.')
    parser.add_argument('--port', help='The custom port to connect to.', default=5222)
    parser.add_argument('--nick', help='The nick to use in MUC rooms')
    parser.add_argument('--socket', help='The IPC file used to receive messages')
    parser.add_argument('rooms', nargs='+', help='The list of rooms to join')

    return parser.parse_args()

def main():
    args = parse_arguments()

    api = RedmineApi(args.forge)

    bot = Bot(args, api)
    signal.signal(signal.SIGINT, bot.exit)

    ctx = zmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.connect("ipc:///tmp/rugamia.ipc")
    socket.setsockopt_string(zmq.SUBSCRIBE, "")

    bot.start()

    while True:
        try:
            res = socket.recv_multipart()
        except KeyboardInterrupt:
            return
        else:
            if len(res) != 2:
                print("Wrong message received on IPC socket")
            else:
                bot.send_message_to_room(res[0].decode(), res[1].decode())

if __name__ == '__main__':
    main()
