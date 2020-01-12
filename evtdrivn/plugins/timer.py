# -*- coding: UTF-8 -*-
from evtdrivn.signal import EVT_DRI_TIMING
from .base import BasePlugin
from threading import Thread, Event


class Timer(BasePlugin):
    """ 定时产生信号。 """
    def __init__(self, interval=None, toggle=EVT_DRI_TIMING):
        """
        :param
            interval    : 定时信号发生器事件间隔
            toggle      : 定时发生的事件
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
        elif self._parent.is_alive():
            self.__run__()

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

    def __resume__(self):
        self._no_suspend.set()

    def __close__(self):
        self._no_suspend.set()
        self._close_evt.set()


