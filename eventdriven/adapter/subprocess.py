# -*- coding: UTF-8 -*-
""" 子进程适配器 Subprocess （父进程控制器部分）

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


适配器方法：

class Subprocess(BasePlugin):
    @property
    def process(self):
        # 返回子进程的进程对象。

    def add_plugin(self, hdl, *args, **kwargs):
        # 为子进程通信桥控制器安装适配器。

    def get_instance(self, hdl, *args, **kwargs):
        # 在子进程创建实例，并返回虚拟实例在父进程进行方法调用。

    def del_instance(self, instance):
        # 删除子进程下的实例（注意这只是删除在全局的标记）

    def method_call(self, attr_chain, *args, **kwargs):
        #调用子进程实例方法。

    def property_get(self, attr_chain):
        # 请求获取子进程的属性文本值。

    def __getitem__(self, item):
        # 返回已定义的虚拟实例。

    def __setitem__(self, key, value):
        # 添加引用实例对象。



"""
from ._subprocess import (EVT_INSTANCE_INIT, EVT_ADD_ADAPTER, EVT_WORKER_INSTANCE_CALL, EVT_SUBPROCESS_SHUTDOWN,
                          EVT_WORKER_PROPERTY_GET, EVT_INSTANCE_DEL, EVT_WORKER_GET_ITEM, EVT_WORKER_PREPARE)
from ._subprocess import subprocess_main_thread, default_worker_initializer
from .pending import EventPending
from ..signal import (EVT_DRI_BEFORE, EVT_DRI_OTHER, EVT_DRI_RETURN,
                      EVT_DRI_SUBMIT, EVT_DRI_ERROR, EVT_DRI_CLEAN)
from .base import AbstractAdapter
from ..session import session
from multiprocessing import Pipe, Process, Event as ProcessEvent, Semaphore, Lock as ProcessLock
from threading import Lock as ThreadLock
import pickle

__all__ = [
    'Subprocess', 'EVT_WORKER_PREPARE',
    'VirtualAttribute', 'VirtualInstance', 'create_process_channel_pairs', 'QueueifyPipeConnection'
]


def create_process_channel_pairs():
    """ 创建进程Pipe的队列化通信通道对。"""
    p1, p2 = Pipe()
    return QueueifyPipeConnection(p1, p2), QueueifyPipeConnection(p2, p1)


class QueueifyPipeConnection:
    """ 管道Pipe队列化的连接器。
    主要是为了实现与队列使用方法一致的接口。
    """
    __slots__ = '_p1', '_p2', '_empty', '_unfinished_tasks', '_lock'

    def __init__(self, p1, p2):
        self._p1 = p1
        self._p2 = p2
        # 由于是进程间的数据同步，所以这里只能使用通过进程信号量来共享计数数据。
        # 在这里的信号量仅仅是用于计数put和get的差量。
        self._unfinished_tasks = Semaphore(0)
        # 空队列事件，这将用于join。
        self._empty = ProcessEvent()
        self._lock = ProcessLock()

    def put(self, value):
        with self._lock:
            # 队列数据计数+1
            self._unfinished_tasks.release()
            self._empty.clear()
        self._p1.send(value)

    def get(self):
        return self._p2.recv()

    def task_done(self):
        """ 完成一项任务。"""
        with self._lock:
            # 队列数据计数-1
            self._unfinished_tasks.acquire(False)
            # 为了实现方法join，当取出后队列的数据量为0，那么空队列事件置位。
            if not self._unfinished_tasks.get_value():
                self._empty.set()

    def join(self):
        """ 等待队列化管道被取完。"""
        self._empty.wait()

    def empty(self):
        """ 返回队列未完成任务是否为空。"""
        return self._empty.is_set()


def _get_virtual_attribute_top(self):
    top = self.__parent
    while True:
        if isinstance(top, VirtualInstance):
            break
        top = top.__parent__
    return top.__parent__


class VirtualAttribute:
    """ 子进程实例在父进程的虚拟实例属性。 """
    def __init__(self, parent, name):
        self.__parent = parent
        self.__name = name
        self.__attrs = {}

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
        if item not in self.__attrs:
            self.__attrs[item] = VirtualAttribute(self, item)
        return VirtualAttribute(self, item)

    def __call__(self, *args, **kwargs):
        """ 虚拟方法的调用。就是一个类rpc。"""
        # 顶级父级就是实例对象，所以这里搜索顶级父级以到达实例对象。
        return _get_virtual_attribute_top(self).method_call(self.__chain__, *args, **kwargs).pending()[0]

    def __getitem__(self, item):
        """ 返回__getitem__。"""
        return _get_virtual_attribute_top(self).item_get(self.__chain__, item).pending()[0]

    def __value__(self, sub_name=None):
        """ 返回实例属性的文本值。"""
        if sub_name is None:
            return _get_virtual_attribute_top(self).property_get(self.__chain__).pending()[0]
        else:
            return _get_virtual_attribute_top(self).property_get('%s.%s' % (self.__chain__, sub_name)).pending()[0]


class VirtualInstance:
    """ 子进程实例在父进程的虚拟实例。"""
    def __init__(self, parent, name):
        self.__parent = parent
        self.__name = name
        self.__attrs = {}

    @property
    def __parent__(self):
        """ 返回实例对象的父级。实例对象的父级就是子进程适配器。 """
        return self.__parent

    @property
    def __name__(self):
        """ 返回实例的名称。"""
        return self.__name

    @property
    def __chain__(self):
        """ 返回当前实例对象名称。 """
        return self.__name

    def __getattr__(self, item):
        """ 获取下一级虚拟子级对象/属性。 """
        if item not in self.__attrs:
            self.__attrs[item] = VirtualAttribute(self, item)
        return self.__attrs[item]


class Subprocess(AbstractAdapter):
    """ 实现通过控制器来控制子进程进行处理事件。
    注意的是：子进程的事件处理映射表应该在初始化该适配器实例中传递mapping。
    """
    def __init__(self, init_hdl=None, **init_kwargs):
        """
        :param
            init_hdl    : 子进程工作线程控制器初始化函数。处理函数需要返回控制器实例。
            init_args   : 初始化所需要的列表参数。
            init_kwargs : 初始化所需要的字典参数。

        默认 default_worker_initializer = _subprocess_worker_initializer(mapping, context)

        """
        # 进程通信通道。
        self._parent_channel, self._child_channel = create_process_channel_pairs()

        self._process = None
        # 同步进程事件。
        self._workers_empty_queue_event = ProcessEvent()
        self._workers_idle_event = ProcessEvent()
        # 初始化进程同步事件。
        self._workers_empty_queue_event.set()
        self._workers_idle_event.set()
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
        self.instances = {'workers': VirtualInstance(self, 'workers'),
                          'bri_worker': VirtualInstance(self, 'bri_worker')}

        self._lock = ThreadLock()

    @property
    def process(self):
        return self._process

    def Adapter(self, hdl, args=(), kwargs=None):
        """ 子进程通信桥安装适配器。 """
        kwargs = kwargs or {}
        return self._parent.dispatch(EVT_ADD_ADAPTER, args=(hdl, args, kwargs))

    def del_instance(self, instance):
        """删除子进程下的实例（注意这只是删除在全局的标记）"""
        assert isinstance(instance, VirtualInstance)
        self._parent.dispatch(EVT_INSTANCE_DEL, args=(instance.__name__,))
        del self.instances[instance.__name__]

    def get_instance(self, hdl, *args, **kwargs):
        """ 返回在子进程中创建的实例在父进程中的对应的虚拟实例。"""
        if isinstance(hdl, str):
            return self[hdl]

        ins = None
        cnt = 0
        # 搜索可用的变量名。
        while True:
            name = '_%s__%s' % (hdl.__name__, cnt)
            if name in self.instances:
                cnt += 1
                continue
            ins = VirtualInstance(self, name)
            self.instances[name] = ins
            break
        # 发送初始化实例事件给子进程。
        self._parent.dispatch(EVT_INSTANCE_INIT, args=(hdl, args, kwargs, name))

        return ins

    def method_call(self, chain, *args, **kwargs):
        """ 调用子进程实例方法。 """
        return self._parent.dispatch(EVT_WORKER_INSTANCE_CALL, args=(chain, args, kwargs))

    def property_get(self, chain):
        """ 请求获取子进程的属性文本值。"""
        return self._parent.dispatch(EVT_WORKER_PROPERTY_GET, args=(chain,))

    def item_get(self, chain, item):
        """ 请求获取子进程的__getitem__。"""
        return self._parent.dispatch(EVT_WORKER_GET_ITEM, args=(chain, item))

    def __getitem__(self, item):
        """ 返回已定义的虚拟实例。 """
        if item not in self.instances:
            self.instances[item] = VirtualInstance(self, item)
        return self.instances[item]

    def __setitem__(self, key, value):
        self.instances[key] = value

    def __transfer__(self):
        """ 在事件处理之前拦截所有的事件处理函数，并转发事件给子进程。
        :param
            session['hdl_list']     : 事件处理函数组成的列表
            session['hdl_args']     : 事件处理函数列表参数
            session['hdl_kwargs']   : 事件处理函数字典参数
            session['event_ctx']    : 事件发生上下文
            session['origin_evt']     : 原目标事件
            session['evt']          : 发生的事件
            session['val']          : 发生事件传递的值
        """
        # 为了实现父进程控制器作为工作者处理定义的事件。所以对于除了内置事件外定义的事件都让其进行处理。

        evt = session['evt']
        if evt in (EVT_DRI_SUBMIT, EVT_DRI_OTHER):
            try:
                evt = session['origin_evt']
            except KeyError:
                pass
            if evt == EVT_DRI_RETURN:
                pending_id, value = session['val']
                # 响应事件返回Pending
                pend = self._unfinished_events.pop(pending_id)
                return_list = []
                for ret in value:
                    if type(ret) in pickle.bytes_types:
                        return_list.append(pickle.loads(ret))
                    else:
                        return_list.append(ret)
                pend.set(return_list)
            else:
                self._child_channel.put((evt, session['val'], session['event_ctx'],
                                         session['hdl_args'], session['hdl_kwargs']))

                # 跳过 EVT_DRI_OTHER 和 EVT_DRI_SUBMIT 事件
                self._parent.skip()

    def __patch__(self):
        def is_idle():
            """ 返回工作线程是否处于空闲状态。 """
            return self._workers_idle_event.is_set()

        def pending():
            """ 等待被父进程派遣的事件全部被子进程的工作线程接收。 """
            # 等待的顺序：
            #   1. 父进程队列取空。
            #   2. 等待所有事件处理完毕即已完成事件转发。
            #   3. 子进程通信桥控制器事件队列完全取出并且处于最后一个事件的转发过程中。
            #   4. 子进程通信桥控制器事件最后一个事件处理完毕。
            #   5. 工作线程任务队列被完全取完。
            pending_super()
            self._parent.wait_for_idle()
            self._child_channel.join()
            self._workers_empty_queue_event.wait()

        def wait_for_idle(timeout=None):
            """ 等待子进程下所有的工作线程处于空闲状态。"""
            self._workers_idle_event.wait(timeout)

        def shutdown():
            """ 重写控制器关闭方法，该方法仅通知关闭子进程。"""
            getattr(self._parent, '_Controller__not_suspended').set()
            self._parent.dispatch(EVT_SUBPROCESS_SHUTDOWN)

            # 关闭适配器。
            for adapter in self._parent.adapters:
                adapter.__closing__()

        pending_super = self._parent.pending

        # 替换父进程控制器的事件处理通道为进程队列，以实现父子进程的通信。
        self._parent.event_channel = self._parent_channel
        # 为了实现父子进程的空闲状态的同步，patch控制器的方法is_idle、pending、suspend、is_suspended、resume
        # 这里不能直接改写父进程控制器的状态事件，否则会出现冲突的问题。
        self._parent.is_idle = is_idle
        self._parent.pending = pending
        self._parent.wait_for_idle = wait_for_idle
        self._parent.shutdown = shutdown
        # 为了方便操作子进程，这里直接将适配器以属性添加到控制器
        self._parent.subprocess = self

    def __running__(self):
        # 销毁历史残留的子进程的队列事件。
        self._child_channel.put(EVT_DRI_CLEAN)
        while True:
            ret = self._child_channel.get()
            self._child_channel.task_done()
            if ret == EVT_DRI_CLEAN:
                break

    def __run__(self):
        channel_pairs = self._child_channel, self._parent_channel
        status_events = self._workers_idle_event, self._workers_empty_queue_event
        self._process = Process(target=subprocess_main_thread,
                                args=(channel_pairs,
                                      status_events,
                                      self.__init_hdl, self.__init_kwargs),
                                daemon=self._parent._daemon)
        self._process.start()

    def __exception__(self, error):
        # 子进程事件处理出现错误后给父进程控制器发送错误处理事件
        self._parent.dispatch(EVT_DRI_ERROR, error)

    def __mapping__(self):
        return {
            EVT_DRI_BEFORE: self.__transfer__,
        }

    @staticmethod
    def __unique__():
        return True

    def __dependencies__(self):
        # 依赖Pending适配器，为了进一步实现子进程下的待决操作，公用同一个_unfinished_events。
        event_pending = EventPending()
        self._unfinished_events = event_pending._unfinished_events
        return [event_pending]
