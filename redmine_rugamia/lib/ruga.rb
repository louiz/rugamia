class Ruga
  @@socket_path = nil
  @@muc_server  = nil

  def self.speak(project_name, message)
    return if message.nil? || message.empty?

    load_options unless @@socket_path && @@muc_server

    begin
      socket = UNIXSocket.new(@@socket_path)
      socket.send(project_name + "@" + @@muc_server + "\n" + message, 0)
      socket.close()
    rescue
      puts "Error: sending the message to the bot failed. Make sure it is started."
    end

  end

  private
  def self.load_options
    options = YAML::load(File.open(File.join(Rails.root, 'plugins', 'redmine_rugamia', 'config', 'ruga.yml')))
    @@socket_path = options[Rails.env]['socket']
    @@muc_server = options[Rails.env]['muc_server']
  end

end
