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

Using the redmine_rugamia plugin and UNIX sockets, it delivers realtime
notifications when an issue has been edited on the bug tracker.
"""

import xml.sax.saxutils
import urllib.request
import urllib.error
import configparser
import functools
import datetime
import argparse
import slixmpp
import getpass
import signal
import socket
import urllib
import shlex
import json
import stat
import xml
import sys
import re
import os

import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(levelname)-8s %(message)s')

class Configuration(object):
    def __init__(self, args):
        self.filename = args.config
        config = configparser.ConfigParser()
        config.read(self.filename)
        if 'keys' not in config:
            config.add_section('keys')
        if 'rooms' not in config:
            config.add_section('rooms')
        self.config = config

    def get_jid_key(self, jid):
        return self.config['keys'].get(jid, None)

    def set_jid_key(self, jid, key):
        self.config['keys'][jid] = key
        with open(self.filename, 'w') as configfile:
            self.config.write(configfile)

    def get_project_id(self, room):
        return int(self.config['rooms'][room])

class RedmineApi(object):
    def __init__(self, args, config):
        if args.api_format == 'xml':
            self.parse_issue = self.parse_issue_xml
            self.create_issue_data = self.create_issue_data_xml
        elif args.api_format == 'json':
            self.parse_issue = self.parse_issue_json
            self.create_issue_data = self.create_issue_data_json
        else:
            print("%s is not a valid redmine api format. It should be 'xml' or 'json'" % args.api_format)
            sys.exit(-1)
        self.format = args.api_format
        self.url = args.forge
        self.config = config

    def get_bug_information(self, number):
        uri = "%s/%s" % (self.url, "issues/%s.%s" % (number, self.format))
        response = urllib.request.urlopen(uri)
        if response.getcode() != 200:
            print("Response code: %s" % response.getcode())
            return None
        body = response.read()
        return self.parse_issue(body, number)

    def parse_issue_xml(self, body, number):
        issue = xml.etree.ElementTree.fromstring(body)
        res = {}
        res['status'] = issue.find('status').attrib.get('name')
        res['tracker'] = issue.find('tracker').attrib.get('name')
        res['author'] = issue.find('author').attrib.get('name')
        res['subject'] = issue.find('subject').text
        res['created_on'] = datetime.datetime.strptime(issue.find('created_on').text, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y/%m/%d %H:%M:%S")
        res['updated_on'] = datetime.datetime.strptime(issue.find('updated_on').text, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y/%m/%d %H:%M:%S")
        res['id'] = number
        res['url'] = "%s/issues/%s" % (self.url, number)
        return res

    def parse_issue_json(self, body, number):
        issue = json.loads(body.decode())['issue']
        issue['status'] = issue['status']['name']
        issue['tracker'] = issue['tracker']['name']
        issue['author'] = issue['author']['name']
        issue['subject'] = issue['subject']
        issue['created_on'] = issue['created_on']
        issue['updated_on'] = issue['updated_on']
        issue['url'] = "%s/issues/%s" % (self.url, number)
        issue['id'] = number
        return issue

    def create_issue(self, jid, room, title, body):
        uri = "%s/%s" % (self.url, "issues.%s" % (self.format))
        print(uri)
        key = self.config.get_jid_key(jid)
        if not key:
            return "No. Your jid is not associated with any login of the forge"
        request = urllib.request.Request(url=uri, data=self.create_issue_data(room, title, body),
                                         headers={'X-Redmine-API-Key': key,
                                                  'Content-type': 'application/%s'% (self.format)})
        print(request)
        response = urllib.request.urlopen(request)
        nb = json.loads(response.read().decode())['issue']['id']
        return "Bug created: %s/issues/%s" % (self.url, nb)

    def create_issue_data_json(self, room, title, body):
        data = json.dumps({'issue': { 'project_id': self.config.get_project_id(room), 'subject': title, 'description': body}}).encode()
        print(data)
        return data

class Bot(slixmpp.ClientXMPP):
    def __init__(self, args, api, config):
        self.redmine_api = api
        self.config = config
        print(self.redmine_api)
        jid, password, self.host, self.port = args.jid, getpass.getpass(), args.host, args.port
        slixmpp.ClientXMPP.__init__(self, jid, password)
        self.register_plugin('xep_0045')
        self.nick = args.nick or self.boundjid.node
        # List of room names, associated with a bool telling if the room is joined or not {roomname: bool}
        self.rooms = {name: False for name in args.rooms}
        # {room_names: [message, message]}
        self.messages_to_send_on_join = {}
        self.affiliations = {}
        self.jids = {}

        print("Connecting to %s" % (jid))

        self.add_event_handler('connected', self.on_connected)
        self.add_event_handler('disconnected', self.on_disconnected)
        self.add_event_handler('no_auth', self.on_failed_auth)
        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("groupchat_presence", self.on_groupchat_presence)
        self.add_event_handler("groupchat_message", self.on_groupchat_message)
        self.add_event_handler("message", self.on_message)

    def join_rooms(self):
        for room in self.rooms.keys():
            self.join_room(room)

    def join_room(self, room):
        self.plugin['xep_0045'].joinMUC(room, self.nick)

    def is_room_joined(self, room):
        return self.rooms.get(room, False)

    def start(self):
        self.connect((self.host or self.boundjid.host, self.port))

    def exit(self, sig, frame):
        self.disconnect()

    def send_message_to_jid(self, jid, message):
        message = message.strip()
        print("Actually sending [%s] to %s" % (message, jid))
        stanza = self.make_message(jid)
        stanza['type'] = 'chat'
        stanza['body'] = message
        stanza.send()

    def send_message_to_room(self, room, message):
        message = message.strip()
        if not self.is_room_joined(room):
            self.join_room(room)
            self.set_delayed_message(room, message)
        else:
            print("Actually sending [%s] to %s" % (message, room))
            stanza = self.make_message(room)
            stanza['type'] = 'groupchat'
            stanza['body'] = message
            stanza.enable('html')
            stanza['html']['body'] = htmlize(message)
            stanza.send()

    def set_delayed_message(self, room, message):
        print("Room %s is not joined, delaying message: [%s]" % (room, message))
        if room not in self.messages_to_send_on_join:
            self.messages_to_send_on_join[room] = [message]
        else:
            if len(self.messages_to_send_on_join[room]) < 10:
                self.messages_to_send_on_join[room].append(message)
            else:
                print("Message queue too long, dropping message.")

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

    def on_message(self, message):
        if message['type'] != 'chat':
            return
        if message['from'].bare in self.rooms:
            nick = message['from'].resource
            jid = self.jids.get(message['from'].bare, {}).get(nick, None)
        else:
            jid = message['from'].bare
        print(jid)
        key = message['body']
        self.config.set_jid_key(jid, key)
        self.send_message_to_jid(message['from'], "The key associated with the jid %s is now [%s]" % (jid, key))

    def on_groupchat_presence(self, presence):
        print(presence)
        room = presence['from'].bare
        affiliation = presence['muc']['affiliation']
        nick = presence['from'].resource
        jid = presence['muc']['jid'].bare
        if affiliation:
            if not room in self.affiliations:
                self.affiliations[room] = {}
            self.affiliations[room][nick] = affiliation
        if jid:
            if not room in self.jids:
                self.jids[room] = {}
            self.jids[room][nick] = jid
        print(self.affiliations)
        print(self.jids)
        if nick == self.nick:
            if presence['type'] == 'unavailable':
                self.on_groupchat_leave(room)
            if presence['type'] == 'available':
                print('Room %s joined.' % room)
                self.rooms[room] = True
                if room in self.messages_to_send_on_join:
                    delayed_messages = self.messages_to_send_on_join[room]
                    del self.messages_to_send_on_join[room]
                    for message in delayed_messages:
                        self.send_message_to_room(room, message)

    def get_affilation(self, room, nick):
        return self.affiliations.get(room, {}).get(nick, None)

    def on_groupchat_message(self, message):
        print(message)
        room = message['from'].bare
        nick = message['from'].resource
        if nick == self.nick:
            return
        if message['body'].startswith('!add '):
            jid = self.jids.get(room, {}).get(nick, None)
            if not jid:
                self.send_message_to_room(room, 'No: I cannot see your real JID.')
            affiliation = self.get_affilation(room, nick)
            if not affiliation or affiliation.lower() not in ['owner', 'admin', 'member']:
                self.send_message_to_room(room, 'No: permission denied.')
                return
            try:
                args = shlex.split(message['body'][len('!add '):])
            except ValueError as e:
                self.send_message_to_room(room, "No: %s" % e)
                return
            if len(args) != 2:
                self.send_message_to_room(room, "No: I need two arguments: The title and the description.")
                return
            message = self.redmine_api.create_issue(jid, room, args[0], args[1])
            self.send_message_to_room(room, message)
        else:
            for bug_number in re.findall('#(\d+)', message['body'])[:4]:
                print(bug_number)
                try:
                    info = self.redmine_api.get_bug_information(int(bug_number))
                except urllib.error.HTTPError as e:
                    self.send_message_to_room(room, "Bug %s not found: %s" % (bug_number, e))
                else:
                    print(info)
                    if info:
                        response = "Bug %(url)s – %(subject)s\n%(status)s – %(author)s – Created on: %(created_on)s" % info
                        self.send_message_to_room(room, response)

    def on_groupchat_leave(self, room):
        """
        Just keep track of the fact that we are not in the room anymore, so
        that we know that we need to rejoin it if we want to send a message
        in it, later.
        """
        print("Left room %s" % room)
        self.rooms[room] = False

def htmlize(text):
    text = xml.sax.saxutils.escape(text)
    text = re.sub(r'\++', lambda x: ("<span style='color:green'>%s</span>" % x.group(0)), text)
    text = re.sub(r'-+', lambda x: ("<span style='color:red'>%s</span>" % x.group(0)), text)
    text = text.replace('\n', '<br/>')
    return "<body xmlns='http://www.w3.org/1999/xhtml'><p>%s</p></body>" % text

def parse_arguments():
    parser = argparse.ArgumentParser(description='XMPP bot')

    parser.add_argument('jid', help='The JID used to authenticate the bot on the XMPP network')
    parser.add_argument('--host', help='The custom host to connect to.')
    parser.add_argument('--port', help='The custom port to connect to.', default=5222)
    parser.add_argument('--nick', help='The nick to use in MUC rooms')
    parser.add_argument('--socket', help='The path of the UNIX socket used to receive messages', default="/tmp/rugamia.socket")
    parser.add_argument('--format', dest="api_format", help='The format used by the redmine API', default="json")
    parser.add_argument('--forge', help='The forge where API requests are made (including http://)', default="http://redmine.org")
    parser.add_argument('--config', help='The path of the configuration file (API keys and project ids are kept in it)', default="./rugamia.cfg")
#    parser.add_argument('--key', help='The API key used to log on the forge (the account must be an admin to be able to impersonate other users)')
    parser.add_argument('rooms', nargs='+', help='The list of rooms to join')

    return parser.parse_args()

class RemoteUnixClient(object):
    """
    Represent the other end of a UNIX socket, we read data until EOF, and
    then we “parse” it to send the message into the correct room, using bot
    instance
    """
    def __init__(self, loop, fd, bot):
        self.bot = bot
        self.fd = fd
        self.data = b""
        self.loop = loop

    def on_eof(self):
        l = self.data.split(b"\n", 1)
        if len(l) != 2:
            print("Received message is malformed: %s" % (l))
        else:
            room, msg = l
            self.bot.send_message_to_room(room.decode(), msg.decode())
        self.loop.remove_reader(self.fd)

    def on_readable(self):
        data = self.fd.recv(2048)
        if not data:
            self.on_eof()
        else:
            self.data += data

def on_unix_acceptable(loop, fd, bot):
    c, addr = fd.accept()
    unix_client = RemoteUnixClient(loop, c, bot)
    loop.add_reader(c, functools.partial(unix_client.on_readable))

def on_exit(loop):
    print("Exiting…")
    loop.stop()

def main():
    # Parse command line arguments
    args = parse_arguments()
    config = Configuration(args)
    api = RedmineApi(args, config)

    # Create the XMPP bot (not yet connected or anything)
    bot = Bot(args, api, config)
    bot.start()
    loop = bot.loop

    # Create a listening UNIX socket on the given address
    unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    print("Binding unix socket on %s" % (args.socket))
    try: # Remove the socket from a previous run that didn’t exit cleanly
        os.unlink("%s" % (args.socket))
    except FileNotFoundError:
        pass
    unix_socket.bind("%s" % (args.socket))
    unix_socket.listen(42)
    # Let any process, running under any UID write into that socket
    os.chmod(args.socket, stat.S_IRWXU|stat.S_IRWXG|stat.S_IRWXO)
    loop.add_reader(unix_socket, functools.partial(on_unix_acceptable, loop, unix_socket, bot))

    # Catch the SIGINT signal to exit cleanly
    loop.add_signal_handler(signal.SIGINT, functools.partial(on_exit, loop))

    bot.process()

if __name__ == '__main__':
    main()
