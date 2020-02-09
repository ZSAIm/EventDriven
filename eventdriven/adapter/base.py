# -*- coding: UTF-8 -*-
from abc import ABC
from ..error import UniqueAdapterInstance


class BaseAdapater(ABC):

    def __setup__(self, parent, name, **options):
        """ 安装适配器过程中调用该方法进行初始化。 """
        self._parent = parent
        self._instance_name = name
        self._options = options

    def __name__(self):
        """ 返回适配器实例名称。 """
        return self._instance_name

    def __patch__(self):
        """ __setup__ 之后对控制器进行打补丁。 """
        pass

    def __run__(self):
        """ 控制器启动后调用该方法。 """
        pass

    def __closing__(self):
        """ 控制器发起关闭事件后调用该方法。 """
        pass

    def __closed__(self):
        """ 控制事件关闭后调用该方法。"""
        pass

    def __exception__(self):
        """ 控制器事件处理异常调用该方法。"""
        pass

    def __suspend__(self):
        """ 控制器发起挂起事件后调用该方法。 """
        pass

    def __resume__(self):
        """ 控制器发起恢复挂起状态事件后调用该方法。 """
        pass

    def __mapping__(self):
        """ 返回添加的事件处理映射。 """
        return {}

    def __context__(self):
        """ 返回添加的全局上下文。"""
        return {}

    @staticmethod
    def __unique__():
        """ 返回是否只能安装唯一实例。 """
        return False

    @staticmethod
    def __dependencies__():
        """ 返回适配器依赖列表。 """
        return []


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
        if not isinstance(adapter, BaseAdapater):
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

        # 添加全局上下文。
        self._parent.__global__.update(adapter.__context__())

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