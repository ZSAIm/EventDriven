# -*- coding: UTF-8 -*-

from evtdrivn.signal import EVT_DRI_AFTER, EVT_DRI_BEFORE
from functools import wraps
from collections import defaultdict


class MappingBlueprint:
    """ 控制器事件处理映射蓝图。 """
    def __init__(self):
        self.__registers = defaultdict(list)

    def __iter__(self):
        return iter(self.__registers.items())

    def inherit_from(self, blueprint):
        """ 从另一个蓝图继承事件处理映射。 """
        self.__registers.update(dict(blueprint))

    def register(self, evt):
        """ 注册函数处理映射。 """
        def wrapper(func):
            self.__registers[evt].append(func)
            @wraps(func)
            def wrapped(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapped
        return wrapper

    def hook_before(self):
        """ 事件处理函数之间。 """
        return self.register(EVT_DRI_BEFORE)

    def hook_after(self):
        """ 事件处理函数之后。 """
        return self.register(EVT_DRI_AFTER)


class MappingManager:
    def __init__(self, mapping=None):
        self._mapping = None
        self.set(mapping or {})

    def load_from_blueprint(self, blueprint):
        """ 从事件映射蓝图中加载。 """
        self._mapping = defaultdict(list)
        self.update_from_dict(dict(blueprint))

    def update_from_dict(self, d):
        """ 从字典中更新事件处理映射。 """
        for k, v in dict(d).items():
            if type(v) in (list, tuple):
                self._mapping[k] = list(v)
            else:
                self._mapping[k] = [v]

    def set(self, mapping):
        """ 清空事件映射后更新事件映射。 """
        self.clear()
        self.update_from_dict(mapping)

    def clear(self):
        """ 清空事件映射。 """
        self._mapping = defaultdict(list)

    def remove(self, evt):
        """ 删除指定事件映射。 """
        self._mapping.pop(evt)

    def add(self, evt, *func):
        """ 添加事件映射。 """
        for f in func:
            self._mapping[evt].append(f)

    def get(self, k, default=None):
        return self._mapping.get(k, default)

    def __contains__(self, item):
        return item in self._mapping

    def __getitem__(self, item):
        return self._mapping[item]

    def __iter__(self):
        return iter(self._mapping.items())


