EventDriven
=========================

# 事件驱动控制器

[![Build Status](https://img.shields.io/badge/build-beta-red.svg)](https://github.com/ZSAIm/EventDriven)
[![Build Status](https://img.shields.io/badge/pypi-v0.1.0-blue.svg)](https://pypi.org/project/EventDriven/)

EventDriven 是一个事件驱动控制器。它维持一个事件循环线程，通过提交事件的方式控制控制器执行任务。

## 安装

    $ pip install EventDriven


## 项目说明

### Controller

> 工作原理

```
+------------------------------------+   instance
|          AdapterManager            |<------------ Adapter
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
```

> 说明

- 调用控制器方法 ``dispatch`` 派遣事件EVT（调用控制器方法``submit`` 提交处理函数）。

- ``事件映射管理器(MappingManager)``收到``事件EVT``后搜索其对应的``处理函数(func)``，并将其交由``事件处理循环线程(EventLoop)``处理（提交的处理函数直接交由处理）。

- ``事件处理循环线程``收到``处理函数(func)``后，生成``执行会话(Session)``，并调用函数func。

通过方法``Adapter``可以为控制器``添加适配器``。

### ControllerPool

> 工作原理

```
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
|           ClientPool               |------------------>----------------------------------->----------- ...
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
```

> 说明

- 调用方法``dispatch/submit``，将``事件(EVT)/处理函数(func)``交由``控制器池(ControllerPool)``。

- ``控制器池``收到处理事件，将其推入``待处理事件队列(event_queue)``。

- ``控制器池客户端线程``从``待处理事件队列``中取``待处理事件``，并交给空闲的``客户端控制器``处理，同时返回``待决事件对象(Pending)``。


### 内置适配器 

#### Subprocess(实现控制器支持在子进程操作)

> 工作原理

``` 
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
            |               |  AdapterManager|                    |         |                v     |
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
```

> 说明

- 通过调用``父进程的控制器``方法``dispatch/submit``，将``事件(EVT)/处理函数(func)``发送给``父进程控制器``，同时返回``待决事件对象Pending``。

- ``父进程控制器``将收到的``事件/处理函数``通过``子通信通道(child_channel)``发送到``子进程``的``通信控制器(bri_worker)``。

- ``子进程的通信控制器``再次将``事件/处理函数``转发到``子进程的工作线程池(workers)``。

- ``子进程的工作线程池``处理收到的``事件/处理函数``，事件的``处理会话``结束后将``返回的执行结果``转发到``子进程的通信控制器``。

- ``子进程的通信控制器``将``执行返回结果``通过``父通信通道(parent_channel)``发送到``父进程的控制器``，并通过``待决事件对象(Pending)``返回运行结果。


#### Timer

> 添加定时信号生成器

``` 
+------------------------------------+
|          AdapterManager            |
|                      +---------+   |
|                      |  Timer  |   |     x  
|                      |  x sec  |<--------------- set_timing
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
```

> 说明

- ``定时器(Timer)``通过``初始化``时设定或者方法``set_timing``设定每隔``x``秒发送``时钟事件信号(EVT_DRI_TIMING)``给控制器。

- 通过可以通过方法``set_timing``方法使用``None``作为事件间隔的参数设定时，将停止定时器。

#### Pending

> 事件派遣返回待决事件对象Pending。

``` 
+------------------------------------+
|          AdapterManager            |
|                     +---------+    |
|                     | Pending |    |
|                     +---------+    |
+------------------------------------+
|                                    |
|           func   +-----------------+     EVT
|        +---------|  MappingManager |<------------ dispatch
|        |         +-----------------+——————————-—> Pending
|        |                           |   return
|        v                           |
+------------------------------------+
|                                    |     func
|           EventLoop                |<------------ submit
|                                    |------------> Pending
+------------------------------------+   return
```

> 说明

- 为控制器打补丁，方法``dispatch``和``submit``的调用后将返回``待决事件(Pending)``对象。

- ``Pending对象``通过调用方法``pend``来等待事件的处理，并且以列表的形式返回事件的执行结果。


## 使用例子

### 事件控制器

```python
from eventdriven import Controller, session
from time import time

def event_func():
    runtime = time() - session.start_time
    evt_time = time() - session.evt_time
    print(runtime)
    print(evt_time)

def submit_func(a, b):
    runtime = time() - session.start_time
    print(runtime)
    print(a, b)
    # session['self'] 是当前控制器对象。
    session['self'].shutdown()

con = Controller(mapping={
    1: event_func
}, context={'start_time': time()})
con.run()
# 事件驱动，事件1
con.dispatch(1, context={'evt_time': time()})
# 任务驱动
con.submit(submit_func, args=('a', 'b'))
# 控制器阻塞等待
con.wait()

```

### 使用事件映射蓝图
```python
from eventdriven import Controller, MappingBlueprint
test_model = MappingBlueprint()

EVT_ONE = 'one'
EVT_TWO = 'two'
EVT_THREE = 'three'

@test_model.register(EVT_ONE)
def event_1():
    print('1')

@test_model.register(EVT_TWO)
def event_2():
    print('2')

@test_model.register(EVT_THREE)
def event_3():
    print('3')

con = Controller(mapping=test_model)

con.run()

con.dispatch(EVT_THREE)
con.dispatch(EVT_ONE)
con.dispatch(EVT_TWO)
con.shutdown()
con.wait()

```


### 使用适配器(Adapter)
```python
from eventdriven import Controller, session
from eventdriven.adapter.subprocess import Subprocess

from multiprocessing import current_process

from eventdriven import Controller, session
from eventdriven.adapter.subprocess import Subprocess
from multiprocessing import current_process


def subprocess_print_dispatch(*args):
    print('pid: %s, dishaptch args=%s' % (current_process().pid, args))


def subprocess_print_submit(*args):
    print('pid: %s, submit() args=%s' % (current_process().pid, args))


if __name__ == '__main__':
    print('main pid: %s' % current_process().pid)
    con = Controller()
    con.Adapter(Subprocess(mapping={'1': subprocess_print_dispatch}, maxsize=10))

    con.run()
    con.dispatch('1', args=(1, 2, 3, 4, 5))
    con.submit(subprocess_print_submit, args=(1, 2, 3, 4, 5))
    for i in range(100):
        con.submit(subprocess_print_submit, args=(i,))
    
    con.pend()
    con.shutdown()
    con.wait()


```

### 控制器的事件监听

```python
from eventdriven import Controller, session

def event_track():
    print('event_track: %s=> val=%s' % (session['self'].name, session['val']))


def cpy_event():
    print('event_track(copy): %s=> val=%s' % (session['self'].name, session['val']))


con1 = Controller(mapping={'cpy': event_track})
con2 = Controller(mapping={'cpy': cpy_event})

con1.run()
con2.run()
# con2监听con1的事件'cpy'
con2.listen(con1, allow=('cpy',))

con1.dispatch('cpy', value='hhhhhhhh', context={'a_ctx': 'ok'})


# 结果：
# event_track: 2771409784584=> val=hhhhhhhh
# event_track(copy): 2771412370056=> val=ForwardingPacket(value='hhhhhhhh', context={'a_ctx': 'ok'})

```




