# -*- coding: UTF-8 -*-
""" 控制器池

工作原理:

+------------------------------------+
|                                    |     EVT/func
|           event_queue              |<--------------- dispatch/submit
|                                    |---------------> Pending
+------------------------------------+     return
|   |                     |          |
|   |                     v          |
|   |       func   +-----------------+
|   |    +---------|  MappingManager |
|   |    |         +-----------------+
|   |    |                           |
|   v    v                           |
+------------------------------------+
|                                    |     process1                       process2
|           ClientPool               |------------------+-----------------------------------+----------- ...
|                                    |                  |                                   |
+------------------------------------+                  v                                   v
    ^                                     +-----------------------------+    +-----------------------------+
    |                                     | Session1                    |    | Session2                    |
    |                                     +-----------------------------+    +-----------------------------+
    |                                     | def func1(*args, **kwargs): |    | def func2(*args, **kwargs): |
    |                                     |    ...                      |    |    ...                      |
    |                                     +-----------------------------+    +-----------------------------+
    |                 return                            v                                  v
    ^---------------------------------------------------<----------------------------------<------------ ...

"""

from .controller import Controller
from .mapping import MappingManager
from .signal import EVT_DRI_AFTER, EVT_DRI_SUBMIT
from .session import session
from .utils import Pending
from queue import Queue, Empty

from threading import Event, Lock
import time


class ControllerPool:
    """ 控制器池， 也可以当成是线程池来使用。
    对于每一个线程客户端都有全局上下文：
        session['_cid']      : 线程客户端ID号/索引号。
        session['pool_mgr'] : 控制器池管理器
    """
    def __init__(self, maxsize, mapping=None, context=None,
                 static=None, name=None, daemon=True, *, static_self='pool_mgr'):
        """
        :param
            maxsize     : 最大线程数。
            mapping     : 事件处理映射。
            context     : 全局上下文。
            name        : 控制器池名称。
            daemon      : 守护线程
        """
        assert maxsize > 0

        self._maxsize = maxsize
        self._name = name
        # 共用事件处理映射管理器。
        self.mapping = MappingManager(mapping)
        self.mapping.add(EVT_DRI_AFTER, self.__fetch__)

        # 初始化线程池
        static = dict(static or {})
        self._cli_pool = []
        static[static_self] = self
        for index in range(maxsize):
            # 为线程客户端添加静态的上下文cid用于标识客户端的索引号。
            static['_cid'] = index
            cli_worker = Controller(name=str(name or id(self)) + str(index), mapping=self.mapping,
                                    context=context, static=static, daemon=daemon)
            self._cli_pool.append(cli_worker)
            # 客户端共用一个事件映射，以解决客户端事件处理映射的同步更新。
            # 要注意的是，当线程客户端共用同一个处理处理映射这就意味着
            # 线程客户端不能安装适配器（当适配器会添加事件映射的时候造成重复安装，因为所有的客户端都会各自进行安装。）
            cli_worker.mapping = self.mapping

        # 处理返回消息队列。
        self.__closed = Event()
        self.__lock = Lock()
        self.__not_suspended = Event()
        self.__not_suspended.set()
        self.__idle = Event()
        self.__idle.set()
        self.__pending = Event()
        self.__pending.set()
        # 待处理任务队列。
        self.event_queue = Queue()

    def is_idle(self):
        """ 返回控制器池是否处于空闲状态（没有任务在执行）。"""
        for cli in self._cli_pool:
            if not cli.is_idle():
                return False
        return True

    def is_alive(self):
        """ 返回控制器池是否在运行。
        只要任意一个控制器活动中，那么会返回True。
        """
        for cli in self._cli_pool:
            if cli.is_alive():
                return True
        return False

    def is_suspended(self):
        """ 返回是否处于挂起状态。"""
        return self.__not_suspended.is_set()

    def suspend(self):
        """ 挂起控制器池，这里所谓的挂起就是阻塞工作线程从任务队列取任务。"""
        self.__not_suspended.clear()

    def resume(self):
        """ 恢复挂起。"""
        self.__not_suspended.set()

    def run(self, context=None):
        """ 启动池里面所有的控制器。 """
        if self.is_alive():
            raise AssertionError('控制器池已处于运行状态。')
        self.__closed.clear()
        self.__idle.set()
        with self.__lock:
            # 挂起所有的控制器，以准备就绪状态。
            for cli in self._cli_pool:
                cli.run(context, suspend=True)

            # 搜索待处理事件，并交由线程客户端处理。
            while True:
                cli = self.find_suspended_client()
                if cli:
                    try:
                        data = self.event_queue.get_nowait()
                        self.event_queue.task_done()
                        # 清除工作线程空闲标志。
                        self.__idle.clear()
                        cli.resume()
                        cli.dispatch(*data)
                    except Empty:
                        break
                else:
                    break

    def listen(self, target, allow):
        for cli in self._cli_pool:
            target.listen(cli, allow, cli.name)

    def listened_by(self, queue, allow, name=None):
        for cli in self._cli_pool:
            cli.listened_by(queue, allow, name)

    def submit(self, function=None, args=(), kwargs=None, context=None):
        """ 提交任务到待处理队列。"""
        return self.dispatch(EVT_DRI_SUBMIT, function, context, args, kwargs)

    def dispatch(self, eid, value=None, context=None, args=(), kwargs=None):
        """ 提交事件到待处理队列。"""
        assert not self.__closed.is_set()
        assert self.__not_suspended.is_set()
        context = context or {}
        pending = Pending()
        context['__pending'] = pending
        with self.__lock:
            # 等待pending结束。
            self.__pending.wait()
            # 清除工作线程空闲标志。
            self.__idle.clear()
            cli = self.find_suspended_client()
            if cli:
                # 先恢复线程继续运行。
                cli.resume()
                # 如果存在被挂起的线程客户端，直接将任务分派给线程。
                cli.dispatch(eid, value, context, args, kwargs)
            else:
                data = eid, value, context, args, kwargs
                self.event_queue.put(data)
        return pending

    def clean(self):
        """ 清空待处理任务队列。
        返回未处理任务列表。
        """
        clean_list = []
        with self.__lock:
            while True:
                try:
                    clean_list.append(self.event_queue.get_nowait())
                except Empty:
                    break
        return clean_list

    def pending(self):
        """ 等待任务队列被取空，在pending过程中就阻塞任务派遣方法dispatch/submit."""
        self.__pending.clear()
        self.event_queue.join()
        self.__pending.set()

    def wait_for_idle(self, timeout=None):
        """ 等待所有的线程客户端都处于空闲状态。 """
        self.__idle.wait(timeout)

    def wait(self, timeout=None):
        """ 等待控制器。 """
        endtime = None
        for cli in self._cli_pool:
            if timeout is not None:
                if endtime is None:
                    endtime = time.time() + timeout
                else:
                    timeout = endtime - time.time()
                    if timeout <= 0:
                        break
            cli.wait(timeout)

    join = wait

    def shutdown(self, blocking=False):
        """ 关闭线程池。"""
        self.__closed.set()
        self.__not_suspended.set()
        # 直接通知所有的线程在下一个事件进行关断。
        with self.__lock:
            for cli in self._cli_pool:
                cli.shutdown()

        if blocking:
            self.wait()

    def find_suspended_client(self):
        """ 在线程池里面搜索被挂起的线程。
        在线程池中被挂起意味着未被分配任务。
        """
        for cli in self._cli_pool:
            if cli.is_suspended():
                return cli
        return None

    def __fetch__(self):
        """ 线程客户端从公共队列里面取任务。"""
        # 给取任务上锁是为了同步操作取任务和派遣任务。
        # 避免线程挂起了，但是却又新的任务加入待处理队列。
        with self.__lock:
            # 等待挂起事件。
            self.__not_suspended.wait()
            if not self.__closed.is_set():
                # 处理由控制器池派遣的任务的等待pending对象。
                # 如果是独自派遣给线程客户端的任务不必要处理。
                # 总的来说就是，有__pending属性就处理。
                pending = getattr(session, '__pending', None)
                if pending:
                    pending.set(session['returns'])
                # 若控制器池发起关闭事件信号后，为了及时关闭线程池，避免线程继续往任务队列中取任务。
                try:
                    data = self.event_queue.get_nowait()
                    self.event_queue.task_done()
                    # 给线程客户端分派任务
                    session['self'].dispatch(*data)
                except Empty:
                    # 挂起线程客户端，处于空闲状态，等待分派新的任务。
                    session['self'].suspend()
                    # 判断设置控制器池空闲标志。
                    if all([cli.is_suspended() for cli in self._cli_pool]):
                        self.__idle.set()

    def __iter__(self):
        """ 迭代客户端控制器。 """
        return iter(self._cli_pool)

    def __repr__(self):
        status = 'stopped'
        if self.is_alive():
            status = 'running'
        if self.is_suspended():
            status = 'suspended'

        return '<ControllerPool %s %s %s>' % (self._name, len(self._cli_pool), status)