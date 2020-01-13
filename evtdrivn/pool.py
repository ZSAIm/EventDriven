# -*- coding: UTF-8 -*-
from .controller import Controller
from .mapping import MappingManager
from .signal import EVT_DRI_AFTER, EVT_DRI_SUBMIT, EVT_DRI_RETURN
from .session import session

try:
    # py3
    from queue import Queue, Empty
except ImportError:
    # py2
    from Queue import Queue, Empty

# import threading
from threading import Event, Lock
import time


class ControllerPool:
    """ 控制器池， 也可以当成是线程池来使用。
    对于每一个线程客户端都有全局上下文：
        session['cid']  : 线程客户端ID号/索引号。
        session['pool']
    """
    def __init__(self, maxsize=1, mapping=None, context=None, static=None, name=None, until_commit=True):
        """
        :param
            maxsize     : 最大线程数。
            mapping     : 事件处理映射。
            context     : 全局上下文。
            name        : 控制器池名称。
            until_commit: 如果为True，那么当submit或者dispatch的时候若没有空闲的客户端线程进行安排任务，
                        那么提交任务将无限阻塞直至任务被安排。
        """
        # 线程数必须大于0，且为整数
        assert maxsize > 0 and type(maxsize) is int

        self._maxsize = maxsize

        self._mapping = MappingManager(mapping)
        self._mapping.add(EVT_DRI_AFTER, self.__fetch__)

        # 初始化线程池
        static = static or {}
        static = static.copy()
        self._cli_pool = []
        static['pool'] = self
        for index in range(maxsize):
            static['cli'] = index
            self._cli_pool.append(
                Controller(name=str(name) + str(index), mapping=self._mapping,
                           context=context, static=static)
            )

        # 处理返回消息队列。
        self.return_queue = Queue()
        self.__closed = Event()
        self.__lock = Lock()

        # 待处理任务队列。
        self._event_queue = Queue()
        # 是否等待任务被安排。
        self._until_commit = until_commit

    def update_mapping(self, mapping):
        """ 更新全局的事件处理映射。 """
        self._mapping.set(mapping)
        self._mapping.add(EVT_DRI_AFTER, self.__fetch__)
        # 初始化线程池的事件处理映射
        for cli in self._cli_pool:
            cli.mapping.set(dict(self._mapping))

    def is_alive(self):
        """ 返回控制器池是否在运行。
        只要任意一个控制器活动中，那么会返回True。
        """
        for cli in self._cli_pool:
            if cli.is_alive():
                return True
        return False

    # def is_all_suspended(self):

    def __fetch__(self):
        """ 线程客户端从公共队列里面取任务。"""
        # 给取任务上锁是为了同步操作取任务和派遣任务。
        # 避免线程挂起了，但是却又新的任务加入待处理队列。
        with self.__lock:
            self.return_queue.put((EVT_DRI_RETURN, session['returns'], (), {}))
            # 若控制器池发起关闭事件信号后，为了及时关闭线程池，避免线程继续往任务队列中取任务。
            if not self.__closed.is_set():
                try:
                    data = self._event_queue.get_nowait()
                    # 给线程客户端分派任务
                    if self._until_commit:
                        # 触发任务被安排事件。
                        data[-1].set()
                        data = data[:-1]

                    session['self'].dispatch(*data)
                except Empty:
                    # 挂起线程客户端，处于空闲状态，等待分派新的任务。
                    session['self'].suspend()

    def run(self, context=None):
        """ 启动池里面所有的控制器。 """
        if self.is_alive():
            raise AssertionError('ControllerPool has already been running.')
        self.__closed.clear()

        with self.__lock:
            # 挂起所有的控制器，以准备就绪状态。
            for cli in self._cli_pool:
                cli.run(context, suspend=True)

            # 搜索待处理事件，并交由线程客户端处理。
            while True:
                cli = self.find_suspended_client()
                if cli:
                    try:
                        data = self._event_queue.get_nowait()
                        cli.dispatch(*data)
                    except Empty:
                        break
                else:
                    break

    def pend(self):
        """ 等待任务执行返回。"""

    def listen(self, target, allow):
        for cli in self._cli_pool:
            target.listen(target, allow, cli.name)

    def listened_by(self, queue, allow):
        for cli in self._cli_pool:
            cli.listened_by(queue, allow)

    def submit(self, function=None, args=(), kwargs=None, context=None):
        """ 提交任务到待处理队列。"""
        self.dispatch(EVT_DRI_SUBMIT, [function], context, args, kwargs)

    def dispatch(self, eid, value=None, context=None, args=(), kwargs=None):
        """ 提交事件到待处理队列。"""
        context = context or {}

        self.__lock.acquire()
        cli = self.find_suspended_client()
        if cli:
            # 先恢复线程继续运行。
            cli.resume()
            # 如果存在被挂起的线程客户端，直接将任务分派给线程。
            cli.dispatch(eid, value, context, args, kwargs)
            self.__lock.release()
        else:
            # 没有被空闲的线程客户端，那么将推送到待处理队列。
            if self._until_commit:
                wait_evt = Event()
                data = eid, value, context, args, kwargs, wait_evt
                self._event_queue.put(data)
                self.__lock.release()
                # 等待任务被安排后才返回。
                wait_evt.wait()
            else:
                data = eid, value, context, args, kwargs
                self._event_queue.put(data)
                self.__lock.release()

    def clean(self):
        """ 清空待处理任务队列。
        返回未处理任务列表。
        """
        clean_list = []
        while True:
            try:
                clean_list.append(self._event_queue.get_nowait())
            except Empty:
                break
        return clean_list

    def wait(self, timeout=None):
        """ 等待控制器。 """
        endtime = None
        for cli in self._cli_pool:
            if endtime is None:
                endtime = time.time() + timeout
            else:
                timeout = endtime - time.time()
                if timeout <= 0:
                    break
            cli.wait(timeout)

    def shutdown(self, blocking=False):
        """ 关闭线程池。"""
        self.__closed.set()
        # 避免线程客户端的关闭信号发生在__fetch__分派任务之前。
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
