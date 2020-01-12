EventDriven
=========================

# 事件驱动控制器

[![Build Status](https://img.shields.io/badge/build-beta-red.svg)](https://github.com/ZSAIm/EventDriven)
[![Build Status](https://img.shields.io/badge/pypi-v0.0.0-blue.svg)](https://pypi.org/project/EventDriven/)

EventDriven 是一个事件驱动控制器。它维持一个事件循环线程，通过提交事件的方式控制控制器执行任务。


## 安装

    $ pip install EventDriven


## 使用方法(例子说明)

### 事件控制器

```python
from evtdrivn import Controller, session
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
from evtdrivn import Controller, MappingBlueprint
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


### 使用控制器插件
```python
from evtdrivn import Controller, session
from evtdrivn.plugins.subprocess import Subprocess

from multiprocessing import current_process

from evtdrivn import Controller, session
from evtdrivn.plugins.subprocess import Subprocess
from multiprocessing import current_process


def subprocess_print_dispatch(*args):
    print('pid: %s, dishaptch args=%s' % (current_process().pid, args))


def subprocess_print_submit(*args):
    print('pid: %s, submit() args=%s' % (current_process().pid, args))
    session['self'].shutdown()


if __name__ == '__main__':
    print('main pid: %s' % current_process().pid)
    con = Controller()
    con.add_plugin(Subprocess(mapping={'1': subprocess_print_dispatch}))

    con.run()
    con.dispatch('1', args=(1, 2, 3, 4, 5))
    con.submit(subprocess_print_submit, args=(1, 2, 3, 4, 5))

    con.wait()


```

### 控制器的事件监听

```python
from evtdrivn import Controller, session

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




