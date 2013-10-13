# -*- coding: utf-8 -*-

class NotifierHook < Redmine::Hook::Listener

  PROTOCOL='https'

  def controller_issues_new_after_save(context = {})
    @issue = context[:issue]
    @project = @issue.project
    @user = @issue.author
    if !@issue.description.empty?
      comment = "Comment: #{truncate_string(@issue.description)}"
    else
      comment = ""
    end
    url = "#{PROTOCOL}://#{Setting.host_name}/issues/#{@issue.id}"
    say(@project.identifier, "#{@user.login} created issue #{@issue.subject} #{comment}\n#{url}")
  end

  def controller_issues_edit_after_save(context = {})
    @issue = context[:issue]
    @project = @issue.project
    @journal = context[:journal]
    @user = @journal.user
    if !@journal.notes.empty?
      comment = "Comment: #{truncate_string(@journal.notes)}"
    else
      comment = ""
    end
    url = "#{PROTOCOL}://#{Setting.host_name}/issues/#{@issue.id}"
    if @issue.closed? == true
      say(@project.identifier, "#{@user.login} closed issue #{@issue.subject} #{comment}\n#{url}")
    elsif @issue.reopened? == true
      say(@project.identifier, "#{@user.login} reopened issue #{@issue.subject} #{comment}\n#{url}")
    else
      say(@project.identifier, "#{@user.login} updated issue #{@issue.subject} #{comment}\n#{url}")
    end
  end

  def controller_wiki_edit_after_save(context = {})
    @project = context[:project]
    @page = context[:page]
    @user = @page.content.author
    url = "#{PROTOCOL}://#{Setting.host_name}/projects/#{@project.identifier}/wiki/#{@page.title}"
    if !@page.content.comments.empty?
      comment = " (Comment: #{@page.content.comments})"
    else
      comment = ""
    end
    say(@project.identifier, "#{@user.login} edited the wiki page “#{@page.pretty_title}”#{comment} on #{@project.name}\n#{url}")
  end

private
  def say(project_name, message)
    begin
      Ruga.speak project_name, message
    rescue => e
      puts "Error while sending message to the bot: #{e.message}"
    end
  end

  def truncate_string(text, length = 45, end_string = '…')
    return if text == nil
    words = text.split()
    words[0..(length-1)].join(' ') + (words.length > length ? end_string : '')
  end
end
