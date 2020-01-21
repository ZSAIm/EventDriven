# -*- coding: UTF-8 -*-

from .base import BasePlugin
from ..signal import EVT_DRI_AFTER, EVT_DRI_SUBMIT
from ..session import session
from threading import Lock
from ..utils import Pending


class EventPending(BasePlugin):
    """ 事件等待返回插件。 """
    def __init__(self):
        # 由于一个控制器的线性执行，所以只需要一个列表来存储pending对象就行了，
        # 当执行完一个事件后，列表的第一个元素就是当前执行的任务。
        self._unfinished_events = []
        # 加锁是为了保证pending返回事件的正确顺序
        self._lock = Lock()

    def __patch__(self):
        def dispatch(*args, **kwargs):
            with self._lock:
                pending = Pending()
                self._unfinished_events.append(pending)

                dispatch_super(*args, **kwargs)
                return pending

        def submit(function=None, args=(), kwargs=None, context=None):
            return dispatch(EVT_DRI_SUBMIT, function, context, args, kwargs)

        dispatch_super = self._parent.dispatch

        self._parent.dispatch = dispatch
        self._parent.submit = submit

    def __return__(self):
        """ 返回操作。"""
        pending = self._unfinished_events.pop(0)
        pending.set(session['returns'])

    def __mapping__(self):
        return {
            EVT_DRI_AFTER: self.__return__
        }


