# -*- coding: UTF-8 -*-
""" 子进程插件 Subprocess （父进程控制器部分）

工作原理：

            +--------------------------------+                    +--------------------------------+
            |  Parent Process                |                    |  Child Process                 |
            +--------------------------------+                    +--------------------------------+
  dispatch  |                                |                    |   <bri_worker>                 |
--------------------------->v----------------+                    +----------------+               |
   submit   |               |   Controller   |    child_channel   |   Controller   |  dispatch     |
            |               >---------------->>>>>>>>>>>>>>>>>>>>>>---------------->---------v     |
            |               |                                                      |         |     |
   Pending  |               |                                                      |         |     |
<---------------------------<----------------<<<<<<<<<<<<<<<<<<<<<<---------<------+         |     |
            |               |   Subprocess   |   parent_channel   |         ^                |     |
            |               |   PluginManager|                    |         |                v     |
            |               +----------------+                    +--------------------------------+
            |                                |                    |         ControllerPool         |
            |                                |                    |           <workers>            |
            +--------------------------------+                    +--------------------------------+
                                                                            ^               |
                                                                  >---------|               v
                                                                  |   +----------------------------+
                                                                  |   | Session                    |
                                                                  |   +----------------------------+
                                                                  |   | def func(*args, **kwargs): |
                                                                  |   |    ...                     |
                                                                  ^---+----------------------------+
                                                                   return


接口说明：

控制器补丁：

class Controller:
    def dispatch(self, evt, value=None, context=None, args=(), kwargs=None):
        # 派遣事件给子进程工作线程控制器池。

    def submit(self, function=None, args=(), kwargs=None, context=None):
        # 派遣处理函数给子进程工作线程。

    def pend(self):
        # 等待子进程工作线程控制器池任务队列取空。
        # 仅仅是工作线程任务队列被取完，并不意味着任务全部完成。

    def is_idle(self):
        # 返回子进程工作线程控制器池是否处于空闲状态。


插件方法：

class Subprocess(BasePlugin):
    @property
    def process(self):
        # 返回子进程的进程对象。

    def add_plugin(self, hdl, *args, **kwargs):
        # 为子进程通信桥控制器安装插件。

    def get_instance(self, hdl, *args, **kwargs):
        # 在子进程创建实例，并返回虚拟实例在父进程进行方法调用。




"""
from ._subprocess import (EVT_INSTANCE_INIT, EVT_ADD_PLUGIN, EVT_WORKER_INSTANCE_CALL,
                          EVT_WORKER_IS_IDLE, EVT_WORKER_PROPERTY_GET)
from ._subprocess import subprocess_main_thread, default_worker_initializer
from ..signal import (EVT_DRI_BEFORE, EVT_DRI_SHUTDOWN, EVT_DRI_RETURN, EVT_DRI_SUBMIT)
from .. import session
from .base import BasePlugin
from ..utils import Pending
from multiprocessing import Pipe, Process, Event as ProcessEvent, Semaphore
from threading import Lock as ThreadLock

__all__ = ['Subprocess', ]


def _create_process_channel_pairs():
    """ 创建进程Pipe的队列化通信通道对。"""
    p1, p2 = Pipe()
    return QueueifyConnection(p1, p2), QueueifyConnection(p2, p1)


class QueueifyConnection:
    """ 管道Pipe队列化的连接器。
    主要是为了实现与队列使用方法一致的接口。
    """
    __slots__ = '_p1', '_p2', '_empty', '_unfinished_tasks'

    def __init__(self, p1, p2):
        self._p1 = p1
        self._p2 = p2
        # 由于是进程间的数据同步，所以这里只能使用通过进程信号量来共享计数数据。
        # 在这里的信号量仅仅是用于计数put和get的差量。
        self._unfinished_tasks = Semaphore(0)
        # 空队列事件，这将用于join。
        self._empty = ProcessEvent()

    def put(self, value):
        # 队列数据计数+1
        self._unfinished_tasks.release()
        return self._p1.send(value)

    def get(self):
        ret = self._p2.recv()
        # 队列数据计数-1
        self._unfinished_tasks.acquire(False)
        # 为了实现方法join，当取出后队列的数据量为0，那么空队列事件置位。
        if not self._unfinished_tasks.get_value():
            self._empty.set()
        return ret

    def task_done(self):
        """ 只是从形式上统一队列的方法task_done。
        事实上对于管道队列化的连接器的这方法不会有什么操作。
        """

    def join(self):
        """ 等待队列化管道被取完。"""
        self._empty.wait()


class VirtualAttribute(object):
    """ 子进程实例在父进程的虚拟实例属性。 """
    def __init__(self, parent, name):
        self.__parent = parent
        self.__name = name

    @property
    def __parent__(self):
        """ 返回属性的父对象。"""
        return self.__parent

    @property
    def __chain__(self):
        """ 返回当前级别的虚拟调用链。"""
        return '%s.%s' % (self.__parent.__chain__, self.__name)

    def __getattr__(self, item):
        """ 获取下一级虚拟子级对象/属性。 """
        return VirtualAttribute(self, item)

    def __call__(self, *args, **kwargs):
        """ 虚拟方法的调用。就是一个类rpc。"""
        # 顶级父级就是实例对象，所以这里搜索顶级父级以到达实例对象。
        top = self.__parent
        while True:
            if isinstance(top, VirtualInstance):
                break
            top = top.__parent__

        return top.__parent__.method_call(self.__chain__, *args, **kwargs)

    def __str__(self):
        """ 返回实例属性的文本值。"""
        top = self.__parent
        while True:
            if isinstance(top, VirtualInstance):
                break
            top = top.__parent__
        pend = top.__parent__.property_get(self.__chain__)
        return pend.pend()[0]


class VirtualInstance(object):
    """ 子进程实例在父进程的虚拟实例。"""
    def __init__(self, parent, name):
        self.__parent = parent
        self.__name = name

    @property
    def __parent__(self):
        """ 返回实例对象的父级。实例对象的父级就是子进程插件。 """
        return self.__parent

    @property
    def __chain__(self):
        """ 返回当前实例对象名称。 """
        return self.__name

    def __getattr__(self, item):
        """ 获取下一级虚拟子级对象/属性。 """
        return VirtualAttribute(self, item)


class Subprocess(BasePlugin):
    """ 实现通过控制器来控制子进程进行处理事件。
    注意的是：子进程的事件处理映射表应该在初始化该插件实例中传递mapping。
    """
    def __init__(self, init_hdl=None, **init_kwargs):
        """
        :param
            init_hdl    : 子进程工作线程控制器初始化函数。处理函数需要返回控制器实例。
            init_args   : 初始化所需要的列表参数。
            init_kwargs : 初始化所需要的字典参数。

        默认 init_hdl = _subprocess_worker_init(mapping, context)

        """
        # 进程通信通道。
        self._parent_channel, self._child_channel = _create_process_channel_pairs()

        self._process = None
        # 同步进程事件。
        self._bridge_idle_event = ProcessEvent()
        self._workers_empty_queue_event = ProcessEvent()
        # 初始化进程同步事件。
        self._bridge_idle_event.set()
        # 子进程工作线程控制器池初始化函数和参数。
        if not init_hdl:
            init_hdl = default_worker_initializer

        self.__init_hdl = init_hdl
        self.__init_kwargs = init_kwargs

        # 被派遣处理过程中的事件id: Event。
        self._unfinished_events = {}
        # 事件ID计数
        self._event_count = 0
        # 预实例对象，允许直接把子进程的工作线程当成虚拟实例来使用。
        self.__instances = {'workers': VirtualInstance(self, 'workers')}

        self._lock = ThreadLock()

    @property
    def process(self):
        return self._process

    def add_plugin(self, hdl, *args, **kwargs):
        """ 子进程通信桥安装插件。 """
        return self._parent.dispatch(EVT_ADD_PLUGIN, args=(hdl, args, kwargs))

    def method_call(self, attr_chain, *args, **kwargs):
        """ 调用子进程实例方法。 """
        return self._parent.dispatch(EVT_WORKER_INSTANCE_CALL, args=(attr_chain, args, kwargs))

    def property_get(self, attr_chain):
        """ 请求获取子进程的属性文本值。"""
        return self._parent.dispatch(EVT_WORKER_PROPERTY_GET, args=(attr_chain,))

    def get_instance(self, hdl, *args, **kwargs):
        """ 返回在子进程中创建的实例在父进程中的对应的虚拟实例。"""
        ins = None
        cnt = 0
        # 搜索可用的变量名。
        while True:
            name = '_%s__%s' % (hdl.__name__, cnt)
            if name in self.__instances:
                cnt += 1
                continue
            ins = VirtualInstance(self, name)
            self.__instances[name] = ins
            break
        # 发送初始化实例事件给子进程。
        self._parent.dispatch(EVT_INSTANCE_INIT, args=(hdl, args, kwargs, name))

        return ins

    def __getitem__(self, item):
        """ 返回已定义的虚拟实例。 """
        return self.__instances[item]

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
            pending_id, value = session['val']
            # 响应事件返回Pending
            pend = self._unfinished_events.pop(pending_id)
            pend.set(value)
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
        def dispatch(evt, value=None, context=None, args=(), kwargs=None):
            with self._lock:
                # 为了实现父子进程直接的pending对象，这里为每一个事件赋予一个事件ID进行发送，
                # 当事件处理完成后将带有事件ID进行领取返回结果。
                pending_id = self._event_count
                self._event_count += 1
                # 准备事件ID到上下文。
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

        def is_idle():
            # """ 返回子进程通信桥是否处于空闲状态。"""
            """ 返回工作线程是否处于空闲状态。 """
            pending = dispatch(EVT_WORKER_IS_IDLE)
            return True if pending.pend()[0].lower() == 'true' else False

        def pend():
            """ 等待被父进程派遣的事件全部被子进程的工作线程接收。 """
            # 等待的顺序：
            #   1. 子进程通信桥控制器事件队列完全取出并且处于最后一个事件的转发过程中。
            #   2. 子进程通信桥控制器事件最后一个事件处理完毕，并且在之后的处于空闲状态。
            #   3. 工作线程任务队列被完全取完。
            self._child_channel.join()
            self._bridge_idle_event.wait()
            self._workers_empty_queue_event.wait()

        dispatch_super = self._parent.dispatch
        # 安装父子进程的任务派遣Pending等待。
        self._parent.dispatch = dispatch
        self._parent.submit = submit

        # 替换父进程控制器的事件处理通道为进程队列，以实现父子进程的通信。
        self._parent.event_channel = self._parent_channel
        # 为了实现父子进程的空闲状态的同步，patch控制器的方法is_idle、pend、suspend、is_suspended、resume
        # 这里不能直接改写父进程控制器的状态事件，否则会出现冲突的问题。
        self._parent.is_idle = is_idle
        self._parent.pend = pend
        # 为了方便操作子进程，这里直接将插件以属性添加到控制器
        self._parent.subprocess = self

    def __run__(self):
        channel_pairs = self._child_channel, self._parent_channel
        status_events = self._bridge_idle_event, self._workers_empty_queue_event
        self._process = Process(target=subprocess_main_thread,
                                args=(channel_pairs,
                                      status_events,
                                      self.__init_hdl, self.__init_kwargs))
        self._process.start()

    def __mapping__(self):
        return {
            EVT_DRI_BEFORE: self.__transfer__,
        }

    @staticmethod
    def __unique__():
        return True

