import sys
import threading
from contextlib import contextmanager
import functools
try:
    import __builtin__ as builtins
except ImportError:
    import builtins # python 3+
from types import ModuleType

router = threading.local()

routed_modules = ["os", "os.path", "platform", "ntpath", "posixpath", "zipimport", "genericpath", 
    "posix", "nt", "signal", "time", "sysconfig", "_locale", "locale", "socket", "_socket", "ssl", "_ssl",
    "struct", "_struct", "_symtable", "errno", "fcntl", "grp", "imp", "pwd", "select", "spwd", 
    "syslog", "thread", "_io", "io", "subprocess", "_subprocess", "datetime", "mmap", "msvcrt"]

class RoutedModule(ModuleType):
    def __init__(self, realmod):
        ModuleType.__init__(self, realmod.__name__, getattr(realmod, "__doc__", None))
        object.__setattr__(self, "__realmod__", realmod)
        object.__setattr__(self, "__file__", getattr(realmod, "__file__", None))
    def __repr__(self):
        modname = object.__getattribute__(self, "__name__")
        try:
            self.__currmod__
        except AttributeError:
            return "<module %r (stale)>" % (modname,)
        else:
            if self.__file__:
                return "<module %r from %r>" % (modname, self.__file__)
            else:
                return "<module %r (built-in)>" % (modname,)
    def __dir__(self):
        return dir(self.__currmod__)
    def __getattribute__(self, name):
        if name == "__realmod__":
            return object.__getattribute__(self, "__realmod__")
        elif name == "__name__":
            return object.__getattribute__(self, "__name__")
        elif name == "__currmod__":
            modname = object.__getattribute__(self, "__name__")
            if hasattr(router, "conn"):
                try:
                    return router.conn.modules[modname]
                except ImportError:
                    pass
            if modname in sys.modules:
                return object.__getattribute__(self, "__realmod__")
            else:
                raise AttributeError("No module named %s" % (modname,))
        else:
            return getattr(self.__currmod__, name)
    def __setattr__(self, name, val):
        return setattr(self.__currmod__, name, val)

routed_sys_attrs = set(["byteorder", "platform", "getfilesystemencoding", "getdefaultencoding"])

class SysModule(ModuleType):
    def __init__(self):
        ModuleType.__init__(self, "sys", sys.__doc__)
    def __dir__(self):
        return dir(self.__currmod__)
    def __getattribute__(self, name):
        if name in routed_sys_attrs and hasattr(router, "conn"):
            return getattr(router.conn.modules["sys"], name)
        else:
            return getattr(sys, name)
    def __setattr__(self, name, value):
        if name in routed_sys_attrs and hasattr(router, "conn"):
            setattr(router.conn.modules["sys"], name, value)
        else:
            setattr(sys, name, value)

sys2 = SysModule()

_orig_import = builtins.__import__

def _importer(modname, *args, **kwargs):
    if modname in sys.modules:
        mod = sys.modules[modname]
        if isinstance(mod, (RoutedModule, SysModule)):
            return mod
    elif hasattr(router, "conn"):
        try:
            mod = _orig_import(modname, *args, **kwargs)
        except ImportError:
            mod = router.conn.modules[modname]
    else:
        mod = _orig_import(modname, *args, **kwargs)
    rmod = RoutedModule(mod)
    sys.modules[modname] = rmod
    return rmod

_enabled = False
_prev_builtins = {}

def enable():
    """Enables (activates) the Splitbrain machinery"""
    global _enabled
    if _enabled:
        return
    sys.modules["sys"] = sys2
    for modname in routed_modules:
        try:
            realmod = __import__(modname, [], [], "*")
        except ImportError:
            pass
        else:
            sys.modules[modname] = RoutedModule(realmod)
    builtins.__import__ = _importer
    for funcname in ["open", "execfile", "file"]:
        if not hasattr(builtins, funcname):
            continue
        def mkfunc(funcname, origfunc):
            @functools.wraps(getattr(builtins, funcname))
            def tlbuiltin(*args, **kwargs):
                if hasattr(router, "conn"):
                    func = getattr(router.conn.builtins, funcname)
                else:
                    func = origfunc
                return func(*args, **kwargs)
            return tlbuiltin
        origfunc = getattr(builtins, funcname)
        _prev_builtins[funcname] = origfunc
        setattr(builtins, funcname, mkfunc(funcname, origfunc))
    
    _enabled = True

def disable():
    """Disables (restores) the Splitbrain machinery"""
    global _enabled
    if not _enabled:
        return
    _enabled = False
    for funcname, origfunc in _prev_builtins.items():
        setattr(builtins, funcname, origfunc)
    for modname, mod in sys.modules.items():
        if isinstance(mod, RoutedModule):
            sys.modules[modname] = mod.__realmod__
    sys.modules["sys"] = sys
    builtins.__import__ = _orig_import

@contextmanager
def splitbrain(conn):
    """Enter a splitbrain context in which imports take place over the given RPyC connection (expected to 
    be a SlaveService). You can enter this context only after calling ``enable()``"""
    if not _enabled:
        raise ValueError("Splitbrain not enabled")
    prev_conn = getattr(router, "conn", None)
    prev_modules = sys.modules.copy()
    router.conn = conn
    prev_stdin = conn.modules.sys.stdin
    prev_stdout = conn.modules.sys.stdout
    prev_stderr = conn.modules.sys.stderr
    conn.modules["sys"].stdin = sys.stdin
    conn.modules["sys"].stdout = sys.stdout
    conn.modules["sys"].stderr = sys.stderr
    try:
        yield
    finally:
        conn.modules["sys"].stdin = prev_stdin
        conn.modules["sys"].stdout = prev_stdout
        conn.modules["sys"].stderr = prev_stderr
        sys.modules.clear()
        sys.modules.update(prev_modules)
        router.conn = prev_conn
        if not router.conn:
            del router.conn

@contextmanager
def localbrain():
    """Return to operate on the local machine. You can enter this context only after calling ``enable()``"""
    if not _enabled:
        raise ValueError("Splitbrain not enabled")
    prev_conn = getattr(router, "conn", None)
    prev_modules = sys.modules.copy()
    if hasattr(router, "conn"):
        del router.conn
    try:
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(prev_modules)
        router.conn = prev_conn
        if not router.conn:
            del router.conn


if __name__ == "__main__":
    import rpyc
    enable()

    with rpyc.classic.connect("192.168.1.143") as c:
#        import os
#        print 1, os.getcwd()
#        from os.path import abspath
#        print 2, abspath(".")
#        
#        with splitbrain(c):
#            print 3, abspath(".")
#            from os.path import abspath
#            print 4, abspath(".")
#            print 5, os.getcwd()
#            import twisted
#            print 6, twisted
#            
#            with localbrain():
#                print 6.1, os.getcwd()
#    
#        print 7, twisted
#        try:
#            print twisted.version
#        except AttributeError:
#            print 8, "can't access twisted.version"
#        else:
#            assert False
#    
#        try:
#            import twisted
#        except ImportError:
#            print 9, "can't import twisted"
#        else:
#            assert False
#    
#        with splitbrain(c):
#            print 10, twisted
#            print 11, twisted.version
#        print "======================================================================"
#        
#        import socket
#        s = socket.socket()
#        s.bind(("192.168.1.101", 23))
#        s.listen(1)
#        host, port = s.getsockname()
#        import telnetlib
#        print telnetlib.socket
#
#        import subprocess
#    
#        print repr(subprocess.check_output(["net", "use"])[:100])
#        
        import os
        import plumbum
        print os.name
        print plumbum.local.which("net")
        import win32file
        print type(plumbum)
        print type(win32file)
        
        with splitbrain(c):
            #print telnetlib.socket
            #t = telnetlib.Telnet(host, port, timeout = 5)
            #print t.sock.getsockname()
            #t.close()
            #c.execute("print 5")
            #print repr(subprocess.check_output(["lsmod"])[:100])

            print win32file.CreateFile
            print os.name

            #print type(plumbum), plumbum
            #print type(plumbum), plumbum
            
            # plumbum holds a local cache of the environment, we have to refresh it
            #local.env.__init__()
            print plumbum.local.which("lsmod")

        print plumbum.local.which("net")
        
        #print plumbum.local.which("net")




