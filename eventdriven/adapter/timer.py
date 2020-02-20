# -*- coding: UTF-8 -*-
""" 定时信号发生器适配器 Timer

工作原理

+------------------------------------+
|           AdapterManager           |
|                      +---------+   |
|                      |  Timer  |   |
|                      |  x sec  |   |
|                      +---------+   |
+--------------------------|---------+
|                          |         |
|           EVT_DRI_TIMING |         |
|                          v         |
|            func  +-----------------+
|          +-------|  MappingManager |
|          |       +-----------------+
|          v                         |
+------------------------------------+
|                                    |
|            EventLoop               |
|                                    |
+------------------------------------+
"""

from .base import AbstractAdapter
from threading import Thread, Event

__all__ = ['Timer', 'EVT_DRI_TIMING']

EVT_DRI_TIMING = '|EVT|TIMING|'


class Timer(AbstractAdapter):
    """ 定时产生信号。

    :public method
        set_timing:     设置定时器的定时间隔。
        suspend:        挂起定时器的事件产生。
        resume:         恢复挂起的定时器。
    """

    def __init__(self, interval=None, toggle=EVT_DRI_TIMING):
        """
        :param
            interval    : 定时信号发生器事件间隔。
            toggle      : 定时发生的事件，默认EVT_DRI_TIMING
        """
        self.__timer_thread = None
        self._interval = interval
        self._close_evt = Event()
        self._no_suspend = Event()
        self._no_suspend.set()
        self.__toggle = toggle

    def set_timing(self, interval=None):
        """ 设置定时器信号发生间隔。单位秒
            设置为None即关闭定时器。
        若控制器已经启动了，修改间隔会在下一次信号发生前生效。
        """
        self._interval = interval
        if not interval:
            self._close_evt.set()
        elif self._parent.is_alive() and (not self.__timer_thread or not self.__timer_thread.is_alive()):
            self.__run__()

    def join(self):
        """ 等待线程结束。"""
        if self.__timer_thread and self.__timer_thread.is_alive():
            self.__timer_thread.join()

    def __run__(self):
        if self._interval:
            thread = Thread(target=self.__timing_thread, name=str(self._parent.name) + '-timer')
            self.__timer_thread = thread
            thread.start()

    def __timing_thread(self):
        """ 定时器信号发生线程。 """
        while True:
            self._no_suspend.wait()
            interval = self._interval
            # 避免无限阻塞，屏蔽掉值为None的间隔，而改为关闭定时器。
            if interval is None:
                break
            try:
                if self._close_evt.wait(interval):
                    # 定时器事件被设置说明是要关闭定时器。
                    self._close_evt.clear()
                    break
                # 发送定时信号
                self._parent.dispatch(self.__toggle, interval)
            except TypeError:
                break

    def __suspend__(self):
        self._no_suspend.clear()

    suspend = __suspend__

    def __resume__(self):
        self._no_suspend.set()

    resume = __resume__

    def __closing__(self):
        self._no_suspend.set()
        self._close_evt.set()


