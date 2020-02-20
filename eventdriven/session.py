
from threading import local


class Session(local):
    """ 会话线程变量。
    作为事件处理的会话变量，存在两种上下文形式：

    静态上下文：（以会话变量项的形式引用）
        session['example']

    动态上下文：（以属性形式）
    """
    def __init__(self):
        super(Session, self).__init__()
        self.__static = {}

    @property
    def __static__(self):
        return self.__static

    @__static__.setter
    def __static__(self, value):
        self.__static = dict(value)

    @property
    def __vars__(self):
        d = dict(self.__dict__)
        d.pop('_Session__static')
        return d

    def __getitem__(self, item):
        return self.__static[item]

    def __setitem__(self, key, value):
        self.__static[key] = value

    def __delitem__(self, key):
        del self.__static[key]

    def __context__(self, context):
        context = context.copy()
        context['_Session__static'] = self.__static
        self.__dict__.clear()
        self.__dict__.update(context)


session = Session()
