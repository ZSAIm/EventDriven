
__all__ = [
    'EVT_DRI_SHUTDOWN', 'EVT_DRI_SUBMIT', 'EVT_DRI_BEFORE',
    'EVT_DRI_AFTER', 'EVT_DRI_OTHER',
    'EVT_DRI_CLEAN', 'EVT_DRI_ERROR'
]
# 控制器关闭事件
EVT_DRI_SHUTDOWN = '|EVT|SHUTDOWN|'
# 控制器处理函数提交事件
EVT_DRI_SUBMIT = '|EVT|SUBMIT|'
# 控制器处理函数在处理前的事件
EVT_DRI_BEFORE = '|EVT|BEFORE|'
# 控制器处理函数在处理后的事件
EVT_DRI_AFTER = '|EVT|AFTER|'
# 控制器默认处理函数事件
EVT_DRI_OTHER = '|EVT|OTHER|'
# 控制器事件队列清理事件
EVT_DRI_CLEAN = '|EVT|CLEAN|'
# 控制器事件处理异常错误事件
EVT_DRI_ERROR = '|EVT|ERROR|'

