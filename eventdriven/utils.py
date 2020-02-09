# -*- coding: UTF-8 -*-

from collections import namedtuple
from threading import Event


class Pending(object):
    """ 事件执行的等待事件。 """
    __slots__ = '_event', '_returns'

    def __init__(self):
        self._event = Event()
        self._returns = [None]

    def pend(self, timeout=None):
        """ 等待事件完成，并返回事件的执行返回。"""
        if not self._event.wait(timeout):
            raise TimeoutError('事件返回等待超时!')
        return self.get_value()

    def set(self, value):
        """ 通知事件执行完毕，并且设置返回结果。 """
        self._returns = value
        self._event.set()

    def get_value(self):
        """ 返回事件返回响应值。 """
        return self._returns

    def __iter__(self):
        assert self._returns
        return iter(self._returns)


ForwardingPacket = namedtuple('ForwardingPacket', 'value context')


class Listener(object):
    __slots__ = 'channel', 'allow', 'name'

    def __init__(self, queue, allow, name=None):
        self.channel = queue
        self.allow = allow
        self.name = name

    def check(self, t):
        return t in self.allow

    def push(self, eid, value, context):
        """ 推送监听信息"""
        self.channel.put((eid, ForwardingPacket(value, context), {
            'lname': self.name
        }, (), {}))

    def __eq__(self, other):
        return type(other) is Listener and other.channel == self.channel
