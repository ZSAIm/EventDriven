# -*- coding: UTF-8 -*-
""" 子进程插件 Subprocess （父进程控制器部分）。"""

from ..mapping import MappingBlueprint
from ..controller import Controller
from ..pool import ControllerPool
from ..session import session
from ..signal import (EVT_DRI_AFTER, EVT_DRI_SHUTDOWN, EVT_DRI_RETURN,
                      EVT_DRI_SUSPEND, EVT_DRI_OTHER, EVT_DRI_SUBMIT)
from multiprocessing import Event, Lock as ProcessLock
from queue import Queue
import pickle


class QueueWithEvent(Queue):
    """ 为了实现子进程工作线程的任务队列的状态事件。"""
    def __init__(self, maxsize=0, empty_event=None):
        super(QueueWithEvent, self).__init__(maxsize)
        # 空队列事件
        if not empty_event:
            empty_event = Event()
        self._empty = empty_event
        self._lock = ProcessLock()

    def put(self, item, block=True, timeout=None):
        with self._lock:
            super(QueueWithEvent, self).put(item, block, timeout)
            # 任务入列说明队列必然非空。
            self._empty.clear()

    def task_done(self):
        with self._lock:
            super(QueueWithEvent, self).task_done()
            if not self.unfinished_tasks:
                self._empty.set()


def _subprocess_worker_initializer(*args, **kwargs):
    """ 子进程工作线程控制器初始化。
    若有其他如添加插件需求可以重写该方法。
    需要返回控制器实例。
    :param
        maxsize : 工作线程池的最大数量
        mapping : 工作线程池的事件处理映射
        context : 工作线程池的全局上下文
        static  : 工作线程池的静态上下文
        name    : 工作线程池的名称
        daemon  : 工作线程池是否为守护线程
    """
    return ControllerPool(*args, **kwargs)


# 子进程控制器默认初始化程序。
default_worker_initializer = _subprocess_worker_initializer

_process_bri = MappingBlueprint()
_process_worker = MappingBlueprint()
# 通信桥控制器事件：
EVT_INSTANCE_INIT = '|EVT|INS|INIT|'
EVT_INSTANCE_DEL = '|EVT|INS|DEL|'
EVT_ADD_PLUGIN = '|EVT|PLUGIN|ADD|'
EVT_WORKER_IS_IDLE = '|EVT|WORKER|IDLE|'

# 工作线程控制器事件：
EVT_WORKER_INSTANCE_CALL = '|EVT|INS|CALL|'
EVT_WORKER_PROPERTY_GET = '|EVT|PROPERTY|GET|'


def _parse_instance_chain(chain_str):
    """ 返回对象链解析出来的实例对象。"""
    chain = chain_str.split('.')
    instance_name = chain.pop(0)
    attr = session['instances'][instance_name]
    for attr_name in chain:
        attr = getattr(attr, attr_name)
    return attr


@_process_worker.register(EVT_WORKER_INSTANCE_CALL)
def _instance_call(chain, args, kwargs):
    """ 父进程的调用链解析调用。"""
    return _parse_instance_chain(chain)(*args, **kwargs)


@_process_worker.register(EVT_WORKER_PROPERTY_GET)
def _property_get(chain):
    """ 属性值返回。"""
    return _parse_instance_chain(chain)


@_process_worker.register(EVT_DRI_AFTER)
@_process_bri.register(EVT_DRI_AFTER)
def __return__():
    """ 转发返回消息给父进程。 """
    if session['evt'] != EVT_DRI_SHUTDOWN:
        pending_id = getattr(session, '__pending_id')
        return_list = []
        for d in session['returns']:
            try:
                return_list.append(pickle.dumps(d))
            except TypeError:
                return_list.append(str(d))
        session['bri_worker'].message(EVT_DRI_RETURN, (pending_id, return_list))


@_process_worker.register(EVT_DRI_SHUTDOWN)
@_process_bri.register(EVT_DRI_SHUTDOWN)
def __shutdown__():
    """ 相互关闭。"""
    session['bri_worker'].shutdown()
    session['workers'].shutdown()


@_process_bri.register(EVT_ADD_PLUGIN)
def _add_plugin(hdl, args, kwargs):
    """ 子进程控制器通信桥安装插件。 """
    session['bri_worker'].add_plugin(hdl(*args, **kwargs))


@_process_bri.register(EVT_INSTANCE_DEL)
def _instance_del(instance_name):
    """ 删除实例在全局实例变量的引用。 """
    del session['instance']


@_process_bri.register(EVT_INSTANCE_INIT)
def _instance_initializer(hdl, args, kwargs, name):
    """ 子进程初始化实例。 """
    ins = hdl(*args, **kwargs)
    session['instances'][name] = ins


@_process_bri.register(EVT_DRI_SUSPEND)
def __suspend__():
    """ 工作线程挂起/恢复。 True=suspend; False=resume"""
    if session['val']:
        session['workers'].suspend()
    else:
        session['workers'].resume()


@_process_bri.register(EVT_DRI_OTHER)
@_process_bri.register(EVT_DRI_SUBMIT)
def __goto_work__(*args, **kwargs):
    """ 提交任务。 """
    try:
        evt = session['orig_evt']
    except KeyError:
        evt = session['evt']

    if evt == EVT_DRI_SUBMIT:
        context = session.__vars__
        session['workers'].submit(
            context.pop('function'), args=context.pop('args'), kwargs=context.pop('kwargs'), context=context
        )
    else:
        session['workers'].dispatch(evt, session['val'], args=args, kwargs=kwargs, context=session.__vars__)
    # 跳过任务事件执行。
    session['bri_worker'].skip()


@_process_bri.register(EVT_WORKER_IS_IDLE)
def __is_idle__():
    """ 返回工作线程是否处于空闲状态。"""
    return session['workers'].is_idle()


def subprocess_main_thread(channel_pairs, sync_events, init_hdl, init_kwargs):
    """ 运行在子进程模式下的主线程。"""
    def workers_init():
        """ 工作线程控制器池初始化。"""
        # 初始化静态上下文。
        for cli in workers:
            cli.__static__.update(static)
        # 为了在队列的推入和取出中获得队伍的长度，重写工作线程的任务队列以嵌入进程同步事件。
        workers.event_queue = QueueWithEvent(empty_event=workers_queue_empty_event)
        # 更新工作线程的内部任务事件。
        workers.mapping.update_from(_process_worker)

    def bri_workers_init():
        """ 通信桥控制器初始化。"""
        # 安装进程间通信管道通道。
        bri_worker.event_channel, bri_worker.return_channel = channel_pairs
        # 静态上下文初始化。
        bri_worker.__static__.update(static)

    # 创建工作线程控制器池。
    workers = init_hdl(**init_kwargs)
    # 创建通信桥控制器。
    bri_worker = Controller(mapping=_process_bri)

    # 子进程全局实例容器。
    instances_box = {'workers': workers, 'bri_worker': bri_worker}
    # 为了使得在工作函数中方便引用一些必要的内部对象，更新所有工作线程控制器和通信桥控制器的静态上下文。
    static = {'workers': workers, 'bri_worker': bri_worker, 'instances': instances_box}

    # 同步父子进程的状态事件。
    idle_event, workers_queue_empty_event = sync_events
    setattr(bri_worker, '_Controller__idle', idle_event)

    workers_init()
    bri_workers_init()

    # 开启通信控制器和工作线程控制器。
    workers.run()
    bri_worker.run()
    # 等待控制器的线程退出。
    bri_worker.wait()
    workers.wait()

    # 注意：这里需要传递值True，这将用于告诉父进程的控制器shutdown事件是子进程要求的关闭。
    # 这是因为父进程控制器的shutdown事件只是用于关闭子进程的控制器，
    # 而父进程的控制器需要完全等待子进程关闭后才进行的关闭操作。
    # 这才能保证了返回返回消息队列的完整。
    bri_worker.message(EVT_DRI_SHUTDOWN, True)



