import time
import types
import threading
import inspect
from collections import defaultdict
from . import io
from . import version

__version__ = version.__version__
__all__ = [
    'testbench',
    'module',
    'pure',
    'is_worker_running',
]


# @testbench decorator
def testbench(func):
    '''
    A decorator to mark a testbench function.

    This decorator can be used to define a testbench function.
    '''
    def _testbench_decorator(module_instance=None):
        if module_instance:
            if module_instance.__class__.__name__ not in module.module_instances:
                print(inspect.getsourcelines(func)[0][1])
                raise RuntimeError(
                    'The argument of testbench must be an instance of the module class'
                )
            module_instance._start()
            func(module_instance)
            module_instance._stop()
        else:
            func()
    return _testbench_decorator


# @pure decorator
def pure(func):
    '''
    A decorator to mark a pure Python function.

    This decorator can be used to define a pure Python function.
    Within the pure function you can execute any Python code at compile time.
    The pure function has the following restrictions.
      * It must be a function defined in global scope
      * The call argument must be a constant
      * The return value (if any) must be compilable with the Polyphony compiler
    '''
    def _pure_decorator(*args, **kwargs):

        return func(*args, **kwargs)
    _pure_decorator.func = func
    return _pure_decorator


_is_worker_running = False


def is_worker_running():
    '''
    Returns True if the worker is in the running state, False otherwise.

    Notes
    -----
    This function is provided to stop the worker function in the simulation with Python interpreter.
    In the course of compiling to HDL, this function is always replaced with True.
    '''
    return _is_worker_running


def _module_start(self):
    global _is_worker_running
    if _is_worker_running:
        return
    _is_worker_running = True
    io._enable()
    for w in self._workers:
        w.start()
    time.sleep(0.001)


def _module_stop(self):
    global _is_worker_running
    if not _is_worker_running:
        return
    _is_worker_running = False
    for w in self._workers:
        w.prejoin()
    io._disable()
    for w in self._workers:
        w.join()


def _module_append_worker(self, fn, *args):
    self._workers.append(_Worker(fn, *args))


def _module_deepcopy(self, memo):
    return self


class _ModuleDecorator(object):
    def __init__(self):
        self.module_instances = defaultdict(list)

    def __call__(self, cls):
        def _module_decorator(*args, **kwargs):
            instance = object.__new__(cls)
            instance._start = types.MethodType(_module_start, instance)
            instance._stop = types.MethodType(_module_stop, instance)
            instance.__deepcopy__ = types.MethodType(_module_deepcopy, instance)
            if instance.__init__.__name__ == '_pure_decorator':
                ctor = types.MethodType(instance.__init__.func, instance)
            else:
                ctor = instance.__init__
            instance._ctor = ctor
            instance.append_worker = types.MethodType(_module_append_worker, instance)
            instance._module_decorator = self
            io._enable()
            setattr(instance, '_workers', [])
            instance.__init__(*args, **kwargs)
            io._disable()
            self.module_instances[cls.__name__].append(instance)
            return instance
        _module_decorator.__dict__ = cls.__dict__.copy()
        _module_decorator.cls = cls
        return _module_decorator

    def abort(self):
        for instances in self.module_instances.values():
            for inst in instances:
                inst._stop()


# @module decorator
module = _ModuleDecorator()


class _Worker(threading.Thread):
    def __init__(self, func, *args):
        super().__init__()
        self.func = func
        self.args = args
        self.daemon = True

    def run(self):
        try:
            if self.args:
                self.func(*self.args)
            else:
                self.func()
        except io.PolyphonyIOException as e:
            module.abort()
        except Exception as e:
            module.abort()
            raise e

    def prejoin(self):
        super().join(0.01)
