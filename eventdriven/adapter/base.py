# -*- coding: UTF-8 -*-
from abc import ABC


class AbstractAdapter(ABC):

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

    def __running__(self):
        """ 控制器启动中（线程启动前）。"""
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

    def __exception__(self, error):
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
        """ 返回需要添加的全局动态上下文。"""
        return {}

    def __static__(self):
        """ 返回需要添加的静态上下文。"""
        return {}

    @staticmethod
    def __unique__():
        """ 返回是否只能安装唯一实例。 """
        return False

    @staticmethod
    def __dependencies__():
        """ 返回适配器依赖列表。 """
        return []


