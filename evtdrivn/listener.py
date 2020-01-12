# -*- coding: UTF-8 -*-

from collections import namedtuple

ForwardingPacket = namedtuple('ForwardingPacket', 'value context')


class Listener:
    def __init__(self, queue, allow, name=None):
        self._channel = queue
        self._allow = allow
        self._name = name

    @property
    def allow(self):
        return self._allow

    @property
    def name(self):
        return self._name

    def check(self, t):
        return t in self._allow

    def push(self, eid, value, context):
        """ 推送监听信息"""
        self._channel.put((eid, ForwardingPacket(value, context), {
            'lname': self._name
        }, (), {}))
