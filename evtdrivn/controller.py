# -*- coding: UTF-8 -*-
from __future__ import division
from threading import Event, Thread, current_thread
from multiprocessing import current_process
from .session import session
from .signal import (EVT_DRI_SHUTDOWN, EVT_DRI_AFTER, EVT_DRI_SUBMIT,
                     EVT_DRI_BEFORE, EVT_DRI_OTHER)
from evtdrivn.utils import Listener

try:
    # py3
    from queue import Queue
except ImportError:
    # py2
    from Queue import Queue

from .plugins.base import PluginManager
from .mapping import MappingManager


class Controller:
    """ 事件控制器。 """

    def __init__(self, mapping=None, context=None, channel_pairs=None, plugins=(),
                 static=None, name=None, daemon=True):
        # event_channel 是待处理事件队列。
        # return_channel 是处理返回消息队列。
        self.event_channel, self.return_channel = channel_pairs or (Queue(), Queue())

        # 插件管理器，允许为控制器安装所需插件以增强其功能。
        self.plugins = PluginManager(self, *plugins)

        # 为了更好的在各个控制器之间的友好通信，listeners 存放着当前控制器所被监听的事件。
        # 当控制器发生被监听的事件的时候，控制器会将事件（值和上下文会以ForwardingPacket对象）转发给监听者。
        self._listeners = []

        # 当前控制器线程对象。
        self.__con_thread = None
        self._daemon = daemon
        # global 属于全局上下文环境， runtime 属于运行时上下文环境， event_ctx 属于事件上下文环境。
        # 若存在同样的上下文属性名，那么会以 global < runtime < event_ctx 的优先级覆盖。
        # 其中 event_ctx 属于单次事件的上下文。runtime 属于运行时创建的上下文， global 属于初始化控制器时所创建的上下文。
        context = context or {}
        self._global = context.copy()
        self._runtime = {}

        static = static or {}
        self._static = static.copy()
        # 用于表示控制器的实时状态。
        # no_suspend 显示控制器是否属于非挂起状态，如果非挂起状态时Event.set()， 否则Event.clear()
        # idle 显示控制器是否处于空闲状态。这里所说的空闲的意思是：
        #   控制器是否处于在处理函数中。
        #   ！！注意的是，当控制器处于取出事件过程中，控制器是处于空闲状态的。！！
        self.__no_suspend = Event()
        self.__no_suspend.set()
        self.__idle = Event()

        # skip事件用于跳过当前事件，若已经处于事件处理过程中的话将无效。
        self.__skip = False

        # 若不指定name则默认使用当前控制器实例的内存id号作为name。
        self._name = name or str(id(self))

        # mapping 事件处理映射管理器。
        self.mapping = MappingManager(mapping)

    @property
    def name(self):
        return self._name

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
        return not self.__no_suspend.is_set()

    def suspend(self):
        """ 挂起控制器，等待新的任务进入。 """
        self.__no_suspend.clear()
        # 挂起插件。
        for plugins in self.plugins:
            for plugin in plugins:
                plugin.__suspend__()

    def resume(self):
        """ 从挂起中恢复。 """
        self.__no_suspend.set()

        # 恢复插件。
        for plugins in self.plugins:
            for plugin in plugins:
                plugin.__resume__()

    def add_plugin(self, *plugins):
        """ 添加插件。 """
        p_names = []
        for plugin in plugins:
            # 实例化插件并安装插件
            p_names.append(self.plugins.install(plugin))

        if len(plugins) == 1:
            return p_names[0]

        return p_names

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
        self.event_channel.put((evt, value, context or {}, args, kwargs or {}))

    def message(self, evt, value=None, context=None, args=(), kwargs=None):
        """ 给控制器返回通道推送事件。 """
        self.return_channel.put((evt, value, context or {}, args, kwargs or {}))

    def listen(self, target, allow):
        """ 监听指定控制器的事件。 """
        target._listeners.append(Listener(self.event_channel, allow))

    def listened_by(self, queue, allow):
        """ 允许被队列Queue监听。"""
        self._listeners.append(Listener(queue, allow))

    def shutdown(self):
        """ 发送关闭控制器的信号。"""
        # 恢复被挂起的线程，让其恢复接受任务处理的状态。
        self.__no_suspend.set()

        # 关闭插件。
        for plugins in self.plugins:
            for plugin in plugins:
                plugin.__close__()

        self.dispatch(EVT_DRI_SHUTDOWN)

    close = shutdown

    def run(self, context=None, suspend=False):
        """ 运行控制器事件处理循环线程。
        :param
            context : 运行时提供的上下文。
            suspend : 如果为True，那么启动线程后将挂起，等待恢复状态。
        """
        if self.__con_thread and self.__con_thread.is_alive():
            raise RuntimeError('controller has already been running.')

        if suspend:
            self.__no_suspend.clear()
        else:
            self.__no_suspend.set()

        context = context or {}
        self._runtime = context
        thr = Thread(target=self.__control_thread, daemon=self._daemon,
                     args=(context,), name=self._name)
        self.__con_thread = thr
        thr.start()

        # 启动插件。
        for plugins in self.plugins:
            for plugin in plugins:
                plugin.__run__()

        return thr

    def wait(self, timeout=None):
        """ 等待控制器关闭。
        若控制器已启动，则阻塞至控制器关闭。

        若控制器未启动，则不会发生任何事情。
        """
        if self.__con_thread and self.__con_thread.is_alive():
            self.__con_thread.join(timeout)

    def skip(self):
        """ 跳过当前事件的处理。
        通常这用于事件处理之前的hook_before事件。
        这将跳过：当前事件的处理和hook_after事件的处理，

        注意：若当前事件是EVT_DRI_SHUTDOWN控制器的关闭事件，也同样会进行跳过处理。
        """
        self.__skip = True

    def pend(self):
        """ 等待一个事件处理完毕。"""
        self.__idle.wait()

    def clean(self):
        """ 清空消息队列。

        不允许在控制器启动过程中运行清空队列。
        """
        assert not self.__con_thread or not self.__con_thread.is_alive()
        # 清空完毕标志位。
        clean_finished_flag = object()
        self.return_channel.put(clean_finished_flag)
        self.event_channel.put(clean_finished_flag)
        while True:
            ret = self.return_channel.get()
            if ret is clean_finished_flag:
                break
        while True:
            ret = self.event_channel.get()
            if ret is clean_finished_flag:
                break

    def __control_thread(self, runtime_ctx):
        # 首次进入初始化全局环境。
        session['self'] = self
        session.__static__.update(self._static)
        session.__context__(self._global)
        while True:
            # 进入空闲状态，即没有任务处理的状态。
            self.__idle.set()
            # 如果线程被挂起，那么进入等待。
            self.__no_suspend.wait()
            evt, val, event_ctx, hdl_args, hdl_kwargs = self.event_channel.get()
            # print('%s, %s, %s, %s, %s, %s, %s' % (current_process().pid, current_thread().ident, evt, val, event_ctx, hdl_args, hdl_kwargs))
            # 当取到任务后清除空闲标志位。
            self.__idle.clear()
            # 准备处理函数。
            hdl_list = self.mapping.get(evt, [])

            if evt == EVT_DRI_SUBMIT:
                if hdl_list:
                    event_ctx['function'] = val
                    event_ctx['args'] = hdl_args
                    event_ctx['kwargs'] = hdl_kwargs
                    hdl_args = ()
                    hdl_kwargs = {}
                else:
                    hdl_list = [val]

            # 以监听事件响应来通知所有监听该事件的监听者。
            s_push = (i for i in self._listeners if i.check(evt))
            for i in s_push:
                # 抄送事件响应信息。
                i.push(evt, val, event_ctx)

            # 如果没有定义该事件处理，那么尝试使用默认处理方式。
            if not hdl_list and evt != EVT_DRI_SHUTDOWN:
                hdl_list = self.mapping.get(EVT_DRI_OTHER, [])
                # 默认处理的情况下，强制为事件响应添加属性指向源触发事件ID。
                session['orig_evt'] = evt

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
                for before in befores:
                    if evt == EVT_DRI_BEFORE:
                        break
                    if callable(before):
                        session['hdl_list'] = hdl_list
                        session['hdl_args'] = hdl_args
                        session['hdl_kwargs'] = hdl_kwargs
                        session['event_ctx'] = event_ctx
                        before()
                        del session['hdl_list']
                        del session['hdl_args']
                        del session['hdl_kwargs']
                        del session['event_ctx']

                # 若事件处理被跳过了，那么afters也会被跳过
                if not self.__skip:
                    returns = []
                    for hdl in hdl_list:
                        if callable(hdl):
                            # 事件处理函数
                            returns.append(hdl(*hdl_args, **hdl_kwargs))

                    # 事件处理函数之后。
                    # 如果没有定义事件处理，那么也不会执行事件处理之后。
                    if hdl_list:
                        for after in afters:
                            if evt == EVT_DRI_AFTER:
                                break
                            if callable(after):
                                session['returns'] = returns
                                after()
                                del session['returns']

                del session['evt']
                del session['val']

            if evt == EVT_DRI_OTHER:
                del session['orig_evt']

            # 跳过标志也会跳过控制器的关闭事件。
            if not self.__skip:
                if evt == EVT_DRI_SHUTDOWN:
                    self.__idle.set()
                    break

        # 清除静态环境。
        session.__static__.clear()
        # 清除上下文环境
        session.__context__({})

    def __repr__(self):
        status = 'stopped'
        if self.is_alive():
            status = 'running'
        if self.is_suspended():
            status = 'suspended'

        return '<Controller %s %s>' % (self._name, status)


