import time
import datetime
import re
import sre_constants

from twisted.words.xish import domish
from sqlalchemy.orm import exc
from dbb_douban import DoubanClient

import models

all_commands={}

def __register(cls):
    c=cls()
    all_commands[c.name]=c

class CountingFile(object):
    """A file-like object that just counts what's written to it."""
    def __init__(self):
        self.written=0
    def write(self, b):
        self.written += len(b)
    def close(self):
        pass
    def open(self):
        pass
    def read(self):
        return None

class BaseCommand(object):
    """Base class for command processors."""

    def __get_extended_help(self):
        if self.__extended_help:
            return self.__extended_help
        else:
            return self.help

    def __set_extended_help(self, v):
        self.__extended_help=v

    extended_help=property(__get_extended_help, __set_extended_help)

    def __init__(self, name, help=None, extended_help=None):
        self.name=name
        self.help=help
        self.extended_help=extended_help

    def __call__(self, user, prot, args, session):
        raise NotImplementedError()

    def is_a_url(self, u):
        try:
            s=str(u)
            # XXX:  Any good URL validators?
            return True
        except:
            return False

class ArgRequired(BaseCommand):

    def __call__(self, user, prot, args, session):
        if self.has_valid_args(args):
            self.process(user, prot, args, session)
        else:
            prot.send_plain(user.jid_full, "Arguments required for %s:\n%s"
                % (self.name, self.extended_help))

    def has_valid_args(self, args):
        return args

    def process(self, user, prot, args, session):
        raise NotImplementedError()

class WatchRequired(BaseCommand):

    def __call__(self, user, prot, args, session):
        if self.has_valid_args(args):
            a=args.split(' ', 1)
            newarg=None
            if len(a) > 1: newarg=a[1]
            try:
                watch=session.query(models.Watch).filter_by(
                    url=a[0]).filter_by(user_id=user.id).one()
                self.process(user, prot, watch, newarg, session)
            except exc.NoResultFound:
                prot.send_plain(user.jid_full, "Cannot find watch for %s" % a[0])
        else:
            prot.send_plain(user.jid_full, "Arguments required for %s:\n%s"
                % (self.name, self.extended_help))

    def has_valid_args(self, args):
        return self.is_a_url(args)

    def process(self, user, prot, watch, args, session):
        raise NotImplementedError()

class StatusCommand(BaseCommand):

    def __init__(self):
        super(StatusCommand, self).__init__('status', 'Check your status.')

    def __call__(self, user, prot, args, session):
        rv=[]
        rv.append("Jid:  %s" % user.jid)
        rv.append("Jabber status:  %s" % user.status)
        rv.append("Notify status:  %s"
            % {True: 'Active', False: 'Inactive'}[user.active])
        if user.is_quiet():
            rv.append("All alerts are quieted until %s" % str(user.quiet_until))
        prot.send_plain(user.jid_full, "\n".join(rv))

__register(StatusCommand)


class HelpCommand(BaseCommand):

    def __init__(self):
        super(HelpCommand, self).__init__('help', 'You need help.')

    def __call__(self, user, prot, args, session):
        rv=[]
        if args:
            c=all_commands.get(args.strip().lower(), None)
            if c:
                rv.append("Help for %s:\n" % c.name)
                rv.append(c.extended_help)
            else:
                rv.append("Unknown command %s." % args)
        else:
            for k in sorted(all_commands.keys()):
                rv.append('%s\t%s' % (k, all_commands[k].help))
        prot.send_plain(user.jid_full, "\n".join(rv))

__register(HelpCommand)

class UnwatchCommand(WatchRequired):

    def __init__(self):
        super(UnwatchCommand, self).__init__('unwatch', 'Stop watching a page.')

    def process(self, user, prot, watch, args, session):
        session.delete(watch)
        prot.send_plain(user.jid_full, "Stopped watching %s" % watch.url)

#__register(UnwatchCommand)

class SayCommand(ArgRequired):
    
    def __init__(self):
        super(SayCommand, self).__init__('say', 'Say something.')

    def process(self, user, prot, args, session):
        if args:
            def onSuccess(value):
                prot.send_plain(user.get_jid_full(), "OK, you said: %s" %args) 
            def onError(err):
                prot.send_plain(user.get_jid_full(), "Error, send: %s failed" %args)
                print "addBroadcasting failed: %s" %str(err)
            DoubanClient.addBroadcasting(user.uid, user.key, user.secret, args).addCallbacks(
                callback=onSuccess,
                errback=lambda err: onError(err))
        else:
            prot.send_plain(user.get_jid_full(), "You say nothing :(")

__register(SayCommand)

class OnCommand(BaseCommand):
    def __init__(self):
        super(OnCommand, self).__init__('on', 'Enable notify.')

    def __call__(self, user, prot, args, session):
        user.active=True
        prot.send_plain(user.jid_full, "Enabled notify.")

__register(OnCommand)

class OffCommand(BaseCommand):
    def __init__(self):
        super(OffCommand, self).__init__('off', 'Disable notify.')

    def __call__(self, user, prot, args, session):
        user.active=False
        prot.send_plain(user.jid_full, "Disabled notify.")

__register(OffCommand)

class QuietCommand(ArgRequired):
    def __init__(self):
        super(QuietCommand, self).__init__('quiet', 'Temporarily quiet alerts.')
        self.extended_help="""Quiet alerts for a period of time.

Available time units:  m, h, d

Example, quiet for on hour:
  quiet 1h
"""

    def process(self, user, prot, args, session):
        if not args:
            prot.send_plain(user.jid_full, "How long would you like me to be quiet?")
            return
        m = {'m': 1, 'h': 60, 'd': 1440}
        parts=args.split(' ', 1)
        time=parts[0]
        match = re.compile(r'(\d+)([hmd])').match(time)
        if match:
            t = int(match.groups()[0]) * m[match.groups()[1]]
            u=datetime.datetime.now() + datetime.timedelta(minutes=t)

            user.quiet_until=u
            prot.send_plain(user.jid_full,
                "You won't hear from me again until %s" % str(u))
        else:
            prot.send_plain(user.jid_full, "I don't understand how long you want "
                "me to be quiet.  Try: quiet 5m")

__register(QuietCommand)