require 'zmq'

class Ruga
  @@socket_path   = nil
  @@muc_server      = nil

  @@context = nil
  @@socket = nil
  @@connected = false
  def self.speak(project_name, message)
    return if message.nil? || message.empty?

    load_options unless @@socket_path && @@muc_server
    if !@@connected
      @@context = ZMQ::Context.new(1)
      @@socket = @@context.socket(ZMQ::PUSH)
      @@socket.connect("ipc://" + @@socket_path)
      @@connected = true
    end

    @@socket.send(project_name + "@" + @@muc_server, ZMQ::SNDMORE)
    @@socket.send(message)
  end

  private
  def self.load_options
    options = YAML::load(File.open(File.join(Rails.root, 'plugins', 'redmine_rugamia', 'config', 'ruga.yml')))
    @@socket_path = options[Rails.env]['socket']
    @@muc_server = options[Rails.env]['muc_server']
  end

end
