from pyaib.plugins import plugin_class, observe
from collections import deque
from random import randint
from time import time, sleep
import re


@plugin_class
@plugin_class.requires('db')
class Bucket(object):
    def __init__(self, context, config):
        self.owner = config.owner
        self.ignore = config.ignore
        self.channels = dict((c.keys()[0], c.values()[0])
                             for c in config.channels)
        self.channels_names = [e for e in self.channels]
        self.buckets_names = set(self.channels.values())
        self.dbs = dict((e, context.db.get(e)) for e in self.buckets_names)
        self.last_lines = dict((k, {}) for k
                               in self.channels_names)
        self.last_in = {}
        self.last_out = {}
        self.replying = False

    @observe('IRC_ONCONNECT')
    def onconnect(self, irc_c):
        for channel in self.channels_names:
            irc_c.JOIN(channel)

    def is_admin(self, msg):
        if msg.sender.hostname == self.owner:
            return True

    def reply(self, msg, message):
        if not message or message.strip() == "":
            return

        while self.replying:
            sleep(0.1)

        self.replying = True
        msg.reply(message)
        sleep(1)
        self.replying = False

    def add_line(self, channel, sender, line):
        if sender not in self.last_lines[channel]:
            self.last_lines[channel][sender] = deque(maxlen=100)

        self.last_lines[channel][sender].appendleft(line)

    def get_line(self, channel, sender, needle):
        if sender in self.last_lines[channel]:
            for line in self.last_lines[channel][sender]:
                if needle in line:
                    return line
        return None

    def what_was(self, channel):
        if channel in self.last_out:
            last = self.last_out[channel]
            return "That was {0} #{1!s} ({2} {3})".format(last["key"],
                                                          last["id"],
                                                          last["verb"],
                                                          last["store"])

    def undo_last(self, msg):
        if msg.target in self.last_in:
            last = self.last_in[msg.target]
            if self.is_admin(msg) or last["nick"] == msg.sender:
                self.last_in[msg.target] = None
                return self._delete(msg, last["key"], last["id"])

    def delete_all(self, msg, key):
        if self.is_admin(msg):
            try:
                self._delete_all(msg, key)
                return "Deleted all {0}".format(key)
            except:
                return "No"

    def _delete_all(self, msg, key):
        self.dbs[self.channels[msg.target]].delete(key.strip().lower())

    def delete(self, msg, key, idx):
        if self.is_admin(msg):
            return self._delete(msg, key, idx)

    def _delete(self, msg, key, idx):
        idx = int(idx)
        items = self.dbs[self.channels[msg.target]].get(key.strip().lower())
        if items.value:
            if idx < len(items.value):
                store = list(items.value)[idx]
                verb = items.value[store]["verb"]
                del items.value[store]
                items.commit()
                self.last_in[msg.target] = None
                return "Deleted {0} #{1!s} ({2} {3})".format(key, idx,
                                                             verb, store)
            else:
                return "Wrong #ID"

    def add_item(self, channel, nick, key, verb, store, prev=None):
        items = self.dbs[self.channels[channel]].get(key)
        if items.value:
            if store in items.value:
                return "I already knew that!"

            if len(items.value) > 0 and verb == "<alias>":
                return "Can't alias non-empty fact"

            for e in items.value:
                if items.value[e]["verb"] == "<alias>":
                    if key != prev:
                        return self.add_item(channel, nick, key, verb, store,
                                             key)

            items.value[store.strip()] = {"verb": verb, "nick": nick,
                                          "when": int(time()), "key": key}
            items.commit()
            self.last_in[channel] = {"id": len(items.value), "store": store,
                                     "nick": nick}
            return "Okay, {0}".format(nick)
        else:
            self.dbs[self.channels[channel]].set(key,
                                                 {store: {"verb": verb,
                                                          "nick": nick,
                                                          "when": int(time())}})
            self.last_in[channel] = {"id": 0, "store": store,
                                     "nick": nick, "key": key}
            return "Okay, {0}".format(nick)

    def get_item(self, channel, sender, key, strip_action=False, prev=None):
        key = key.strip()
        key = key.lower()
        items = self.dbs[self.channels[channel]].get(key)
        if items.value:
            idx = randint(0, len(items.value) - 1)
            store = list(items.value)[idx]
            item = items.value[store]

            if store:
                store = store.replace("$who", sender)

                self.last_out[channel] = {"key": key.strip(), "id": idx,
                                          "store": store, "verb": item["verb"]}
                if item["verb"] == "<reply>":
                    return store
                elif item["verb"] == "<action>":
                    if strip_action:
                        return "/me {0}".format(store)
                    else:
                        return "\x01ACTION {0} \x01".format(store)
                elif item["verb"] == "<alias>":
                    if key != prev:
                        return self.get_item(channel, sender, store,
                                             strip_action, key)
                else:
                    return "{} {} {}".format(key, item["verb"],
                                             store)

    @observe('IRC_MSG_PRIVMSG')
    def parse_msg(self, irc_c, msg):
        interesting = (msg.reply_target in self.channels_names
                       and msg.sender not in self.ignore)
        if interesting:
            m = msg.message.strip()

            if m.startswith(irc_c.botnick):
                store = None
                post = ""

                found = re.findall(irc_c.botnick + r"(:|,) (.+) is (.+)", m)
                if found:
                    key = found[0][1]
                    verb = "is"
                    store = found[0][2]

                found = re.findall(irc_c.botnick + r"(:|,) (.+) are (.+)", m)
                if found:
                    key = found[0][1]
                    verb = "are"
                    store = found[0][2]

                found = re.findall(irc_c.botnick + r"(:|,) (.+) <(.*)> (.+)", m)
                if found:
                    key = found[0][1]
                    store = found[0][3]

                    if found[0][2] == "reply":
                        verb = "<reply>"
                    elif found[0][2] == "action":
                        verb = "<action>"
                    elif found[0][2] == "alias":
                        if not self.is_admin(msg):
                            return
                        verb = "<alias>"
                    else:
                        verb = found[0][2]

                found = re.findall(irc_c.botnick + r"(:|,) remember ([^\s]+) (.+)", m)
                if found:
                    line = self.get_line(msg.target, found[0][1], found[0][2])
                    if line:
                        key = found[0][1] + " quotes"
                        verb = "<reply>"
                        store = "<" + found[0][1] + "> " + line
                        post = ", remembered {0} saying {1}".format(found[0][1],
                                                                    line)
                    else:
                        return self.reply(msg, "I don't see it :(")

                found = re.findall(irc_c.botnick + r"(:|,) combine (\d) (.+)", m)
                if found:
                    times = int(found[0][1])
                    key = found[0][2].strip()

                    combination = " ".join([self.get_item(msg.target,
                                                          msg.sender,
                                                          key,
                                                          strip_action=True)
                                            for e in range(times)])
                    return self.reply(msg, combination)

                found = re.findall(irc_c.botnick + r"(:|,) what was that\?", m)
                if found:
                    last = self.what_was(msg.target)
                    if last:
                        return self.reply(msg, last)
                    else:
                        return

                found = re.findall(irc_c.botnick + r"(:|,) delete (.+) #(\d+)", m)
                if found:
                    key = found[0][1]
                    idx = found[0][2]
                    return self.reply(msg, self.delete(msg, key, idx))

                found = re.findall(irc_c.botnick + r"(:|,) undo last", m)
                if found:
                    return self.reply(msg, self.undo_last(msg))

                if store:
                    key = key.lower()
                    self_key = (key == msg.sender
                                or key == "{0} quotes".format(msg.sender))
                    if self_key:
                        return self.reply(msg, "Editing your own factoids?")
                    result = self.add_item(msg.target, msg.sender, key, verb,
                                           store.strip())
                    return self.reply(msg, result + post)
                else:
                    reply = self.get_item(msg.target, msg.sender,
                                          m[len(irc_c.botnick) + 2:])
                    if reply:
                        return self.reply(msg, reply)
                    else:
                        return self.reply(msg, "What?")

            else:
                parts = msg.message.split(":")
                parts.reverse()
                message = parts[0].strip()
                reply = self.get_item(msg.target, msg.sender, message)

                if reply:
                    self.reply(msg, reply)
                else:
                    self.add_line(msg.target, msg.sender, msg.message)
