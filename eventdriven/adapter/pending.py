# -*- coding: UTF-8 -*-
""" 事件处理返回等待事件 Pending

工作原理

+------------------------------------+
|          AdapterManager            |
|                     +---------+    |
|                     | Pending |    |
|                     +---------+    |
+------------------------------------+
|                                    |
|           func   +-----------------+     EVT
|        +---------|  MappingManager |<------------ dispatch
|        |         +-----------------+——————————-—> Pending
|        |                           |   return
|        v                           |
+------------------------------------+
|                                    |     func
|           EventLoop                |<------------ submit
|                                    |------------> Pending
+------------------------------------+   return


接口说明：

控制器补丁：

class Controller:
    def dispatch(self, evt, value=None, context=None, args=(), kwargs=None):
        # 返回事件处理等待对象。

    def submit(self, function=None, args=(), kwargs=None, context=None):
        # 返回事件处理等待对象。

"""


from .base import AbstractAdapter
from ..signal import EVT_DRI_AFTER, EVT_DRI_SUBMIT
from ..session import session
from ..utils import Pending
from threading import RLock


__all__ = ['EventPending']


class EventPending(AbstractAdapter):
    """ 事件等待返回适配器。 """
    def __init__(self):
        # 由于一个控制器的线性执行，所以只需要一个列表来存储pending对象就行了，
        # 当执行完一个事件后，列表的第一个元素就是当前执行的任务。
        self._unfinished_events = {}
        # 加锁是为了保证pending返回事件的正确顺序
        self._lock = RLock()
        self._event_count = 0

    def __closed__(self):
        # 清除未决事件。
        with self._lock:
            while True:
                try:
                    self._unfinished_events.popitem()[1].set([None])
                except KeyError:
                    break

    def __patch__(self):
        def dispatch(evt, value=None, context=None, args=(), kwargs=None):
            with self._lock:
                pending_id = self._event_count
                self._event_count += 1
                context = dict(context or {})
                context['__pending_id'] = pending_id
                # 准备pending事件等待返回对象。
                pending = Pending()
                self._unfinished_events[pending_id] = pending
                # 控制器提交任务。
                dispatch_super(evt, value, context, args, kwargs)
                return pending

        def submit(function=None, args=(), kwargs=None, context=None):
            return dispatch(EVT_DRI_SUBMIT, function, context, args, kwargs)

        dispatch_super = self._parent.dispatch

        self._parent.dispatch = dispatch
        self._parent.submit = submit

    def __return__(self):
        """ 返回操作。"""
        pending_id = getattr(session, '__pending_id', None)
        if pending_id is not None:
            with self._lock:
                pend = self._unfinished_events.pop(pending_id)
                pend.set(session['returns'])

    def __mapping__(self):
        return {
            EVT_DRI_AFTER: self.__return__
        }


