# -*- coding: UTF-8 -*-
""" 事件驱动控制器。

工作原理:

+------------------------------------+   instance
|           AdapterManager           |<------------ Adapter
+------------------------------------+
|   |                     |          |
|   |                     v          |
|   |       func   +-----------------+     EVT
|   |    +---------|  MappingManager |<------------ dispatch
|   |    |         +-----------------+
|   |    |                           |
|   v    v                           |
+------------------------------------+
|                                    |     func
|           EventLoop                |<------------ submit
|                                    |
+------------------------------------+
    ^                    |    process     +-----------------------------+
    |                    +--------------> | Session                     |
    |                                     +-----------------------------+
    |                                     | def func(*args, **kwargs):  |
    |                                     |    ...                      |
    |                                     +-----------------------------+
    |                 return                           v
    ^--------------------------------------------------<

"""

from threading import Thread, Event
from queue import Queue
from .adapter import AbstractAdapter
from .session import session
from .event import (EVT_DRI_SHUTDOWN, EVT_DRI_AFTER, EVT_DRI_SUBMIT,
                    EVT_DRI_BEFORE, EVT_DRI_OTHER, EVT_DRI_CLEAN)
from .utils import Listener
from .mapping import MappingManager
from .error import ListenerAlreadyExisted, UniqueAdapterInstance


class Controller:
    """ 事件控制器。

    :public property
        adapters:       适配器管理器
        mapping:        事件处理映射管理器
        event_channel:  事件处理队列通道

    """

    def __init__(self, mapping=None, context=None, event_channel=None, adapters=(),
                 static=None, name=None, daemon=True):
        """
        :param
            mapping:        事件处理映射，类型dict/MappingBlueprint。
            context:        全局动态上下文。类型dict。
                            - 该字典的键值将以属性的方式添加到会话线程变量session，如session.example
                            - 除了python内部变量关键字外，还有不允许使用的属性名:
                            - 若属于监听转发得到的事件
            event_channel:  可指定事件队列通道。类型Queue。
            adapters:       初始化添加适配器。类型继承AbstractAdapter类。
            static:         静态上下文。类型dict
                            - 该字典的键值将以项的方式添加到会话线程变量session，如session['example']
                            - 不允许使用的健:
                                * ALL CASE:         'self', 'evt', 'val'
                                * EVT_DRI_OTHER:    'origin_evt'
                                * EVT_DRI_SUBMIT:   'function', 'args', 'kwargs', 'evt_context'
                                * EVT_DRI_BEFORE:   'hdl_init', 'hdl_args', 'hdl_kwargs'
                                * EVT_DRI_AFTER:    'returns'
            name:           该名称将用用于线程名称。
            daemon:         该daemon将用于线程的创建时传递。
        """
        # event_channel 是待处理事件队列。
        self.event_channel = event_channel or Queue()

        # 适配器管理器，允许为控制器安装所需适配器以增强其功能。
        self.adapters = AdapterManager(self, *adapters)

        # 为了更好的在各个控制器之间的友好通信，listeners 存放着当前控制器所被监听的事件。
        # 当控制器发生被监听的事件的时候，控制器会将事件（值和上下文会以ForwardingPacket对象）转发给监听者。
        self.listeners = []

        # 当前控制器线程对象。
        self.__con_thread = None
        self._daemon = daemon
        # global 属于全局上下文环境， runtime 属于运行时上下文环境， event_ctx 属于事件上下文环境。
        # 若存在同样的上下文属性名，那么会以 global < runtime < event_ctx 的优先级覆盖。
        # 其中 event_ctx 属于单次事件的上下文。runtime 属于运行时创建的上下文， global 属于初始化控制器时所创建的上下文。
        self._global = dict(context or {})
        self._runtime = {}

        self._static = dict(static or {})
        # 用于表示控制器的实时状态。
        # no_suspend 显示控制器是否属于非挂起状态，如果非挂起状态时Event.set()， 否则Event.clear()
        # idle 显示控制器是否处于空闲状态。这里所说的空闲的意思是：
        #   控制器是否处于在处理函数中。
        #   ！！注意的是，当控制器处于取出事件过程中，控制器是处于空闲状态的。！！
        self.__not_suspended = Event()
        self.__not_suspended.set()
        self.__idle = Event()
        # pending事件过程中
        self.__pending = Event()
        self.__pending.set()
        # skip事件用于跳过当前事件，若已经处于事件处理过程中的话将无效。
        self.__skip = False

        # 若不指定name则默认使用当前控制器实例的内存id号作为name。
        self._name = name or str(id(self))

        # mapping 事件处理映射管理器。
        self.mapping = MappingManager(mapping)

    @property
    def name(self):
        """ 返回控制器名称。 """
        return self._name

    @property
    def __global__(self):
        """ 返回全局上下文环境。"""
        return self._global

    @property
    def __runtime__(self):
        """ 返回运行时上下文环境。"""
        return self._runtime

    @property
    def __static__(self):
        """ 返回静态上下文环境。"""
        return self._static

    def Adapter(self, *adapters):
        """ 添加适配器。 """
        p_names = []
        for adapter in adapters:
            # 实例化适配器并安装适配器
            p_names.append(self.adapters.install(adapter))

        if len(adapters) == 1:
            return p_names[0]

        return p_names

    def is_idle(self):
        """ 返回控制器是否处于空闲状态。
        注意：当控制器处于取出事件过程中，控制器是处于空闲状态的。
        """
        return self.__idle.is_set()

    def is_alive(self):
        """ 返回控制器是否处于运行中。 """
        return self.__con_thread and self.__con_thread.is_alive()

    def is_suspended(self):
        """ 返回控制器是否被挂起。 """
        return not self.__not_suspended.is_set()

    def suspend(self):
        """ 挂起控制器，等待新的任务进入。 """
        self.__not_suspended.clear()
        # 挂起适配器。
        for adapter in self.adapters:
            adapter.__suspend__()

    def resume(self):
        """ 从挂起中恢复。 """
        self.__not_suspended.set()

        # 恢复适配器。
        for adapter in self.adapters:
            adapter.__resume__()

    def submit(self, function=None, args=(), kwargs=None, context=None):
        """ 提交处理任务。 """
        self.dispatch(EVT_DRI_SUBMIT, function, context, args, kwargs)

    def dispatch(self, evt, value=None, context=None, args=(), kwargs=None):
        """ 给控制器事件处理队列通道推送事件。
        :param
            evt     :   事件信号ID，
            value   :   发起事件传递的值，之后会以event.val发给事件响应对象。
            context :   为事件响应创建的上下文，以属性方式解包给event。
            args    :   传递给事件处理函数的列表参数
            kwargs  :   传递给事件处理函数的字典参数
        """
        # 不允许对挂起的控制器分派任务。
        assert not self.is_suspended()
        self.__pending.wait()
        self.event_channel.put((evt, value, dict(context or {}), args, kwargs or {}))

    def listen(self, target, allow, name=None):
        """ 监听指定控制器的事件。 """
        if self.event_channel in target.listeners:
            raise ListenerAlreadyExisted()
        target.listeners.append(Listener(self.event_channel, allow, name))

    def listened_by(self, queue, allow, name=None):
        """ 允许被队列Queue监听。"""
        if queue in self.listeners:
            raise ListenerAlreadyExisted()
        self.listeners.append(Listener(queue, allow, name))

    def shutdown(self):
        """ 发送关闭控制器的信号。"""
        # 恢复被挂起的线程，让其恢复接受任务处理的状态。
        self.__not_suspended.set()

        self.dispatch(EVT_DRI_SHUTDOWN)

        # 关闭适配器。
        for adapter in self.adapters:
            adapter.__closing__()

    close = shutdown

    def run(self, context=None, suspend=False):
        """ 运行控制器事件处理循环线程。
        :param
            context : 运行时提供的上下文。
            suspend : 如果为True，那么启动线程后将挂起，等待恢复状态。
        """
        if self.__con_thread and self.__con_thread.is_alive():
            raise RuntimeError('控制器已经处于运行状态。')

        if suspend:
            self.__not_suspended.clear()
        else:
            self.__not_suspended.set()

        for adapter in self.adapters:
            adapter.__running__()

        context = dict(context or {})
        self._runtime = context
        thr = Thread(target=self.__eventloop_thread, daemon=self._daemon,
                     args=(context,), name=self._name)
        self.__con_thread = thr
        thr.start()

        for adapter in self.adapters:
            adapter.__run__()

        return thr

    def pending(self):
        """ 等待事件处理队列被取完。在pending过程中就阻塞任务派遣方法dispatch/submit."""
        self.__pending.clear()
        self.event_channel.join()
        self.__pending.set()

    def wait_for_idle(self, timeout=None):
        """ 等待控制器进入空闲状态。
        进入空闲状态并不意味着再下一刻不会进入工作状态。
        因为本质上空闲状态是当控制器线程处于事件获取的过程中，控制器是处于空闲状态的。
        """
        self.__idle.wait(timeout)

    def wait(self, timeout=None):
        """ 等待控制器关闭。
        若控制器已启动，则阻塞至控制器关闭。

        若控制器未启动，则不会发生任何事情。
        """
        if self.__con_thread and self.__con_thread.is_alive():
            self.__con_thread.join(timeout)

    join = wait

    def skip(self):
        """ 跳过当前事件的处理。
        通常这用于事件处理之前的hook_before事件。
        这将跳过当前事件之后的所有函数调用。

        注意：若当前事件是EVT_DRI_SHUTDOWN控制器的关闭事件，也同样会进行跳过处理。
        """
        self.__skip = True

    def clean(self):
        """ 清空消息队列。

        不允许在控制器启动过程中运行清空队列。
        """
        assert not self.__con_thread or not self.__con_thread.is_alive()
        # 清空完毕标志位。
        self.event_channel.put(EVT_DRI_CLEAN)
        while True:
            ret = self.event_channel.get()
            self.event_channel.task_done()
            if ret == EVT_DRI_CLEAN:
                break

    def __eventloop_thread(self, runtime_ctx):
        # 首次进入初始化全局环境。
        session.__static__ = self._static
        session['self'] = self
        session.__context__(self._global)
        try:
            while True:
                # 进入空闲状态，即没有任务处理的状态。
                self.__idle.set()
                # 如果线程被挂起，那么进入等待。
                self.__not_suspended.wait()
                evt, val, event_ctx, hdl_args, hdl_kwargs = self.event_channel.get()
                self.event_channel.task_done()
                # 当取到任务后清除空闲标志位。
                self.__idle.clear()
                # 准备处理函数。
                hdl_list = self.mapping.get(evt, [])

                if evt == EVT_DRI_SUBMIT:
                    # 如果重写了EVT_DRI_SUBMIT的事件处理，那么提交的任务将更新于静态上下文里面
                    # _function, _args, _kwargs
                    if hdl_list:
                        session['function'] = val
                        session['args'] = hdl_args
                        session['kwargs'] = hdl_kwargs
                        hdl_args = ()
                        hdl_kwargs = {}
                    else:
                        hdl_list = [val]

                # 以监听事件响应来通知所有监听该事件的监听者。
                s_push = (i for i in self.listeners if i.check(evt))
                for i in s_push:
                    # 抄送事件响应信息。
                    i.push(evt, val, event_ctx)

                # 如果没有定义该事件处理，那么尝试使用默认处理方式。
                if not hdl_list and evt != EVT_DRI_SHUTDOWN:
                    hdl_list = self.mapping.get(EVT_DRI_OTHER, [])
                    # 默认处理的情况下，强制为事件响应添加属性指向源触发事件ID。
                    session['origin_evt'] = evt

                    evt = EVT_DRI_OTHER

                befores = self.mapping.get(EVT_DRI_BEFORE, [])
                afters = self.mapping.get(EVT_DRI_AFTER, [])

                if hdl_list or befores or afters:
                    session['evt'] = evt
                    session['val'] = val
                    # 创建响应事件，context是dispatch的上下文。
                    # 优先级：self._global < runtime_ctx < event_ctx
                    d = dict(self._global)
                    d.update(runtime_ctx)
                    d.update(event_ctx)
                    # 设置上下文环境。
                    session.__context__(d)

                    # 恢复跳过事件标志。
                    self.__skip = False
                    # 事件处理函数之前。
                    if befores:
                        session['hdl_list'] = hdl_list
                        session['hdl_args'] = hdl_args
                        session['hdl_kwargs'] = hdl_kwargs
                        session['evt_context'] = event_ctx
                        for before in befores:
                            if evt == EVT_DRI_BEFORE:
                                break
                            if callable(before):
                                before()
                        # 销毁静态上下文
                        del session['hdl_list']
                        del session['hdl_args']
                        del session['hdl_kwargs']
                        del session['evt_context']

                    # 若事件处理被跳过了，那么afters也会被跳过
                    if not self.__skip:
                        returns = []
                        for hdl in hdl_list:
                            if callable(hdl):
                                # 事件处理函数
                                returns.append(hdl(*hdl_args, **hdl_kwargs))
                    # 分开两次判断是为了确定在事件处理过程中的跳过操作。
                    if not self.__skip:
                        # 事件处理函数之后。
                        # 如果是在控制器关闭的事件EVT_DRI_SHUTDOWN的处理后，那么将不做事件后处理。
                        # 如果希望跳过控制器的关闭事件，可以在事件处理前或事件处理处进行添加处理映射。
                        if afters and evt != EVT_DRI_SHUTDOWN:
                            session['returns'] = returns
                            for after in afters:
                                if evt == EVT_DRI_AFTER:
                                    break
                                if callable(after):
                                    after()
                            del session['returns']

                    # 销毁事件层级的静态上下文参数。
                    del session['evt']
                    del session['val']

                if evt == EVT_DRI_OTHER:
                    # 清除other静态上下文
                    del session['origin_evt']
                elif evt == EVT_DRI_SUBMIT:
                    try:
                        # 销毁任务提交submit添加的静态上下文，若存在
                        del session['function']
                        del session['args']
                        del session['kwargs']
                    except KeyError:
                        pass
                # 跳过标志也会跳过控制器的关闭事件。
                if not self.__skip and evt == EVT_DRI_SHUTDOWN:
                    self.__idle.set()
                    break
        except Exception as err:
            # 发生异常的下的适配器处理。
            for adapter in self.adapters:
                adapter.__exception__(err)
            raise
        finally:
            # 清除静态环境。
            session.__static__.clear()
            # 清除上下文环境
            session.__context__({})

            # 控制器已关闭处理事件。
            for adapter in self.adapters:
                adapter.__closed__()

    def __repr__(self):
        status = 'stopped'
        if self.is_alive():
            status = 'running'
        if self.is_suspended():
            status = 'suspended'

        return '<Controller %s %s>' % (self._name, status)


class AdapterManager:
    def __init__(self, parent, *init_plug):
        self._parent = parent
        self._adapters = {}
        for p in init_plug:
            self.install(p)
        self.__name_adapter = {}

    def install(self, adapter):
        """ 安装适配器。
        :param
            plugin  : 适配器实例。
        :return
            返回实例化的适配器名称。
        """
        if not isinstance(adapter, AbstractAdapter):
            raise TypeError('can only install adapter instance inherited from BaseAdapter.')

        pt = type(adapter)
        if pt not in self._adapters:
            self._adapters[pt] = []
        else:
            # 检查适配器是否只允许安装一个实例。
            if adapter.__unique__():
                raise UniqueAdapterInstance('this adapter can only install one instance.')

        # 安装适配器依赖。
        for dependency in adapter.__dependencies__():
            if type(dependency) not in self._adapters:
                self.install(dependency)

        # 引入适配器实例。
        name = adapter.__class__.__name__.lower()
        if hasattr(self, name):
            cnt = 0
            while True:
                cnt += 1
                if not hasattr(self, name + str(cnt)):
                    break
            name = name + str(cnt)

        # 安装适配器。
        adapter.__setup__(self._parent, name)
        # 打补丁。
        adapter.__patch__()
        # 安装事件处理函数。
        # 为了支持多个适配器添加同一事件，这里使用添加而不是使用更新覆盖的方法。
        for evt, hdl in adapter.__mapping__().items():
            self._parent.mapping.add(evt, hdl)

        # 添加全局动态上下文。
        self._parent.__global__.update(adapter.__context__())
        # 添加静态上下文。
        self._parent.__static__.update(adapter.__static__())
        self.__name_adapter[name] = adapter
        self._adapters[pt].append(adapter)

        return name

    def __iter__(self):
        """ 返回所有的适配器实例。 """
        it = []
        for adapters in self._adapters.values():
            it.extend(adapters)
        return iter(it)

    def __getitem__(self, item):
        return self.__name_adapter[item]