# -*- coding: UTF-8 -*-


class Error(Exception):
    pass


class ListenerAlreadyExisted(Error):
    """ 使用对应通道的监听者已经存在了。"""
