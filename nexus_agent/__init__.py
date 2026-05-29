import sys
try:
    import lark_agent
except ImportError:
    sys.modules['lark_agent'] = sys.modules[__name__]

from .agent import root_agent

