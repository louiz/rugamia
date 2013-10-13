# -*- coding: utf-8 -*-

require 'redmine'
require_dependency 'notifier_hook'
require_dependency 'zmq'

Redmine::Plugin.register :redmine_irc_notifications do
  name 'Ruĝamia Notifications plugin'
  author 'Florent Le Coz'
  description 'A plugin to send updates to a zmq IPC socket, read by rugamia bot, and then sent in XMPP MUCs.'
  version '1.0'
end
