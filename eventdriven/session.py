
from threading import local


class Session(local):
    def __init__(self):
        super(Session, self).__init__()
        self.__static = {}

    @property
    def __static__(self):
        return self.__static

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
