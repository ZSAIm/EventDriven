# -*- coding: UTF-8 -*-
from abc import ABC


class BasePlugin(ABC):

    def __setup__(self, parent, name, **options):
        """ 安装插件过程中调用该方法进行初始化插件。 """
        self._parent = parent
        self._instance_name = name
        self._options = options

    def __name__(self):
        """ 返回插件实例名称。 """
        return self._instance_name

    def __patch__(self):
        """ __setup__ 之后对控制器进行打补丁。 """
        pass

    def __run__(self):
        """ 控制器启动后调用该方法。 """
        pass

    def __close__(self):
        """ 控制器发起关闭事件后调用该方法。 """
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
        """ 返回插件依赖列表。 """
        return []


class UniquePluginInstance(Exception):
    pass


class PluginManager:
    def __init__(self, parent, *init_plug):
        self._parent = parent
        self.__plugins = {}
        for p in init_plug:
            self.install(p)
        self.__name_plugin = {}

    def install(self, plugin):
        """ 安装插件。
        :param
            plugin  : 插件实例。
        :return
            返回实例化的插件名称。
        """
        if not isinstance(plugin, BasePlugin):
            raise TypeError('can only install plugin instance inherited from BasePlugin.')

        pt = type(plugin)
        if pt not in self.__plugins:
            self.__plugins[pt] = []
        else:
            # 检查插件是否只允许安装一个实例。
            if plugin.__unique__():
                raise UniquePluginInstance('this plugin can only install one instance.')

        # 安装插件依赖。
        for dependency in plugin.__dependencies__():
            if type(dependency) not in self.__plugins:
                self.install(dependency)

        # 引入插件实例。
        name = plugin.__class__.__name__.lower()
        if hasattr(self, name):
            cnt = 0
            while True:
                cnt += 1
                if not hasattr(self, name + str(cnt)):
                    break
            name = name + str(cnt)

        # 安装插件。
        plugin.__setup__(self._parent, name)
        # 打补丁。
        plugin.__patch__()
        # 安装事件处理函数。
        # 为了支持多个插件添加同一事件，这里使用添加而不是使用更新覆盖的方法。
        for evt, hdl in plugin.__mapping__().items():
            self._parent.mapping.add(evt, hdl)

        # 添加全局上下文。
        self._parent.__global__.update(plugin.__context__())

        self.__name_plugin[name] = plugin
        self.__plugins[pt].append(plugin)

        return name

    def __iter__(self):
        """ 返回所有的插件实例。 """
        return iter(self.__plugins.values())

    def __getitem__(self, item):
        return self.__name_plugin[item]