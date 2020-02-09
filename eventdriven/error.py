# -*- coding: UTF-8 -*-


class Error(Exception):
    pass


class ListenerAlreadyExisted(Error):
    """ 使用对应通道的监听者已经存在了。"""


class UniqueAdapterInstance(Error):
    pass


class DisabledDispatch(Error):
    """ 派遣事件被禁用。"""

