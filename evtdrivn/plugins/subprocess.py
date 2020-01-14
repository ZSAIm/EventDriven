# -*- coding: UTF-8 -*-

from multiprocessing import Pipe, Process, Event
from .base import BasePlugin
from ..session import session
from ..signal import (EVT_DRI_BEFORE, EVT_DRI_AFTER, EVT_DRI_SHUTDOWN, EVT_DRI_RETURN, EVT_DRI_SUSPEND, EVT_DRI_OTHER,
                      EVT_DRI_SUBMIT)
from .. import Controller


def _create_process_channel_pairs():
    """ 创建进程Pipe的队列化通信通道对。"""
    p1, p2 = Pipe()
    return QueueifyConnection(p1, p2), QueueifyConnection(p2, p1)


class QueueifyConnection:
    """ 管道Pipe队列化的连接器。
    主要是为了实现与队列使用方法一致的接口。
    """
    __slots__ = '_p1', '_p2'

    def __init__(self, p1, p2):
        self._p1 = p1
        self._p2 = p2

    def put(self, value):
        return self._p1.send(value)

    def get(self):
        return self._p2.recv()

    def task_done(self):
        pass


def _subprocess_worker_init(mapping=None, context=None):
    """ 子进程工作线程控制器初始化。
    若有其他如添加插件需求可以重写该方法。
    需要返回控制器实例。
    """
    return Controller(mapping, context)


# 子进程控制器默认初始化程序。
default_worker_initializer = _subprocess_worker_init


class Subprocess(BasePlugin):
    """ 实现通过控制器来控制子进程进行处理事件。
    注意的是：子进程的事件处理映射表应该在初始化该插件实例中传递mapping。
    """
    def __init__(self, init_hdl=None, *init_args, **init_kwargs):
        """
        :param
            init_hdl    : 子进程工作线程控制器初始化函数。处理函数需要返回控制器实例。
            init_args   : 初始化所需要的列表参数。
            init_kwargs : 初始化所需要的字典参数。

        默认 init_hdl = _subprocess_worker_init(mapping, context)

        """
        self._parent_channel, self._child_channel = _create_process_channel_pairs()

        self._process = None
        self._idle = Event()
        self._no_suspend = Event()
        self._idle.set()
        self._no_suspend.set()
        if not init_hdl:
            init_hdl = default_worker_initializer

        self.__init_hdl = init_hdl
        self.__init_args = init_args
        self.__init_kwargs = init_kwargs

    @property
    def process(self):
        return self._process

    def __transfer__(self):
        """ 在事件处理之前拦截所有的事件处理函数，并转发事件给子进程。
        :param
            session['hdl_list']     : 事件处理函数组成的列表
            session['hdl_args']     : 事件处理函数列表参数
            session['hdl_kwargs']   : 事件处理函数字典参数
            session['event_ctx']    : 事件发生上下文
            session['orig_evt']     : 原目标事件
            session['evt']          : 发生的事件
            session['val']          : 发生事件传递的值
        """
        # 父进程的控制器的事件处理映射不同于子进程的事件处理映射，
        # 在没有定义响应事件的情况下，会处理默认事件。此时目标事件会被更改为EVT_DRI_OTHER，
        # 所以在事件转发的阶段，尝试获取原目标事件，避免错误的转发事件。
        try:
            evt = session['orig_evt']
        except KeyError:
            evt = session['evt']

        if evt == EVT_DRI_RETURN:
            self._parent.message(EVT_DRI_RETURN, session['val'], session['event_ctx'])
        else:
            # 拦截事件处理函数列表。
            session['hdl_list'].clear()
            self._child_channel.put((evt, session['val'], session['event_ctx'],
                                     session['hdl_args'], session['hdl_kwargs']))

        # 若是关闭控制器事件 EVT_DRI_SHUTDOWN，那么屏蔽该事件，
        # 而是应当在子进程将要关闭后发送关闭信号给父进程。
        # 这里通过判断事件EVT_DRI_SHUTDOWN的值来判断是否由子进程发送的关闭事件。
        if evt == EVT_DRI_SHUTDOWN and not session['val']:
            self._parent.skip()

    def __patch__(self):
        def is_idle():
            """ 返回子进程是否处于空闲状态。"""
            return self._idle.is_set()

        def pend():
            """ 等待当前事件的完成。 """
            self._idle.wait()

        def is_suspended():
            """ 返回是否被挂起。 """
            return not self._no_suspend.is_set()

        def suspend():
            """ 挂起控制器。 """
            # 为了加快挂起的操作
            self._no_suspend.clear()
            self._parent.dispatch(EVT_DRI_SUSPEND, True)

        def resume():
            """ 从挂起状态恢复。 """
            # 为了加快恢复挂起状态的操作
            self._no_suspend.set()
            self._parent.dispatch(EVT_DRI_SUSPEND, False)

        # 替换父进程控制器的事件处理通道为进程队列，以实现父子进程的通信。
        self._parent.event_channel = self._parent_channel
        # 为了实现父子进程的空闲状态的同步，patch控制器的方法is_idle、pend、suspend、is_suspended、resume
        # 这里不能直接改写父进程控制器的状态事件，否则会出现冲突的问题。
        self._parent.is_idle = is_idle
        self._parent.pend = pend
        self._parent.is_suspended = is_suspended
        self._parent.suspend = suspend
        self._parent.resume = resume
        # 为了方便操作子进程，这里直接将插件以属性添加到控制器
        self._parent.subprocess = self

    def __run__(self):
        channel_pairs = self._child_channel, self._parent_channel
        status_events = self._idle, self._no_suspend
        self._process = Process(target=_subprocess_main_thread,
                                args=(channel_pairs,
                                      status_events,
                                      self.__init_hdl, self.__init_args, self.__init_kwargs))
        self._process.start()

    def __mapping__(self):
        return {
            EVT_DRI_BEFORE: self.__transfer__,
        }

    @staticmethod
    def __unique__():
        return True


def _subprocess_main_thread(channel_pairs, status_events, init_hdl, init_args, init_kwargs):
    """ 运行在子进程模式下的主线程。"""

    def __return__():
        """ 转发返回消息给父进程。 """
        # 为了避免pickle，无法序列化处理的对象。
        # 字符串化所有的对象，以列表的形式返回消息。
        bri_worker.message(EVT_DRI_RETURN, [str(ret) for ret in session['returns']])

    def __shutdown__():
        """ 相互关闭。"""
        bri_worker.shutdown()
        worker.shutdown()

    def __suspend__():
        """ resume / suspend，父进程操作方法映射。"""
        if session['val']:
            worker.suspend()
        else:
            worker.resume()

    def __goto_work__(*args, **kwargs):
        """ 提交任务。 """
        try:
            evt = session['orig_evt']
        except KeyError:
            evt = session['evt']

        if evt == EVT_DRI_SUBMIT:
            context = session.__vars__
            func = context.pop('function')
            func_args = context.pop('args')
            func_kwargs = context.pop('kwargs')
            worker.submit(func, args=func_args, kwargs=func_kwargs, context=context)
            bri_worker.skip()
        else:
            worker.dispatch(evt, session['val'], args=args, kwargs=kwargs, context=session.__vars__)

    bri_worker = Controller(mapping={
        EVT_DRI_OTHER: __goto_work__,
        EVT_DRI_SUBMIT: __goto_work__,
        EVT_DRI_SUSPEND: __suspend__,
        EVT_DRI_SHUTDOWN: __shutdown__
    })
    # 通信线程的通道替换为父子进程之间的通信通道。
    bri_worker.event_channel, bri_worker.return_channel = channel_pairs

    worker = init_hdl(*init_args, **init_kwargs)
    # 为了同步父子进程的状态。
    idle_event, not_suspend = status_events
    setattr(worker, '_Controller__idle', idle_event)
    setattr(worker, '_Controller__no_suspend', not_suspend)
    # 添加必要的内部处理映射。
    worker.mapping.add(EVT_DRI_AFTER, __return__)
    worker.mapping.add(EVT_DRI_SHUTDOWN, __shutdown__)
    worker.run()
    bri_worker.run()

    bri_worker.wait()
    worker.wait()
    # 注意：这里需要传递值True，这将用于告诉父进程的控制器shutdown事件是子进程要求的关闭。
    # 这是因为父进程控制器的shutdown事件只是用于关闭子进程的控制器，
    # 而父进程的控制器需要完全等待子进程关闭后才进行的关闭操作。
    # 这才能保证了返回返回消息队列的完整。
    bri_worker.message(EVT_DRI_SHUTDOWN, True)
